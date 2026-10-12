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
    share the per-IP throttle: MAX_FAILURES attempts pass, the next one 429s.

    Round-c2: these are GENUINE account-creating attempts (all succeed), so the
    burst still trips the throttle even after P1 makes client-error 422s record
    nothing — see test_weak_register_rejections_do_not_burn_ip_budget."""
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


# ---- round-c2 back-edge (MC 10467): DA P0/P1 + tester U1 ----


def test_poison_username_cannot_lock_real_ip(client, fresh_auth):
    """(e) DA P0: usernames are only length-validated, so before the fix login/register
    for username "ip:1.2.3.4" wrote THAT address's IP throttle bucket — 5 failed
    logins for a non-existent "ip:127.0.0.1" gave honest clients at 127.0.0.1 a
    429 on login AND register. The reserved "ip:" prefix must be rejected at BOTH
    entry points BEFORE any limiter record: login as a bad-credential 401 (dummy
    verify, no enumeration leak), register as the invalid-username 422. The real
    127.0.0.1 bucket must stay untouched — a correct login from it still succeeds.
    """
    limiter = fresh_auth.login_limiter
    poison = "ip:127.0.0.1"
    attacker = {"CF-Connecting-IP": "198.51.100.66"}
    # Register attempt on the reserved-namespace username -> 422, records nothing.
    r = client.post(REGISTER, json={"username": poison, "password": "Sup3r-Secret-1"},
                    headers=attacker)
    assert r.status_code == 422
    assert "ip:127.0.0.1" not in limiter._failures
    # Login attempts on it -> the SAME 401 shape as bad credentials (no leak), and
    # no ip-keyed entry is created for the real address (the attacker's OWN bucket
    # absorbs the attempts — that is the anti-DoS point, see RESULT).
    for _ in range(limiter.MAX_FAILURES):
        r = client.post(LOGIN, json={"username": poison, "password": "wrong-pass"},
                        headers=attacker)
        assert r.status_code == 401
    assert "ip:127.0.0.1" not in limiter._failures
    assert "ip:127.0.0.1" not in limiter._locked_until
    # The honest client AT 127.0.0.1 is unaffected: correct login still 200.
    _mkuser("honest127", "Sup3r-Secret-1")
    r = client.post(LOGIN, json={"username": "honest127", "password": "Sup3r-Secret-1"},
                    headers={"CF-Connecting-IP": "127.0.0.1"})
    assert r.status_code == 200


def test_weak_register_rejections_do_not_burn_ip_budget(client, fresh_auth):
    """(f) DA P1: register recorded the IP attempt BEFORE validation, so 5 cheap
    weak-password 422s locked a whole shared-egress (CGNAT) IP — including correct
    logins. An IP attempt must be recorded only when the request reaches the real
    account-creation/collision path; client-error 422s record nothing. So after 5
    weak 422s from one IP, a correct login AND a good register from that IP both
    still succeed."""
    limiter = fresh_auth.login_limiter
    ip = "203.0.113.77"
    for i in range(limiter.MAX_FAILURES):  # 5 = MAX_FAILURES weak-password 422s
        r = client.post(REGISTER, json={"username": f"weak-{i}", "password": "short"},
                        headers={"CF-Connecting-IP": ip})
        assert r.status_code == 422
    assert "ip:203.0.113.77" not in limiter._failures  # client errors never recorded
    # A correct login from that IP succeeds (bucket not poisoned)...
    _mkuser("cgnat-user", "Sup3r-Secret-9")
    r = client.post(LOGIN, json={"username": "cgnat-user", "password": "Sup3r-Secret-9"},
                    headers={"CF-Connecting-IP": ip})
    assert r.status_code == 200
    # ...and a GENUINE register from it still succeeds (only real attempts burn budget).
    r = client.post(REGISTER, json={"username": "cgnat-good", "password": "Sup3r-Secret-9"},
                    headers={"CF-Connecting-IP": ip})
    assert r.status_code == 200


def test_p5_valid_login_does_not_reset_ip_bucket(client, fresh_auth):
    """(U1) Copied from the TEST seat's independent probes (tester-probes-t2).
    Behavioural pin: 'one valid account must not unmask a spray' — a successful
    login must NOT clear the per-IP failure bucket."""
    _mkuser("attacker-acct", "sup3r-secret-1")
    limiter = fresh_auth.login_limiter
    ip = "5.5.5.5"
    for i in range(limiter.MAX_FAILURES - 1):
        assert fresh_auth.login(f"victim-{i}", "bad-pass", client_ip=ip) is None
    # A valid login from the SAME ip succeeds (bucket not yet full)...
    assert fresh_auth.login("attacker-acct", "sup3r-secret-1", client_ip=ip) is not None
    # ...but it must NOT have cleared the accumulated ip failures:
    assert fresh_auth.login("victim-x", "bad-pass", client_ip=ip) is None  # 5th -> locks
    try:
        fresh_auth.login("attacker-acct", "sup3r-secret-1", client_ip=ip)
        raise AssertionError("valid login unmasked the ip spray (bucket was reset)")
    except fresh_auth.LoginLockedOut:
        pass  # correct: the ip stays throttled despite the valid login
