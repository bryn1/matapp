#!/usr/bin/env python3
"""
recipes.py — Genererar veckans recept via lokal Ollama (mistral:7b).

Anpassat för: 2 vuxna + 2 barn (3 och 6 år), vegetariskt + kyckling + fisk.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

def _build_offers_text(offers: list[dict]) -> str:
    lines = []
    for o in offers:
        price_part = f"{o['price']} kr" if o.get("price") else "okänt pris"
        orig_part = ""
        if o.get("original_price") and o["original_price"] != o.get("price"):
            orig_part = f" (ord. {o['original_price']} kr)"
        lines.append(f"- {o['name']}: {price_part}{orig_part}")
    return "\n".join(lines) if lines else "Inga erbjudanden tillgängliga."


def _build_household_text(settings: Optional[dict]) -> str:
    if not settings:
        return (
            "- 2 vuxna + 2 barn (3 år och 6 år)\n"
            "- Kost: vegetariskt, kyckling och fisk är ok (INTE rött kött)\n"
            "- 3-åringen behöver mjuka texturer — notera om något behöver servas separat eller mosas\n"
            "- Barnvänligt: enkla smaker, INTE starkt kryddat, INTE stark ost\n"
            "- 6-åringen äter ungefär som vuxna men föredrar bekanta smaker"
        )
    adults = settings.get("adults", 2)
    kids_count = settings.get("kids_count", 0)
    diet = settings.get("diet", ["vegetarian", "chicken", "fish"])
    allergies = settings.get("allergies", [])
    exclude = settings.get("exclude_items", [])
    budget = settings.get("budget_sek")
    fast = settings.get("fast_days", 2)
    medium = settings.get("medium_days", 2)
    long_ = settings.get("long_days", 1)
    occasion = settings.get("occasion")

    diet_labels = {
        "vegetarian": "vegetariskt", "chicken": "kyckling", "fish": "fisk",
        "pork": "fläsk", "beef": "nötkött", "vegan": "veganskt",
    }
    diet_str = ", ".join(diet_labels.get(d, d) for d in diet) or "blandat"

    lines = [f"- {adults} vuxna" + (f" + {kids_count} barn" if kids_count else "")]
    lines.append(f"- Kost: {diet_str}")
    if allergies:
        lines.append(f"- Allergier/undviks: {', '.join(allergies)}")
    if exclude:
        lines.append(f"- Uteslut dessa ingredienser: {', '.join(exclude)}")
    if budget:
        lines.append(f"- Veckbudget: ca {budget} kr")
    lines.append(f"- Tillagningstid: {fast} snabba dagar (<30 min), {medium} mellantid (30–45 min), {long_} lång dag (>45 min)")
    if settings.get("include_dessert"):
        lines.append("- Inkludera dessert en dag i veckan")
    if settings.get("include_starter"):
        lines.append("- Inkludera förrätt en dag i veckan")
    if settings.get("festive_meals"):
        lines.append("- Inkludera en festligare middag (gäster/firande)")
    if occasion:
        lines.append(f"- SPECIELLT TILLFÄLLE DENNA VECKA: {occasion} — anpassa recepten för detta!")
    return "\n".join(lines)


def _build_prompt(
    offers: list[dict],
    candidate_recipes: Optional[list[dict]] = None,
    recent_names: Optional[list[str]] = None,
    liked_recipes: Optional[list[dict]] = None,
    disliked_names: Optional[list[str]] = None,
    settings: Optional[dict] = None,
) -> str:
    offers_text = _build_offers_text(offers)
    household_text = _build_household_text(settings)
    n_recipes = (settings or {}).get("recipes_per_week", 5)

    history_section = ""
    if recent_names:
        history_section += "\nSENASTE VECKORS MIDDAGAR (undvik dessa):\n"
        for name in recent_names:
            history_section += f"- {name}\n"

    if disliked_names:
        history_section += "\nRÄTTER SOM HUSHÅLLET INTE GILLAT (skippa dessa):\n"
        for name in disliked_names:
            history_section += f"- {name}\n"

    if liked_recipes:
        history_section += "\nRÄTTER SOM HUSHÅLLET ÄLSKAT (kan återanvändas med variation):\n"
        for r in liked_recipes[:5]:
            stars = r.get("rating", "?")
            history_section += f"- {r['name']} (betyg: {stars}/7, lagad {r.get('times_cooked', 1)} gång)\n"

    import db as _db
    candidates_section = ""
    if candidate_recipes:
        candidates_section = f"\nRECEPT FRÅN LOKAL DATABAS — välj {n_recipes} av dessa:\n"
        for i, r in enumerate(candidate_recipes[:100], 1):
            name = r.get("name", "Okänt")
            rating = f"⭐{r['site_rating']}" if r.get("site_rating") else ""
            # Build ingredient list with amounts for LLM context
            raw_ings = r.get("ingredients") or []
            if isinstance(raw_ings, str):
                try:
                    raw_ings = json.loads(raw_ings)
                except Exception:
                    raw_ings = []
            ing_parts = []
            for ing in raw_ings[:8]:
                item = (ing.get("item") if isinstance(ing, dict) else str(ing)) or ""
                qty = (ing.get("quantity") if isinstance(ing, dict) else "") or ""
                if not qty:
                    qty = _db.lookup_standard_amount(item, servings=4)
                ing_parts.append(f"{qty} {item}".strip() if qty else item)
            ing_str = ", ".join(ing_parts[:6])
            url = r.get("url", "")
            translate_url = r.get("translate_url", "")
            translate_note = f" [INTERNATIONELLT translate_url={translate_url}]" if translate_url else ""
            candidates_section += f"{i}. {name} {rating} [{ing_str}] {url}\n"

    return f"""Du är en familjevänlig kokbok-assistent som hjälper en svensk familj att planera veckans mat.

HUSHÅLL:
{household_text}
{history_section}
VECKANS ERBJUDANDEN FRÅN WILLYS:
{offers_text}
{candidates_section}
UPPGIFT:
Välj exakt {n_recipes} recept bland recepten ovan för familjen.
Fördela ungefär: snabba vardagsrätter (max 30 min), familjemiddagar (max 45 min), fredagsmys.

KRAV:
- Välj bland recepten från lokal databas ovan — använd deras exakta namn och source_url
- Minst hälften av recepten ska ha ett erbjudandeprodukt som HUVUDINGREDIENS
- Portioner anpassade för hushållets storlek
- Varje ingrediens MÅSTE ha en realistisk quantity (t.ex. "400 g", "3 st", "2 dl") — lämna ALDRIG quantity tom
- Variera rätterna från föregående veckor
- Använd svenska produktnamn och mått (gram, dl, msk, tsk, st)
- Bevara source_url från katalogen exakt som det står ovan
- Om receptet är internationellt (har translate_url), inkludera translate_url i JSON-svaret
- Inkludera key_ingredients: en lista med 3-6 kärngredienser på svenska (för sök-funktion)

Svara ENBART med giltig JSON. Returnera antingen en JSON-array direkt, eller ett objekt med nyckeln "recipes". Exempel på struktur:
{{"recipes": [
  {{
    "name": "🍝 Pasta med tomatsås",
    "type": "weekday",
    "prep_time": 10,
    "cook_time": 20,
    "servings": 4,
    "toddler_note": "Mosa pastan för den yngsta" eller null,
    "uses_sale_items": ["pasta", "tomater"],
    "source_url": "https://www.tasteline.com/..." eller null,
    "key_ingredients": ["pasta", "tomat", "lök", "vitlök"],
    "ingredients": [
      {{"item": "pasta", "quantity": "400 g", "on_sale": true}},
      {{"item": "tomater", "quantity": "4 st", "on_sale": false}}
    ],
    "instructions": ["Koka vatten...", "Stek..."],
    "barnvanlighetstips": "Låt barnen röra om såsen!"
  }}
]}}

Type-värden: "weekday" (vardagsrätt), "family" (familjemiddag), "fredagsmys"
"""


def _extract_json(text: str) -> Optional[str]:
    text = text.strip()

    # Direct array
    if text.startswith("["):
        return text

    # JSON object with a recipes/recept key (happens when format: "json" is used)
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            for key in ("recipes", "recept", "rätter", "ratter", "data"):
                if key in obj and isinstance(obj[key], list):
                    return json.dumps(obj[key], ensure_ascii=False)
        except Exception:
            pass

    # Array inside code block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        inner = match.group(1).strip()
        if inner.startswith("["):
            return inner
        if inner.startswith("{"):
            try:
                obj = json.loads(inner)
                for key in ("recipes", "recept", "rätter", "ratter", "data"):
                    if key in obj and isinstance(obj[key], list):
                        return json.dumps(obj[key], ensure_ascii=False)
            except Exception:
                pass

    # Array anywhere in text
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        return match.group(0)

    return None


def generate_recipes(
    offers: list[dict],
    candidate_recipes: Optional[list[dict]] = None,
    recent_names: Optional[list[str]] = None,
    liked_recipes: Optional[list[dict]] = None,
    disliked_names: Optional[list[str]] = None,
    settings: Optional[dict] = None,
    model: str = "mistral:7b",
    ollama_url: str = "http://localhost:11434",
) -> list[dict]:
    """
    Genererar 5 recept via lokal Ollama baserat på veckans erbjudanden.

    Args:
        offers: Lista med erbjudanden från willys.fetch_offers()
        candidate_recipes: Valfria riktiga recept från catalog/search.py
        recent_names: Receptnamn från senaste veckorna (undviks)
        liked_recipes: Recept som hushållet gillat (återanvänds med variation)
        disliked_names: Recept som hushållet inte gillat (undviks)
        model: Ollama-modell att använda
        ollama_url: Ollama server URL

    Returns:
        Lista med 5 recept-dicts

    Raises:
        RuntimeError: Om Ollama inte kan generera giltiga recept
    """
    prompt = _build_prompt(
        offers, candidate_recipes, recent_names, liked_recipes, disliked_names, settings
    )

    logger.info(f"Genererar recept via Ollama ({model})...")
    logger.info(f"Antal erbjudanden: {len(offers)}, kandidatrecept: {len(candidate_recipes or [])}")

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Försök {attempt}/{MAX_RETRIES}...")
            resp = requests.post(
                f"{ollama_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
                timeout=600,
            )
            resp.raise_for_status()
            response_text = resp.json().get("response", "")
            logger.info(f"Svar från Ollama (första 300 tecken): {response_text[:300]}")

            json_str = _extract_json(response_text)
            if not json_str:
                raise ValueError("Kunde inte hitta JSON-array i Ollama-svar")

            recipes = json.loads(json_str)

            if not isinstance(recipes, list) or len(recipes) == 0:
                raise ValueError(f"Ogiltig receptlista: {type(recipes).__name__}")

            required_fields = {"name", "type", "ingredients", "instructions"}
            for i, recipe in enumerate(recipes):
                missing = required_fields - set(recipe.keys())
                if missing:
                    raise ValueError(f"Recept {i+1} saknar fält: {missing}")

            logger.info(f"Lyckades generera {len(recipes)} recept")
            return recipes

        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            logger.warning(f"Försök {attempt} misslyckades (ogiltigt svar): {e}")

        except requests.RequestException as e:
            last_error = e
            logger.error(f"Ollama API-fel: {e}")

    raise RuntimeError(
        f"Kunde inte generera recept efter {MAX_RETRIES} försök. Senaste fel: {last_error}"
    )


# Chicken count per week, repeating: week1→1, week2→2, week3→2, week4→1, ...
CHICKEN_PATTERN = [1, 2, 2, 1]

LAX_WORDS      = {"lax", "laxfilé", "gravlax", "rökt lax", "laxbiff", "salmon", "laxsida"}
CHICKEN_WORDS  = {"kyckling", "kycklingfilé", "kycklinglår", "kycklingbröst", "kycklingben",
                  "kycklingvinge", "kycklingfärs", "hel kyckling"}
FISH_WORDS     = {"fisk", "torsk", "abborre", "gös", "sej", "makrill", "tonfisk", "räkor",
                  "räka", "hummer", "krabba", "bläckfisk", "pilgrimsmussla", "mussla",
                  "sill", "strömming", "hälleflundra", "tilapia", "pangasius", "kolja",
                  "sardiner", "ansjovis", "tångräkor", "fiskfilé", "fisksoppa", "fiskpinnar",
                  "fiskbullar", "fiskgratäng", "fiskpaj"}


def _recipe_text(row: dict) -> str:
    return " ".join([
        row.get("name", ""),
        " ".join(row.get("key_ingredients") or []),
        " ".join(str(i) for i in (row.get("ingredients") or [])),
    ]).lower()


def _is_lax(row: dict) -> bool:
    return any(w in _recipe_text(row) for w in LAX_WORDS)


def _is_chicken(row: dict) -> bool:
    return any(w in _recipe_text(row) for w in CHICKEN_WORDS)


def _is_fish(row: dict) -> bool:
    """True if the recipe contains fish/seafood (but not just lax — use _is_lax for that)."""
    text = _recipe_text(row)
    return any(w in text for w in FISH_WORDS | LAX_WORDS)


def _catalog_to_recipe(row: dict, recipe_type: str, offer_terms: set[str]) -> dict:
    """Convert a recipe_catalog row to the recipe dict format used by the rest of the app."""
    import db as _db
    ingredients_raw = row.get("ingredients") or []
    ingredients = []
    for ing in ingredients_raw:
        if isinstance(ing, dict):
            # Already has structure — fill in quantity if empty
            item_name = ing.get("item", "")
            qty = ing.get("quantity") or ""
            if not qty and item_name:
                qty = _db.lookup_standard_amount(item_name, servings=4)
            ingredients.append({**ing, "quantity": qty})
        else:
            ing_str = str(ing)
            on_sale = any(t in ing_str.lower() for t in offer_terms)
            qty = _db.lookup_standard_amount(ing_str, servings=4)
            ingredients.append({"item": ing_str, "quantity": qty, "on_sale": on_sale})

    all_ing_text = " ".join(i["item"].lower() for i in ingredients)
    uses_sale_items = [t for t in offer_terms if t in all_ing_text]

    total_min = row.get("total_time_min")
    if total_min:
        prep_time = max(5, total_min // 3)
        cook_time = total_min - prep_time
    else:
        prep_time = None
        cook_time = None

    return {
        "name": row["name"],
        "type": recipe_type,
        "prep_time": prep_time,
        "cook_time": cook_time,
        "servings": 4,
        "toddler_note": None,
        "uses_sale_items": uses_sale_items,
        "source_url": row.get("url"),
        "key_ingredients": row.get("key_ingredients") or [],
        "ingredients": ingredients,
        "instructions": row.get("instructions") or [],
        "barnvanlighetstips": None,
    }


def select_recipes(
    catalog_candidates: list[dict],
    offers: list[dict],
    week_num: int = 1,
    disliked_names: Optional[list[str]] = None,
    settings: Optional[dict] = None,
    exclude_names: Optional[list[str]] = None,
    count: Optional[int] = None,
    allowed_diets: Optional[list[str]] = None,
) -> list[dict]:
    """
    Picks 5 recipes directly from catalog_candidates without calling Ollama.

    Diet balance per week (based on CHICKEN_PATTERN):
      - 1 or 2 chicken recipes (alternating weekly)
      - exactly 1 fish recipe, never salmon
      - remainder vegetarian (2 or 3)

    Chicken and fish counts now respect the provided allowed_diets.

    Types assigned by cook time: fastest non-fish → weekday (×2),
    next → family (×2), fish → fredagsmys.
    """
    n_recipes = count or (settings or {}).get("recipes_per_week", 5)
    ad = {d.lower() for d in (allowed_diets or [])}
    unrestricted = not allowed_diets  # None or empty = no restriction
    has_chicken  = unrestricted or 'chicken' in ad or 'meat' in ad
    has_fish     = unrestricted or 'fisk' in ad or 'fish' in ad
    fish_count    = 1 if has_fish else 0
    fish_count    = min(fish_count, n_recipes)
    chicken_count = CHICKEN_PATTERN[(week_num - 1) % len(CHICKEN_PATTERN)] if has_chicken else 0
    chicken_count = min(chicken_count, n_recipes - fish_count)
    veg_count     = n_recipes - chicken_count - fish_count
    logger.info(f"Vecka {week_num}: {chicken_count} kyckling, {fish_count} fisk (ej lax), {veg_count} vegetariskt")

    offer_terms: set[str] = set()
    for o in (offers if isinstance(offers, list) else []):
        name = o.get("name", "") if isinstance(o, dict) else ""
        for word in name.lower().split():
            if len(word) >= 4:
                offer_terms.add(word)

    all_excluded = {n.lower() for n in (disliked_names or []) + (exclude_names or [])}
    candidates = [c for c in catalog_candidates if c["name"].lower() not in all_excluded]

    chicken_pool = [c for c in candidates if _is_chicken(c)]
    fish_pool    = [c for c in candidates if _is_fish(c) and not _is_chicken(c) and not _is_lax(c)]
    veg_pool     = [c for c in candidates if not _is_chicken(c) and not _is_fish(c)]

    picked_rows: list[tuple[dict, bool]] = []  # (row, is_fish)
    used: set[str] = set()
    committed_ingredients: set[str] = set()  # ingredient tokens already in the weekly plan

    def _ingredient_tokens(row: dict) -> set[str]:
        """Return a set of lower-cased ingredient words (≥4 chars) from a catalog row."""
        tokens: set[str] = set()
        ing_list = row.get("ingredients") or []
        if isinstance(ing_list, list):
            for ing in ing_list:
                item_text = (ing.get("item") if isinstance(ing, dict) else str(ing)) or ""
                tokens.update(w for w in item_text.lower().split() if len(w) >= 4)
        ki = row.get("key_ingredients") or []
        if isinstance(ki, list):
            for k in ki:
                tokens.update(w for w in str(k).lower().split() if len(w) >= 4)
        return tokens

    def _overlap_score(row: dict) -> int:
        """Number of ingredient tokens this recipe shares with already-committed recipes."""
        return len(_ingredient_tokens(row) & committed_ingredients)

    def _take(pool: list[dict], n: int, is_fish: bool = False) -> int:
        nonlocal committed_ingredients
        taken = 0
        remaining_pool = [c for c in pool if (c.get("url") or c["name"]) not in used]
        while taken < n and remaining_pool:
            best = max(
                range(len(remaining_pool)),
                key=lambda i: _overlap_score(remaining_pool[i]) * 2 - i * 0.1
            )
            c = remaining_pool.pop(best)
            picked_rows.append((c, is_fish))
            used.add(c.get("url") or c["name"])
            committed_ingredients |= _ingredient_tokens(c)
            taken += 1
        return taken

    # Chicken
    got = _take(chicken_pool, chicken_count)
    if got < chicken_count and chicken_count > 0:
        logger.warning(f"Bara {got}/{chicken_count} kycklingrecept tillgängliga, fyller från vegetariskt")
        _take(veg_pool, chicken_count - got)

    # Fish (fredagsmys) — always exactly 1, never lax
    if not _take(fish_pool, fish_count, is_fish=True):
        logger.warning("Inga fiskrecept utan lax i katalogen — hoppar över fiskrätt denna vecka")

    # Vegetarian — fill remaining slots
    remaining = n_recipes - len(picked_rows)
    got = _take(veg_pool, remaining)
    if got < remaining:
        _take([c for c in candidates if (c.get("url") or c["name"]) not in used], remaining - got)

    if len(picked_rows) < n_recipes and count is None:
        raise RuntimeError(
            f"Inte tillräckligt med kandidater i katalogen ({len(picked_rows)}/{n_recipes}). "
            "Kör scrape.py för att fylla katalogen."
        )

    non_fish = [(row, False) for row, is_fish in picked_rows if not is_fish]
    fish     = [(row, True)  for row, is_fish in picked_rows if is_fish]
    non_fish.sort(key=lambda x: x[0].get("total_time_min") or 45)

    result: list[dict] = []
    for i, (row, _) in enumerate(non_fish):
        rtype = "weekday" if i < 2 else "family"
        result.append(_catalog_to_recipe(row, rtype, offer_terms))
    for row, _ in fish:
        r = _catalog_to_recipe(row, "fredagsmys", offer_terms)
        r["fish_recipe"] = True
        result.append(r)

    logger.info(f"Valde {len(result)} recept direkt från databasen (ingen Ollama-generering)")
    for r in result:
        flag = " 🐟" if r.get("fish_recipe") else ""
        logger.info(f"  • {r['name']} ({r['type']}, {r.get('prep_time','?')}+{r.get('cook_time','?')} min){flag}")

    return result[:n_recipes]

def format_recipe_text(recipe: dict) -> str:
    lines = []
    type_labels = {
        "weekday": "Vardagsrätt",
        "family": "Familjemiddag",
        "fredagsmys": "Fredagsmys",
    }
    lines.append(recipe.get("name", "Okänt recept"))
    lines.append(f"Typ: {type_labels.get(recipe.get('type', ''), recipe.get('type', ''))}")
    lines.append(
        f"Tid: {recipe.get('prep_time','?')} min förberedelse + "
        f"{recipe.get('cook_time','?')} min tillagning | {recipe.get('servings', 4)} portioner"
    )
    if recipe.get("toddler_note"):
        lines.append(f"👶 Småbarnsanpassning: {recipe['toddler_note']}")
    if recipe.get("uses_sale_items"):
        lines.append(f"💰 Veckans erbjudanden: {', '.join(recipe['uses_sale_items'])}")
    lines.append("\nIngredienser:")
    for ing in recipe.get("ingredients", []):
        sale = " 💰" if ing.get("on_sale") else ""
        lines.append(f"  • {ing.get('quantity', '')} {ing.get('item', '')}{sale}")
    lines.append("\nGörså:")
    for i, step in enumerate(recipe.get("instructions", []), 1):
        lines.append(f"  {i}. {step}")
    if recipe.get("barnvanlighetstips"):
        lines.append(f"\n💡 Barnvänlighetstips: {recipe['barnvanlighetstips']}")
    return "\n".join(lines)
