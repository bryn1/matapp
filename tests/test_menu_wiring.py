"""tests.test_menu_wiring — regression tests for MC 1355.5 (T4b menu wiring).

Covers the audit T4b DoD, all OFFLINE (no network — the offers DB is seeded with
fixture rows via ``upsert_week``):

  1. a menu built after seeding offers carries non-empty ``used_offer_ids``
     for at least one day;
  2. invalid weeks (malformed AND not-a-real-week: ``2026-W54``, ``2026-W00``,
     ``9999-W99``, ``0000-W01``) -> 422, never 500 (BUG-1/BUG-2 boundary fix);
  3. an empty offers DB -> 200 with every day degraded to ``used_offer_ids: []``;
  4. the selected-store filter actually restricts which offers are used;
  5. the boot-time ingest populates the offers DB (``scheduler.periodic.main``
     wired to the app DB URL) and is fail-tolerant (a raising ingest must not
     crash boot).
"""
from __future__ import annotations

import pytest

from app import auth_service, security
from src.offers_db.store import upsert_week

WEEK = "2026-W37"


def _mkuser_and_login(client, username: str, password: str) -> None:
    """Insert a user row and log in (same idiom as test_stores_portback)."""
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


def _seed_offers(week_key: str = WEEK) -> None:
    """Seed the (temp) offers DB with fixture rows for two grocers."""
    from app import db as dbm

    rows = [
        # willys offers hitting "Köttbullar med gräddsås och potatis" ingredients
        {"grocer_id": "willys", "external_id": "w-1", "week_key": week_key,
         "name": "Köttfärs nöt 500g", "price_cents": 4990, "unit": "g",
         "valid_from": "2026-09-07", "valid_to": "2026-09-13"},
        {"grocer_id": "willys", "external_id": "w-2", "week_key": week_key,
         "name": "Grädde 5dl", "price_cents": 1690, "unit": "dl",
         "valid_from": "2026-09-07", "valid_to": "2026-09-13"},
        # ica offers hitting "Köttfärssås och spagetti" ingredients
        {"grocer_id": "ica", "external_id": "i-1", "week_key": week_key,
         "name": "Krossade tomater 500g", "price_cents": 990, "unit": "g",
         "valid_from": "2026-09-07", "valid_to": "2026-09-13"},
        {"grocer_id": "ica", "external_id": "i-2", "week_key": week_key,
         "name": "Spagetti 400g", "price_cents": 1090, "unit": "g",
         "valid_from": "2026-09-07", "valid_to": "2026-09-13"},
    ]
    session = dbm._Session()
    try:
        upsert_week(session, rows, week_key)
    finally:
        session.close()


# ------------------------------------------------------------- 1. seeded menu

def test_menu_with_seeded_offers_has_used_offer_ids(client):
    """A menu built after seeding offers carries non-empty used_offer_ids."""
    _mkuser_and_login(client, "menuuser", "pw-menu-1")
    _seed_offers()
    r = client.get(f"/api/menu?week={WEEK}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["week_key"] == WEEK
    assert len(body["suggestions"]) == 3
    all_used = [oid for s in body["suggestions"] for d in s["days"]
                for oid in d["used_offer_ids"]]
    assert all_used, "expected at least one day with non-empty used_offer_ids"


# ------------------------------------------------------------- 2. invalid weeks

@pytest.mark.parametrize("bad", ["2026-W54", "2026-W00", "9999-W99", "0000-W01",
                                 "notaweek", "26-W5"])
def test_menu_invalid_week_is_422(client, bad):
    """Malformed AND not-a-real-week keys -> 422 (BUG-1/BUG-2 fixed at boundary)."""
    _mkuser_and_login(client, "weekuser", "pw-week-1")
    r = client.get(f"/api/menu?week={bad}")
    assert r.status_code == 422, f"{bad}: expected 422, got {r.status_code}"


# ------------------------------------------------------------- 3. empty offers DB

def test_menu_empty_offers_db_degrades_200(client):
    """Empty offers DB -> 200, every day degraded to used_offer_ids: []."""
    _mkuser_and_login(client, "emptyuser", "pw-empty-1")
    r = client.get(f"/api/menu?week={WEEK}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["week_key"] == WEEK
    for s in body["suggestions"]:
        for d in s["days"]:
            assert d["used_offer_ids"] == []


# --------------------------------------------------- 4. selected-store filter

def test_selected_store_filter_restricts_offers(client):
    """With only 'ica' selected ON THE USER'S PROFILE (MC 10348: the per-user
    profile is the single source of truth), no willys offer id may appear."""
    _mkuser_and_login(client, "storeuser", "pw-store-1")
    _seed_offers()
    # validated against the LIVE planner config (O1) through the real API
    r = client.put("/api/profile", json={
        "persons": 2, "meal_days": 5, "kron_budget": 800,
        "selected_stores": ["ica"],
    })
    assert r.status_code == 200, r.text
    r = client.get(f"/api/menu?week={WEEK}")
    assert r.status_code == 200, r.text
    used = [oid for s in r.json()["suggestions"] for d in s["days"]
            for oid in d["used_offer_ids"]]
    assert used, "ica offers were seeded — expected hits"
    # willys rows have offer ids 1-2, ica rows 3-4 (insertion order in _seed_offers)
    assert all(oid > 2 for oid in used), f"willys offers leaked into menu: {used}"


# ------------------------------------------------------- 5. boot-time ingest

def test_boot_ingest_populates_offers_db(client, monkeypatch):
    """The lifespan's ingest pass writes normalized offers into the app DB."""
    import app.main as app_main
    from src.scheduler import periodic

    def fake_pull(grocer_cfg, week_key, session=None):
        if grocer_cfg.grocer_id != "willys":
            return {"grocer_id": grocer_cfg.grocer_id,
                    "week_key": week_key, "entries": []}
        # valid window = the RUNNING week (C4 drops out-of-week offers)
        from src.normalizer.chain_mapper import iso_week_bounds
        monday, sunday = iso_week_bounds(week_key)
        return {"grocer_id": "willys", "week_key": week_key, "entries": [
            {"external_id": "boot-1", "name": "Köttfärs nöt 500g",
             "price": 49.9, "valid_from": monday.isoformat(),
             "valid_to": sunday.isoformat()},
        ]}

    monkeypatch.setattr(periodic, "pull_grocer", fake_pull)
    app_main.run_boot_ingest()

    from app import db as dbm
    from src.normalizer.chain_mapper import iso_week_bounds
    from src.offers_db.store import list_offers_in_week

    week_key = periodic.choose_week(None)
    monday, sunday = iso_week_bounds(week_key)
    session = dbm._Session()
    try:
        rows = list_offers_in_week(session, week_key)
    finally:
        session.close()
    assert any("Köttfärs" in (r.name or "") for r in rows), \
        "boot ingest did not populate the offers DB"


def test_boot_ingest_failure_is_fail_tolerant(client, monkeypatch):
    """A raising boot ingest must not crash app startup (log + continue)."""
    import app.main as app_main

    def boom():
        raise RuntimeError("grocer feed down")

    monkeypatch.setattr(app_main, "run_boot_ingest", boom)
    from fastapi.testclient import TestClient

    with TestClient(app_main.app) as c:  # context manager -> lifespan runs
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
