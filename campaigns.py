#!/usr/bin/env python3
"""
campaigns.py — Unified campaign/offer fetcher for all supported Swedish grocery chains.

API status (as tested 2026-03-22):
  willys    ✅ Axfood REST API  (willys.se/axfood/rest + /search/campaigns)
  hemkop    ✅ Axfood REST API  (hemkop.se/axfood/rest + /search/campaigns)
  tempo     ✅ Axfood REST API  (tempo.se/axfood/rest + /search/campaigns)
  handlarn  ✅ Axfood REST API  (handlarn.se/axfood/rest + /search/campaigns)
  ica       ⚠️  Store list public (handla.ica.se/api/store/v1), campaign API returns 403
  coop      ❌  external.api.coop.se returns empty body without auth token
  lidl      ❌  No accessible public API found
  citygross ❌  API returns 404
  netto     ❌  Salling Group — no public API
  spar      ❌  No public API
  direkten  ❌  No public API

Usage:
    from campaigns import fetch_campaigns
    offers = fetch_campaigns(store_name="Willys Karlskrona", chain="willys",
                             chain_store_id="2153")
"""

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "sv-SE,sv;q=0.9",
}

_BLOCKED = {
    "öl", "vin", "sprit", "cider", "whisky", "vodka", "gin", "rom",
    "godis", "choklad", "kex", "chips", "snacks", "popcorn",
    "diskmedel", "rengöring", "tvättmedel", "torkpapper", "hushållspapper",
    "toalettpapper", "blöjor", "schampo", "tvål", "deodorant", "tandkräm",
    "rakning", "hygien", "hudkräm", "parfym", "hårvård", "tampong", "bindor",
}


def _is_blocked(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in _BLOCKED)


def _offer(name: str, price=None, original_price=None, promo_label="",
           chain="") -> dict:
    return {
        "name": name,
        "price": price,
        "original_price": original_price,
        "promo_label": promo_label,
        "dealer_id": chain,
    }


# ── Axfood (Willys, Hemköp, Tempo, Handlar'n) ────────────────────────────────

_AXFOOD_DOMAINS = {
    "willys":   "https://www.willys.se",
    "hemkop":   "https://www.hemkop.se",
    "tempo":    "https://www.tempo.se",
    "handlarn": "https://www.handlarn.se",
}


def _axfood_find_store_id(store_name: str, chain: str) -> Optional[str]:
    domain = _AXFOOD_DOMAINS.get(chain, "https://www.willys.se")
    url = f"{domain}/axfood/rest/search/store"
    try:
        r = requests.get(url, params={"q": store_name.split()[0], "pageSize": 20},
                         headers=_HEADERS, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception as e:
        logger.warning(f"Axfood store search failed ({chain}): {e}")
        return None

    store_lower = store_name.lower()
    for s in results:
        if store_lower in s.get("name", "").lower():
            return str(s["storeId"])
    if results:
        return str(results[0]["storeId"])
    return None


def _axfood_fetch_campaigns(store_id: str, chain: str) -> list[dict]:
    domain = _AXFOOD_DOMAINS.get(chain, "https://www.willys.se")
    offers = []
    page = 0
    while True:
        try:
            r = requests.get(
                f"{domain}/search/campaigns",
                params={"storeId": store_id, "size": 30, "page": page},
                headers={**_HEADERS, "Referer": f"{domain}/erbjudanden"},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error(f"Axfood campaigns page {page} failed: {e}")
            break

        for raw in data.get("results", []):
            name = raw.get("name", "").strip()
            if not name or _is_blocked(name):
                continue
            price_val = raw.get("priceValue")
            price_fmt = str(raw.get("price", "") or "").replace(",", ".").replace(" kr", "").strip()
            try:
                price_val = price_val or float(price_fmt)
            except (ValueError, TypeError):
                pass
            orig = None
            m = re.match(r"([\d,]+)\s*kr", str(raw.get("comparePrice", "") or ""))
            if m:
                try:
                    orig = float(m.group(1).replace(",", "."))
                except ValueError:
                    pass
            promos = raw.get("potentialPromotions", [])
            label = ""
            if promos:
                p = promos[0]
                label = (p.get("conditionLabel") or p.get("rewardLabel") or
                         p.get("textLabelGenerated") or "").strip()
            offers.append(_offer(name, price_val, orig, label, chain))

        total_pages = data.get("pagination", {}).get("numberOfPages", 1)
        page += 1
        if page >= total_pages:
            break
    return offers


# ── ICA ───────────────────────────────────────────────────────────────────────
# Store list is public; campaign API requires session auth (returns 403).
# We can store ICA store IDs in the DB for future use when API becomes accessible.

_ICA_STORES_CACHE: list[dict] = []


def _ica_find_store_id(lat: float, lon: float) -> Optional[str]:
    """
    Find nearest ICA store ID by coordinates using the public store list API.
    Returns the store's 'id' field (e.g. '01815').
    Campaign fetching is not currently possible (API requires auth).
    """
    global _ICA_STORES_CACHE
    try:
        if not _ICA_STORES_CACHE:
            r = requests.get("https://handla.ica.se/api/store/v1",
                             headers=_HEADERS, timeout=15)
            r.raise_for_status()
            _ICA_STORES_CACHE = r.json()
        # Find closest store by coordinates
        best = None
        best_dist = float("inf")
        for s in _ICA_STORES_CACHE:
            s_lat = float(s.get("latitude") or 0)
            s_lon = float(s.get("longitude") or 0)
            if not s_lat:
                continue
            dist = ((s_lat - lat)**2 + (s_lon - lon)**2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = s
        return str(best["id"]) if best else None
    except Exception as e:
        logger.warning(f"ICA store lookup failed: {e}")
    return None


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_campaigns(
    store_name: str,
    chain: str,
    chain_store_id: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> list[dict]:
    """
    Fetch this week's campaign offers for a store.

    Falls back to Willys if chain has no scraper.
    Returns [] if nothing is available (never raises).
    """
    chain = (chain or "willys").lower()

    # Axfood group
    if chain in _AXFOOD_DOMAINS:
        sid = chain_store_id or _axfood_find_store_id(store_name, chain)
        if not sid:
            logger.warning(f"Could not resolve {chain} store ID for '{store_name}'")
            return []
        offers = _axfood_fetch_campaigns(sid, chain)
        logger.info(f"{chain} '{store_name}' (id={sid}): {len(offers)} campaign items")
        return offers

    # ICA: campaign API requires auth; fall back to HTML-scraped national offers
    if chain == "ica":
        try:
            from scrape_offers_html import scrape_html_offers
            offers = scrape_html_offers("ica")
            logger.info(f"ICA '{store_name}': {len(offers)} national offers via HTML scrape")
            return offers
        except Exception as e:
            logger.warning(f"ICA HTML scrape failed: {e}")
        return []

    # Citygross: try HTML scrape
    if chain == "citygross":
        try:
            from scrape_offers_html import scrape_html_offers
            offers = scrape_html_offers("citygross")
            logger.info(f"Citygross '{store_name}': {len(offers)} offers via HTML scrape")
            return offers
        except Exception as e:
            logger.warning(f"Citygross HTML scrape failed: {e}")
        return []

    # Coop, Lidl, Netto, Spar, Direkten: no accessible public API
    logger.info(f"Chain '{chain}' has no accessible campaign API — returning empty offers")
    return []


if __name__ == "__main__":
    import sys, logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    chain = sys.argv[1] if len(sys.argv) > 1 else "willys"
    name  = sys.argv[2] if len(sys.argv) > 2 else "Karlskrona"
    sid   = sys.argv[3] if len(sys.argv) > 3 else None
    offers = fetch_campaigns(name, chain, sid)
    print(f"\n{len(offers)} erbjudanden för {chain} '{name}':")
    for o in offers[:20]:
        p = f"{o['price']} kr" if o["price"] else "?"
        print(f"  {o['name']}: {p}")
