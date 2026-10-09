"""MODULE M5b — ONE shared offer<->recipe matcher (MC 10350, audit finding 8).

The planner's ``used_offer_ids`` attribution and the optimizer's
``andel_extrapris`` both ask a variant of "does this offer name hit this
recipe / ingredient?". They used to carry two divergent tokenizers (TITLE+
INGREDIENTS vs ingredients-only, and a Swedish stopword counted as a match
token), so the same response could say "0 % extrapris" while listing 10 used
offers, and "Pytt i panna" matched an offer through the word "i". This module
is the single definition of a "hit": one tokenizer, one stopword set.

Consumers: src/planner/menu.py (attribution) and app/optimizer/optimizer.py
(share + ratio) — the same import direction as the src/planner/weeks.py
precedent (MC 1355.5): src stays self-contained, app may import src.

Token rule (both sides, no exceptions): whitespace word, lowercased,
alphabetic, min length 2, Swedish function words excluded.
"""
from __future__ import annotations

import json

# Swedish function words — never a match token, on either side of a match.
SVENSKA_STOPPORD = frozenset({
    "i", "med", "och", "på", "till", "av", "den", "det", "en", "ett",
    "eller", "för", "som", "vid", "efter",
})


def _significant(word: str) -> bool:
    """The ONE token rule: alphabetic, length >= 2, not a stopword."""
    return len(word) >= 2 and word.isalpha() and word not in SVENSKA_STOPPORD


def word_tokens(text: str) -> frozenset:
    """The shared tokenizer: significant lowercased words of a text."""
    return frozenset(w for w in (text or "").lower().split() if _significant(w))


def recipe_ingredient_names(recipe) -> list:
    """Lowercased raw ingredient names of a recipe (C-RDB ingredients_json).
    The andel_extrapris denominator counts these names, tokenized or not."""
    try:
        ings = json.loads(recipe.ingredients_json or "[]")
    except (ValueError, TypeError):
        ings = []
    return [(i.get("name") or "").strip().lower()
            for i in ings if (i.get("name") or "").strip()]


def recipe_tokens(recipe) -> frozenset:
    """Match tokens of a recipe: title words plus ingredient names — the
    attribution scope the planner has always used (TITLE or INGREDIENTS),
    run through the one token rule."""
    tokens = set(word_tokens(getattr(recipe, "title", "") or ""))
    tokens.update(name for name in recipe_ingredient_names(recipe) if _significant(name))
    return frozenset(tokens)


def offer_tokens(offer) -> frozenset:
    """Match tokens of an offer name."""
    return word_tokens(getattr(offer, "name", "") or "")


def offer_hits_recipe(offer, recipe) -> bool:
    """Deterministic: does this offer share a significant token with the
    recipe's title or one of its ingredient names?"""
    return bool(offer_tokens(offer) & recipe_tokens(recipe))


def offer_hits_ingredient(offer, ingredient_name: str) -> bool:
    """Deterministic: does this offer share a significant word-token with this
    single ingredient name? (the andel_extrapris ingredient match)"""
    return bool(offer_tokens(offer) & word_tokens(ingredient_name))
