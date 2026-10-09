"""tests.test_menu_diversity — MC 10351 (dogfood 10064.7 finding 3).

The live bug: greedy hit-count ranking left the three seeds (101/202/303)
nothing to vary — förslag 1 and 2 were byte-identical for the live week and
förslag 3 differed only in the last dish. The fix is the exclusion cascade in
menu._diverse_plans: suggestion k plans over the rotated roster minus earlier
suggestions' dishes, degrading to weaker exclusions when the pool cannot fill
the week.

Live-shaped fixture: the boot-seeded starter roster (18 dishes, all eligible
at persons=2) + a sample offer week. Covers:
  * meal_days=5 -> the three suggestions' dish SETS are pairwise DISTINCT;
  * accept of seed 202/303 after a fresh GET is still 200 (recompute equality
    through the ONE shared assembly — byte-identical accept preserved);
  * meal_days=7 -> graceful degradation: every suggestion still plans all 7
    days (never fewer, never starves); later seeds MAY reuse earlier dishes;
  * rotation exclusion applies BEFORE the cascade: titles accepted last week
    appear in NO suggestion of the following week.
"""
from __future__ import annotations

from app import db as dbm
from app import security
from src.offers_db.store import upsert_week

WEEK = "2026-W41"          # the live dogfood week (offers + Monday 2026-10-19)
FOLLOWING_WEEK = "2026-W42"

OFFER_ROWS = [
    {"grocer_id": "willys", "external_id": "w-1", "name": "Köttfärs 500g",
     "price_cents": 4990, "unit": "g",
     "valid_from": "2026-10-19", "valid_to": "2026-10-25"},
    {"grocer_id": "willys", "external_id": "w-2", "name": "Grädde 3 dl",
     "price_cents": 1290, "unit": "dl",
     "valid_from": "2026-10-19", "valid_to": "2026-10-25"},
    {"grocer_id": "coop", "external_id": "c-1", "name": "Tacokrydda",
     "price_cents": 890, "unit": "p",
     "valid_from": "2026-10-19", "valid_to": "2026-10-25"},
    {"grocer_id": "coop", "external_id": "c-2", "name": "Pasta 500g",
     "price_cents": 1590, "unit": "g",
     "valid_from": "2026-10-19", "valid_to": "2026-10-25"},
]


def _login(client, username: str, password: str = "pw-div-1") -> None:
    r = client.post("/api/auth/register",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.headers["set-cookie"].split(";")[0].split("=")[1]
    client.cookies.set(security.SESSION_COOKIE, token)


def _profile(client, meal_days: int) -> None:
    """persons=2 keeps the WHOLE 18-dish starter roster eligible (smallest
    starter serving is 2) — the diversity fixture must not hinge on the
    persons filter."""
    r = client.put("/api/profile",
                   json={"persons": 2, "meal_days": meal_days,
                         "kron_budget": 800})
    assert r.status_code == 200, r.text


def _seed_offers(week: str = WEEK) -> None:
    session = dbm._Session()
    try:
        upsert_week(session, [dict(r, week_key=week) for r in OFFER_ROWS],
                    week)
    finally:
        session.close()


def _by_seed(body: dict) -> dict:
    """seed -> ordered dish list, straight off the GET response."""
    return {s["seed"]: [d["dish_id"] for d in s["days"]]
            for s in body["suggestions"]}


def test_suggestions_pairwise_distinct_five_days(client):
    """meal_days=5, 18-dish roster, sample offers: no two suggestions may
    carry the same dish SET — the live week's byte-identical 1≡2 fails here."""
    dbm.boot()
    _login(client, "divuser")
    _profile(client, meal_days=5)
    _seed_offers()
    resp = client.get(f"/api/menu?week={WEEK}")
    assert resp.status_code == 200, resp.text
    dishes = _by_seed(resp.json())
    assert set(dishes) == {101, 202, 303}
    for seed, titles in dishes.items():
        assert len(titles) == 5, f"seed {seed} must plan all 5 days"
    s1, s2, s3 = (set(dishes[seed]) for seed in (101, 202, 303))
    assert s1 != s2 and s1 != s3 and s2 != s3, "suggestions must be pairwise distinct"
    # The exact live symptom: suggestion 2 excluded suggestion 1's dishes.
    assert s1 & s2 == set(), "suggestion 2 must drop suggestion 1's dishes"


def test_accept_seed_202_and_303_recompute_equal(client):
    """Accept recompute equality through the cascade: after a FRESH GET the
    dishes offered for seed 202/303 echo back to accept and still 200 (plans.py
    recomputes via the same _assemble_menu — the cascade changes WHICH plan
    each seed gets, never the byte-identity of a recompute)."""
    dbm.boot()
    _login(client, "accdiv")
    _profile(client, meal_days=5)
    _seed_offers()
    for seed in (202, 303):
        offered = _by_seed(client.get(f"/api/menu?week={WEEK}").json())
        r = client.post("/api/menu/accept",
                        json={"seed": seed, "week": WEEK,
                              "dishes": offered[seed]})
        assert r.status_code == 200, r.text
        assert r.json()["dishes"] == offered[seed]


def test_meal_days_seven_degrades_gracefully(client):
    """18 dishes cannot fill 3 disjoint 7-day weeks (3×7 > 18): the cascade
    degrades instead of starving. Guaranteed (MC 10376 tightened this): every
    suggestion still plans FULL 7 days (never fewer, never starve), the pair
    S1/S2 keeps the strict exclusion, and NO pair is set-identical — the
    last rung may reuse earlier dishes but never byte-reproduce a card."""
    dbm.boot()
    _login(client, "sevuser")
    _profile(client, meal_days=7)
    _seed_offers()
    resp = client.get(f"/api/menu?week={WEEK}")
    assert resp.status_code == 200, resp.text
    dishes = _by_seed(resp.json())
    for seed, titles in dishes.items():
        assert len(titles) == 7, f"seed {seed} must still plan all 7 days"
    s1, s2, s3 = (set(dishes[seed]) for seed in (101, 202, 303))
    assert s1 & s2 == set(), "seed 202 keeps the strict exclusion (11 >= 7)"
    assert s2 != s3, "the weaker fallback must still drop seed 202's dishes"
    assert s1 != s3, "MC 10376: the last rung never byte-reproduces card 1"
    assert len(s1 & s3) < 7 and len(s2 & s3) < 7, \
        "MC 10376: degraded reuse must keep shared < meal_days for every pair"


def test_seven_days_after_accept_no_pair_identical(client):
    """MC 10376 parent repro, degraded shape: meal_days=7, one accept excludes
    its 7 titles for the FOLLOWING week (roster 11, 21 slots > 11 titles).
    At base the exclusion ladder's last rung falls back to the UNCHANGED pool
    and greedy reconstitutes card 1 exactly (S1 vs S3 shared=7). Guaranteed:
    every seed still fills 7 days, no two cards are set-identical, and every
    pair shares fewer than meal_days titles."""
    dbm.boot()
    _login(client, "degrad7")
    _profile(client, meal_days=7)
    _seed_offers()
    first = _by_seed(client.get(f"/api/menu?week={WEEK}").json())
    r = client.post("/api/menu/accept",
                    json={"seed": 101, "week": WEEK, "dishes": first[101]})
    assert r.status_code == 200, r.text
    _seed_offers(FOLLOWING_WEEK)
    dishes = _by_seed(client.get(f"/api/menu?week={FOLLOWING_WEEK}").json())
    for seed, titles in dishes.items():
        assert len(titles) == 7, f"seed {seed} must still plan all 7 days"
    sets = {seed: set(titles) for seed, titles in dishes.items()}
    for a, b in ((101, 202), (101, 303), (202, 303)):
        assert sets[a] != sets[b], f"S{a} vs S{b} identical card set"
        assert len(sets[a] & sets[b]) < 7, \
            f"S{a} vs S{b} share all 7 titles — degradation must minimise overlap"


def test_five_days_week2_after_accept_no_pair_identical(client):
    """MC 10376 DA's second degraded shape: one accept, then the FOLLOWING
    week at meal_days=5 (rotation drops the 5 accepted titles from the 18-dish
    roster; the strict exclusion cannot hold for the third seed). Guaranteed:
    every seed fills 5 days, no pair is set-identical and shared < meal_days —
    overlap is minimised, the last rung never falls back to the unchanged
    pool (at base S1/S3 ride the same pool and S1 vs S3 can collide)."""
    dbm.boot()
    _login(client, "degrad5w2")
    _profile(client, meal_days=5)
    _seed_offers()
    first = _by_seed(client.get(f"/api/menu?week={WEEK}").json())
    r = client.post("/api/menu/accept",
                    json={"seed": 101, "week": WEEK, "dishes": first[101]})
    assert r.status_code == 200, r.text
    _seed_offers(FOLLOWING_WEEK)
    dishes = _by_seed(client.get(f"/api/menu?week={FOLLOWING_WEEK}").json())
    for seed, titles in dishes.items():
        assert len(titles) == 5, f"seed {seed} must plan all 5 days"
    sets = {seed: set(titles) for seed, titles in dishes.items()}
    for a, b in ((101, 202), (101, 303), (202, 303)):
        assert sets[a] != sets[b], f"S{a} vs S{b} identical card set"
        assert len(sets[a] & sets[b]) < 5, \
            f"S{a} vs S{b} share all 5 titles — degradation must minimise overlap"


def test_rotation_exclusion_precedes_cascade(client):
    """The 42-day rotation pool runs FIRST: titles accepted last week may not
    appear in ANY suggestion the following week — the cascade plans over the
    ROTATED roster, never the raw one."""
    dbm.boot()
    _login(client, "rotdiv")
    _profile(client, meal_days=5)
    _seed_offers()
    accepted = _by_seed(client.get(f"/api/menu?week={WEEK}").json())[101]
    r = client.post("/api/menu/accept",
                    json={"seed": 101, "week": WEEK, "dishes": accepted})
    assert r.status_code == 200, r.text
    _seed_offers(FOLLOWING_WEEK)
    following = _by_seed(
        client.get(f"/api/menu?week={FOLLOWING_WEEK}").json())
    for seed, titles in following.items():
        assert len(titles) == 5, f"seed {seed} still fills the week"
        assert not set(titles) & set(accepted), (
            f"rotation-excluded title served to seed {seed} — the cascade "
            "must run on the rotated roster")
