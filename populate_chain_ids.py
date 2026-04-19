#!/usr/bin/env python3
"""
populate_chain_ids.py — Resolve and store chain-specific store IDs for all
stores in swedish_stores that don't yet have a chain_store_id.

Run after populate_stores.py:
    python3 populate_chain_ids.py                  # all chains
    python3 populate_chain_ids.py --chain willys   # one chain only
    python3 populate_chain_ids.py --dry-run        # show what would be resolved

APIs used:
  Axfood (willys, hemkop, tempo, handlarn) — /axfood/rest/search/store?q=...
  ICA                                       — handla.ica.se/api/store/v1/stores/
  Coop                                      — coop.se/api/ecommerce/store-selector
  Lidl  — national offers only, no per-store ID needed
  Citygross — citygross.se API

No public APIs: netto, spar, direkten
"""

import argparse
import math
import time
import logging
import sys

import requests

import db
from campaigns import _AXFOOD_DOMAINS, _HEADERS, _ica_find_store_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NO_API_CHAINS = {"netto", "spar", "direkten"}


def _axfood_resolve(store: dict, chain: str) -> str | None:
    domain = _AXFOOD_DOMAINS.get(chain, "https://www.willys.se")
    name = store["name"]
    city = store.get("city") or ""
    # Try full name, then city only
    for query in [name, city, name.split()[0]]:
        if not query.strip():
            continue
        try:
            r = requests.get(
                f"{domain}/axfood/rest/search/store",
                params={"q": query, "pageSize": 20},
                headers=_HEADERS, timeout=15,
            )
            r.raise_for_status()
            results = r.json().get("results") or []
        except Exception as e:
            logger.warning(f"  {chain} API error: {e}")
            return None

        name_lower = name.lower()
        # Best match: name contains our query AND lat/lon is close
        best = None
        best_dist = float("inf")
        for s in results:
            s_name = s.get("name", "").lower()
            gp = s.get("geoPoint") or {}
            s_lat  = gp.get("latitude") or s.get("latitude") or s.get("lat") or 0
            s_lon  = gp.get("longitude") or s.get("longitude") or s.get("lon") or 0
            if not s_lat:
                continue
            # Rough distance in km
            dist = math.sqrt((s_lat - store["lat"])**2 + (s_lon - store["lon"])**2) * 111
            if dist < best_dist:
                best_dist = dist
                best = s

        if best and best_dist < 2.0:  # within 2km — confident match
            return str(best["storeId"])

    return None


def _ica_resolve(store: dict) -> str | None:
    """ICA store ID stored for reference; campaigns require auth and can't be fetched."""
    return _ica_find_store_id(store["lat"], store["lon"])


_RESOLVERS = {
    "willys":   lambda s: _axfood_resolve(s, "willys"),
    "hemkop":   lambda s: _axfood_resolve(s, "hemkop"),
    "tempo":    lambda s: _axfood_resolve(s, "tempo"),
    "handlarn": lambda s: _axfood_resolve(s, "handlarn"),
    "ica":      _ica_resolve,  # ID stored for reference; campaigns need auth
    # coop, lidl, citygross, netto, spar, direkten: no accessible API
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", default=None, help="Only process this chain")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=9999)
    args = parser.parse_args()

    db.stores_migrate()

    chains = [args.chain] if args.chain else list(_RESOLVERS.keys())
    total_ok = total_fail = 0

    for chain in chains:
        if chain in NO_API_CHAINS:
            print(f"{chain}: no public API — skipping")
            continue

        resolver = _RESOLVERS.get(chain)
        if not resolver:
            print(f"{chain}: no resolver — skipping")
            continue

        stores = db.stores_needing_chain_id(chain)[:args.limit]
        print(f"\n{chain}: {len(stores)} stores need chain_store_id")

        ok = fail = 0
        for i, store in enumerate(stores, 1):
            print(f"  [{i}/{len(stores)}] {store['name']} ({store.get('city','')})", end=" ", flush=True)
            if args.dry_run:
                print("(dry-run)")
                continue

            chain_id = resolver(store)
            if chain_id:
                db.store_set_chain_id(store["id"], chain_id)
                print(f"→ {chain_id}")
                ok += 1
            else:
                print("→ NOT FOUND")
                fail += 1

            time.sleep(0.3)  # be polite

        total_ok += ok
        total_fail += fail
        print(f"  {chain}: {ok} resolved, {fail} not found")

    if not args.dry_run:
        print(f"\nDone. Total resolved: {total_ok}, not found: {total_fail}")


if __name__ == "__main__":
    main()
