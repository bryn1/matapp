"""MODULE M5 — planner (deterministic constraint solver).

Implements REV6 CONTRACT C6 ``plan_menu`` (src/planner/menu.py).

Turns the week's in-week offers (C-OW, keyed, already filtered by C4) and the
recipe set (C-RDB, passed in the signature) into a weekly meal plan for one
family, honouring that family's LOCAL constraints (N5) maximally while
maximising offer-hits.

Determinism (N7): ``seed`` is fixed (default 1234), all randomness flows
through a single ``random.Random(seed)`` instance, and there is no wall-clock
randomness — the same inputs reproduce the same :class:`Plan` bit-for-bit
(idempotent). Dates are derived from the ISO ``week_key``, never from ``now``.

Contracts honoured:
  - C-OW (offers): taken as given, no re-filter here — C4 owns the running-week
    predicate.
  - C-RDB (recipes): read from the ``recipes`` argument, never a phantom seam.
  - N5: budget / måltidsantal / personer / allergier / vegetariskt applied here.
  - Degradation: a day with no matching offer still gets a dish with
    ``used_offer_ids == []`` and is emitted with an INFO log (never dropped).
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta

logger = logging.getLogger("kvallsmat.planner")

# Display week keys are ISO 8601 week dates, e.g. "2026-W34".
from .weeks import week_to_monday as _week_to_monday  # MC 1355.5: ONE shared helper
# MC 10350 (finding 8): ONE shared offer matcher — the optimizer computes
# andel_extrapris through the same tokenizer, so attribution and ratio agree.
from .match import offer_hits_recipe as _offer_hits_recipe


@dataclass(frozen=True)
class FamilyPrefs:
    """One family's local planning constraints (N5). Passed into C6."""

    meal_days: int = 5            # måltidsantal — how many dinners to plan
    persons: int = 4              # personer — how many to feed (each day)
    vegetarian: bool = False      # True -> only vegetarian dishes
    allergens: tuple = ()         # excluded allergens (e.g. ("mjölk", "fisk"))
    budget_tier: str | None = None  # None = no budget filter; else budget|mid|premium

    def __post_init__(self):
        if self.meal_days <= 0:
            raise ValueError("meal_days must be >= 1")
        if self.persons <= 0:
            raise ValueError("persons must be >= 1")
        object.__setattr__(self, "allergens", tuple(self.allergens or ()))


# 1=budget (cheapest) .. 3=premium (most expensive)
_TIER_RANK = {"budget": 1, "mid": 2, "premium": 3}


class Plan(dict):
    """The C6 output value. A plain dict is the contract shape; this subclasses it
    so the type hint is meaningful while staying a real dict for consumers."""

    __slots__ = ()


def _offer_hit_count(recipe, offers) -> int:
    """Number of *offers* in the week that hit this recipe (deterministic);
    used to maximise offer-hits across the plan."""

    return sum(1 for o in offers if _offer_hits_recipe(o, recipe))


def _allowed(recipe, family: FamilyPrefs) -> bool:
    """N5 constraint predicate: is this recipe eligible for the family?"""

    if family.vegetarian and not recipe.vegetarian:
        return False
    try:
        allergens = json.loads(recipe.allergens_json or "[]")
    except (ValueError, TypeError):
        allergens = []
    if family.allergens and any(a in allergens for a in family.allergens):
        return False
    if family.budget_tier is not None:
        if _TIER_RANK.get(recipe.budget_tier, 2) > _TIER_RANK[family.budget_tier]:
            return False
    if (recipe.servings or 1) < family.persons:
        return False
    return True


def _week_dates(week_key: str, meal_days: int) -> list[str]:
    monday = _week_to_monday(week_key)
    return [(monday + timedelta(days=i)).isoformat() for i in range(meal_days)]


def plan_menu(week_key: str, offers: list, recipes: list,
              family: FamilyPrefs, seed: int = 1234) -> Plan:
    """CONTRACT C6 — deterministically plan the week for one family.

    Returns ``Plan`` with ``{"week_key": ..., "days": [{"date", "dish_id",
    "used_offer_ids": [...]}]}``.

    Strategy (all deterministic):
      1. Filter recipes to those respecting the family's LOCAL constraints (N5).
      2. Rank candidates by descending offer-hit count (maximise offer-hits);
         ties broken by a seeded shuffle/order so results are reproducible.
      3. Assign one dish per planned day (left-to-right over the week), never
         repeating a dish within the week. Each day takes the matching offers
         (the offers that hit the chosen dish) — 0-offer days degrade cleanly.
      ``week_key``'s dates come from the ISO week, never from the clock.
    """

    rng = random.Random(seed)
    dates = _week_dates(week_key, family.meal_days)

    eligible = [r for r in recipes if _allowed(r, family)]
    # Deterministic rank: hit-count DESC, then (MC 1355.18) kid-friendly wins
    # TIES when the family prefers it, then a seeded, stable tie-break. getattr
    # keeps the key working for any FamilyPrefs carrier; with the flag off (or
    # no kid-friendly recipes) the key is byte-identical to the pre-T11 one.
    prefer_kid = bool(getattr(family, "prefer_kid_friendly", False))

    def _rank(r):
        kid = (getattr(r, "kid_friendly", 0) or 0) if prefer_kid else 0
        return (-_offer_hit_count(r, offers), -kid, rng.random())
    pool = sorted(eligible, key=_rank)

    days: list[dict] = []
    used_dish_ids: set = set()
    remaining_offers = {o.offer_id: o for o in offers}

    for d in dates:
        chosen = None
        for r in pool:
            if r.title not in used_dish_ids:
                chosen = r
                break
        if chosen is None:
            logger.info("Planner: no unused eligible recipe remains for %s", d)
            break
        used_dish_ids.add(chosen.title)
        hits = [o.offer_id for o in offers
                if o.offer_id in remaining_offers
                and _offer_hits_recipe(o, chosen)]
        if not hits:
            logger.info("Planner: day %s '%s' has no matching offer (recipe-only)",
                        d, chosen.title)
        for oid in hits:
            remaining_offers.pop(oid, None)
        days.append({
            "date": d,
            "dish_id": chosen.title,
            "used_offer_ids": hits,
        })

    return Plan({"week_key": week_key, "days": days})
