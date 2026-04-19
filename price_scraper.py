#!/usr/bin/env python3
"""
price_scraper.py — Willys price history scraper.

Uses the Willys open JSON search API (no auth needed):
  GET https://www.willys.se/search?q=PRODUCT

Only runs during 04:00–08:45 UTC (respects Willys crawl window).
10 second delay between requests.

Usage:
    python3 price_scraper.py --test kaffe
    python3 price_scraper.py --watchlist-only
"""

import argparse
import datetime
import logging
import re
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "price_scraper.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

WILLYS_SEARCH_URL = "https://www.willys.se/search"
CRAWL_START_UTC = datetime.time(4, 0)
CRAWL_END_UTC = datetime.time(8, 45)
REQUEST_DELAY_S = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; matapp/1.0; personal meal planner)",
    "Accept": "application/json",
}


def _in_crawl_window() -> bool:
    """Return True if current UTC time is within the allowed crawl window."""
    now = datetime.datetime.utcnow().time()
    return CRAWL_START_UTC <= now <= CRAWL_END_UTC


def _normalize_product_name(name: str) -> str:
    """Lowercase, strip leading quantities/digits, remove common brand noise."""
    s = name.lower().strip()
    s = re.sub(r'^\d+[\s\.,xX]*(?:g|kg|ml|cl|dl|l|st|förp|paket)?\s*', '', s)
    return s.strip()


def scrape_product_price(query: str) -> list[dict]:
    """
    Search Willys for a product, return top 3 results with prices.
    Silently returns [] if outside crawl window.
    """
    if not _in_crawl_window():
        logger.debug(f"Outside crawl window, skipping: {query}")
        return []

    try:
        resp = requests.get(
            WILLYS_SEARCH_URL,
            params={"q": query, "page": 1},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Willys search failed for '{query}': {e}")
        return []

    results_raw = data.get("results", [])
    if not results_raw and "products" in data:
        results_raw = data["products"]

    out = []
    for item in results_raw[:3]:
        name = item.get("name") or item.get("displayName") or ""
        price = item.get("priceValue") or item.get("price")
        if isinstance(price, str):
            try:
                price = float(price.replace(",", ".").replace(" ", ""))
            except ValueError:
                price = None

        compare_price = item.get("comparePrice")
        if isinstance(compare_price, str):
            try:
                compare_price = float(compare_price.replace(",", ".").replace(" ", ""))
            except ValueError:
                compare_price = None

        out.append({
            "willys_code": str(item.get("code") or item.get("id") or ""),
            "product_name": name,
            "normalized": _normalize_product_name(name or query),
            "price": price,
            "compare_price": compare_price,
            "unit": item.get("comparePriceUnit") or item.get("unit"),
        })

    logger.info(f"Willys '{query}': {len(out)} results")
    return out


def scrape_offer_prices(offer_names: list[str]) -> list[dict]:
    """Scrape prices for this week's Willys offers."""
    all_prices = []
    for name in offer_names:
        prices = scrape_product_price(name)
        all_prices.extend(prices)
        if prices:
            time.sleep(REQUEST_DELAY_S)
    return all_prices


def scrape_watchlist_prices(watchlist: list[dict]) -> list[dict]:
    """Scrape prices for user's watchlist items."""
    all_prices = []
    for item in watchlist:
        query = item.get("query", "")
        if not query:
            continue
        prices = scrape_product_price(query)
        all_prices.extend(prices)
        if prices:
            time.sleep(REQUEST_DELAY_S)
    return all_prices


def main() -> int:
    parser = argparse.ArgumentParser(description="Matapp price scraper")
    parser.add_argument("--test", metavar="QUERY", help="Test search for a single query")
    parser.add_argument("--watchlist-only", action="store_true", help="Only scrape watchlist items")
    args = parser.parse_args()

    import db
    db_path = BASE_DIR / "data" / "matapp.db"
    db.init_db(db_path)

    if args.test:
        logger.info(f"Testing Willys search for: {args.test}")
        # Temporarily allow outside window for --test
        results = scrape_product_price.__wrapped__(args.test) if hasattr(scrape_product_price, '__wrapped__') else _search_forced(args.test)
        if results:
            for r in results:
                print(f"  {r['product_name']}: {r['price']} kr ({r['compare_price']} {r['unit']})")
        else:
            print("No results (may be outside crawl window — use --test to force)")
        return 0

    if args.watchlist_only:
        logger.info("Scraping watchlist-only prices")
        watchlist = db.get_watchlist(db_path)
        if not watchlist:
            logger.info("Watchlist is empty")
            return 0
        prices = scrape_watchlist_prices(watchlist)
        db.save_prices(prices, db_path)
        logger.info(f"Saved {len(prices)} watchlist price records")
        return 0

    return 0


def _search_forced(query: str) -> list[dict]:
    """Like scrape_product_price but bypasses crawl window check (for --test)."""
    try:
        resp = requests.get(
            WILLYS_SEARCH_URL,
            params={"q": query, "page": 1},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Willys search failed for '{query}': {e}")
        return []

    results_raw = data.get("results", [])
    if not results_raw and "products" in data:
        results_raw = data["products"]

    out = []
    for item in results_raw[:3]:
        name = item.get("name") or item.get("displayName") or ""
        price = item.get("priceValue") or item.get("price")
        if isinstance(price, str):
            try:
                price = float(price.replace(",", ".").replace(" ", ""))
            except ValueError:
                price = None

        compare_price = item.get("comparePrice")
        if isinstance(compare_price, str):
            try:
                compare_price = float(compare_price.replace(",", ".").replace(" ", ""))
            except ValueError:
                compare_price = None

        out.append({
            "willys_code": str(item.get("code") or item.get("id") or ""),
            "product_name": name,
            "normalized": _normalize_product_name(name or query),
            "price": price,
            "compare_price": compare_price,
            "unit": item.get("comparePriceUnit") or item.get("unit"),
        })

    return out


if __name__ == "__main__":
    # For --test, always override the window check
    if "--test" in sys.argv:
        idx = sys.argv.index("--test")
        query = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if query:
            import db as _db
            _db.init_db(BASE_DIR / "data" / "matapp.db")
            logger.info(f"Testing Willys search for: {query}")
            results = _search_forced(query)
            if results:
                for r in results:
                    print(f"  {r['product_name']}: {r['price']} kr ({r['compare_price']} {r['unit']})")
            else:
                print("No results returned from Willys")
            sys.exit(0)

    sys.exit(main())
