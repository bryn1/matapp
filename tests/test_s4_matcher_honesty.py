"""tests.test_s4_matcher_honesty — matapp-security S4 matcher honesty (MC 10469).

test_matcher_shared.py already pins the ONE tokenizer (stopword "i" never
matches — deleting the stopword rule turns THAT file RED, so it is not
restated here). The uncovered half was the DISCOUNT rule of andel_extrapris:
app.optimizer.optimizer._offer_extrapris_matches counts an ingredient only when
the matching offer is actually on extrapris (regular > sale). Strip that rule
and undiscounted offers inflate every dish's "extrapris" ratio while the label
still says discount. These three tests pin the rule from both sides:
  * a real token-match that is NOT discounted counts 0.0
    -> plant: drop `is_extraprice(offer) and` from the rule -> RED
  * a discounted token-match counts its ingredients
    -> plant: make src/planner/match.offer_hits_ingredient always False -> RED
  * a discounted NON-match counts 0.0
    -> plant: make offer_hits_ingredient always True -> RED
Transcripts in T4-RESULT.md.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.optimizer.optimizer import andel_extrapris

PYTT_INGS = ["potatis", "ägg", "lök"]


def _recipe(title: str, ingredients: list[str]):
    return SimpleNamespace(
        title=title,
        ingredients_json=json.dumps([{"name": n} for n in ingredients]),
    )


def _offer(name: str, regular: int, sale: int):
    return SimpleNamespace(offer_id=1, name=name,
                           regular_price_cents=regular, price_cents=sale)


def test_undiscounted_matching_offer_never_counts():
    """Token-match is real, but full price -> the DISCOUNT rule alone must
    keep andel_extrapris at zero (otherwise "andel_extrapris" is a lie)."""
    egg = _offer("Ägg 10 st", regular=4000, sale=4000)  # NOT extraprice
    assert andel_extrapris(_recipe("Pytt i panna", PYTT_INGS), [egg]) == 0.0


def test_discounted_matching_offer_counts_its_ingredients():
    """The rule's positive side: one discounted offer hitting 1 of 3
    ingredients is exactly 1/3 — not 0 (rule dropped) and not 1 (match-always)."""
    egg = _offer("Ägg 10 st", regular=4000, sale=2500)
    assert andel_extrapris(_recipe("Pytt i panna", PYTT_INGS),
                           [egg]) == pytest.approx(1 / 3)


def test_discounted_nonmatching_offer_never_counts():
    """A discounted offer sharing no significant token counts 0.0 (guards the
    match half of the rule against always-true)."""
    glass = _offer("Glass i strutar 1 l", regular=6000, sale=3000)
    assert andel_extrapris(_recipe("Pytt i panna", PYTT_INGS), [glass]) == 0.0
