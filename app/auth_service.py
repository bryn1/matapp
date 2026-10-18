"""app.auth_service — server-side opaque session store + login/logout/me logic (Phase 3 T2 auth).

The session-cookie value is UNPREDICTABLE and OPAQUE (``secrets.token_urlsafe``), and
holds no user data; the token -> username mapping lives server-side in a SessionStore.
This is the auth-seam PHASE0-map §A2 requires: "server-sidig session (opak cookie) —
ingen egen krypto, bibliotek". We store the token, the owning username and the two
expiry timestamps S3 (MC 10468) added: issued-at and last-seen.

Single concern: session lifecycle + identity resolution against the users table.
Password hashing stays in app/security.py; HTTP wire-up is in app/routers/auth.py.

NOTE (Phase 3 scope): sessions are held in-memory for this phase — restarting the
process drops them. That is acceptable for the C2 harness; persisting sessions to the
DB is deferred (Phase 5 / api-lager can back this store with a table if required).

FILE HYGIENE (MC 10469): this file crosses the 400-line HARD cap by design — the
S4 session-knob clamp with its c2 bounds + fallback warning (gate DA-F2/F3) is
the one app/ change the wave allows. The named LoginRateLimiter split is
DEFERRED to MC 10711 (separate wave); splitting here would touch every consumer.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import time

from fastapi import HTTPException, Request

from app import security
from app import db as dbm
import app.models  # noqa: F401  (register users table before queries)
from app.models.users import User


def current_user_or_401(request: Request) -> User:
    """FastAPI dependency: resolve the session cookie to a User; 401 otherwise.

    The ONE shared auth-gate dependency (MC 1355.16): the profile and menu
    routers already inline this exact gate; the stores router's new
    ``?postal_code=`` preview reuses THIS copy instead of a third inline one
    (T10b §4 — same gate as profile/menu, 401 never 200).
    """
    token = request.cookies.get(security.SESSION_COOKIE)
    user = current_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


# S4 c2 accept-range for the session knobs (gate DA-F2, 202610150832):
# MIN 60 s — routers/auth int()'s the TTL into Set-Cookie Max-Age, so any value
# below 1 shipped a dead-on-arrival cookie (gate-measured TTL=0.4 -> Max-Age=0
# kills login fully suite-green); sub-minute lifetimes are operationally
# meaningless anyway. MAX 10 y — beyond that a session never effectively
# expires (gate-measured TTL=1e308 emitted a 309-digit Max-Age), the posture
# this clamp exists to forbid.
SESSION_KNOB_MIN_SECONDS = 60.0
SESSION_KNOB_MAX_SECONDS = 315_360_000.0
# Strict digit grammar — float() alone accepts Python underscore literals, so a
# "1_000" typo would otherwise silently ship as a real (16-minute) policy.
_KNOB_RE = re.compile(r"(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def _env_seconds(name: str, default: float) -> float:
    """Session-knob clamp (S4 F3-deferred + c2 bounds, MC 10469): unset -> the
    shipped default silently; set-but-insane (grammar, range, inf via huge
    exponent) -> the shipped default WITH one warning line on the app logger
    (existing idiom, see app/db.py) — never a crash-boot, never a zero- or
    infinite-expiry policy, never a silently-discarded operator override."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = None
    if _KNOB_RE.fullmatch(raw):
        cand = float(raw)  # "1e400" -> inf without raising; bounds reject it
        if SESSION_KNOB_MIN_SECONDS <= cand <= SESSION_KNOB_MAX_SECONDS:
            val = cand
    if val is None:
        logging.getLogger("kvallsmat.app").warning(
            "%s=%r is not a session knob in %g..%g seconds; using shipped "
            "default %g", name, raw, SESSION_KNOB_MIN_SECONDS,
            SESSION_KNOB_MAX_SECONDS, default)
        return default
    return val


class SessionStore:
    """Thread-safe mapping of opaque session token -> (username, issued, last_seen).

    ``token`` is server-generated with high entropy (unpredictable/opaque) and is
    the ONLY thing stored in the cookie.

    S3 (MC 10468): sessions EXPIRE — a fixed absolute TTL since issue plus an
    idle timeout since the last request. An expired lookup behaves EXACTLY like
    an unknown token (``get`` returns None -> the SAME 401, no oracle that can
    tell "expired" from "never issued"), and the expired entry is evicted on
    that access. The map is BOUNDED: create() (login/register, i.e. every
    session issue) runs a cheap expiry sweep and then trims oldest-inserted to
    MAX_SESSIONS — same bounded-memory idiom as LoginRateLimiter._bound_state,
    no scheduler, no new dependency. ``now`` stays injectable on all
    mutating/querying methods — the clock seam the tests use (S2 idiom).
    """

    # Env overrides let an operator (and the runtime-proof harness) shorten
    # expiry without a code change; the Set-Cookie Max-Age follows TTL.
    TTL = _env_seconds("MATAPP_SESSION_TTL", 30 * 24 * 3600)
    IDLE_TIMEOUT = _env_seconds("MATAPP_SESSION_IDLE", 14 * 24 * 3600)
    MAX_SESSIONS = 10_000  # size cap (S3: no unbounded growth)

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[str, float, float]] = {}
        self._lock = threading.Lock()

    def _expired(self, issued: float, last_seen: float, now: float) -> bool:
        """True iff past the absolute TTL or idle past IDLE_TIMEOUT."""
        return now - issued >= self.TTL or now - last_seen >= self.IDLE_TIMEOUT

    def create(self, username: str, now: float | None = None) -> str:
        """Issue a new opaque token bound to *username*; sweep + bound (S3)."""
        now = time.monotonic() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (username, now, now)
            self._sweep(now)
        return token

    def get(self, token: str, now: float | None = None) -> str | None:
        """Return the owning username iff *token* is a live session; else None.

        Expired entries are deleted here and answered exactly like unknown
        tokens; a live hit refreshes last_seen (the idle clock restarts).
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return None
            username, issued, last_seen = entry
            if self._expired(issued, last_seen, now):
                del self._sessions[token]
                return None
            self._sessions[token] = (username, issued, now)
            return username

    def delete(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def _sweep(self, now: float) -> None:
        """Drop expired sessions, then oldest-inserted to enforce MAX_SESSIONS.

        Called with ``self._lock`` held, from create() only — login/register is
        where new entries arrive, so that is where bounding pays off (S3).
        Evicted sessions simply must log in again, exactly like a process
        restart forces today.
        """
        for token in [t for t, (_, issued, seen) in self._sessions.items()
                      if self._expired(issued, seen, now)]:
            del self._sessions[token]
        while len(self._sessions) > self.MAX_SESSIONS:
            del self._sessions[next(iter(self._sessions))]  # oldest-inserted first


# Module-level singleton used by the router; tests may substitute a fresh store.
sessions = SessionStore()


# MC 10711 split: the limiter lives in app.auth_limiter; the re-import keeps the names in THIS
# module's namespace, so every consumer (router, conftest, tests) reads the SAME objects as before.
from app.auth_limiter import LoginLockedOut, LoginRateLimiter, login_limiter  # noqa: E402,F401


def authenticate(username: str, password: str) -> User | None:
    """Return the User iff *username* exists and *password* matches its hash.

    Timing-equalized (audit 1188 F3): an unknown username runs the SAME argon2
    verify against a module-level dummy hash (generated at import from a fixed
    dummy string) so the response time matches an existing username's. Target:
    existing vs non-existing within 2x.
    """
    if dbm._Session is None:
        dbm.boot()
    session = dbm._Session()
    try:
        user = session.query(User).filter_by(username=username).first()
        if user is None:
            security.verify_password(password, security.DUMMY_HASH)
            return None
        if not security.verify_password(password, user.password_hash):
            return None
        return user
    finally:
        session.close()


def login(username: str, password: str, client_ip: str | None = None) -> str | None:
    """create an opaque session token for *username* iff creds are valid, else None.

    Raises LoginLockedOut when *username* is currently locked out (audit 1188 F2)
    or — S2 (MC 10467) — when *client_ip* has sprayed MAX_FAILURES failed logins
    across usernames inside WINDOW (router maps either to 429). A failed attempt
    is counted per username AND per client IP; a successful one resets only the
    username counter, never the IP bucket.
    """
    if login_limiter.is_locked(username):
        raise LoginLockedOut(username)
    if client_ip is not None and login_limiter.is_ip_throttled(client_ip):
        raise LoginLockedOut(client_ip)
    if username.startswith(login_limiter.IP_PREFIX):
        # DA P0 (MC 10467 c2): the "ip:" namespace belongs to the IP throttle
        # keys ALONE. Before this guard a username "ip:1.2.3.4" reached
        # record_failure() and wrote THAT address's IP bucket, letting anyone
        # lock honest clients out of login+register for LOCKOUT. Reject it as a
        # bad credential BEFORE any username is recorded (no enumeration leak:
        # the F3 dummy verify still runs so timing matches an unknown username),
        # while still counting the IP attempt so reserved-prefix spam is no
        # free-argon2 path.
        security.verify_password(password, security.DUMMY_HASH)
        if client_ip is not None:
            login_limiter.record_ip_attempt(client_ip)
        return None
    user = authenticate(username, password)
    if user is None:
        login_limiter.record_failure(username)
        if client_ip is not None:
            login_limiter.record_ip_attempt(client_ip)
        return None
    login_limiter.record_success(username)
    return sessions.create(user.username)


def current_user(token: str | None) -> User | None:
    """Resolve an opaque session token back to its User (None if invalid/expired)."""
    if not token:
        return None
    username = sessions.get(token)
    if username is None:
        return None
    if dbm._Session is None:
        dbm.boot()
    session = dbm._Session()
    try:
        return session.query(User).filter_by(username=username).first()
    finally:
        session.close()


def logout(token: str | None) -> None:
    """Invalidate a session token (idempotent)."""
    if token:
        sessions.delete(token)


# ---------------------------------------------------------------------------
# Open registration (MC 1355.7, owner-ratified 2026-09-24: open registration)
# ---------------------------------------------------------------------------

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200  # S2: over-long bodies must never reach argon2
MAX_USERNAME_LENGTH = 64

# S2 (MC 10467): small built-in weak list (casefolded; every entry is long
# enough to pass MIN_PASSWORD_LENGTH, so it is the ONLY thing rejecting them).
WEAK_PASSWORDS = frozenset({
    "password", "passw0rd", "password1", "12345678", "123456789", "1234567890",
    "qwerty123", "abc12345", "letmein123", "iloveyou", "football", "baseball",
    "welcome1", "admin123", "monkey123", "dragon123", "sunshine", "princess",
})

# Character classes for the "single character class" rule (S2). Unicode-aware
# islower/isupper/isdigit keep Swedish åäö in the letter classes.
_CHAR_CLASS_TESTS = (
    lambda c: c.islower(),
    lambda c: c.isupper(),
    lambda c: c.isdigit(),
    lambda c: not c.isalnum(),  # symbol/punctuation/space class
)


def _is_weak_password(password: str) -> bool:
    """True iff on the built-in weak list or drawn from a single character class."""
    if password.casefold() in WEAK_PASSWORDS:
        return True
    classes = sum(1 for matches in _CHAR_CLASS_TESTS if any(matches(c) for c in password))
    return classes < 2


class UsernameTaken(Exception):
    """Raised by register() when the username already exists (-> HTTP 409)."""


class WeakPassword(Exception):
    """Raised by register() on a too-short/over-long/weak password or bad
    username (-> 422)."""


def register(username: str, password: str,
             client_ip: str | None = None) -> str:
    """Create a new account and return an opaque session token (auto-login).

    Same mechanism as login — the users table, app.security hashing and the
    module SessionStore; no second user mechanism. Raises UsernameTaken when
    the username exists (router -> 409) and WeakPassword on a too-short or
    over-long password, a built-in-weak / single-character-class password, or an
    empty/over-long/reserved-namespace username (router -> 422). S2: the password
    rules all run BEFORE hashing. Per DA P1 (c2) an attempt counts against the
    shared per-IP throttle only once it reaches the real account-creation/
    collision path — client-error 422s record nothing, so cheap rejections
    cannot lock a whole shared-egress (CGNAT) address out (LoginLockedOut -> 429
    comes from the check at entry).
    """
    if client_ip is not None and login_limiter.is_ip_throttled(client_ip):
        raise LoginLockedOut(client_ip)
    name = (username or "").strip()
    if (not name or len(name) > MAX_USERNAME_LENGTH
            or name.startswith(login_limiter.IP_PREFIX)):
        # DA P0 (MC 10467 c2): "ip:" keys belong to the IP throttle alone — a
        # username in that namespace must be un-creatable, same 422 shape.
        raise WeakPassword("username must be 1-64 characters")
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPassword(
            f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    if _is_weak_password(password):
        raise WeakPassword("password too common; use multiple character classes")
    if client_ip is not None:
        login_limiter.record_ip_attempt(client_ip)
    if dbm._Session is None:
        dbm.boot()
    session = dbm._Session()
    try:
        if session.query(User).filter_by(username=name).first() is not None:
            raise UsernameTaken(name)
        session.add(User(username=name,
                         password_hash=security.hash_password(password)))
        session.commit()
    finally:
        session.close()
    return sessions.create(name)
