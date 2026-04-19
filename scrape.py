#!/usr/bin/env python3
"""
scrape.py — Bulk scraper that builds the personal recipe catalog.

Searches Swedish recipe sites for vegetarian, chicken, and fish recipes
with site rating >= 3.5, then fetches full recipe detail and saves to
the recipe_catalog table.

Usage:
    python3 scrape.py                    # Bulk seed (all sites, all categories)
    python3 scrape.py --category fisk    # Only fish recipes
    python3 scrape.py --limit 50         # Cap results (default: no limit)
    python3 scrape.py --dry-run          # Print without saving

Cron (monthly, 1st Sunday 03:00):
    0 3 1 * * cd /home/lektove/ai-sandbox/matapp && python3 scrape.py >> logs/scrape.log 2>&1
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9",
}
TIMEOUT = 10
MIN_RATING = 3.5

DIET_QUERIES = {
    "vegetarian": ["vegetarisk", "vegetariskt"],
    "chicken": ["kycklingrecept", "kyckling"],
    "fish": ["fiskrecept", "fisk lax torsk"],
}

SITES = [
    "tasteline.com",
    "ica.se",
    "arla.se",
    "receptfavoriter.se",
    "koket.se",
]


def _get(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.debug(f"Fetch failed {url}: {e}")
        return None


def _parse_rating(text: str) -> Optional[float]:
    import re
    m = re.search(r"(\d+[.,]\d+)", text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    return None


def _fetch_detail(url: str, source: str) -> Optional[dict]:
    """Fetch full recipe page and extract ingredients + instructions."""
    soup = _get(url)
    if not soup:
        return None
    try:
        name_el = soup.select_one("h1")
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            return None

        site_rating = None
        for sel in [".rating", ".stars", "[class*='rating']", "[itemprop='ratingValue']"]:
            el = soup.select_one(sel)
            if el:
                raw = el.get_text(strip=True) or el.get("content", "")
                site_rating = _parse_rating(raw)
                if site_rating:
                    break

        ingredients = []
        for sel in ["[itemprop='recipeIngredient']", ".ingredient", ".ingredients li",
                    ".recipe-ingredients li", "ul.ingredients li"]:
            els = soup.select(sel)
            if els:
                ingredients = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
                break

        instructions = []
        for sel in ["[itemprop='recipeInstructions'] li", ".instructions li",
                    ".recipe-instructions li", ".steps li", "ol.instructions li"]:
            els = soup.select(sel)
            if els:
                instructions = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
                break

        return {
            "name": name,
            "url": url,
            "source": source,
            "site_rating": site_rating,
            "ingredients": ingredients,
            "instructions": instructions,
        }
    except Exception as e:
        logger.debug(f"detail parse failed {url}: {e}")
        return None


def _search_site(site: str, query: str) -> list[dict]:
    """Search a single site and return (name, url, site_rating) candidates."""
    results = []

    if site == "tasteline.com":
        url = f"https://www.tasteline.com/recept/?q={quote_plus(query)}&sort=rating"
        soup = _get(url)
        if not soup:
            return []
        for card in soup.select(".recipe-card, article.recipe, .search-result-item")[:6]:
            try:
                name_el = card.select_one("h2, h3, .recipe-title, .title")
                link_el = card.select_one("a[href]")
                rating_el = card.select_one(".rating, .stars, [class*='rating']")
                if not name_el or not link_el:
                    continue
                name = name_el.get_text(strip=True)
                href = link_el.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.tasteline.com" + href
                rating = _parse_rating(rating_el.get_text(strip=True)) if rating_el else None
                results.append({"name": name, "url": href, "site_rating": rating, "source": site})
            except Exception:
                continue

    elif site == "ica.se":
        url = f"https://www.ica.se/recept/sok/?q={quote_plus(query)}"
        soup = _get(url)
        if not soup:
            return []
        for card in soup.select(".recipe-card, .search-result, article")[:6]:
            try:
                name_el = card.select_one("h2, h3, .recipe-name, .heading")
                link_el = card.select_one("a[href]")
                if not name_el or not link_el:
                    continue
                name = name_el.get_text(strip=True)
                href = link_el.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.ica.se" + href
                if name and len(name) >= 3:
                    results.append({"name": name, "url": href, "site_rating": None, "source": site})
            except Exception:
                continue

    elif site == "arla.se":
        url = f"https://www.arla.se/recept/?q={quote_plus(query)}"
        soup = _get(url)
        if not soup:
            return []
        for card in soup.select(".recipe-card, .recipe-tile, article")[:6]:
            try:
                name_el = card.select_one("h2, h3, .recipe-title")
                link_el = card.select_one("a[href]")
                if not name_el or not link_el:
                    continue
                name = name_el.get_text(strip=True)
                href = link_el.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.arla.se" + href
                if name and len(name) >= 3:
                    results.append({"name": name, "url": href, "site_rating": None, "source": site})
            except Exception:
                continue

    elif site == "receptfavoriter.se":
        url = f"https://www.receptfavoriter.se/?s={quote_plus(query)}"
        soup = _get(url)
        if not soup:
            return []
        for card in soup.select("article, .recipe-card, .hentry")[:6]:
            try:
                name_el = card.select_one("h2, h3, .entry-title")
                link_el = card.select_one("a[href]")
                rating_el = card.select_one(".rating, .stars")
                if not name_el or not link_el:
                    continue
                name = name_el.get_text(strip=True)
                href = link_el.get("href", "")
                rating = _parse_rating(rating_el.get_text(strip=True)) if rating_el else None
                if name and len(name) >= 3:
                    results.append({"name": name, "url": href, "site_rating": rating, "source": site})
            except Exception:
                continue

    elif site == "koket.se":
        url = f"https://www.koket.se/sok/?q={quote_plus(query)}"
        soup = _get(url)
        if not soup:
            return []
        for card in soup.select(".recipe-card, article, .search-hit")[:6]:
            try:
                name_el = card.select_one("h2, h3, .recipe-title, .title")
                link_el = card.select_one("a[href]")
                if not name_el or not link_el:
                    continue
                name = name_el.get_text(strip=True)
                href = link_el.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.koket.se" + href
                if name and len(name) >= 3:
                    results.append({"name": name, "url": href, "site_rating": None, "source": site})
            except Exception:
                continue

    return results


def scrape_category(diet_type: str, limit: Optional[int] = None) -> list[dict]:
    """Scrape all sites for a diet category, fetch full details, return enriched recipes."""
    queries = DIET_QUERIES.get(diet_type, [diet_type])
    seen_urls: set[str] = set()
    enriched: list[dict] = []

    for query in queries:
        for site in SITES:
            try:
                candidates = _search_site(site, query)
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"Site {site} failed for '{query}': {e}")
                continue

            for cand in candidates:
                url = cand.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                site_rating = cand.get("site_rating")
                if site_rating is not None and site_rating < MIN_RATING:
                    logger.debug(f"Skipping low-rated ({site_rating}): {cand['name']}")
                    continue

                detail = _fetch_detail(url, site)
                time.sleep(0.4)
                if not detail:
                    logger.debug(f"No detail fetched for {url}")
                    continue

                # If detail has a rating and it's too low, skip
                if detail.get("site_rating") is not None and detail["site_rating"] < MIN_RATING:
                    logger.debug(f"Skipping (detail rating {detail['site_rating']}): {detail['name']}")
                    continue

                # Prefer detail's rating, fall back to listing rating
                final_rating = detail.get("site_rating") or site_rating

                recipe = {
                    "name": detail["name"],
                    "url": url,
                    "source": site,
                    "diet_type": diet_type,
                    "site_rating": final_rating,
                    "ingredients": detail.get("ingredients", []),
                    "key_ingredients": [],  # Set by Ollama in future; empty for now
                    "instructions": detail.get("instructions", []),
                }
                enriched.append(recipe)
                logger.info(f"[{diet_type}] {recipe['name']} (rating: {final_rating}) — {site}")

                if limit and len(enriched) >= limit:
                    return enriched

    return enriched


def main() -> int:
    parser = argparse.ArgumentParser(description="Matapp recipe catalog scraper")
    parser.add_argument("--category", choices=list(DIET_QUERIES.keys()),
                        help="Only scrape this diet category")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max recipes to collect total")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print found recipes without saving")
    args = parser.parse_args()

    categories = [args.category] if args.category else list(DIET_QUERIES.keys())
    all_recipes: list[dict] = []

    for diet_type in categories:
        logger.info(f"=== Scraping category: {diet_type} ===")
        remaining = None
        if args.limit:
            remaining = args.limit - len(all_recipes)
            if remaining <= 0:
                break
        recipes = scrape_category(diet_type, limit=remaining)
        all_recipes.extend(recipes)
        logger.info(f"Category {diet_type}: found {len(recipes)} recipes")

    logger.info(f"Total recipes found: {len(all_recipes)}")

    if args.dry_run:
        for r in all_recipes:
            print(f"[{r['diet_type']}] {r['name']} ({r['site_rating']}) — {r['source']}")
            if r["ingredients"]:
                print(f"  Ingredients: {', '.join(r['ingredients'][:5])}")
            print(f"  URL: {r['url']}")
        return 0

    import db
    config_path = BASE_DIR / "config.json"
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    db_path = BASE_DIR / config.get("db_path", "data/matapp.db")
    db.init_db(db_path)

    saved = db.save_catalog_recipes(all_recipes, db_path)
    logger.info(f"Saved {saved} recipes to catalog (deduped by URL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
