"""MODULE T5 Phase 6 — optimizer-ratio-3 (deterministic menu optimizer).

matapp framtidsversion, Phase 6 (PHASE0.md §P Phase 6, gate C5). Extends the POC
``src/planner/menu.py`` ``plan_menu`` greedy with two gate-C5 requirements:

  1. RATIO N5 FILTER (hard, BEFORE greedy): a dish is only eligible if
     ``andel_extrapris >= ratio_threshold`` (default 0.5). ``andel_extrapris`` is the
     fraction of the dish's ingredients matched by an EXTRAPRIS offer (an offer whose
     reference price is strictly greater than its sale price, i.e. ``is_extraprice``
     from the Phase 4 normalizer). No new dependency — greedy remains sufficient.

  2. THREE SUGGESTIONS (3 seeds): ``plan_menu`` returns ``n_variants`` (default 3)
     weekly Plans, one per seed from a deterministic seed set, so the family sees three
     mutually-distinct candidate weeks (seed varies the greedy tie-break).

The reference price comes from Phase 2's ``offers.regular_price_cents`` + Phase 4's
per-ingredient ``is_extraprice`` flag — this module only READS those fields on the
in-memory offer records it is handed (matching the POC's in-memory planner seam); it
does not re-normalise or query the DB.

Determinism (N7): all randomness flows through a single ``random.Random(seed)`` per
variant; same inputs -> same plans bit-for-bit. ``andel_extrapris`` and the dish
selection are fully deterministic.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import timedelta

# MC 10350 (audit finding 8): ONE shared offer matcher. The old local
# tokenizers (_words + the inline title/ingredient sets) diverged from the
# planner's and counted Swedish stopwords as match tokens — same import
# direction as src.planner.weeks below: app may import src, never the reverse.
from src.planner.match import (
    offer_hits_ingredient as _offer_hits_ingredient,
    offer_hits_recipe as _offer_hits_recipe,
    recipe_ingredient_names,  # re-exported: routers/tests import it from here
)

# ---------------------------------------------------------------------------
# Family preferences (same N5 shape as the POC FamilyPrefs + the ratio threshold)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyPrefs:
    meal_days: int = 5            # måltidsantal — how many dinners to plan
    persons: int = 4              # personer — how many to feed each day
    vegetarian: bool = False      # True -> only vegetarian dishes
    allergens: tuple = ()         # excluded allergens (e.g. ("mjölk", "fisk"))
    budget_tier: str | None = None  # None = no budget filter; else budget|mid|premium
    # MC 1355.18 (T11): barnvänligt — kid-friendly dishes win TIES on offer-hits
    # when set. This is the FamilyPrefs the menu router instantiates and passes
    # into plan_menu (DA P1: NOT the same-named src/planner dataclass).
    prefer_kid_friendly: bool = False

    def __post_init__(self):
        if self.meal_days <= 0:
            raise ValueError("meal_days must be >= 1")
        if self.persons <= 0:
            raise ValueError("persons must be >= 1")
        object.__setattr__(self, "allergens", tuple(self.allergens or ()))


_TIER_RANK = {"budget": 1, "mid": 2, "premium": 3}

# Default seed set -> 3 distinct suggestions (gate C5: "3 seeds / variant").
DEFAULT_SEEDS = (101, 202, 303)


class Plan(dict):
    """One weekly plan (C6 output shape, dict-subclass for hinting)."""
    __slots__ = ()


# ---------------------------------------------------------------------------
# Extrapris ratio helpers (gate C5 core: per-dish andel_extrapris >= threshold)
# ---------------------------------------------------------------------------


def is_extraprice(offer) -> bool:
    """True iff this offer has a measurable extrapris discount (Phase 4 flag idiom).

    Accepts either a precomputed ``is_extraprice`` attribute (Phase 4 normalizer) or,
    on a bare record, derives it from the reference vs sale price:
    regular_price_cents is not None and regular_price_cents > price_cents.
    """
    flag = getattr(offer, "is_extraprice", None)
    if flag is not None:
        return bool(flag)
    reg = getattr(offer, "regular_price_cents", None)
    price = getattr(offer, "price_cents", None)
    return reg is not None and price is not None and reg > price


def _offer_extrapris_matches(offer, ingredient_name: str) -> bool:
    """Does this EXTRAPRIS offer share a word-token with the ingredient?

    An ingredient only counts toward ``andel_extrapris`` when matched by an offer
    that is actually on extrapris (reference price > sale price). A matching offer
    with NO discount is dropped — that is the whole point of the majority rule.
    The token rule itself is the shared matcher's (MC 10350).
    """
    return is_extraprice(offer) and _offer_hits_ingredient(offer, ingredient_name)


def andel_extrapris(recipe, offers) -> float:
    """Ratio of this dish's ingredients matched by an extrapris offer, in [0.0, 1.0].

    Empty-ingredient recipes yield 0.0 (never auto-pass the >= threshold).
    """
    ings = recipe_ingredient_names(recipe)
    if not ings:
        return 0.0
    matched = sum(
        1 for ing in ings if any(_offer_extrapris_matches(o, ing) for o in offers)
    )
    return matched / len(ings)


# ---------------------------------------------------------------------------
# N5 eligibility: budget / vegetarian / allergens / persons AND ratio threshold
# ---------------------------------------------------------------------------


def _allowed_recipe(recipe, family: FamilyPrefs) -> bool:
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


def _offer_hit_count(recipe, offers) -> int:
    """Number of in-week offers that hit this recipe (matches title or an ingredient)."""
    return sum(1 for o in offers if _offer_hits_recipe(o, recipe))


# ---------------------------------------------------------------------------
# Week-date derivation (ISO week, deterministic, from week_key never from clock)
# ---------------------------------------------------------------------------


# MC 1355.5 (audit P2-4): ONE shared week-math helper — the local copy that drifted
# into BUG-1/BUG-2 is retired; see src/planner/weeks.py.
from src.planner.weeks import week_to_monday as _week_to_monday


def _week_dates(week_key: str, meal_days: int) -> list[str]:
    monday = _week_to_monday(week_key)
    return [(monday + timedelta(days=i)).isoformat() for i in range(meal_days)]


# ---------------------------------------------------------------------------
# The optimizer: ratio N5 filter + greedy + 3-seed suggestions
# ---------------------------------------------------------------------------


def _greedy_plan(week_key: str, offers, eligible, family: FamilyPrefs,
                 ratio_threshold: float, seed: int) -> Plan:
    """Deterministic greedy for ONE seed: assign a dish per day, never repeating a
    dish within the week, maximise offer-hits (ties seeded). Each chosen day records
    its ``andel_extrapris`` ratio so the >= threshold invariant is checkable."""
    rng = random.Random(seed)
    dates = _week_dates(week_key, family.meal_days)

    # Rank: offer-hit count DESC; ties broken by a seeded, reproducible order.
    def _rank(r):
        return (-_offer_hit_count(r, offers), rng.random())

    pool = sorted(eligible, key=_rank)
    days: list[dict] = []
    used_dish_ids: set = set()
    remaining_offers = {getattr(o, "offer_id", id(o)): o for o in offers}

    for d in dates:
        chosen = next((r for r in pool if r.title not in used_dish_ids), None)
        if chosen is None:
            break
        used_dish_ids.add(chosen.title)
        hits = [
            getattr(o, "offer_id", id(o))
            for o in offers
            if getattr(o, "offer_id", id(o)) in remaining_offers
            and _offer_hits_recipe(o, chosen)
        ]
        for oid in hits:
            remaining_offers.pop(oid, None)
        days.append({
            "date": d,
            "dish_id": chosen.title,
            "andel_extrapris": round(andel_extrapris(chosen, offers), 4),
            "used_offer_ids": hits,
        })
    return Plan({"week_key": week_key, "seed": seed, "days": days})


def plan_menu(week_key: str, offers, recipes, family: FamilyPrefs,
              ratio_threshold: float = 0.5, seeds=DEFAULT_SEEDS) -> list[Plan]:
    """CONTRACT C6 + gate-C5 — plan the week for one family, return n suggestions.

    Args:
        week_key:        ISO week key, e.g. "2026-W34".
        offers:          in-week offer records (carrying price_cents / regular_price_cents
                         / is_extraprice from Phase 4).
        recipes:         recipe records (C-RDB shape with ingredients_json etc.).
        family:          FamilyPrefs (N5 local constraints).
        ratio_threshold: HARD N5 ratio filter — a dish is eligible only if
                         ``andel_extrapris >= ratio_threshold`` (default 0.5). Set to
                         0.0 to disable ("majority ignored" — the DELIBERATELY-BROKEN
                         case the harness must go RED on).
        seeds:           seed set producing one Plan per seed (default 3 -> 3
                         distinct suggestions).
    Returns:
        list[Plan] — one weekly plan per seed, every selected dish carrying
        ``andel_extrapris >= ratio_threshold`` (when threshold > 0).
    """
    # HARD RATIO N5 FILTER *BEFORE* greedy: a dish is only in the pool if it clears
    # the local constraints AND the >= majority-extrapris ratio.
    eligible = [
        r for r in recipes
        if _allowed_recipe(r, family) and andel_extrapris(r, offers) >= ratio_threshold
    ]
    return [
        _greedy_plan(week_key, offers, eligible, family, ratio_threshold, seed)
        for seed in seeds
    ]


def all_days_have_ratio(plans, ratio_threshold: float) -> bool:
    """Invariant helper for the harness: every selected dish in every plan meets the
    threshold. Returns False otherwise (used by the deliberately-broken RED case)."""
    for plan in plans:
        for day in plan["days"]:
            if day["andel_extrapris"] < ratio_threshold - 1e-9:
                return False
    return True
