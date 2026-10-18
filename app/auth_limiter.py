"""app.auth_limiter — the login/register abuse limiter, split out of app.auth_service (MC 10711).

Module-level singleton used by the router via app.auth_service's re-import; tests may substitute a
fresh limiter (the conftest idiom is unchanged: monkeypatch.setattr(auth_service, "login_limiter",
auth_service.LoginRateLimiter()) still rebinds the name every consumer reads).
"""
from __future__ import annotations

import threading
import time


class LoginRateLimiter:
    """Thread-safe failed-attempt counter with lockout (audit 1188 F2; matapp S2).

    Same scope/idiom as SessionStore: in-memory, module-level, guarded by a
    threading.Lock. After MAX_FAILURES failed logins for a username inside the
    WINDOW seconds, that username is locked out for LOCKOUT seconds — login()
    refuses it (429 at the router) without touching the DB. A successful login
    resets the counter.

    S2 (MC 10467) extends THIS limiter — no second limiter — with a per-client-IP
    scope keyed under IP_PREFIX (shared across login failures and registration
    attempts from that address, so cross-username spraying and register bursts
    trip the same MAX_FAILURES/WINDOW/LOCKOUT gate). Successful logins never
    reset the IP bucket (one valid account must not unmask a spray).

    The state dicts are BOUNDED (S2): at most MAX_KEYS entries per dict, and
    entries older than WINDOW (failures) or past LOCKOUT (locks) are evicted on
    every record. ``now`` stays injectable on all mutating/querying methods —
    that is the clock seam the tests use.
    """

    MAX_FAILURES = 5
    WINDOW = 15 * 60  # failure window: 15 minutes
    LOCKOUT = 15 * 60  # lockout duration: 15 minutes
    MAX_KEYS = 10_000  # size cap per state dict (S2: no unbounded growth)
    IP_PREFIX = "ip:"  # namespace: a client IP never collides with a username

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_locked(self, username: str, now: float | None = None) -> bool:
        """True iff *username* is currently locked out."""
        now = time.monotonic() if now is None else now
        with self._lock:
            until = self._locked_until.get(username)
            if until is None:
                return False
            if now >= until:
                del self._locked_until[username]  # lockout expired
                return False
            return True

    def record_failure(self, username: str, now: float | None = None) -> None:
        """Count one failed login; lock the username at MAX_FAILURES in WINDOW."""
        now = time.monotonic() if now is None else now
        with self._lock:
            recent = [t for t in self._failures.get(username, []) if now - t < self.WINDOW]
            recent.append(now)
            self._failures[username] = recent
            if len(recent) >= self.MAX_FAILURES:
                self._locked_until[username] = now + self.LOCKOUT
            self._bound_state(now)

    def _bound_state(self, now: float) -> None:
        """Evict TTL-stale entries, then oldest-inserted to enforce MAX_KEYS (S2).

        Called with ``self._lock`` held. Under a cap breach the oldest-inserted
        key is dropped first even if still fresh — bounded memory is the point
        (the review's unbounded dict was the finding); evicted keys simply start
        counting again, exactly like a process restart does today.
        """
        for key in [k for k, ts in self._failures.items() if not ts
                    or now - ts[-1] >= self.WINDOW]:
            del self._failures[key]
        for key in [k for k, until in self._locked_until.items() if now >= until]:
            del self._locked_until[key]
        while len(self._failures) > self.MAX_KEYS:
            del self._failures[next(iter(self._failures))]  # oldest-inserted first
        while len(self._locked_until) > self.MAX_KEYS:
            del self._locked_until[next(iter(self._locked_until))]

    # ---- per-client-IP scope (S2): same gate, namespaced key, no second dict ----

    def is_ip_throttled(self, client_ip: str, now: float | None = None) -> bool:
        """True iff *client_ip* has hit MAX_FAILURES attempts inside WINDOW."""
        return self.is_locked(self.IP_PREFIX + client_ip, now)

    def record_ip_attempt(self, client_ip: str, now: float | None = None) -> None:
        """Count one abusive attempt (login failure / register) from *client_ip*."""
        self.record_failure(self.IP_PREFIX + client_ip, now)

    def record_success(self, username: str) -> None:
        """A successful login clears the username's failure counter."""
        with self._lock:
            self._failures.pop(username, None)


# Module-level singleton used by the router; tests may substitute a fresh limiter.
login_limiter = LoginRateLimiter()


class LoginLockedOut(Exception):
    """Raised by login() when the username is locked out (maps to HTTP 429)."""
