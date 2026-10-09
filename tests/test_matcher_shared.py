"""tests.test_matcher_shared — MC 10350 (live-dogfood finding 8): ONE shared matcher.

The live bug: "Pytt i panna" showed "0 % extrapris" while used_offer_ids listed
10 offers — the planner attributed via title tokens INCLUDING the Swedish stopword
"i" (so "Glass i strutar 1 l" was a hit), the optimizer's andel_extrapris matched
ingredients-only through a different tokenizer. Both now go through
src/planner/match.py; these tests pin the three acceptance properties.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.optimizer.optimizer import FamilyPrefs, is_extraprice
from app.optimizer.optimizer import plan_menu as optimize
from src.planner.match import offer_hits_recipe, word_tokens
from src.planner.menu import plan_menu

WEEK = "2026-W37"


def _recipe(title, ingredients, servings=4):
    return SimpleNamespace(
        title=title,
        ingredients_json=json.dumps([{"name": n} for n in ingredients]),
        vegetarian=False, allergens_json="[]", budget_tier=None,
        servings=servings, kid_friendly=0,
    )


def _offer(oid, name, regular=None, price=None):
    return SimpleNamespace(offer_id=oid, name=name,
                           regular_price_cents=regular, price_cents=price)


PYTT = _recipe("Pytt i panna med ägg", ["potatis", "ägg", "lök"])
GLASS = _offer(1, "Glass i strutar 1 l", regular=6000, price=3000)   # discounted
EGG = _offer(2, "Ägg 10 st", regular=4000, price=2500)               # discounted


# --------------------------- the live stopword bug is dead

def test_stopword_i_never_makes_a_hit():
    # "Glass i strutar 1 l" must NOT hit "Pytt i panna med ägg" ...
    assert not offer_hits_recipe(GLASS, PYTT)
    plan = plan_menu(WEEK, [GLASS], [PYTT], FamilyPrefs(meal_days=1, persons=4))
    assert plan["days"][0]["used_offer_ids"] == []
    # ... in EITHER matcher (the optimizer attributed the same offer before).
    plans = optimize(WEEK, [GLASS], [PYTT], FamilyPrefs(meal_days=1, persons=4),
                     ratio_threshold=0.0)
    assert plans[0]["days"][0]["used_offer_ids"] == []
    # The stopword itself carries no tokens on either side.
    assert "i" not in word_tokens(PYTT.title) | word_tokens(GLASS.name)


def test_innehållsord_offer_still_hits():
    # An "ägg" offer still hits pytt i panna via the title/ingredient word.
    assert offer_hits_recipe(EGG, PYTT)
    plan = plan_menu(WEEK, [EGG], [PYTT], FamilyPrefs(meal_days=1, persons=4))
    assert plan["days"][0]["used_offer_ids"] == [EGG.offer_id]
    plans = optimize(WEEK, [EGG], [PYTT], FamilyPrefs(meal_days=1, persons=4),
                     ratio_threshold=0.0)
    assert plans[0]["days"][0]["used_offer_ids"] == [EGG.offer_id]


# --------------------------- attribution and ratio agree

def test_discounted_attributed_offer_implies_andel_over_zero():
    # Fixture: the discounted "ägg" offer is BOTH attributed (used_offer_ids)
    # and an extrapris ingredient match — used_offer_ids may no longer claim
    # hits that andel_extrapris then reports as 0 %.
    offers = [GLASS, EGG]
    prefs = FamilyPrefs(meal_days=2, persons=4)
    by_id = {o.offer_id: o for o in offers}
    plans = optimize(WEEK, offers, [PYTT,
                   _recipe("Omelett med ägg och grädde", ["ägg", "grädde"])],
                   prefs, ratio_threshold=0.0)
    checked = 0
    for plan in plans:
        for day in plan["days"]:
            for oid in day["used_offer_ids"]:
                if is_extraprice(by_id[oid]):
                    checked += 1
                    assert day["andel_extrapris"] > 0.0, (
                        f"{by_id[oid].name!r} attributed to {day['dish_id']!r} "
                        f"but andel_extrapris is 0 — the two matchers diverged")
    assert checked > 0, "fixture must actually attribute a discounted offer"
