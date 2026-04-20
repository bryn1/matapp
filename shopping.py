#!/usr/bin/env python3
"""
shopping.py — Bygger en kombinerad inköpslista från veckans recept.

Grupperar ingredienser per kategori, deduplicerar, och markerar
varor som är på rea denna vecka.
"""

import logging
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

# Kategorier i önskad ordning
CATEGORY_ORDER = ["frukt/grönt", "mejeri", "kött", "torrvaror", "övrigt", "hemma"]

# Nyckelord för automatisk kategoritilldelning (case-insensitiv delmatchning)
# Mer specifika matchningar ska komma före generella
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "frukt/grönt": [
        "tomat", "lök", "vitlök", "paprika", "zucchini", "broccoli", "blomkål",
        "morot", "rotsak", "potatis", "sötpotatis", "spenat", "sallad", "gurka",
        "äpple", "päron", "banan", "apelsin", "citron", "lime", "avokado",
        "svamp", "majs", "ärtor", "bönor", "linser", "purjolök", "selleri",
        "ingefära", "koriander", "basilika", "persilja", "dill", "timjan",
        "rosmarin", "grönsak", "frukt", "bär", "mango", "ananas",
    ],
    "mejeri": [
        "mjölk", "grädde", "crème fraiche", "creme fraiche", "filmjölk",
        "yoghurt", "smör", "margarin", "ost", "mozzarella", "parmesan",
        "halloumi", "fetaost", "feta", "kvarg", "ägg", "gräddfil",
        "ricotta", "mascarpone", "keso",
    ],
    "kött": [
        "kyckling", "kycklingfilé", "kycklinglår", "kycklingbröst",
        "köttfärs", "malet kött", "biff", "fläsk", "bacon", "skinka",
        "korv", "falukorv", "isterband", "lax", "fisk", "räkor", "tonfisk",
    ],
    "torrvaror": [
        "pasta", "ris", "couscous", "bulgur", "quinoa", "nudlar",
        "mjöl", "socker", "salt", "peppar", "krydda", "olja", "olivolja",
        "rapsolja", "vinäger", "soja", "sojaost", "tomatsås", "tomatpuré",
        "konserv", "burk", "kikärtor", "linser", "bönor", "soppa",
        "buljong", "fond", "ketchup", "senap", "majonnäs", "dressing",
        "honung", "sirap", "vanilj", "bakpulver", "jäst", "ströbröd",
        "havregryn", "müsli", "cornflakes", "knäcke", "bröd", "tortilla",
        "pitabröd", "nötter", "mandlar", "frön", "torkad", "torkade",
    ],
}


_CATEGORY_KW_SORTED = sorted(
    [(keyword, category) for category, keywords in CATEGORY_KEYWORDS.items() for keyword in keywords],
    key=lambda x: len(x[0]),
    reverse=True
)

def _guess_category(item_name: str) -> str:
    """
    Gissar kategori för en ingrediens baserat på namn.
    Returnerar 'övrigt' om inget nyckelord matchar.
    """
    name_lower = item_name.lower()
    for keyword, category in _CATEGORY_KW_SORTED:
        if keyword in name_lower:
            return category
    return "övrigt"


FOOD_SYNONYMS = {
    "sal": "salt",
    "papper": "peppar",
    "black pepper": "peppar",
    "lökar": "lök",
    "rödlök": "lök",
    "rödlökar": "lök",
    "isbergssallad": "sallad (isbergs)",
    "röd paprika": "paprika",
    "gul paprika": "paprika",
    "grön paprika": "paprika",
}


def _normalize_item_name(name: str) -> str:
    """Normaliserar ingrediensnamn för deduplicering."""
    s = name.strip().lower()
    # Strip parentheticals: "rödlök(ar)" → "rödlök"
    s = re.sub(r'\s*\([^)]*\)', '', s).strip()
    # Remove leading color/state adjectives: "röda lökar" → "lökar"
    s = re.sub(r'^(röd[a]?|vit[a]?|gul[a]?|grön[a]?|halv[a]?|färsk[a]?)\s+', '', s).strip()
    # Apply synonym canonicalization
    s = FOOD_SYNONYMS.get(s, s)
    return s


def _merge_similar(aggregated: dict, combine_quantities_fn) -> dict:
    """Fuzzy-merge ingredient keys with similarity >= 0.82."""
    keys = list(aggregated.keys())
    skip = set()
    merged = {}
    for i, k1 in enumerate(keys):
        if k1 in skip:
            continue
        for k2 in keys[i + 1:]:
            if k2 in skip:
                continue
            ratio = SequenceMatcher(None, k1, k2).ratio()
            if ratio >= 0.82:
                aggregated[k1]["quantity"] = combine_quantities_fn(
                    aggregated[k1]["quantity"], aggregated[k2]["quantity"]
                )
                if aggregated[k2].get("on_sale"):
                    aggregated[k1]["on_sale"] = True
                skip.add(k2)
        merged[k1] = aggregated[k1]
    return merged


def _parse_quantity(quantity_str: str) -> tuple[float, str]:
    """
    Försöker parsa en kvantitetssträng till (antal, enhet).
    T.ex. "400 g" → (400.0, "g"), "2 st" → (2.0, "st").
    Returnerar (0.0, quantity_str) om parsning misslyckas.
    """
    if not quantity_str:
        return (0.0, "")

    match = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zA-ZåäöÅÄÖ]*)\s*$", quantity_str.strip())
    if match:
        amount_str = match.group(1).replace(",", ".")
        unit = match.group(2).strip()
        try:
            return (float(amount_str), unit)
        except ValueError:
            pass

    return (0.0, quantity_str)


def _combine_quantities(q1: str, q2: str) -> str:
    """
    Kombinerar två kvantitetssträngar om de har samma enhet.
    T.ex. "400 g" + "200 g" = "600 g".
    Om enheterna skiljer sig listas de med "+".
    """
    amount1, unit1 = _parse_quantity(q1)
    amount2, unit2 = _parse_quantity(q2)

    if unit1 and unit2 and unit1.lower() == unit2.lower() and amount1 > 0 and amount2 > 0:
        combined = amount1 + amount2
        # Formatera snyggt: heltal om möjligt
        if combined == int(combined):
            return f"{int(combined)} {unit1}"
        else:
            return f"{combined:.1f} {unit1}"

    # Olika enheter eller unparsable — lista separat
    parts = [p for p in [q1, q2] if p]
    return " + ".join(parts)


def _is_on_sale(item_name: str, offers: list[dict]) -> bool:
    """
    Whole-word match between ingredient tokens (>=4 chars) and sale offers.
    Only returns True if the offer has is_real_sale=True.
    """
    tokens = re.findall(r"[a-zåäö]{4,}", item_name.lower())
    if not tokens:
        return False
    for offer in offers:
        if not offer.get("is_real_sale"):
            continue
        offer_name_lower = offer.get("name", "").lower()
        for token in tokens:
            if re.search(r'\b' + re.escape(token) + r'\b', offer_name_lower):
                return True
    return False


def build_shopping_list(recipes: list[dict], offers: list[dict], pantry_items: list[str] = [],
                         chain: str = None, store_id: str = None) -> list[dict]:
    """
    Bygger en kombinerad och sorterad inköpslista från alla recept.

    Args:
        recipes: Lista med recept-dicts från recipes.generate_recipes()
        offers: Lista med erbjudanden från willys.fetch_offers()

    Returns:
        Lista med kategorier och deras ingredienser:
        [
            {
                "category": "frukt/grönt",
                "items": [
                    {"item": "tomater", "quantity": "8 st", "on_sale": True},
                    ...
                ]
            },
            ...
        ]
    """
    # Samla alla ingredienser med normaliserat namn som nyckel
    # Värde: {"quantity": str, "on_sale": bool, "original_name": str}
    aggregated: dict[str, dict] = {}
    pantry_lower = [p.lower() for p in pantry_items]

    for recipe in recipes:
        recipe_name = recipe.get("name", "Okänt recept")
        is_fish_recipe = recipe.get("fish_recipe", False)
        for ing in recipe.get("ingredients", []):
            item = ing.get("item", "").strip()
            if not item:
                continue

            quantity = ing.get("quantity", "")
            on_sale_flag = ing.get("on_sale", False)

            norm_key = _normalize_item_name(item)

            if norm_key in aggregated:
                # Kombinera kvantiteter
                existing = aggregated[norm_key]
                existing["quantity"] = _combine_quantities(
                    existing["quantity"], quantity
                )
                # Om varan är på rea i något recept, markera som rea
                if on_sale_flag:
                    existing["on_sale"] = True
            else:
                in_pantry = any(p in norm_key for p in pantry_lower)
                # Fish recipe ingredients go to "hemma" — buy at fish counter, not Willys
                category = "hemma" if is_fish_recipe else _guess_category(item)
                aggregated[norm_key] = {
                    "item": item,  # Behåll originalskapitalisering
                    "quantity": quantity,
                    "on_sale": on_sale_flag,
                    "category": category,
                    "in_pantry": in_pantry,
                }

    # Fuzzy-merge near-duplicate ingredient keys
    aggregated = _merge_similar(aggregated, _combine_quantities)

    logger.info(f"Aggregerade {len(aggregated)} unika ingredienser från {len(recipes)} recept")

    # Kontrollera mot erbjudanden — uppdatera on_sale baserat på faktiska erbjudanden
    for norm_key, data in aggregated.items():
        if not data["on_sale"]:
            data["on_sale"] = _is_on_sale(data["item"], offers)

    # Lookup store product for each item (package name + price) if store is known
    if chain and store_id:
        try:
            import db as _db
            for norm_key, data in aggregated.items():
                product = _db.lookup_store_product(chain, store_id, data["item"])
                if product:
                    data["store_product"] = product["name"]
                    data["store_price"] = product["price"]
                    data["store_compare"] = product.get("compare_price") or ""
                else:
                    data["store_product"] = None
                    data["store_price"] = None
                    data["store_compare"] = ""
        except Exception as e:
            logger.warning(f"Store product lookup failed: {e}")

    # Gruppera per kategori
    by_category: dict[str, list[dict]] = defaultdict(list)
    for data in aggregated.values():
        category = data["category"]
        item_dict = {
            "item": data["item"],
            "quantity": data["quantity"],
            "on_sale": data["on_sale"],
            "in_pantry": data.get("in_pantry", False),
        }
        if data.get("store_product"):
            item_dict["store_product"] = data["store_product"]
            item_dict["store_price"] = data["store_price"]
            item_dict["store_compare"] = data["store_compare"]
        by_category[category].append(item_dict)

    # Sortera varje kategori alfabetiskt
    for category in by_category:
        by_category[category].sort(key=lambda x: x["item"].lower())

    # Bygg slutresultat i rätt kategoriordning
    result = []
    seen_categories = set()

    for category in CATEGORY_ORDER:
        items = by_category.get(category, [])
        result.append({
            "category": category,
            "items": items,
        })
        seen_categories.add(category)

    # Lägg till eventuella okategoriserade kategorier sist
    for category, items in by_category.items():
        if category not in seen_categories:
            result.append({
                "category": category,
                "items": items,
            })

    # Logga statistik
    total_items = sum(len(cat["items"]) for cat in result)
    sale_items = sum(
        1 for cat in result for item in cat["items"] if item["on_sale"]
    )
    logger.info(
        f"Inköpslista klar: {total_items} varor totalt, "
        f"{sale_items} på rea denna vecka"
    )

    return result


def format_shopping_list(shopping_list: list[dict], use_emoji: bool = True) -> str:
    """
    Formaterar inköpslistan som läsbar text.

    Args:
        shopping_list: Resultat från build_shopping_list()
        use_emoji: Om True, visas 💰 vid reapriser

    Returns:
        Formaterad textsträng
    """
    lines = []
    for category_data in shopping_list:
        items = category_data["items"]
        if not items:
            continue

        category = category_data["category"].upper()
        lines.append(f"\n{category}")
        lines.append("─" * len(category))

        for item in items:
            sale_mark = " 💰" if (use_emoji and item.get("on_sale")) else (" [REA]" if item.get("on_sale") else "")
            qty = item.get("quantity", "")
            name = item.get("item", "")
            if qty:
                lines.append(f"  □ {qty} {name}{sale_mark}")
            else:
                lines.append(f"  □ {name}{sale_mark}")

    return "\n".join(lines)
