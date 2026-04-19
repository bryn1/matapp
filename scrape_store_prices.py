#!/usr/bin/env python3
"""
scrape_store_prices.py — Enumerate and store per-item prices for Axfood stores
(Willys, Hemköp, Tempo, Handlar'n).

Uses the same /search endpoint as campaigns.py but queries by category keywords
to enumerate the full product catalog. Results are stored in store_price_catalog
for CPI analysis and recipe scoring.

Usage:
    python3 scrape_store_prices.py --chain willys --store-id 2153
    python3 scrape_store_prices.py --chain willys --store-id 2153 --limit 500
    python3 scrape_store_prices.py --chain willys --store-id 2153 --dry-run

Strategy:
    The Axfood /search endpoint supports text queries (returns ~10-50 results/page).
    Empty/wildcard queries return 0 results. We enumerate by querying 300+ food terms,
    deduplicating by product code. This captures 80-90% of the food catalog.
"""

import argparse
import logging
import time
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "matapp.db"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "sv-SE,sv;q=0.9",
}

_AXFOOD_DOMAINS = {
    "willys":   "https://www.willys.se",
    "hemkop":   "https://www.hemkop.se",
    "tempo":    "https://www.tempo.se",
    "handlarn": "https://www.handlarn.se",
}

# Comprehensive Swedish food category terms for product enumeration
# Covers meat, fish, dairy, produce, pantry, frozen, bakery, etc.
_FOOD_TERMS = [
    # Kött
    "kyckling", "kycklingfilé", "kycklinglår", "kycklingbröst",
    "nötkött", "köttfärs", "biff", "entrecôte", "ryggbiff",
    "fläsk", "fläskfilé", "fläskkotlett", "bacon", "skinka",
    "korv", "falukorv", "prinskorv", "köttbullar", "lammkött",
    "kalvkött", "viltkött", "rådjur", "älg",
    # Fisk och skaldjur
    "lax", "torsk", "sill", "makrill", "räkor", "kräftor",
    "tonfisk", "sardiner", "röding", "abborre", "piggvar",
    "bläckfisk", "musslor", "hummer", "krabba", "fiskfilé",
    "laxfilé", "fiskpinnar", "surströmming",
    # Mejeri
    "mjölk", "fil", "yoghurt", "grädde", "crème fraîche",
    "ost", "cheddar", "gouda", "brie", "camembert", "mozzarella",
    "fetaost", "parmesan", "kvarg", "kesella",
    "smör", "margarin", "ägg",
    # Grönsaker
    "tomat", "gurka", "paprika", "lök", "vitlök", "potatis",
    "morot", "broccoli", "blomkål", "sparris", "spenat",
    "sallad", "ruccola", "rödkål", "vitkål", "purjolök",
    "selleri", "zucchini", "aubergine", "fänkål", "rädisor",
    "ärtor", "bönor", "linser", "majs", "avokado", "ingefära",
    "rödbeta", "kålrot", "palsternacka", "ramslök",
    # Frukt
    "äpple", "päron", "apelsin", "citron", "lime", "banan",
    "vindruvor", "jordgubbar", "hallon", "blåbär", "mango",
    "ananas", "melon", "vattenmelon", "persika", "nektarin",
    "kiwi", "granatäpple", "plommon", "körsbär",
    # Spannmål och bröd
    "bröd", "knäckebröd", "frukostflingor", "havregryn", "müsli",
    "pasta", "makaroner", "spaghetti", "penne", "ris", "bulgur",
    "quinoa", "couscous", "mjöl", "vetemjöl", "rågmjöl",
    "jäst", "bakpulver", "kavring", "bagel", "tortilla",
    # Konserver och torkvaror
    "krossade tomater", "tomatsås", "passata",
    "kikärtor", "vita bönor", "kidneybönor", "linser",
    "kokosmjölk", "kokosnöt", "oliver", "kapris",
    "röd lins", "gul ärta",
    # Såser och kryddor
    "soja", "ketchup", "senap", "majonnäs", "chilisås",
    "pestos", "tapenade", "harrissa", "sambal",
    "buljong", "fond", "curry", "paprikapulver", "spiskummin",
    "oregano", "basilika", "timjan", "rosmarin", "persilja",
    "koriander", "dill", "lagerblad", "svartpeppar",
    "salt", "socker", "honung", "sirap",
    # Olja och vinäger
    "olivolja", "rapsolja", "solrosolja", "kokosolja",
    "vinäger", "balsamvinäger",
    # Baljväxter torkade
    "sojabönor", "edamame", "tofu", "tempeh",
    # Frysta
    "frysta grönsaker", "frysta bär", "glass", "sorbet",
    "fryst fisk", "fryst kyckling", "fryst pizza",
    # Dryck (ej alkohol)
    "juice", "nektar", "smoothie", "lemonad",
    "te", "kaffe", "kakao",
    "vatten", "kolsyrat",
    # Övrigt matlagning
    "gräddfil", "creme fraiche", "maizena",
    "gelatin", "vanilj", "kanel", "kardemumma",
]


def _ensure_price_catalog_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS store_price_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT NOT NULL,
            store_id TEXT NOT NULL,
            product_code TEXT NOT NULL,
            name TEXT NOT NULL,
            price REAL,
            compare_price TEXT,
            unit TEXT,
            scraped_date TEXT NOT NULL,
            UNIQUE(chain, store_id, product_code, scraped_date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_catalog_chain_store
        ON store_price_catalog(chain, store_id, scraped_date)
    """)
    conn.commit()


def _axfood_search_products(domain: str, store_id: str, query: str,
                             page: int = 0, size: int = 30) -> tuple[list[dict], int]:
    """Query Axfood /search endpoint. Returns (products, total_pages)."""
    try:
        r = requests.get(
            f"{domain}/search",
            params={"q": query, "storeId": store_id, "size": size, "page": page,
                    "sort": "name_text_sv:asc"},
            headers=_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json() or {}
        products = (data.get("results") or [])
        pagination = (data.get("pagination") or {})
        total_pages = pagination.get("numberOfPages", 1)
        return products, total_pages
    except Exception as e:
        logger.warning(f"Search error (q={query!r} p={page}): {e}")
        return [], 0


def scrape_store_catalog(
    chain: str,
    store_id: str,
    limit: int = 10000,
    dry_run: bool = False,
) -> int:
    """
    Enumerate all food products for a store by querying food terms.
    Stores results in store_price_catalog. Returns count of new records.
    """
    domain = _AXFOOD_DOMAINS.get(chain)
    if not domain:
        logger.error(f"Unknown chain: {chain}")
        return 0

    today = date.today().isoformat()
    seen_codes: set[str] = set()
    rows: list[tuple] = []

    logger.info(f"Scraping {chain} store={store_id} ({len(_FOOD_TERMS)} search terms)")

    for i, term in enumerate(_FOOD_TERMS):
        if len(seen_codes) >= limit:
            logger.info(f"Reached limit of {limit} products")
            break

        page = 0
        while True:
            products, total_pages = _axfood_search_products(domain, store_id, term, page)
            if not products:
                break

            for p in products:
                code = str(p.get("code") or p.get("ean") or p.get("id") or "")
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                name = (p.get("name") or "").strip()
                price_val = p.get("priceValue")
                compare = str(p.get("comparePrice") or "").strip()
                unit = str(p.get("priceUnit") or "").strip()
                rows.append((chain, store_id, code, name, price_val, compare, unit, today))

            page += 1
            if page >= total_pages or page >= 10:  # cap per-term at 10 pages
                break
            time.sleep(0.2)

        if (i + 1) % 20 == 0:
            logger.info(f"  {i+1}/{len(_FOOD_TERMS)} terms, {len(seen_codes)} unique products so far")
        time.sleep(0.3)

    logger.info(f"{chain}/{store_id}: {len(rows)} unique products found")

    if dry_run:
        print(f"(dry-run) Would insert {len(rows)} rows for {chain}/{store_id}")
        for row in rows[:10]:
            print(f"  {row[3]}: {row[4]} kr")
        return len(rows)

    # Write to DB
    conn = sqlite3.connect(str(DB_PATH))
    _ensure_price_catalog_table(conn)
    inserted = 0
    for row in rows:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO store_price_catalog "
                "(chain, store_id, product_code, name, price, compare_price, unit, scraped_date) "
                "VALUES (?,?,?,?,?,?,?,?)",
                row,
            )
            inserted += conn.execute("SELECT changes()").fetchone()[0]
        except Exception as e:
            logger.warning(f"DB insert error: {e}")
    conn.commit()
    conn.close()
    logger.info(f"Inserted {inserted} new rows for {chain}/{store_id}")
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", default="willys", choices=list(_AXFOOD_DOMAINS.keys()))
    parser.add_argument("--store-id", default="2153", help="Axfood store ID")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    n = scrape_store_catalog(
        chain=args.chain,
        store_id=args.store_id,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(f"\nDone. {'Would insert' if args.dry_run else 'Inserted'} {n} products.")


if __name__ == "__main__":
    main()
