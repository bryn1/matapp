"""tests.test_stores_per_user — MC 10348 (live-dogfood findings 1/4/5).

The store selection is PER-USER and auth-gated, with profile.selected_stores as
the single source of truth:

  * anonymous POST /api/stores/select and GET /api/stores/selected -> 401
    (never 200 — the same gate as profile/menu);
  * user A selecting a chain changes A's /api/menu offer_sources but NOT B's;
  * duplicate / unknown / >3 store ids -> 422, never a 500 (the old
    IntegrityError path is gone with the retired global store_selection table).

Offline (temp DB, seeded offer rows — no network), repo client-fixture idiom.
"""
from __future__ import annotations

from app import auth_service, security
from src.offers_db.store import upsert_week

WEEK = "2026-W37"


def _mkuser_and_login(client, username: str, password: str) -> str:
    """Insert a user row and log in; return the session token (TestClient
    cookie is set too — same idiom as test_stores_portback)."""
    from app import db as dbm
    from app.models.users import User

    if dbm._Session is None:
        dbm.boot()
    session = dbm._Session()
    try:
        existing = session.query(User).filter_by(username=username).first()
        if existing is None:
            session.add(User(username=username,
                             password_hash=security.hash_password(password)))
            session.commit()
    finally:
        session.close()
    token = auth_service.login(username, password)
    assert token is not None
    client.cookies.set(security.SESSION_COOKIE, token)
    return token


def _seed_two_chains() -> None:
    """Seed one willys + one ica offer row for WEEK (fixture rows, no network)."""
    from app import db as dbm

    rows = [
        {"grocer_id": "willys", "external_id": "w-1", "week_key": WEEK,
         "name": "Köttfärs nöt 500g", "price_cents": 4990, "unit": "g",
         "valid_from": "2026-09-07", "valid_to": "2026-09-13"},
        {"grocer_id": "ica", "external_id": "i-1", "week_key": WEEK,
         "name": "Krossade tomater 500g", "price_cents": 990, "unit": "g",
         "valid_from": "2026-09-07", "valid_to": "2026-09-13"},
    ]
    session = dbm._Session()
    try:
        upsert_week(session, rows, WEEK)
    finally:
        session.close()


# ------------------------------------------------------------- 1. auth gates

def test_anon_post_select_is_401(client):
    """No cookie -> 401, never 200 (finding 1: the endpoint was anon-writable)."""
    r = client.post("/api/stores/select", json={"store_ids": ["willys"]})
    assert r.status_code == 401, r.text


def test_anon_get_selected_is_401(client):
    r = client.get("/api/stores/selected")
    assert r.status_code == 401, r.text


def test_invalid_cookie_is_401(client):
    client.cookies.set(security.SESSION_COOKIE, "not-a-real-token")
    assert client.post("/api/stores/select",
                       json={"store_ids": []}).status_code == 401
    assert client.get("/api/stores/selected").status_code == 401


# ------------------------------------------------------ 2. per-user menu scoping

def test_selecting_willys_changes_menu_for_a_not_b(client):
    """A selects 'willys' -> A's menu offer_sources are willys-only; B (no
    selection) keeps seeing both seeded chains — no global bleed (finding 5)."""
    _seed_two_chains()
    token_b = _mkuser_and_login(client, "peruser-b", "pw-b-1")

    def grocers_seen():
        r = client.get(f"/api/menu?week={WEEK}")
        assert r.status_code == 200, r.text
        return {o["grocer_id"] for o in r.json()["offer_sources"]}

    # B before A selects anything: both seeded chains are offered
    assert grocers_seen() == {"willys", "ica"}

    _mkuser_and_login(client, "peruser-a", "pw-a-1")
    r = client.post("/api/stores/select", json={"store_ids": ["willys"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"selected": ["willys"]}

    assert grocers_seen() == {"willys"}           # A: only Willys

    client.cookies.set(security.SESSION_COOKIE, token_b)
    assert grocers_seen() == {"willys", "ica"}    # B: untouched

    # and the single source of truth is the profile row itself
    client.cookies.set(security.SESSION_COOKIE, token_b)
    prof_b = client.get("/api/profile")
    assert prof_b.status_code == 404              # B never saved a profile


# ---------------------------------------------------- 3. validation -> 422, no 500

def test_duplicate_store_ids_422_not_500(client):
    """Finding 4: duplicates were an unhandled IntegrityError 500."""
    _mkuser_and_login(client, "dupuser", "pw-dup-1")
    r = client.post("/api/stores/select", json={"store_ids": ["willys", "willys"]})
    assert r.status_code == 422, r.text
    assert "duplicate" in r.json()["detail"]
    # state stayed clean: nothing was selected
    assert client.get("/api/stores/selected").json() == []


def test_unknown_store_id_422(client):
    _mkuser_and_login(client, "unkuser", "pw-unk-1")
    r = client.post("/api/stores/select", json={"store_ids": ["butiken-x"]})
    assert r.status_code == 422, r.text
    assert "unknown store_id" in r.json()["detail"]


def test_more_than_three_stores_422(client):
    _mkuser_and_login(client, "maxuser", "pw-max-1")
    r = client.post("/api/stores/select",
                    json={"store_ids": ["ica", "willys", "coop", "lidl"]})
    assert r.status_code == 422, r.text
    assert "at most 3" in r.json()["detail"]


def test_selection_does_not_clobber_other_profile_fields(client):
    """POST /select replaces ONLY the selection — persons/budget/postal stay."""
    _mkuser_and_login(client, "keepuser", "pw-keep-1")
    r = client.put("/api/profile", json={
        "persons": 6, "meal_days": 7, "kron_budget": 1234,
        "selected_stores": ["ica"],
    })
    assert r.status_code == 200, r.text
    assert client.post("/api/stores/select",
                       json={"store_ids": ["willys"]}).status_code == 200
    prof = client.get("/api/profile").json()
    assert prof["selected_stores"] == ["willys"]
    assert prof["persons"] == 6 and prof["meal_days"] == 7
    assert prof["kron_budget"] == 1234
