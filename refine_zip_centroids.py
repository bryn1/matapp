#!/usr/bin/env python3
"""
refine_zip_centroids.py — Replace GeoNames city-level coords with precise
per-zip centroids computed from OSM addr:postcode nodes.

Memory-efficient: processes one tile at a time, writes to DB immediately
using a running (lat_sum, lon_sum, count) average — never holds all nodes.

Run after populate_zip_centroids.py (takes ~10–20 min):
    python3 refine_zip_centroids.py
    python3 refine_zip_centroids.py --resume   # skip already-done tiles
    python3 refine_zip_centroids.py --dry-run
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import requests

import db

# Sweden bounding box
LAT_MIN, LAT_MAX = 55.2, 69.1
LON_MIN, LON_MAX = 10.9, 24.2

LAT_STEPS = 8
LON_STEPS = 6

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

TILE_PROGRESS_FILE = Path(__file__).parent / "data" / ".zip_refine_progress"


def _tiles():
    lat_size = (LAT_MAX - LAT_MIN) / LAT_STEPS
    lon_size = (LON_MAX - LON_MIN) / LON_STEPS
    for i in range(LAT_STEPS):
        for j in range(LON_STEPS):
            yield (
                round(LAT_MIN + i * lat_size, 4),
                round(LON_MIN + j * lon_size, 4),
                round(LAT_MIN + (i + 1) * lat_size, 4),
                round(LON_MIN + (j + 1) * lon_size, 4),
            )


def _query_tile(s, w, n, e, retries=3) -> dict:
    """Fetch addr:postcode nodes within Sweden's administrative boundary (bbox pre-filter).
    Using area["ISO3166-1"="SE"] ensures Finnish/Norwegian nodes are excluded even
    in tiles that overlap neighbouring countries.
    Returns {zip_code: (lat_sum, lon_sum, count)} — memory-efficient accumulator.
    """
    query = (
        f"[out:json][timeout:60][bbox:{s},{w},{n},{e}];\n"
        f'area["ISO3166-1"="SE"][admin_level=2]->.sweden;\n'
        f'node(area.sweden)["addr:postcode"];\n'
        f"out;"
    )
    for attempt in range(retries):
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                r = requests.post(endpoint, data={"data": query}, timeout=65)
                if not r.text.strip():
                    raise ValueError("empty body")
                accum: dict[str, list] = {}
                for el in r.json().get("elements", []):
                    zc = el.get("tags", {}).get("addr:postcode", "").replace(" ", "")
                    if not zc:
                        continue
                    if zc not in accum:
                        accum[zc] = [0.0, 0.0, 0]
                    accum[zc][0] += el["lat"]
                    accum[zc][1] += el["lon"]
                    accum[zc][2] += 1
                return accum
            except Exception as ex:
                print(f"    {endpoint} attempt {attempt+1}: {ex}", flush=True)
        time.sleep(3)
    return {}


def _merge_into_db(accum: dict, conn: sqlite3.Connection) -> int:
    """Merge tile accumulator into zip_refine_work table. Returns rows updated."""
    if not accum:
        return 0
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zip_refine_work (
            zip_code  TEXT PRIMARY KEY,
            lat_sum   REAL NOT NULL DEFAULT 0,
            lon_sum   REAL NOT NULL DEFAULT 0,
            cnt       INTEGER NOT NULL DEFAULT 0
        )
    """)
    rows = [
        (zc, v[0], v[1], v[2], v[0], v[1], v[2])
        for zc, v in accum.items()
    ]
    conn.executemany("""
        INSERT INTO zip_refine_work (zip_code, lat_sum, lon_sum, cnt)
        VALUES (?,?,?,?)
        ON CONFLICT(zip_code) DO UPDATE SET
            lat_sum = lat_sum + excluded.lat_sum,
            lon_sum = lon_sum + excluded.lon_sum,
            cnt     = cnt     + excluded.cnt
    """, [(zc, v[0], v[1], v[2]) for zc, v in accum.items()])
    return len(rows)


def _apply_final_centroids(conn: sqlite3.Connection) -> int:
    """Compute final centroids from work table and update zip_centroids."""
    conn.execute("""
        UPDATE zip_centroids SET
            lat = ROUND((SELECT lat_sum/cnt FROM zip_refine_work w WHERE w.zip_code=zip_centroids.zip_code), 6),
            lon = ROUND((SELECT lon_sum/cnt FROM zip_refine_work w WHERE w.zip_code=zip_centroids.zip_code), 6)
        WHERE zip_code IN (SELECT zip_code FROM zip_refine_work WHERE cnt > 0)
    """)
    updated = conn.execute("SELECT changes()").fetchone()[0]
    conn.execute("DROP TABLE IF EXISTS zip_refine_work")
    return updated


def _load_progress() -> set:
    if TILE_PROGRESS_FILE.exists():
        return set(TILE_PROGRESS_FILE.read_text().splitlines())
    return set()


def _save_progress(done: set):
    TILE_PROGRESS_FILE.write_text("\n".join(sorted(done)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume",  action="store_true")
    args = parser.parse_args()

    all_tiles = list(_tiles())
    print(f"Grid: {LAT_STEPS}×{LON_STEPS} = {len(all_tiles)} tiles")

    if args.dry_run:
        print("Dry run — no changes.")
        return

    done_tiles = _load_progress() if args.resume else set()
    if done_tiles:
        print(f"Resuming: {len(done_tiles)} tiles already done")

    total_nodes = 0
    total_zips  = 0

    with db.get_connection() as conn:
        for idx, (s, w, n, e) in enumerate(all_tiles, 1):
            tile_key = f"{s},{w},{n},{e}"
            if tile_key in done_tiles:
                print(f"[{idx}/{len(all_tiles)}] skip (done)", flush=True)
                continue

            print(f"[{idx}/{len(all_tiles)}] ({s},{w})→({n},{e}) ", end="", flush=True)
            t0 = time.time()
            accum = _query_tile(s, w, n, e)
            elapsed = time.time() - t0

            node_count = sum(v[2] for v in accum.values())
            print(f"{node_count} nodes, {len(accum)} zips in {elapsed:.1f}s", flush=True)

            _merge_into_db(accum, conn)
            conn.commit()

            total_nodes += node_count
            total_zips   = conn.execute(
                "SELECT COUNT(*) FROM zip_refine_work"
            ).fetchone()[0] if node_count else total_zips

            done_tiles.add(tile_key)
            _save_progress(done_tiles)
            time.sleep(1.5)

        print(f"\nAll tiles done. Total nodes processed: {total_nodes}")
        print("Applying final centroids to zip_centroids table…")
        updated = _apply_final_centroids(conn)
        conn.commit()

    print(f"Updated {updated} zip codes with precise OSM centroids")

    if TILE_PROGRESS_FILE.exists():
        TILE_PROGRESS_FILE.unlink()

    with db.get_connection() as conn:
        distinct = conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT lat,lon FROM zip_centroids)"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM zip_centroids").fetchone()[0]
    print(f"Result: {distinct} distinct coord pairs for {total} zip codes")


if __name__ == "__main__":
    main()
