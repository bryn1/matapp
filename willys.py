#!/usr/bin/env python3
"""
willys.py — Hämtar veckans erbjudanden från Willys via deras egna REST-API.

Endpoints:
  Store search: /axfood/rest/search/store?q={name}&pageSize=10
  Campaigns:    /search/campaigns?storeId={id}&size=30&page=0

Filtrerar bort alkohol, godis, chips, rengöringsmedel och icke-matvaror.
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.willys.se"
PAGE_SIZE = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.willys.se/erbjudanden",
    "Accept-Language": "sv-SE,sv;q=0.9",
}

BLOCKED_KEYWORDS = [
    # Alkohol
    "öl", "vin", "sprit", "cider", "alkohol", "whisky", "vodka", "gin",
    "rom", "punsch", "aperitif", "champagne", "prosecco",
    # Godis & snacks
    "godis", "choklad", "kakor", "kex", "skumgummi", "lakrits",
    "chips", "snacks", "popcorn", "ostbågar", "puffar",
    # Rengöring & hushåll
    "diskmedel", "rengöring", "tvättmedel", "torkpapper",
    "hushållspapper", "toalettpapper", "blöjor", "våtservetter",
    "städ", "avfettning", "badrumsrent",
    # Hygien / icke-mat
    "schampo", "tvål", "deodorant", "tandkräm", "rakning", "hygien",
    "hudkräm", "lotion", "parfym", "hårvård", "munvatten",
    "rakhyvel", "rakgel", "tampong", "bindor",
]


def _is_blocked(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in BLOCKED_KEYWORDS)


def _find_store_id(store_name: str) -> Optional[str]:
    """Look up storeId by fuzzy name match."""
    # Use the first word(s) of the store name as search query
    query = store_name.split()[0] if store_name else ""
    url = f"{BASE_URL}/axfood/rest/search/store"
    params = {"q": query, "pageSize": 20}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        logger.warning(f"Store search failed: {e}")
        return None

    store_lower = store_name.lower()
    # Exact substring match first
    for s in results:
        if store_lower in s.get("name", "").lower():
            sid = s["storeId"]
            logger.info(f"Hittade butik: {s['name']} (storeId={sid})")
            return sid
    # Fallback: first result
    if results:
        s = results[0]
        sid = s["storeId"]
        names = [r.get("name", "") for r in results]
        logger.warning(
            f"Hittade inte exakt matchning för '{store_name}'. "
            f"Tillgängliga: {names}. Använder: {s['name']} (storeId={sid})"
        )
        return sid

    logger.error(f"Inga butiker hittades för '{store_name}'")
    return None


def _fetch_campaign_page(store_id: str, page: int) -> tuple[list[dict], int]:
    """Fetch one page of campaign products. Returns (results, total_pages)."""
    url = f"{BASE_URL}/search/campaigns"
    params = {"storeId": store_id, "size": PAGE_SIZE, "page": page}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    pagination = data.get("pagination", {})
    total_pages = pagination.get("numberOfPages", 1)
    return data.get("results", []), total_pages


def _parse_offer(raw: dict) -> dict:
    """Convert a raw campaign result to a normalised offer dict."""
    name = raw.get("name", "").strip()

    # Sale price
    price_val = raw.get("priceValue")
    price_fmt = raw.get("price", "")
    if isinstance(price_fmt, str):
        price_fmt = price_fmt.replace(",", ".").replace(" kr", "").strip()
        try:
            price_val = price_val or float(price_fmt)
        except ValueError:
            pass

    # Original/compare price — extract numeric from "XX,XX kr/kg" style
    compare_raw = raw.get("comparePrice", "") or ""
    original_price = None
    if compare_raw:
        import re
        m = re.match(r"([\d,]+)\s*kr", compare_raw)
        if m:
            try:
                original_price = float(m.group(1).replace(",", "."))
            except ValueError:
                pass

    # Promotion label
    promos = raw.get("potentialPromotions", [])
    promo_label = ""
    if promos:
        p = promos[0]
        promo_label = (
            p.get("conditionLabel")
            or p.get("rewardLabel")
            or p.get("textLabelGenerated")
            or ""
        ).strip()

    return {
        "name": name,
        "price": price_val,
        "original_price": original_price,
        "promo_label": promo_label,
        "category": "",
        "dealer_id": "willys",
    }


def fetch_offers(store_name: Optional[str] = None) -> list[dict]:
    """
    Hämtar och filtrerar veckans erbjudanden från Willys.

    Args:
        store_name: Butiksnamn (t.ex. "Karlskrona Slottsbacken").

    Returns:
        Lista med normaliserade offer-dicts:
        [{"name": str, "price": float|None, "original_price": float|None,
          "promo_label": str, "category": str, "dealer_id": str}, ...]
    """
    # Resolve store ID
    store_id = None
    if store_name:
        store_id = _find_store_id(store_name)
    if not store_id:
        logger.error("Kunde inte lösa storeId — returnerar tom lista")
        return []

    # Fetch all pages
    all_raw: list[dict] = []
    page = 0
    while True:
        try:
            results, total_pages = _fetch_campaign_page(store_id, page)
        except Exception as e:
            logger.error(f"Fel vid hämtning av kampanjsida {page}: {e}")
            break
        all_raw.extend(results)
        logger.info(f"Hämtade sida {page + 1}/{total_pages} ({len(results)} produkter)")
        page += 1
        if page >= total_pages:
            break

    logger.info(f"Totalt {len(all_raw)} råerbjudanden hämtade")

    # Parse and filter
    parsed = []
    blocked_count = 0
    for raw in all_raw:
        offer = _parse_offer(raw)
        if not offer["name"]:
            continue
        if _is_blocked(offer["name"]):
            blocked_count += 1
            continue
        parsed.append(offer)

    logger.info(
        f"Erbjudanden efter filtrering: {len(parsed)} "
        f"(filtrerade bort {blocked_count} ej relevanta produkter)"
    )
    return parsed


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = sys.argv[1] if len(sys.argv) > 1 else "Karlskrona Slottsbacken"
    offers = fetch_offers(store_name=store)
    print(f"\nHittade {len(offers)} erbjudanden för '{store}':\n")
    for o in offers[:20]:
        price = f"{o['price']} kr" if o["price"] else "?"
        promo = f" ({o['promo_label']})" if o["promo_label"] else ""
        print(f"  {o['name']}: {price}{promo}")
    if len(offers) > 20:
        print(f"  ... och {len(offers) - 20} till")
