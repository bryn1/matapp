#!/usr/bin/env python3
"""
populate_zip_centroids.py — Download all Swedish postal codes with precise
lat/lon from GeoNames and store in the local zip_centroids table.

Run once (or yearly — postal codes rarely change):
    python3 populate_zip_centroids.py
    python3 populate_zip_centroids.py --dry-run
"""

import argparse
import io
import sys
import zipfile

import requests

import db

GEONAMES_URL = "https://download.geonames.org/export/zip/SE.zip"


def fetch_and_parse() -> list[tuple]:
    """Download SE.zip from GeoNames, return list of (zip_code, place_name, lat, lon)."""
    print(f"Downloading {GEONAMES_URL} …")
    r = requests.get(GEONAMES_URL, timeout=30)
    r.raise_for_status()

    rows = []
    seen = {}  # zip_code → (lat, lon, place_name) — keep first occurrence

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        with zf.open("SE.txt") as f:
            for line in f:
                parts = line.decode("utf-8").rstrip("\n").split("\t")
                if len(parts) < 11:
                    continue
                zip_code   = parts[1].replace(" ", "")
                place_name = parts[2]
                try:
                    lat = float(parts[9])
                    lon = float(parts[10])
                except ValueError:
                    continue
                if zip_code not in seen:
                    seen[zip_code] = (lat, lon, place_name)

    rows = [(z, v[2], v[0], v[1]) for z, v in seen.items()]
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = fetch_and_parse()
    print(f"Parsed {len(rows)} unique zip codes")

    if args.dry_run:
        for r in rows[:5]:
            print(f"  {r[0]}  {r[1]:<25} lat={r[2]:.4f} lon={r[3]:.4f}")
        print("  …")
        print("Dry run — no DB changes.")
        return

    db.init_db()
    total = db.zip_upsert_bulk(rows)
    print(f"DB updated. Total zip codes in DB: {total}")


if __name__ == "__main__":
    main()
