"""tests.test_journey_api — MC 10349 (the owner's journey: read surfaces + regression).

Offline temp-DB via the shared ``client`` fixture (login idiom from
test_rotation_accept). Pins the contract of the two NEW thin reads the UI
journey rests on, plus the accept path it drives:

  * GET /api/menu/accepted — 401 anon (never 200); 404 "no accepted plan" for
    a fresh user (the shopping/build 404 shape); after a happy-path accept:
    200 {week_key, seed, dishes[]} in the echoed render order; 422 gate on a
    not-real week (the shared menu/shopping boundary);
  * GET /api/recipe/{title} — 401 anon; 200 starter-roster shape
    {title, category, servings, vegetarian, kid_friendly,
    ingredients:[{name, qty, unit}], allergens:[...]}; 404 unknown title;
    the literal /api/recipe/ratings keeps matching (no shadow by /{title});
  * POST /api/menu/accept happy path still 200 (UI-journey regression guard).
"""
from __future__ import annotations

from urllib.parse import quote

from app import db as dbm, security

WEEK = "2026-W37"


def _login(client, username: str, password: str = "pw-journey-1") -> None:
    r = client.post("/api/auth/register",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.headers["set-cookie"].split(";")[0].split("=")[1]
    client.cookies.set(security.SESSION_COOKIE, token)


def _offer(client, week: str, seed: int) -> list[str]:
    """The dishes GET /api/menu serves for (week, seed) — what accept echoes."""
    menu = client.get(f"/api/menu?week={week}").json()
    sug = next(s for s in menu["suggestions"] if s["seed"] == seed)
    return [d["dish_id"] for d in sug["days"]]


# ------------------------------------------------- GET /api/menu/accepted

def test_menu_accepted_requires_auth(client):
    dbm.boot()
    r = client.get("/api/menu/accepted")
    assert r.status_code == 401, "anon must never read another surface (gate C6)"


def test_menu_accepted_404_for_fresh_user(client):
    dbm.boot()
    _login(client, "journeyfresh")
    r = client.get("/api/menu/accepted")
    assert r.status_code == 404, r.text
    # same honest answer shape as POST /api/shopping/build's 404
    assert "no accepted plan" in r.json()["detail"]


def test_menu_accepted_shape_matches_the_accepted_plan(client):
    dbm.boot()
    _login(client, "journeyacc")
    dishes = _offer(client, WEEK, 101)
    assert client.post("/api/menu/accept",
                       json={"seed": 101, "week": WEEK,
                             "dishes": dishes}).status_code == 200

    r = client.get(f"/api/menu/accepted?week={WEEK}")
    assert r.status_code == 200, r.text
    assert r.json() == {"week_key": WEEK, "seed": 101, "dishes": dishes}

    # per-user isolation: a second household has accepted nothing
    client.cookies.clear()
    _login(client, "journeyother")
    assert client.get(f"/api/menu/accepted?week={WEEK}").status_code == 404


def test_menu_accepted_week_gate(client):
    dbm.boot()
    _login(client, "journeygate")
    assert client.get("/api/menu/accepted?week=garbage").status_code == 422
    assert client.get("/api/menu/accepted?week=2026-W54").status_code == 422


# --------------------------------------------------- GET /api/recipe/{title}

def test_recipe_requires_auth(client):
    dbm.boot()
    assert client.get("/api/recipe/Tacopaj").status_code == 401


def test_recipe_starter_roster_shape(client):
    dbm.boot()
    _login(client, "recipeuser")
    r = client.get("/api/recipe/Tacopaj")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Tacopaj"
    assert body["category"] == "husmanskost"
    assert body["servings"] == 6
    assert body["vegetarian"] is False
    assert body["kid_friendly"] is True
    assert all(set(i.keys()) == {"name", "qty", "unit"}
               for i in body["ingredients"])
    assert {"name": "köttfärs", "qty": 500, "unit": "g"} in body["ingredients"]
    assert "mjölk" in body["allergens"] and "gluten" in body["allergens"]
    # a title with spaces must survive URL encoding end to end
    assert client.get("/api/recipe/" + quote("Pannkakor med sylt")).status_code == 200


def test_recipe_unknown_title_404_and_no_route_shadow(client):
    dbm.boot()
    _login(client, "recipe404")
    r = client.get("/api/recipe/" + quote("Rakröst med gurka och skinka"))
    assert r.status_code == 404, r.text
    # the catch-all /{title} is registered LAST — the literal path still wins
    assert client.get("/api/recipe/ratings").status_code == 200


# --------------------------------------------- accept happy path (regression)

def test_accept_happy_path_still_200(client):
    dbm.boot()
    _login(client, "journeyregress")
    dishes = _offer(client, WEEK, 202)
    r = client.post("/api/menu/accept",
                    json={"seed": 202, "week": WEEK, "dishes": dishes})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "week_key": WEEK, "dishes": dishes}
