"""tests.test_abuse_controls_t2 — matapp-security S2 login/registration abuse (MC 10467).

The independent review (Claude Code R4 2026-10-11 on cabbb49) found: lockout was
per-USERNAME only, so cross-username spraying from one IP was unthrottled; /api/auth/register
was unthrottled (15 accounts in 0.7s); a 1 MB password reached argon2 (19 MiB per hash);
and the failure counters were an unbounded in-memory dict. These four tests pin the fix:
(a) same-IP cross-username spraying 429s, (b) register bursts 429, (c) over-long passwords
die at validation WITHOUT hashing, (d) failure state is size-capped + TTL-evicted.
"""
from __future__ import annotations

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"


def test_cross_username_spraying_same_ip_429(client, fresh_auth):
    """(a) 5 failed logins for username A from one IP, then username B from the
    SAME ip must 429 — spraying across usernames is throttled per IP. A different
    client IP stays unaffected while the first one is throttled."""
    limiter = fresh_auth.login_limiter
    for _ in range(limiter.MAX_FAILURES):
        r = client.post(LOGIN, json={"username": "victim-a", "password": "wrong-pass"})
        assert r.status_code == 401
    r = client.post(LOGIN, json={"username": "victim-b", "password": "wrong-pass"})
    assert r.status_code == 429
    # A different IP is NOT blocked by this IP's throttle (header source honoured).
    r = client.post(LOGIN, json={"username": "victim-b", "password": "wrong-pass"},
                    headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 401


def test_register_burst_from_one_ip_429(client, fresh_auth):
    """(b) The review created 15 accounts in 0.7s unthrottled; registration must
    share the per-IP throttle: MAX_FAILURES attempts pass, the next one 429s."""
    limiter = fresh_auth.login_limiter
    statuses = []
    for i in range(limiter.MAX_FAILURES + 1):
        r = client.post(REGISTER, json={"username": f"burst-{i}",
                                        "password": f"sup3r-secret-{i}"})
        statuses.append(r.status_code)
    assert statuses[:limiter.MAX_FAILURES] == [200] * limiter.MAX_FAILURES
    assert statuses[limiter.MAX_FAILURES] == 429


def test_overlong_password_rejected_without_hashing(client, monkeypatch):
    """(c) A 1 MB password must die at request validation with a 422 and never
    reach argon2 — on register AND on login (the login path verifies even unknown
    usernames against the dummy hash, so it is the cheaper DoS target)."""
    from app import security

    calls = {"hash": 0, "verify": 0}
    real_hash, real_verify = security.hash_password, security.verify_password

    def spy_hash(plain):
        calls["hash"] += 1
        return real_hash(plain)

    def spy_verify(plain, encoded):
        calls["verify"] += 1
        return real_verify(plain, encoded)

    monkeypatch.setattr(security, "hash_password", spy_hash)
    monkeypatch.setattr(security, "verify_password", spy_verify)

    huge = "x" * (1024 * 1024)
    r = client.post(REGISTER, json={"username": "overlong", "password": huge})
    assert r.status_code == 422
    assert calls["hash"] == 0, "over-long password reached argon2 on register"
    r = client.post(LOGIN, json={"username": "overlong", "password": huge})
    assert r.status_code == 422
    assert calls["verify"] == 0, "over-long password reached argon2 on login"


def test_failure_state_bounded_by_size_cap_and_ttl(fresh_auth):
    """(d) The failure dicts must be bounded: a size cap on distinct keys and TTL
    eviction of stale entries, both on the existing injectable-clock seam."""
    limiter = fresh_auth.login_limiter
    limiter.MAX_KEYS = 50  # instance override: exercise the cap cheaply
    for i in range(200):
        limiter.record_failure(f"sprayer-{i}", now=0.0)
    assert len(limiter._failures) <= 50
    assert len(limiter._locked_until) <= 50

    # TTL eviction on the injected clock: everything older than WINDOW is gone
    # (failures AND lockouts) after the next record_failure call.
    for i in range(limiter.MAX_FAILURES):
        limiter.record_failure("locked-v", now=0.0)
    assert "locked-v" in limiter._locked_until
    limiter.record_failure("late", now=limiter.WINDOW + 1.0)
    assert limiter._locked_until == {}
    assert list(limiter._failures) == ["late"]
