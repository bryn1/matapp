"""tests.test_session_ttl_t3 — matapp-security S3 session expiry (MC 10468).

The independent review (R4 on d82ad30) found: SessionStore is a plain
token -> username dict with no TTL, no last-seen and no bound — only logout
or a process restart ended a session, Set-Cookie carried no Max-Age, and the
map grew without bound. These tests pin the fix on the EXISTING store
(no second session layer): (a) a token past the absolute 30-day TTL 401s
EXACTLY like a never-issued token (byte-identical, no oracle), (b) a session
idle past the 14-day timeout 401s — the 401 is forced on a FRESHLY issued
session so only the idle arm can fire (round-c3: deleting the idle arm left
every test green before), (c) the login Set-Cookie carries Max-Age matching
the TTL, (d) the map is size-bounded and expired entries are swept on the
next login, (e) the shipped TTL/IDLE policy values are what the doc
advertises. Clock control uses the seam the S2 limiter tests established
(injectable now= / module-level time seam) — no sleeping.
"""
from __future__ import annotations

import time

import pytest

LOGIN = "/api/auth/login"
ME = "/api/auth/me"
COOKIE = "matapp_session"

# The S3 policy values ARE the assertion constants (so these fail as real
# assertions at base, where sessions never expire and no cap exists):
TTL = 30 * 24 * 3600        # absolute session lifetime: 30 days
IDLE_TIMEOUT = 14 * 24 * 3600  # expiry after 14 days without any request


@pytest.fixture(autouse=True)
def _clean_session_knobs(monkeypatch):
    """MATAPP_SESSION_TTL / MATAPP_SESSION_IDLE are read ONCE at SessionStore
    import — an exported knob value in the runner env would silently shift the
    class values this file's clock arithmetic runs against (false RED, P3).
    conftest.py scrubs them at import top (collection-time safety, round-c4);
    this per-test delenv is the belt-and-braces for the values themselves."""
    monkeypatch.delenv("MATAPP_SESSION_TTL", raising=False)
    monkeypatch.delenv("MATAPP_SESSION_IDLE", raising=False)


class _FakeTime:
    """auth_service only ever calls time.monotonic() — a patchable clock."""

    def __init__(self, start: float) -> None:
        self.t = start

    def monotonic(self) -> float:
        return self.t


def _mkuser(username: str, password: str) -> None:
    """Insert a user row directly (temp-DB engine is already booted by client())."""
    from app import db as dbm
    from app import security
    from app.models.users import User

    session = dbm._Session()
    try:
        session.add(User(username=username,
                         password_hash=security.hash_password(password)))
        session.commit()
    finally:
        session.close()


def _login(client, username: str, password: str) -> str:
    """Log in and return the raw session token from the Set-Cookie header."""
    r = client.post(LOGIN, json={"username": username, "password": password})
    assert r.status_code == 200
    return r.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]


def test_session_past_absolute_ttl_401(client, fresh_auth, monkeypatch):
    """(a) The ABSOLUTE arm, ISOLATED (round-c2 gate): last_seen is kept fresh
    by authenticated hits inside the idle window, so the final 401 can ONLY
    come from issue-age > TTL (idle-age < IDLE at that instant) — deleting the
    absolute arm of _expired() must turn this test RED. The 401 is answered
    EXACTLY like a never-issued token (byte-identical body, no oracle) and the
    entry is evicted from the map on that access."""
    clock = _FakeTime(1_000.0)
    monkeypatch.setattr(fresh_auth, "time", clock)
    _mkuser("ttl-user", "Sup3r-Secret-1")
    token = _login(client, "ttl-user", "Sup3r-Secret-1")
    assert client.get(ME, cookies={COOKIE: token}).status_code == 200  # live

    # Slide the IDLE clock across the whole 30 d so only the absolute arm can
    # fire at the end: each hit lands one second shy of the idle timeout.
    clock.t += IDLE_TIMEOUT - 1  # issued ~14 d, last_seen 1 s old
    assert client.get(ME, cookies={COOKIE: token}).status_code == 200
    clock.t += IDLE_TIMEOUT - 1  # issued ~28 d, last_seen 1 s old
    assert client.get(ME, cookies={COOKIE: token}).status_code == 200
    clock.t += TTL - 2 * IDLE_TIMEOUT  # issued 2 s shy of TTL, idle ~2 d old
    assert client.get(ME, cookies={COOKIE: token}).status_code == 200

    clock.t += 3  # issued TTL+1 s: ONLY the absolute arm sees this as expired
    r_expired = client.get(ME, cookies={COOKIE: token})
    r_unknown = client.get(ME, cookies={COOKIE: "never-issued-token"})
    assert r_expired.status_code == 401
    assert r_expired.content == r_unknown.content  # identical 401 shape
    assert token not in fresh_auth.sessions._sessions  # evicted on access


def test_session_idle_past_timeout_401(client, fresh_auth, monkeypatch):
    """(b) The IDLE arm + last_seen refresh pinned, IDLE ARM ISOLATED (round-c3):
    after a mid-window hit the session must STILL be alive at issue-age ~2x IDLE
    (last_seen-age < IDLE) — true only if get() slid the idle clock, so deleting
    the refresh line in get() turns that assertion RED. Then a SECOND, FRESHLY
    issued session expires at idle-age IDLE+1 s while its issue-age stays < TTL:
    that 401 can ONLY come from the idle arm — deleting the idle arm of
    _expired(), doubling IDLE_TIMEOUT, or keying it to TTL must turn THIS RED."""
    clock = _FakeTime(1_000.0)
    monkeypatch.setattr(fresh_auth, "time", clock)
    _mkuser("idle-user", "Sup3r-Secret-1")
    token = _login(client, "idle-user", "Sup3r-Secret-1")

    clock.t += IDLE_TIMEOUT - 1  # one second inside the idle window: refreshed
    assert client.get(ME, cookies={COOKIE: token}).status_code == 200

    clock.t += IDLE_TIMEOUT - 1  # issued ~28 d (< 30 d TTL), idle only ~1 s old
    r = client.get(ME, cookies={COOKIE: token})
    assert r.status_code == 200, "hit did not slide the idle clock (last_seen stale)"

    # A FRESH session issued now must die inside the absolute TTL — the idle
    # arm alone can do that (its issue-age never reaches TTL here).
    fresh_token = _login(client, "idle-user", "Sup3r-Secret-1")  # issued ~28 d
    clock.t += IDLE_TIMEOUT + 1  # fresh: idle-age == issue-age == 14 d + 1 s < TTL
    r = client.get(ME, cookies={COOKIE: fresh_token})
    assert r.status_code == 401, "idle arm not enforced inside the absolute TTL"
    assert fresh_token not in fresh_auth.sessions._sessions

    # The refreshed session is idle-expired by now too (both arms fire for it).
    r = client.get(ME, cookies={COOKIE: token})
    assert r.status_code == 401
    assert token not in fresh_auth.sessions._sessions


def test_shipped_expiry_policy_values(fresh_auth):
    """(e) round-c3 pin: the values the ARCHITECTURE doc advertises are the
    values SessionStore SHIPS — absolute TTL 30 d, idle 14 d (the cookie
    Max-Age in (c) reads the first of these)."""
    assert fresh_auth.SessionStore.TTL == TTL
    assert fresh_auth.SessionStore.IDLE_TIMEOUT == IDLE_TIMEOUT


def test_login_set_cookie_carries_max_age(client, fresh_auth):
    """(c) Set-Cookie gains Max-Age matching the session TTL — while the
    existing attributes stay EXACTLY as before (pin for the later cookie
    guard card): HttpOnly, Secure, SameSite=lax."""
    _mkuser("cookie-user", "Sup3r-Secret-1")
    r = client.post(LOGIN, json={"username": "cookie-user",
                                 "password": "Sup3r-Secret-1"})
    assert r.status_code == 200
    raw = r.headers.get("set-cookie", "")
    assert f"Max-Age={int(TTL)}" in raw
    lowered = raw.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered


def test_session_map_bounded_burst_and_sweep(fresh_auth, monkeypatch):
    """(d) A login burst cannot grow the map without bound: at most
    MAX_SESSIONS entries survive, and the next login after the TTL sweeps
    every expired entry (no scheduler — the sweep rides create())."""
    store = fresh_auth.sessions
    store.MAX_SESSIONS = 50  # instance override: exercise the cap cheaply (S2 idiom)
    for i in range(200):
        store.create(f"u-{i}")
    assert len(store._sessions) <= 50  # base: 200 entries, unbounded -> RED

    monkeypatch.setattr(fresh_auth, "time", _FakeTime(time.monotonic() + TTL + 1))
    store.create("post-expiry")  # a login runs the cheap expiry sweep
    assert len(store._sessions) == 1  # only the fresh session survives
