"""tests.test_stores_portback — regression tests for the MC 1355.3 T3 port-back.

Covers the stores router (ported from the deployed hosting copy) and the audit
P1-2 fix: profile store validation must reject UNKNOWN and DUPLICATE store ids
with 422, validated against the PlannerConfig catalog (O1 — the same catalog
/api/stores serves, never a second app-side list).
"""
from __future__ import annotations

from app import auth_service, security

EXPECTED_STORES = {"ica", "willys", "coop", "lidl"}


def _mkuser_and_login(client, username: str, password: str) -> None:
    """Insert a user row and log in (TestClient keeps the session cookie).

    The session cookie is Secure=True, which httpx will not replay over the
    plain-http testserver — so the token is set on the client explicitly.
    """
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


# ---------------------------------------------------------------- stores router

def test_get_stores_returns_planner_catalog(client):
    """GET /api/stores serves the PlannerConfig grocers read at request time."""
    r = client.get("/api/stores")
    assert r.status_code == 200
    body = r.json()
    assert {s["store_id"] for s in body} == EXPECTED_STORES
    for s in body:
        assert s["enabled"] is True
        assert s["chain_type"]  # chain key present (defaults to grocer_id)


def test_select_known_stores_persists(client):
    """MC 10348: selection is AUTH-GATED and PER-USER (profile.selected_stores)."""
    _mkuser_and_login(client, "sel1", "pw-sel-1")
    r = client.post("/api/stores/select", json={"store_ids": ["ica", "coop"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"selected": ["ica", "coop"]}
    sel = client.get("/api/stores/selected")
    assert sel.status_code == 200
    assert [s["store_id"] for s in sel.json()] == ["ica", "coop"]


def test_select_unknown_store_422(client):
    _mkuser_and_login(client, "sel2", "pw-sel-2")
    r = client.post("/api/stores/select", json={"store_ids": ["butiken-x"]})
    assert r.status_code == 422
    assert "unknown store_id" in r.json()["detail"]


def test_select_more_than_three_stores_422(client):
    _mkuser_and_login(client, "sel3", "pw-sel-3")
    r = client.post("/api/stores/select",
                    json={"store_ids": ["ica", "willys", "coop", "ica"]})
    assert r.status_code == 422
    assert "at most 3" in r.json()["detail"]


# --------------------------------------------- profile store validation (P1-2)

def test_profile_unknown_store_422(client):
    _mkuser_and_login(client, "p1user", "right-pass")
    r = client.put("/api/profile", json={
        "persons": 2, "meal_days": 5, "kron_budget": 800,
        "selected_stores": ["butiken-x"],
    })
    assert r.status_code == 422
    assert "unknown store_id" in r.json()["detail"]


def test_profile_duplicate_stores_422(client):
    _mkuser_and_login(client, "p2user", "right-pass")
    r = client.put("/api/profile", json={
        "persons": 2, "meal_days": 5, "kron_budget": 800,
        "selected_stores": ["ica", "ica", "ica"],
    })
    assert r.status_code == 422
    assert "duplicate" in r.json()["detail"]


def test_profile_known_distinct_stores_accepted(client):
    """Positive control: the same body with valid distinct stores still saves."""
    _mkuser_and_login(client, "p3user", "right-pass")
    r = client.put("/api/profile", json={
        "persons": 2, "meal_days": 5, "kron_budget": 800,
        "selected_stores": ["ica", "willys"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is True
    got = client.get("/api/profile")
    assert got.status_code == 200
    assert got.json()["selected_stores"] == ["ica", "willys"]


def test_profile_validation_uses_planner_catalog_not_second_list(client, monkeypatch):
    """O1 pin: shrink the catalog via the live PLANNER and the 422 follows it."""
    from src.config import PlannerConfig
    import app.config as app_config

    monkeypatch.setattr(
        app_config, "PLANNER",
        PlannerConfig(grocers=[PlannerConfig.grocer("ica", "https://x")]),
    )
    _mkuser_and_login(client, "p4user", "right-pass")
    # willys is no longer in the catalog -> must 422 even though it is a
    # "known" default grocer (proves validation reads the live catalog).
    r = client.put("/api/profile", json={
        "persons": 2, "meal_days": 5, "kron_budget": 800,
        "selected_stores": ["willys"],
    })
    assert r.status_code == 422
    assert "unknown store_id" in r.json()["detail"]
    # and the stores router serves the same shrunken catalog (one source).
    stores = client.get("/api/stores").json()
    assert [s["store_id"] for s in stores] == ["ica"]
