#!/usr/bin/env python3
"""
catalog_build.py — Bygg upp recipe_catalog från svenska matkajter.

Hämtar recept med betyg >= 3.5 från:
  tasteline.com, ica.se, arla.se, receptfavoriter.se, koket.se

Kör en gång (eller månadsvis) för att populera databasen.
recipe_catalog används sedan av run.py istället för Ollama.

Användning:
    python3 catalog_build.py              # Alla källor, standard gräns
    python3 catalog_build.py --limit 500  # Max 500 recept totalt
    python3 catalog_build.py --source tasteline  # Bara en källa
    python3 catalog_build.py --min-rating 4.0    # Striktare betyg
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

MIN_RATING = 3.5
PAGE_DELAY = 0.5   # sekunder mellan sidanrop
RECIPE_DELAY = 0.3  # sekunder mellan receptanrop

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9",
    # Avoid brotli — some servers send it but the lib isn't always installed
    "Accept-Encoding": "gzip, deflate",
}


def _get(url: str, timeout: int = 15) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.debug(f"GET failed {url}: {e}")
        return None


def _parse_iso_duration(value: str) -> Optional[int]:
    """Parse ISO 8601 duration (PT30M, PT1H30M) to total minutes."""
    if not value:
        return None
    m = re.match(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?', str(value))
    if not m:
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    mins = int(m.group(3) or 0)
    total = days * 1440 + hours * 60 + mins
    return total if total > 0 else None


def _parse_rating(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", ".")
    m = re.search(r"(\d+\.\d+|\d+)", text)
    if m:
        return float(m.group(1))
    return None


def _extract_jsonld(soup: BeautifulSoup) -> Optional[dict]:
    """Return the first Recipe JSON-LD block, or None."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = next((d for d in data if d.get("@type") == "Recipe"), data[0] if data else {})
            if data.get("@type") == "Recipe":
                return data
        except Exception:
            continue
    return None


def _fetch_recipe(url: str, source: str) -> Optional[dict]:
    """
    Fetch a recipe detail page and return a catalog-ready dict, or None.
    Tries JSON-LD first, then falls back to HTML selectors.
    """
    soup = _get(url)
    if not soup:
        return None

    jld = _extract_jsonld(soup)

    # Name
    if jld and jld.get("name"):
        name = jld["name"].strip()
    else:
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""
    if not name:
        return None

    # Rating
    site_rating = None
    if jld and jld.get("aggregateRating"):
        site_rating = _parse_rating(jld["aggregateRating"].get("ratingValue"))
    if site_rating is None:
        for sel in ["[itemprop='ratingValue']", "[class*='rating']", ".stars"]:
            el = soup.select_one(sel)
            if el:
                site_rating = _parse_rating(el.get("content") or el.get_text())
                if site_rating:
                    break

    # Ingredients
    ingredients = []
    if jld and jld.get("recipeIngredient"):
        ingredients = [str(i).strip() for i in jld["recipeIngredient"] if str(i).strip()]
    if not ingredients:
        for sel in ["[itemprop='recipeIngredient']", ".ingredient", ".ingredients li",
                    ".recipe-ingredients li"]:
            els = soup.select(sel)
            if els:
                ingredients = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
                break

    # Instructions
    instructions = []
    if jld and jld.get("recipeInstructions"):
        raw = jld["recipeInstructions"]
        if isinstance(raw, list):
            for step in raw:
                if isinstance(step, dict):
                    # HowToSection may nest itemListElement (some sites use "type", some "@type")
                    step_type = step.get("@type") or step.get("type") or ""
                    if "HowToSection" in step_type and step.get("itemListElement"):
                        for sub in step["itemListElement"]:
                            text = (sub.get("text") or sub.get("name") or "").strip()
                            if text:
                                instructions.append(text)
                    else:
                        text = (step.get("text") or step.get("name") or "").strip()
                        if text:
                            instructions.append(text)
                elif isinstance(step, str) and step.strip():
                    instructions.append(step.strip())
        elif isinstance(raw, str) and raw.strip():
            instructions = [raw.strip()]
    if not instructions:
        for sel in ["[itemprop='recipeInstructions'] li", ".instructions li",
                    ".recipe-instructions li", ".steps li", "ol li"]:
            els = soup.select(sel)
            if els:
                instructions = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
                break

    # Total time
    total_time_min = None
    if jld:
        total_time_min = (_parse_iso_duration(jld.get("totalTime"))
                          or _parse_iso_duration(jld.get("cookTime")))
        if total_time_min is None:
            prep = _parse_iso_duration(jld.get("prepTime"))
            cook = _parse_iso_duration(jld.get("cookTime"))
            if prep and cook:
                total_time_min = prep + cook

    return {
        "name": name,
        "url": url,
        "source": source,
        "site_rating": site_rating,
        "total_time_min": total_time_min,
        "ingredients": ingredients,
        "instructions": instructions,
    }


# ─── Per-source listing crawlers ──────────────────────────────────────────────

def _crawl_tasteline(limit: int, min_rating: float, existing_urls: set = None) -> list[dict]:
    """
    Tasteline: paginated listing at /recept/page/N/.
    Fetches recipe URLs, then detail pages for rating.
    Skips URLs already in the database.
    """
    source = "tasteline.com"
    seen = set(existing_urls or [])
    results = []
    page = 1

    while len(results) < limit:
        url = f"https://www.tasteline.com/recept/page/{page}/" if page > 1 else "https://www.tasteline.com/recept/"
        soup = _get(url)
        if not soup:
            break

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if (href.startswith("https://www.tasteline.com/recept/")
                    and href.count("/") >= 5
                    and href not in seen):
                links.append(href)
                seen.add(href)

        if not links:
            break

        for recipe_url in links:
            if len(results) >= limit:
                break
            if recipe_url in (existing_urls or set()):
                logger.debug(f"  skip (already in DB): {recipe_url}")
                continue
            recipe = _fetch_recipe(recipe_url, source)
            time.sleep(RECIPE_DELAY)
            if not recipe:
                continue
            rating = recipe.get("site_rating")
            if rating is not None and rating < min_rating:
                logger.debug(f"  skip {recipe['name']} (rating {rating})")
                continue
            if rating is not None:
                logger.info(f"  ✓ {recipe['name']} ({rating:.1f}★) [{source}]")
                results.append(recipe)

        page += 1
        time.sleep(PAGE_DELAY)

    return results


def _crawl_site_generic(
    listing_url_fn,  # callable(page_num) -> url string
    recipe_link_filter,  # callable(href) -> bool
    base_url: str,
    source: str,
    limit: int,
    min_rating: float,
    max_pages: int = 30,
    existing_urls: set = None,
) -> list[dict]:
    """Generic paginated crawler for sites with standard recipe listings.
    Skips URLs already in the database."""
    seen = set(existing_urls or [])
    results = []
    page = 1

    while len(results) < limit and page <= max_pages:
        url = listing_url_fn(page)
        soup = _get(url)
        if not soup:
            break

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            if recipe_link_filter(href) and href not in seen:
                links.append(href)
                seen.add(href)

        if not links:
            break

        for recipe_url in links:
            if len(results) >= limit:
                break
            if recipe_url in (existing_urls or set()):
                logger.debug(f"  skip (already in DB): {recipe_url}")
                continue
            recipe = _fetch_recipe(recipe_url, source)
            time.sleep(RECIPE_DELAY)
            if not recipe:
                continue
            rating = recipe.get("site_rating")
            if rating is not None and rating < min_rating:
                continue
            if rating is not None:
                logger.info(f"  ✓ {recipe['name']} ({rating:.1f}★) [{source}]")
            else:
                logger.info(f"  ✓ {recipe['name']} (no rating) [{source}]")
            results.append(recipe)

        page += 1
        time.sleep(PAGE_DELAY)

    return results


def _crawl_ica(limit: int, min_rating: float, existing_urls: set = None) -> list[dict]:
    def listing_url(page):
        return f"https://www.ica.se/recept/?page={page}"

    def is_recipe(href):
        return re.match(r"https://www\.ica\.se/recept/[^/]+/\d+", href) is not None

    return _crawl_site_generic(
        listing_url, is_recipe, "https://www.ica.se", "ica.se",
        limit, min_rating, existing_urls=existing_urls,
    )


def _crawl_arla(limit: int, min_rating: float, existing_urls: set = None) -> list[dict]:
    def listing_url(page):
        return f"https://www.arla.se/recept/?page={page}"

    def is_recipe(href):
        return (href.startswith("https://www.arla.se/recept/")
                and href.count("/") >= 5
                and not href.endswith("/recept/"))

    return _crawl_site_generic(
        listing_url, is_recipe, "https://www.arla.se", "arla.se",
        limit, min_rating, existing_urls=existing_urls,
    )


def _crawl_receptfavoriter(limit: int, min_rating: float, existing_urls: set = None) -> list[dict]:
    def listing_url(page):
        return f"https://www.receptfavoriter.se/page/{page}/"

    def is_recipe(href):
        return (href.startswith("https://www.receptfavoriter.se/")
                and href.count("/") >= 4
                and not any(x in href for x in ["/page/", "/category/", "/tag/"]))

    return _crawl_site_generic(
        listing_url, is_recipe, "https://www.receptfavoriter.se", "receptfavoriter.se",
        limit, min_rating, existing_urls=existing_urls,
    )


def _crawl_koket(limit: int, min_rating: float, existing_urls: set = None) -> list[dict]:
    def listing_url(page):
        return f"https://www.koket.se/recept/?page={page}"

    def is_recipe(href):
        return (href.startswith("https://www.koket.se/")
                and href.count("/") >= 4
                and not any(x in href for x in ["/recept/", "/kockar/", "/tv-", "/mat-"]))

    return _crawl_site_generic(
        listing_url, is_recipe, "https://www.koket.se", "koket.se",
        limit, min_rating, existing_urls=existing_urls,
    )


SOURCES = {
    "tasteline": _crawl_tasteline,
    "ica": _crawl_ica,
    "arla": _crawl_arla,
    "receptfavoriter": _crawl_receptfavoriter,
    "koket": _crawl_koket,
}


def _load_existing_urls(db_path: Path) -> set:
    """Return set of URLs already in recipe_catalog."""
    try:
        with db.get_connection(db_path) as conn:
            rows = conn.execute("SELECT url FROM recipe_catalog").fetchall()
            return {row[0] for row in rows}
    except Exception:
        return set()


def build_catalog(
    sources: list[str] = None,
    limit_per_source: int = 200,
    min_rating: float = MIN_RATING,
    db_path: Path = db.DEFAULT_DB,
):
    if sources is None:
        sources = list(SOURCES.keys())

    existing_urls = _load_existing_urls(db_path)
    if existing_urls:
        logger.info(f"Hoppar över {len(existing_urls)} recept som redan finns i databasen.")

    total_saved = 0
    for name in sources:
        crawler = SOURCES.get(name)
        if not crawler:
            logger.warning(f"Unknown source: {name}")
            continue
        logger.info(f"\n=== {name} (min rating {min_rating}) ===")
        try:
            recipes = crawler(limit_per_source, min_rating, existing_urls=existing_urls)
        except Exception as e:
            logger.error(f"{name} crawler failed: {e}")
            continue

        saved = db.save_catalog_recipes(recipes, db_path)
        total_saved += saved
        # Update existing_urls so next source also skips these
        existing_urls.update(r["url"] for r in recipes)
        logger.info(f"  Sparade {saved}/{len(recipes)} recept från {name}")

    logger.info(f"\nKlar. Totalt sparade: {total_saved} recept.")
    return total_saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bygg recipe_catalog från svenska matkajter")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max recept per källa (standard: 200)")
    parser.add_argument("--min-rating", type=float, default=MIN_RATING,
                        help=f"Minsta betyg (standard: {MIN_RATING})")
    parser.add_argument("--source", choices=list(SOURCES.keys()),
                        help="Kör bara en källa")
    parser.add_argument("--db", default=str(db.DEFAULT_DB),
                        help="Sökväg till SQLite-databasen")
    args = parser.parse_args()

    sources = [args.source] if args.source else None
    build_catalog(
        sources=sources,
        limit_per_source=args.limit,
        min_rating=args.min_rating,
        db_path=Path(args.db),
    )
