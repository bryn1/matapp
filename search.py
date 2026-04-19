#!/usr/bin/env python3
"""
search.py — Hämtar recept från svenska och internationella matkajter.

Svenska källor: tasteline.com, ica.se, arla.se, receptfavoriter.se, koket.se
Internationella: bbcgoodfood.com, allrecipes.com, food.com
Ingredienser från icke-svenska recept översätts automatiskt till svenska.
Returnerar kandidatrecept för Ollama att inspireras av.
"""

import logging
import re
import time
from typing import Optional
from urllib.parse import quote_plus, urlencode

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}
TIMEOUT = 12
MAX_PER_SOURCE = 3
MAX_RESULTS = 14

# ─── Rate limiter ─────────────────────────────────────────────────────────────

class _DomainRateLimiter:
    """Keeps per-domain delay to avoid hammering any single site."""

    def __init__(self, min_delay: float = 1.0):
        self._last: dict[str, float] = {}
        self._min_delay = min_delay

    def wait(self, domain: str) -> None:
        now = time.monotonic()
        last = self._last.get(domain, 0.0)
        elapsed = now - last
        if elapsed < self._min_delay:
            time.sleep(self._min_delay - elapsed)
        self._last[domain] = time.monotonic()


_rate_limiter = _DomainRateLimiter(min_delay=1.5)


# ─── HTTP helper ──────────────────────────────────────────────────────────────

def _get(url: str, domain: str) -> Optional[BeautifulSoup]:
    _rate_limiter.wait(domain)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.debug(f"Fetch failed {url}: {e}")
        return None


# ─── Google Translate link ────────────────────────────────────────────────────

def google_translate_url(url: str, source_lang: str = "auto", target_lang: str = "sv") -> str:
    """Return a Google Translate link that shows the recipe page in Swedish."""
    params = urlencode({"sl": source_lang, "tl": target_lang, "u": url})
    return f"https://translate.google.com/translate?{params}"


# ─── Ingredient translation ───────────────────────────────────────────────────

def translate_ingredients(ingredients: list[str], source_lang: str = "en") -> list[str]:
    """
    Translate a list of ingredient strings to Swedish.
    Uses deep-translator (Google Translate free tier).
    Falls back to original strings on any error.
    """
    if not ingredients or source_lang == "sv":
        return ingredients
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source=source_lang, target="sv")
        translated = []
        for ing in ingredients:
            try:
                result = translator.translate(ing)
                translated.append(result if result else ing)
            except Exception:
                translated.append(ing)
        return translated
    except ImportError:
        logger.warning("deep-translator not installed — ingredients not translated")
        return ingredients
    except Exception as e:
        logger.warning(f"Translation failed: {e}")
        return ingredients


# ─── Swedish sources ──────────────────────────────────────────────────────────

def _search_tasteline(query: str) -> list[dict]:
    domain = "tasteline.com"
    url = f"https://www.tasteline.com/recept/?q={quote_plus(query)}&sort=rating"
    soup = _get(url, domain)
    if not soup:
        return []

    results = []
    for card in soup.select(".recipe-card, article.recipe, .search-result-item")[:MAX_PER_SOURCE]:
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
            rating = rating_el.get_text(strip=True) if rating_el else None
            results.append({"name": name, "url": href, "rating": rating,
                             "source": domain, "lang": "sv", "ingredients": []})
        except Exception:
            continue
    return results


def _search_ica(query: str) -> list[dict]:
    domain = "ica.se"
    url = f"https://www.ica.se/recept/sok/?q={quote_plus(query)}"
    soup = _get(url, domain)
    if not soup:
        return []

    results = []
    for card in soup.select(".recipe-card, .search-result, article")[:MAX_PER_SOURCE]:
        try:
            name_el = card.select_one("h2, h3, .recipe-name, .heading")
            link_el = card.select_one("a[href]")
            if not name_el or not link_el:
                continue
            name = name_el.get_text(strip=True)
            href = link_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.ica.se" + href
            if not name or len(name) < 3:
                continue
            results.append({"name": name, "url": href, "rating": None,
                             "source": domain, "lang": "sv", "ingredients": []})
        except Exception:
            continue
    return results


def _search_arla(query: str) -> list[dict]:
    domain = "arla.se"
    url = f"https://www.arla.se/recept/?q={quote_plus(query)}"
    soup = _get(url, domain)
    if not soup:
        return []

    results = []
    for card in soup.select(".recipe-card, .recipe-tile, article")[:MAX_PER_SOURCE]:
        try:
            name_el = card.select_one("h2, h3, .recipe-title")
            link_el = card.select_one("a[href]")
            if not name_el or not link_el:
                continue
            name = name_el.get_text(strip=True)
            href = link_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.arla.se" + href
            if not name or len(name) < 3:
                continue
            results.append({"name": name, "url": href, "rating": None,
                             "source": domain, "lang": "sv", "ingredients": []})
        except Exception:
            continue
    return results


def _search_receptfavoriter(query: str) -> list[dict]:
    domain = "receptfavoriter.se"
    url = f"https://www.receptfavoriter.se/?s={quote_plus(query)}"
    soup = _get(url, domain)
    if not soup:
        return []

    results = []
    for card in soup.select("article, .recipe-card, .hentry")[:MAX_PER_SOURCE]:
        try:
            name_el = card.select_one("h2, h3, .entry-title")
            link_el = card.select_one("a[href]")
            rating_el = card.select_one(".rating, .stars")
            if not name_el or not link_el:
                continue
            name = name_el.get_text(strip=True)
            href = link_el.get("href", "")
            rating = rating_el.get_text(strip=True) if rating_el else None
            if not name or len(name) < 3:
                continue
            results.append({"name": name, "url": href, "rating": rating,
                             "source": domain, "lang": "sv", "ingredients": []})
        except Exception:
            continue
    return results


def _search_koket(query: str) -> list[dict]:
    domain = "koket.se"
    url = f"https://www.koket.se/sok/?q={quote_plus(query)}"
    soup = _get(url, domain)
    if not soup:
        return []

    results = []
    for card in soup.select(".recipe-card, article, .search-hit")[:MAX_PER_SOURCE]:
        try:
            name_el = card.select_one("h2, h3, .recipe-title, .title")
            link_el = card.select_one("a[href]")
            if not name_el or not link_el:
                continue
            name = name_el.get_text(strip=True)
            href = link_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.koket.se" + href
            if not name or len(name) < 3:
                continue
            results.append({"name": name, "url": href, "rating": None,
                             "source": domain, "lang": "sv", "ingredients": []})
        except Exception:
            continue
    return results


# ─── International sources ────────────────────────────────────────────────────

def _search_bbcgoodfood(query: str) -> list[dict]:
    """BBC Good Food (English). Top-rated, family-friendly recipes."""
    domain = "bbcgoodfood.com"
    url = f"https://www.bbcgoodfood.com/search?q={quote_plus(query)}"
    soup = _get(url, domain)
    if not soup:
        return []

    results = []
    for card in soup.select("article, .card, [class*='recipe-card'], [class*='card--recipe']")[:MAX_PER_SOURCE]:
        try:
            name_el = card.select_one("h2, h3, [class*='card__title'], [class*='card-title']")
            link_el = card.select_one("a[href]")
            rating_el = card.select_one("[class*='rating'], [class*='stars']")
            if not name_el or not link_el:
                continue
            name = name_el.get_text(strip=True)
            href = link_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://www.bbcgoodfood.com" + href
            if not name or len(name) < 3 or "/recipes/" not in href:
                continue
            rating = rating_el.get_text(strip=True) if rating_el else None
            results.append({
                "name": name,
                "url": href,
                "translate_url": google_translate_url(href, source_lang="en"),
                "rating": rating,
                "source": domain,
                "lang": "en",
                "ingredients": [],
            })
        except Exception:
            continue
    return results


def _search_allrecipes(query: str) -> list[dict]:
    """AllRecipes (English/international, community-rated)."""
    domain = "allrecipes.com"
    url = f"https://www.allrecipes.com/search?q={quote_plus(query)}"
    soup = _get(url, domain)
    if not soup:
        return []

    results = []
    for card in soup.select("article, [class*='card'], [data-type='Recipe']")[:MAX_PER_SOURCE]:
        try:
            name_el = card.select_one("h2, h3, span.card__title, [class*='card__title']")
            link_el = card.select_one("a[href]")
            rating_el = card.select_one("[class*='rating'], span.rating-stars")
            if not name_el or not link_el:
                continue
            name = name_el.get_text(strip=True)
            href = link_el.get("href", "")
            if not href.startswith("http"):
                href = "https://www.allrecipes.com" + href
            if not name or len(name) < 3 or "/recipe/" not in href:
                continue
            rating = rating_el.get_text(strip=True) if rating_el else None
            results.append({
                "name": name,
                "url": href,
                "translate_url": google_translate_url(href, source_lang="en"),
                "rating": rating,
                "source": domain,
                "lang": "en",
                "ingredients": [],
            })
        except Exception:
            continue
    return results


def _search_food52(query: str) -> list[dict]:
    """Food52 (English, community recipes, often vegetarian-friendly)."""
    domain = "food52.com"
    url = f"https://food52.com/recipes?q={quote_plus(query)}"
    soup = _get(url, domain)
    if not soup:
        return []

    results = []
    for card in soup.select("article, [class*='recipe'], [class*='grid-item']")[:MAX_PER_SOURCE]:
        try:
            name_el = card.select_one("h3, h2, [class*='title']")
            link_el = card.select_one("a[href]")
            if not name_el or not link_el:
                continue
            name = name_el.get_text(strip=True)
            href = link_el.get("href", "")
            if href and not href.startswith("http"):
                href = "https://food52.com" + href
            if not name or len(name) < 3:
                continue
            results.append({
                "name": name,
                "url": href,
                "translate_url": google_translate_url(href, source_lang="en"),
                "rating": None,
                "source": domain,
                "lang": "en",
                "ingredients": [],
            })
        except Exception:
            continue
    return results


# ─── Full recipe detail fetcher ───────────────────────────────────────────────

def _parse_rating_float(text: str) -> Optional[float]:
    m = re.search(r"(\d+[.,]\d+)", text.replace(",", "."))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    return None


def fetch_recipe_detail(url: str, source: str, source_lang: str = "sv") -> Optional[dict]:
    """
    Fetch full recipe detail from a recipe page.
    For non-Swedish recipes, translates ingredients to Swedish.
    Returns {name, url, translate_url, source, lang, site_rating, ingredients, instructions}.
    """
    domain = source if "." in source else url.split("/")[2]
    soup = _get(url, domain)
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
                site_rating = _parse_rating_float(el.get_text(strip=True) or el.get("content", ""))
                if site_rating:
                    break

        ingredients = []
        for sel in ["[itemprop='recipeIngredient']", ".ingredient", ".ingredients li",
                    ".recipe-ingredients li", "ul.ingredients li", "[class*='ingredient']"]:
            els = soup.select(sel)
            if els:
                ingredients = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
                break

        # Translate ingredients to Swedish if from a non-Swedish source
        original_ingredients = ingredients[:]
        if source_lang != "sv" and ingredients:
            ingredients = translate_ingredients(ingredients, source_lang=source_lang)

        instructions = []
        for sel in ["[itemprop='recipeInstructions'] li", ".instructions li",
                    ".recipe-instructions li", ".steps li", "ol.instructions li",
                    "[class*='instruction'] li", "[class*='step'] li"]:
            els = soup.select(sel)
            if els:
                instructions = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
                break

        result = {
            "name": name,
            "url": url,
            "source": source,
            "lang": source_lang,
            "site_rating": site_rating,
            "ingredients": ingredients,
            "original_ingredients": original_ingredients if source_lang != "sv" else [],
            "instructions": instructions,
        }
        if source_lang != "sv":
            result["translate_url"] = google_translate_url(url, source_lang=source_lang)
        return result
    except Exception as e:
        logger.debug(f"fetch_recipe_detail failed {url}: {e}")
        return None


# ─── Main ─────────────────────────────────────────────────────────────────────

# Swedish sources first, then international
SWEDISH_SEARCHERS = [
    (_search_tasteline, "sv"),
    (_search_ica, "sv"),
    (_search_arla, "sv"),
    (_search_receptfavoriter, "sv"),
    (_search_koket, "sv"),
]

INTERNATIONAL_SEARCHERS = [
    (_search_bbcgoodfood, "en"),
    (_search_allrecipes, "en"),
    (_search_food52, "en"),
]

# Combined — Swedish preferred, international fills remaining slots
SEARCHERS = SWEDISH_SEARCHERS + INTERNATIONAL_SEARCHERS


def search_recipes(offers: list[dict], max_results: int = MAX_RESULTS) -> list[dict]:
    """
    Söker recept från svenska och internationella matkajter.
    Ingredienser från internationella recept översätts automatiskt till svenska.

    Args:
        offers: Lista med erbjudanden från willys.fetch_offers()
        max_results: Max antal kandidatrecept att returnera

    Returns:
        Lista med receptkandidater (namn, url, translate_url, betyg, källa, språk)
    """
    if not offers:
        return []

    search_items = [o["name"] for o in offers[:5]]
    logger.info(f"Söker recept för: {search_items}")

    all_results = []
    seen_names = set()

    for item in search_items[:3]:
        for searcher, lang in SEARCHERS:
            try:
                results = searcher(item)
                for r in results:
                    name_key = r["name"].lower()
                    if name_key not in seen_names:
                        seen_names.add(name_key)
                        r["search_query"] = item
                        if "lang" not in r:
                            r["lang"] = lang
                        all_results.append(r)
            except Exception as e:
                logger.debug(f"Searcher {searcher.__name__} failed for '{item}': {e}")
                continue

        if len(all_results) >= max_results:
            break

    intl_count = sum(1 for r in all_results if r.get("lang") != "sv")
    logger.info(
        f"Hittade {len(all_results)} kandidatrecept "
        f"({len(all_results) - intl_count} svenska, {intl_count} internationella)"
    )
    return all_results[:max_results]
