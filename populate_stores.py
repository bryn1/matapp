#!/usr/bin/env python3
"""
populate_stores.py — Fetch all Swedish grocery stores from OpenStreetMap and
store them in the local swedish_stores table.

Run once to build the DB, then monthly to refresh:
    python3 populate_stores.py
    python3 populate_stores.py --dry-run   # show count only, no DB write

Chains covered: ICA, Willys, Coop/Konsum, Hemköp, Lidl, Citygross, Netto,
                Tempo, Handlar'n, Direkten, Spar
"""

import argparse
import concurrent.futures
import json
import logging
import sys
import time

import requests

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Sweden bounding box (south, west, north, east)
_SWEDEN_BBOX = "55.2,10.9,69.1,24.2"

_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Regex used inside Overpass query — matches any of our target chains
_NAME_REGEX = (
    "ICA|Willys|Willy:|Coop|Konsum|Hemköp|Hemkop|Lidl|"
    "Citygross|City Gross|Netto|Tempo|Handlar|Direkten|Spar"
)

_GAS_KEYWORDS = {
    "shell", "circle k", "preem", "ok ", "st1", "ingo", "tesla supercharger",
    "q8", "statoil", "tamoil", "jet ", "drive-in", "bensin",
}


def _is_gas_station(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in _GAS_KEYWORDS)


def _detect_chain(name: str) -> str | None:
    """Map a store name to a canonical chain slug, or None if unknown."""
    n = name.lower()
    if "willys" in n or "willy:" in n:
        return "willys"
    if "ica" in n or "kvantum" in n or "maxi" in n:
        return "ica"
    if "hemköp" in n or "hemkop" in n:
        return "hemkop"
    if "coop" in n or "konsum" in n:
        return "coop"
    if "lidl" in n:
        return "lidl"
    if "citygross" in n or "city gross" in n:
        return "citygross"
    if "netto" in n:
        return "netto"
    if "tempo" in n:
        return "tempo"
    if "handlar" in n:
        return "handlarn"
    if "direkten" in n:
        return "direkten"
    if "spar" in n:
        return "spar"
    return None


def _overpass_query(query: str, timeout: int = 60) -> list[dict]:
    """Query all Overpass mirrors in parallel; return first successful response."""

    def _try(endpoint: str) -> list[dict]:
        r = requests.post(endpoint, data={"data": query}, timeout=timeout)
        if not r.text.strip():
            raise ValueError("empty body")
        data = r.json()
        return data.get("elements", [])

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_try, ep): ep for ep in _OVERPASS_ENDPOINTS}
        for fut in concurrent.futures.as_completed(futs, timeout=timeout + 10):
            ep = futs[fut]
            try:
                els = fut.result()
                logger.info(f"Overpass OK from {ep}: {len(els)} elements")
                for f in futs:
                    f.cancel()
                return els
            except Exception as e:
                logger.warning(f"Overpass {ep}: {e}")
    return []


def fetch_all_stores() -> list[dict]:
    """Fetch all matching grocery stores in Sweden from OSM."""
    query = (
        f"[out:json][timeout:180][bbox:{_SWEDEN_BBOX}];\n"
        f"(\n"
        f'  node["shop"~"supermarket|grocery|convenience"]["name"~"{_NAME_REGEX}",i];\n'
        f'  way["shop"~"supermarket|grocery|convenience"]["name"~"{_NAME_REGEX}",i];\n'
        f");\n"
        f"out center;"
    )
    logger.info("Querying Overpass for all Swedish grocery stores (may take ~60s)…")
    t0 = time.time()
    elements = _overpass_query(query, timeout=200)
    logger.info(f"Overpass returned {len(elements)} raw elements in {time.time()-t0:.1f}s")

    stores = []
    seen_osm = set()

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name", "").strip()
        if not name or _is_gas_station(name):
            continue

        chain = _detect_chain(name)
        if not chain:
            continue

        osm_id = str(el["id"])
        if osm_id in seen_osm:
            continue
        seen_osm.add(osm_id)

        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not lat or not lon:
            continue

        stores.append({
            "osm_id":   osm_id,
            "chain":    chain,
            "name":     name,
            "lat":      float(lat),
            "lon":      float(lon),
            "address":  " ".join(filter(None, [
                tags.get("addr:street", ""),
                tags.get("addr:housenumber", ""),
            ])).strip() or None,
            "zip_code": tags.get("addr:postcode", "").replace(" ", "") or None,
            "city":     tags.get("addr:city", "") or None,
        })

    return stores


def print_summary(stores: list[dict]) -> None:
    from collections import Counter
    counts = Counter(s["chain"] for s in stores)
    print(f"\nTotal stores found: {len(stores)}")
    print(f"{'Chain':<15} {'Count':>6}")
    print("-" * 23)
    for chain, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"{chain:<15} {count:>6}")


def main():
    parser = argparse.ArgumentParser(description="Populate Swedish stores DB from OSM")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and count but don't write to DB")
    args = parser.parse_args()

    stores = fetch_all_stores()
    print_summary(stores)

    if args.dry_run:
        print("\nDry run — no DB changes.")
        return

    db.init_db()
    n = db.stores_upsert_bulk(stores)
    total = db.stores_count()
    print(f"\nDB updated: {n} rows changed. Total in DB: {total}")
    print(f"Last updated: {db.stores_last_updated()}")


if __name__ == "__main__":
    main()
