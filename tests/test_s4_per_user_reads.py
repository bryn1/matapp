"""tests.test_s4_per_user_reads — matapp-security S4 router-level per-user reads
(MC 10469).

test_stores_per_user.py pins that one user's SELECTION does not change the
other's MENU. This file pins the READ side the routers answer directly: with two
authenticated users holding different selections, each user's GET
/api/stores/selected and GET /api/profile show ONLY their own stores. A
router-level fixed/shared user_id (the classic "cache the first resolved user"
bug) turns both tests RED — plant-proven, transcript in T4-RESULT.md.
"""
from __future__ import annotations

from app import security

REGISTER = "/api/auth/register"
PW = "Sup3r-Secret-1"
COOKIE = security.SESSION_COOKIE


def _register_and_select(client, username: str, stores: list[str]) -> str:
    """Register via the API (auto-login), select stores, return the session
    token so a test can switch identities on the shared client jar."""
    r = client.post(REGISTER, json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    token = r.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    # explicit set (fleet idiom, test_stores_per_user): the TestClient jar
    # over http://testserver does not resend the Secure-flagged cookie.
    client.cookies.set(COOKIE, token)
    r = client.post("/api/stores/select", json={"store_ids": stores})
    assert r.status_code == 200, r.text
    return token


def _as(client, token: str):
    client.cookies.set(COOKIE, token)


def _setup_two_users(client):
    """A selected willys; B selected ica — disjoint on purpose."""
    token_a = _register_and_select(client, "s4r-a", ["willys"])
    token_b = _register_and_select(client, "s4r-b", ["ica"])
    return token_a, token_b


def test_user_a_reads_only_its_own_selection(client):
    token_a, _ = _setup_two_users(client)
    _as(client, token_a)
    sel = client.get("/api/stores/selected")
    assert sel.status_code == 200, sel.text
    assert sel.json() == [{"store_id": "willys"}]
    prof = client.get("/api/profile")
    assert prof.status_code == 200, prof.text
    assert prof.json()["selected_stores"] == ["willys"]


def test_user_b_reads_only_its_own_selection(client):
    _, token_b = _setup_two_users(client)
    _as(client, token_b)
    sel = client.get("/api/stores/selected")
    assert sel.status_code == 200, sel.text
    assert sel.json() == [{"store_id": "ica"}]
    prof = client.get("/api/profile")
    assert prof.status_code == 200, prof.text
    assert prof.json()["selected_stores"] == ["ica"]
