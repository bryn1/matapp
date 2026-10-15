"""tests.test_s4_body_size_guard — MC 10469 card DoD-4: oversized-body guard
for the T2 password cap (S2, MC 10467).

LoginBody._cap_length (app/routers/auth.py) rejects password values longer
than auth_service.MAX_PASSWORD_LENGTH characters at REQUEST validation (422)
on BOTH /api/auth/login and /api/auth/register, before argon2 ever sees the
body — the CPU-DoS posture (an oversized password would otherwise cost the
same as one actually verified, login probes unknown usernames against the
dummy hash). This file pins the observable contract the T2 code implements,
guarding the cap against future removal.

The oversized probe is built FROM the shipped constant, so inflating the cap
inflates the probe — the test can never silently degrade into a valid-body
test. Plant-proven RED (sec/.tmp/plants4.log, real hunks): the login guard
fires with the router validator alone neutered; the register guard fires when
the whole cap path goes (router validator AND the auth_service belt) —
register is defence-in-depth and its 422 is belt-backed, measured.

Scope line (honest): the USERNAME field is not body-size-capped on login (an
oversized username is a query miss -> the dummy hash -> 401, no cap exists to
guard); register caps usernames via MAX_USERNAME_LENGTH's own 422. DoD-4
names the T2 password body-size cap; that is what this guards.
"""
from __future__ import annotations

from app import auth_service

LOGIN = "/api/auth/login"
REGISTER = "/api/auth/register"

# 212 chars at the shipped cap of 200 — and always cap+12 if the cap moves.
OVERSIZED = "S3cure-Mix-" + "x" * (1 + auth_service.MAX_PASSWORD_LENGTH)


def _assert_rejected(response):
    assert response.status_code == 422, response.text
    assert str(auth_service.MAX_PASSWORD_LENGTH) in response.text, (
        "the rejection must name the cap it enforces")
    assert "matapp_session=" not in response.headers.get("set-cookie", ""), (
        "a rejected oversized body must never mint a session")


def test_login_rejects_oversized_password_body(client):
    """Login answers an oversized password with 422 + the cap text, WITHOUT
    the body reaching argon2 and WITHOUT minting a session (T2 cap as
    implemented in LoginBody._cap_length)."""
    _assert_rejected(client.post(LOGIN, json={
        "username": "body-sz-a", "password": OVERSIZED}))


def test_register_rejects_oversized_password_body(client):
    """Register answers the same 422 + cap text (router validator in front,
    auth_service WeakPassword belt behind), mints nothing, and LEAVES STATE
    CLEAN: no account was created — a well-formed login for that username is
    the same 401 as for any unknown user."""
    _assert_rejected(client.post(REGISTER, json={
        "username": "body-sz-b", "password": OVERSIZED}))
    r = client.post(LOGIN, json={"username": "body-sz-b",
                                 "password": "Sup3r-Secret-1"})
    assert r.status_code == 401, (
        "the rejected register left a user row behind: " + r.text)
