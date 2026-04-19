#!/usr/bin/env python3
"""
scrape_offers_html.py — HTML-based offer scrapers for chains without public JSON APIs.

Chains covered:
  ICA       — ica.se/erbjudanden/  (6 national offers visible in static HTML)
  Coop      — no parseable offers in static HTML (SPA, requires JS)
  Lidl      — requires browser JS; skipped
  Citygross — check below

Usage:
    python3 scrape_offers_html.py                  # all chains
    python3 scrape_offers_html.py --chain ica
    python3 scrape_offers_html.py --dry-run
"""

import argparse
import logging
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sv-SE,sv;q=0.9",
}

_BLOCKED = {
    "öl", "vin", "sprit", "cider", "whisky", "vodka", "gin", "rom",
    "godis", "choklad", "kex", "chips", "snacks", "popcorn",
    "diskmedel", "rengöring", "tvättmedel", "torkpapper", "hushållspapper",
    "toalettpapper", "blöjor", "schampo", "tvål", "deodorant", "tandkräm",
}


def _is_blocked(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in _BLOCKED)


def _offer(name, price=None, promo_label="", chain="", detail=""):
    return {
        "name": name,
        "price": price,
        "original_price": None,
        "promo_label": promo_label,
        "dealer_id": chain,
        "detail": detail,
    }


def _extract_price(text: str):
    """Extract numeric price value from Swedish price strings like '30 kr', '2 för 30 kr', '79:-/kg'."""
    if not text:
        return None
    m = re.search(r"(\d+(?:[,\.]\d+)?)\s*(?:kr|:-)", text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    return None


# ── ICA ───────────────────────────────────────────────────────────────────────

def _ica_scrape() -> list[dict]:
    """
    Fetch ICA national weekly offers from ica.se/erbjudanden/.
    Returns the offers visible in static HTML (typically 6 national offers).
    Store-specific offers require JavaScript/session auth — not accessible here.
    """
    try:
        r = requests.get("https://www.ica.se/erbjudanden/", headers=_HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"ICA HTML fetch failed: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select(".offer-card.data-promotion")
    logger.info(f"ICA: found {len(cards)} offer cards in static HTML")

    offers = []
    for card in cards:
        name = card.get("data-promotion-name", "").strip()
        if not name or _is_blocked(name):
            continue
        price_el = card.select_one("[class*=price]")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price_val = _extract_price(price_text)
        promo_m = re.search(r"(\d+\s+för)", price_text)
        promo_label = promo_m.group(1).strip() if promo_m else ""
        detail_el = card.select_one("[class*=detail], [class*=description], p")
        detail = detail_el.get_text(strip=True) if detail_el else ""
        offers.append(_offer(name, price_val, promo_label, "ica", detail))

    logger.info(f"ICA: {len(offers)} food offers (after filter)")
    return offers


# ── Citygross ─────────────────────────────────────────────────────────────────

def _citygross_scrape() -> list[dict]:
    """
    Attempt to fetch Citygross weekly offers from citygross.se/erbjudanden/.
    """
    try:
        r = requests.get("https://www.citygross.se/erbjudanden/", headers=_HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"Citygross HTML fetch failed: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    # Try various selectors
    offers = []
    for sel in ["[class*=offer-card]", "[class*=product-card]", "[class*=campaign]", "article"]:
        cards = soup.select(sel)
        if len(cards) > 3:
            logger.info(f"Citygross: found {len(cards)} cards with selector '{sel}'")
            for card in cards:
                name_el = card.select_one("h2, h3, h4, [class*=title], [class*=name]")
                price_el = card.select_one("[class*=price]")
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                if not name or _is_blocked(name):
                    continue
                price_text = price_el.get_text(strip=True) if price_el else ""
                price_val = _extract_price(price_text)
                offers.append(_offer(name, price_val, "", "citygross"))
            break

    logger.info(f"Citygross: {len(offers)} food offers")
    return offers


# ── Coop ──────────────────────────────────────────────────────────────────────

def _coop_scrape() -> list[dict]:
    """
    Coop's offer pages are dynamically rendered (React SPA). The static HTML
    contains no product offer data. Returns empty list.
    """
    logger.info("Coop: SPA — no offers in static HTML, skipping")
    return []


# ── Dispatch ─────────────────────────────────────────────────────────────────

_SCRAPERS = {
    "ica": _ica_scrape,
    "citygross": _citygross_scrape,
    "coop": _coop_scrape,
}


def scrape_html_offers(chain: str) -> list[dict]:
    """Public entry point — fetch HTML offers for a chain. Returns [] if not supported."""
    fn = _SCRAPERS.get(chain.lower())
    if not fn:
        logger.debug(f"No HTML scraper for chain '{chain}'")
        return []
    return fn()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", default=None, help="Only process this chain")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    chains = [args.chain] if args.chain else list(_SCRAPERS.keys())

    for chain in chains:
        print(f"\n── {chain} ──")
        if args.dry_run:
            print(f"  (dry-run) would call {chain} scraper")
            continue
        offers = scrape_html_offers(chain)
        print(f"  {len(offers)} offers:")
        for o in offers[:10]:
            p = f"{o['price']} kr" if o["price"] else "?"
            pl = f"  [{o['promo_label']}]" if o["promo_label"] else ""
            print(f"    {o['name']}: {p}{pl}")
        if len(offers) > 10:
            print(f"    ... and {len(offers) - 10} more")
        time.sleep(1)


if __name__ == "__main__":
    main()
