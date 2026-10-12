"""tests.test_session_ttl_t3 — matapp-security S3 session expiry (MC 10468).

The independent review (R4 on d82ad30) found: SessionStore is a plain
token -> username dict with no TTL, no last-seen and no bound — only logout
or a process restart ended a session, Set-Cookie carried no Max-Age, and the
map grew without bound. These four tests pin the fix on the EXISTING store
(no second session layer): (a) a token past the absolute 30-day TTL 401s
EXACTLY like a never-issued token (byte-identical, no oracle), (b) a session
idle past the 14-day timeout 401s, (c) the login Set-Cookie carries Max-Age
matching the TTL, (d) the map is size-bounded and expired entries are swept
on the next login. Clock control uses the seam the S2 limiter tests
established (injectable now= / module-level time seam) — no sleeping.
"""
from __future__ import annotations

import time

LOGIN = "/api/auth/login"
ME = "/api/auth/me"
COOKIE = "matapp_session"

# The S3 policy values ARE the assertion constants (so these fail as real
# assertions at base, where sessions never expire and no cap exists):
TTL = 30 * 24 * 3600        # absolute session lifetime: 30 days
IDLE_TIMEOUT = 14 * 24 * 3600  # expiry after 14 days without any request


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
    """(a) A token older than the absolute TTL is answered EXACTLY like a token
    that never existed: same 401 status AND byte-identical body (no expired vs
    unknown oracle), and the entry is evicted from the map on that access."""
    clock = _FakeTime(1_000.0)
    monkeypatch.setattr(fresh_auth, "time", clock)
    _mkuser("ttl-user", "Sup3r-Secret-1")
    token = _login(client, "ttl-user", "Sup3r-Secret-1")
    assert client.get(ME, cookies={COOKIE: token}).status_code == 200  # live

    clock.t += TTL + 1  # past the absolute 30-day lifetime
    r_expired = client.get(ME, cookies={COOKIE: token})
    r_unknown = client.get(ME, cookies={COOKIE: "never-issued-token"})
    assert r_expired.status_code == 401
    assert r_expired.content == r_unknown.content  # identical 401 shape
    assert token not in fresh_auth.sessions._sessions  # evicted on access


def test_session_idle_past_timeout_401(client, fresh_auth, monkeypatch):
    """(b) A request inside the idle window refreshes last-seen (200), but once
    IDLE_TIMEOUT passes without ANY request the session 401s — while still
    inside the absolute TTL, so this pins the IDLE rule alone, not the TTL."""
    clock = _FakeTime(1_000.0)
    monkeypatch.setattr(fresh_auth, "time", clock)
    _mkuser("idle-user", "Sup3r-Secret-1")
    token = _login(client, "idle-user", "Sup3r-Secret-1")

    clock.t += IDLE_TIMEOUT - 1  # one second inside the idle window: refreshed
    assert client.get(ME, cookies={COOKIE: token}).status_code == 200

    clock.t += IDLE_TIMEOUT + 1  # idle that long (total ~28 d, still < 30 d TTL)
    r = client.get(ME, cookies={COOKIE: token})
    assert r.status_code == 401
    assert token not in fresh_auth.sessions._sessions


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
