#!/usr/bin/env python3
"""
run.py — Huvudskript för Matapp.

Kör varje måndag (via cron) för att:
1. Hämta veckans erbjudanden från Willys
2. Söka i recipe_catalog efter matchande recept
3. Fallback: sök receptinspiration från svenska matkajter (search.py)
4. Generera recept via lokal Ollama (recipes.py)
5. Bygga inköpslista och spara till SQLite (db.py)

Argument:
    --force         Kör om även om veckan redan finns i databasen
    --no-search     Hoppa över web-scraping (använd bara catalog)
"""

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).parent


def load_config() -> dict:
    with open(BASE_DIR / "config.json", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Matapp — veckans recept & inköpslista")
    parser.add_argument("--force", action="store_true", help="Kör om trots att veckan redan finns")
    parser.add_argument("--no-search", action="store_true", help="Hoppa över web-scraping")
    args = parser.parse_args()

    today = datetime.date.today()
    iso = today.isocalendar()
    week_num = iso[1]
    year = iso[0]

    logger.info(f"=== Matapp — Vecka {week_num}/{year} ===")

    # ── Konfiguration ──────────────────────────────────────────────────────────
    try:
        config = load_config()
        logger.info(f"Konfiguration laddad. Butik: {config.get('willys_store', 'okänd')}")
    except Exception as e:
        logger.error(f"Kunde inte ladda config.json: {e}")
        return 1

    ollama_model = config.get("ollama_model", "mistral:7b")
    ollama_url = config.get("ollama_url", "http://localhost:11434")
    max_recipe_minutes = config.get("max_recipe_minutes", None)

    # ── Databas ────────────────────────────────────────────────────────────────
    import db
    db_path = BASE_DIR / config.get("db_path", "data/matapp.db")
    db.init_db(db_path)
    db.init_staples(config.get("staples", []), db_path)

    # ── Kontrollera om veckan redan finns ──────────────────────────────────────
    existing = db.get_recipes_for_week(week_num, year, db_path)
    if existing and not args.force:
        logger.info(f"Vecka {week_num}/{year} finns redan ({len(existing)} recept). Kör med --force för att köra om.")
        return 0

    # ── Hämta Willys-erbjudanden ───────────────────────────────────────────────
    try:
        from willys import fetch_offers
        offers = fetch_offers(store_name=config.get("willys_store"))
        logger.info(f"Hämtade {len(offers)} erbjudanden från Willys")
    except Exception as e:
        logger.error(f"Fel vid hämtning av Willys-erbjudanden: {e}")
        offers = []

    # ── Prisskrapning (tyst om utanför tidsfönster eller fel) ─────────────────
    try:
        from price_scraper import scrape_offer_prices, scrape_watchlist_prices
        offer_prices = scrape_offer_prices([o["name"] for o in offers])
        db.save_prices(offer_prices, db_path)
        watchlist = db.get_watchlist(db_path)
        if watchlist:
            wl_prices = scrape_watchlist_prices(watchlist)
            db.save_prices(wl_prices, db_path)
        cheap = db.get_cheap_offers([o["name"] for o in offers], db_path)
        for o in offers:
            o["historically_cheap"] = o["name"].lower() in cheap
    except Exception as e:
        logger.warning(f"Prisskrapning hoppades över: {e}")

    # ── Läs recepthistorik från databasen ─────────────────────────────────────
    recent_names = db.get_recent_recipe_names(weeks_back=8, db_path=db_path)
    liked_recipes = db.get_liked_recipes(db_path=db_path)
    disliked_names = db.get_disliked_recipes(db_path=db_path)

    if recent_names:
        logger.info(f"Undviker {len(recent_names)} recept från senaste 8 veckorna")

    # ── Sök i recipe_catalog ───────────────────────────────────────────────────
    # Extract individual meaningful words from all offer names.
    # The new Willys API gives full names like "Kycklingfilé Sverige 900g" —
    # we need individual words like "kycklingfilé" to match recipe ingredients.
    _SKIP_WORDS = {
        "och", "med", "av", "i", "till", "på", "för", "från", "el", "eller",
        "per", "st", "kg", "g", "ml", "cl", "dl", "l", "pack", "förp",
        "fryst", "färsk", "rökt", "skivad", "skivat", "skivade",
        "sverige", "irland", "klass", "ekologisk", "ekologiskt",
        "2-pack", "4-pack", "6-pack", "1-pack", "3-pack",
    }
    import re as _re
    _seen: set[str] = set()
    offer_terms: list[str] = []
    for o in offers:
        for word in _re.split(r"[\s\-/,]+", o["name"].lower()):
            word = word.strip(".")
            if len(word) >= 4 and word not in _SKIP_WORDS and not word[0].isdigit():
                if word not in _seen:
                    _seen.add(word)
                    offer_terms.append(word)
    logger.info(f"Extraherade {len(offer_terms)} söktermer från erbjudanden")
    catalog_candidates = db.get_catalog_candidates(
        offer_terms=offer_terms,
        exclude_names=recent_names,
        limit=50,
        max_time_min=max_recipe_minutes,
        allowed_diets=config.get("diet"),
        force_diets=["fisk"],   # always include fish even with no offer match
        db_path=db_path,
    )
    logger.info(f"Hittade {len(catalog_candidates)} kandidater i recipe_catalog")

    # ── Välj veckans recept direkt från databasen ──────────────────────────────
    try:
        from recipes import select_recipes
        recipes = select_recipes(
            catalog_candidates=catalog_candidates,
            offers=offers,
            week_num=week_num,
            disliked_names=disliked_names,
            allowed_diets=config.get("diet"),
        )
    except Exception as e:
        logger.error(f"Fel vid receptgenerering: {e}")
        return 1

    # ── Bygg inköpslista ───────────────────────────────────────────────────────
    try:
        from shopping import build_shopping_list, _guess_category
        shopping_list = build_shopping_list(recipes, offers, pantry_items=config.get("pantry_items", []))
        total_items = sum(len(cat["items"]) for cat in shopping_list)
        logger.info(f"Inköpslista byggd: {total_items} varor")

        # Append habitual items (learned + staples) — non-fatal on failure
        try:
            habitual = db.get_due_habitual_items(db_path=db_path)
            existing = {it["item"].strip().lower() for cat in shopping_list for it in cat["items"]}
            n_added = n_staples = n_habit = 0
            for h in habitual:
                if h["item"].strip().lower() in existing:
                    continue
                category = _guess_category(h["item"])
                entry = {
                    "item": h["item"],
                    "quantity": "",
                    "on_sale": False,
                    "in_pantry": False,
                    "source": h["source"],
                }
                bucket = next((c for c in shopping_list if c["category"] == category), None)
                if bucket is None:
                    shopping_list.append({"category": category, "items": [entry]})
                else:
                    bucket["items"].append(entry)
                n_added += 1
                if h["source"] == "staples":
                    n_staples += 1
                else:
                    n_habit += 1
            logger.info(
                f"Lagt till {n_added} vanevaror "
                f"({n_staples} fasta, {n_habit} inlärda)"
            )
        except Exception as e:
            logger.warning(f"Kunde inte lägga till vanevaror: {e}")

    except Exception as e:
        logger.error(f"Fel vid byggande av inköpslista: {e}")
        return 1

    # ── Spara till databasen ───────────────────────────────────────────────────
    try:
        db.save_recipes(recipes, week_num, year, db_path)
        db.save_shopping_list(shopping_list, week_num, year, db_path)
        logger.info("Sparat till databasen")
    except Exception as e:
        logger.error(f"Fel vid sparande till databas: {e}")
        return 1

    # ── Markera använda catalog-recept ─────────────────────────────────────────
    recipe_names = {r.get("name", "").lower() for r in recipes}
    for cand in catalog_candidates:
        if cand.get("name", "").lower() in recipe_names and cand.get("url"):
            db.mark_catalog_used(cand["url"], db_path)

    logger.info(f"=== Klar! Vecka {week_num} är planerad. Besök http://localhost:{config.get('serve_port', 8765)} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
