"""
normalizer.py — Converts matapp shopping list items into store search queries.

The shopping list uses Swedish cooking ingredient names (e.g. "nötfärs",
"crème fraiche", "kycklingfilé(er)"). Store search engines need clean,
unambiguous queries without quantity suffixes or parentheticals.

Also assigns a requested_quantity (number of packages) based on the
quantity string (e.g. "600 g" with a 500g package → 2 packages).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Synonyms: matapp name → preferred search term.
# Add entries when search results are consistently wrong for a term.
SEARCH_SYNONYMS: dict[str, str] = {
    "nötfärs": "nötfärs",
    "köttfärs": "nötfärs",
    "fläskfärs": "fläskfärs",
    "lammfärs": "lammfärs",
    "kycklingfilé": "kycklingfilé",
    "kycklingbröst": "kycklingfilé",
    "kycklinglår": "kycklinglår",
    "kyckling": "hel kyckling",
    "crème fraiche": "crème fraiche",
    "crème fraîche": "crème fraiche",
    "creme fraiche": "crème fraiche",
    "gräddfil": "gräddfil",
    "vispgrädde": "vispgrädde",
    "matlagningsgrädde": "matlagningsgrädde",
    "krossade tomater": "krossade tomater",
    "hela tomater": "hela tomater",
    "tomatpuré": "tomatpuré",
    "kokosmjölk": "kokosmjölk",
    "kikärtor": "kikärtor",
    "mat- och baksmör": "smör",
    "soltorkade tomater": "soltorkade tomater",
    "körsbärstomater": "körsbärstomater",
    "röd currypasta": "röd currypasta",
    "grön currypasta": "grön currypasta",
}

# Items to skip entirely — pantry staples not worth ordering individually.
# User is assumed to always have these.
SKIP_ITEMS: set[str] = {
    "salt", "svartpeppar", "peppar", "olivolja", "rapsolja", "socker",
    "strösocker", "vaniljsocker", "bakpulver", "vetemjöl", "mjöl",
    "vatten", "lagerblad", "torkad oregano", "torkad timjan",
    "torkad basilika", "mald spiskummin", "mald koriander",
    "rökt paprikapulver", "fänkålsfrö",
}


@dataclass
class NormalizedItem:
    original: str           # raw shopping list item name
    query: str              # cleaned search query for store API
    quantity_str: str       # original quantity string, e.g. "600 g"
    skip: bool = False      # True → do not search (pantry staple)


def normalize(item_name: str, quantity: str = "") -> NormalizedItem:
    """
    Convert a shopping list item name to a clean store search query.

    Steps:
    1. Strip quantity suffixes embedded in the name ("nötfärs 600g" → "nötfärs")
    2. Strip parentheticals ("kycklingfilé(er)" → "kycklingfilé")
    3. Strip leading color/size adjectives ("röda tomater" → "tomater")
    4. Lowercase and strip whitespace
    5. Apply synonym map
    6. Check skip list

    Returns NormalizedItem with skip=True for pantry staples.
    """
    name = item_name.strip()

    # Strip trailing quantity suffix if name has one ("pasta 400 g" → "pasta")
    name = re.sub(r'\s+\d+[\d.,]*\s*(g|kg|ml|dl|cl|l|st|msk|tsk|förp|pack)\s*$',
                  '', name, flags=re.IGNORECASE).strip()

    # Strip leading quantity prefix ("2 ägg" → "ägg", "1 dl mjölk" → "mjölk")
    # Match: optional number + optional unit + space, only when followed by a word
    name = re.sub(
        r'^\d+[\d.,]?\s*(?:dl|ml|cl|l|kg|g|st|msk|tsk|knippe|kruka|röd|)?\s+(?=\S)',
        '', name, flags=re.IGNORECASE
    ).strip()

    # Strip parentheticals ("kycklingfilé(er)" → "kycklingfilé")
    name = re.sub(r'\s*\([^)]*\)', '', name).strip()

    # Strip leading color/state adjectives
    name = re.sub(
        r'^(röd[a]?|vit[a]?|gul[a]?|grön[a]?|halv[a]?|färsk[a]?|torkad[e]?|mald[a]?)\s+',
        '', name, flags=re.IGNORECASE
    ).strip()

    lower = name.lower()

    # Apply synonym map (exact match on cleaned lowercase)
    query = SEARCH_SYNONYMS.get(lower, lower)

    skip = lower in SKIP_ITEMS

    return NormalizedItem(
        original=item_name,
        query=query,
        quantity_str=quantity.strip(),
        skip=skip,
    )


def normalize_shopping_list(items: list[dict]) -> list[NormalizedItem]:
    """
    Normalize a full shopping list (list of {item, quantity, ...} dicts).
    Filters out items with skip=True.
    """
    result = []
    for it in items:
        name = it.get("item", "").strip()
        qty = it.get("quantity", "")
        if not name:
            continue
        norm = normalize(name, qty)
        result.append(norm)
    return result
