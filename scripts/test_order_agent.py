#!/usr/bin/env python3
"""
test_order_agent.py — Phase 1+2 test script for the order agent.

Phase 1 (search only, no auth):
    python3 scripts/test_order_agent.py
    python3 scripts/test_order_agent.py --store willys
    python3 scripts/test_order_agent.py --store hemkop --week 12 --year 2026
    python3 scripts/test_order_agent.py --store willys --query "nötfärs 600g"

Phase 2 (login + cart + slots):
    python3 scripts/test_order_agent.py --store willys --fill-cart --dry-run
        → login, search, compute what would be added — does NOT touch cart
    python3 scripts/test_order_agent.py --store willys --fill-cart
        → login, actually fill cart, print total, then CLEAR cart
    python3 scripts/test_order_agent.py --store willys --list-slots

Credentials via env vars:
    WILLYS_USERNAME=user@example.com WILLYS_PASSWORD=secret python3 ...
    HEMKOP_USERNAME=... HEMKOP_PASSWORD=...

Exit codes:
    0  pass (match rate ≥ 70%, or cart filled + cleared without error)
    1  fail
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time

# Make sure we can import from the matapp root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from order_agent.db_additions import ensure_tables
from order_agent.normalizer import normalize, normalize_shopping_list, SKIP_ITEMS
from order_agent.stores.axfood import AxfoodAdapter
from order_agent.stores.ica import IcaAdapter


def get_current_week() -> tuple[int, int]:
    today = datetime.date.today()
    return today.isocalendar()[1], today.year


def load_shopping_list(week_num: int, year: int) -> list[dict]:
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT item, quantity FROM weekly_shopping "
        "WHERE week_num=? AND year=? AND checked=0 AND in_pantry=0",
        (week_num, year),
    )
    return [{"item": r["item"], "quantity": r["quantity"] or ""} for r in cur.fetchall()]


# ── Phase 1: search-only test ─────────────────────────────────────────────────

def _make_adapter(store: str, store_id: str | None):
    """Return the correct adapter for the given store chain."""
    if store.lower() == "ica":
        return IcaAdapter(store_id=store_id)
    return AxfoodAdapter(chain=store, store_id=store_id)


def run_search_test(
    store: str,
    items: list[dict],
    store_id: str | None = None,
    verbose: bool = True,
) -> dict:
    adapter = _make_adapter(store, store_id)

    normalized = normalize_shopping_list(items)

    total = 0
    skipped = 0
    matched = 0
    no_match = 0
    errors = 0

    match_rows = []
    skip_rows = []
    no_match_rows = []

    for norm in normalized:
        if norm.skip:
            skipped += 1
            skip_rows.append(norm.original)
            continue

        total += 1
        try:
            results = adapter.search(norm.query, norm.quantity_str)
        except Exception as e:
            print(f"  ERROR searching '{norm.query}': {e}")
            errors += 1
            no_match_rows.append((norm.original, norm.query, str(e)))
            continue

        if results:
            matched += 1
            best = results[0]
            match_rows.append((norm.original, norm.query, best))
        else:
            no_match += 1
            no_match_rows.append((norm.original, norm.query, None))

    match_rate = matched / total if total > 0 else 0.0

    if verbose:
        _print_report(store, match_rows, skip_rows, no_match_rows, match_rate, total, skipped, errors)

    return {
        "total": total,
        "matched": matched,
        "skipped": skipped,
        "no_match": no_match,
        "errors": errors,
        "match_rate": match_rate,
        "match_rows": match_rows,
    }


def _print_report(
    store: str,
    match_rows: list,
    skip_rows: list,
    no_match_rows: list,
    match_rate: float,
    total: int,
    skipped: int,
    errors: int,
) -> None:
    print(f"\n{'='*65}")
    print(f"  Order Agent — Search Test — {store.upper()}")
    print(f"{'='*65}")

    if match_rows:
        print(f"\n✅  MATCHED ({len(match_rows)}/{total})")
        print(f"  {'Shopping list item':<28} {'Query':<22} {'Best match':<30} {'Price':>8}  {'Qty'}")
        print(f"  {'-'*28} {'-'*22} {'-'*30} {'-'*8}  {'-'*8}")
        for orig, query, p in match_rows:
            price_str = f"{p.price_sek:.2f} kr" if p.price_sek else "—"
            print(f"  {orig:<28} {query:<22} {p.name[:30]:<30} {price_str:>8}  {p.quantity_desc}")

    if no_match_rows:
        print(f"\n❌  NO MATCH ({len(no_match_rows)}/{total})")
        for orig, query, reason in no_match_rows:
            suffix = f" [{reason}]" if isinstance(reason, str) else ""
            print(f"  {orig:<28} → '{query}'{suffix}")

    if skip_rows:
        print(f"\n⏭   SKIPPED pantry staples ({skipped}): {', '.join(skip_rows)}")

    if errors:
        print(f"\n⚠️  ERRORS: {errors}")

    pct = match_rate * 100
    status = "PASS ✅" if match_rate >= 0.70 else "FAIL ❌"
    print(f"\n{'─'*65}")
    print(f"  Match rate: {len(match_rows)} / {total} = {pct:.0f}%  →  {status}")
    if match_rate < 0.70:
        print(f"  Confirmation requires ≥ 70% match rate.")
    print(f"{'='*65}\n")


def run_single_query(store: str, query_str: str) -> None:
    """Quick ad-hoc search for one item."""
    import re
    m = re.match(r'^(.+?)\s+(\d[\d.,]*\s*(?:g|kg|ml|dl|l|st|förp|pack))$', query_str, re.IGNORECASE)
    if m:
        item_name, quantity = m.group(1).strip(), m.group(2).strip()
    else:
        item_name, quantity = query_str.strip(), ""

    norm = normalize(item_name, quantity)
    adapter = _make_adapter(store, None)

    print(f"\nSearching {store.upper()} for: '{norm.query}' (hint: '{norm.quantity_str}')")
    if norm.skip:
        print("  → Pantry staple, would be skipped in full run.")
        return

    results = adapter.search(norm.query, norm.quantity_str)
    if not results:
        print("  → No results.")
        return

    print(f"\n  {'#':<3} {'Name':<45} {'Score':>6}  {'Price':>8}  {'Qty'}")
    print(f"  {'-'*3} {'-'*45} {'-'*6}  {'-'*8}  {'-'*10}")
    for i, p in enumerate(results, 1):
        price_str = f"{p.price_sek:.2f} kr" if p.price_sek else "—"
        avail = "" if p.available else " [OOS]"
        print(f"  {i:<3} {p.name[:45]:<45} {p.score:>6.3f}  {price_str:>8}  {p.quantity_desc}{avail}")
    print()


# ── Phase 2: cart test ────────────────────────────────────────────────────────

def get_credentials(store: str) -> tuple[str, str]:
    """
    Read credentials from environment.
    Env vars: WILLYS_USERNAME / WILLYS_PASSWORD, HEMKOP_USERNAME / HEMKOP_PASSWORD, etc.
    """
    prefix = store.upper()
    username = os.environ.get(f"{prefix}_USERNAME", "")
    password = os.environ.get(f"{prefix}_PASSWORD", "")
    return username, password


def run_fill_cart_test(
    store: str,
    items: list[dict],
    store_id: str | None,
    dry_run: bool,
) -> bool:
    """
    Phase 2 cart test.

    dry_run=True  → login + search, compute what would be added, print totals,
                    do NOT call add_to_cart or clear_cart.
    dry_run=False → actually add items to cart, print real total, then clear cart.

    Returns True on success.
    """
    username, password = get_credentials(store)
    if not username or not password:
        print(f"\n❌  No credentials for {store.upper()}.")
        print(f"    Set {store.upper()}_USERNAME and {store.upper()}_PASSWORD env vars.")
        return False

    adapter = _make_adapter(store, store_id)

    # ── 1. Login ──────────────────────────────────────────────────────────────
    print(f"\n[1/4] Logging in to {store.upper()}...")
    ok = adapter.login(username, password)
    if not ok:
        print("❌  Login failed. Check credentials.")
        return False
    print("✅  Login OK")

    # ── 2. Search for all items ───────────────────────────────────────────────
    print(f"\n[2/4] Searching for shopping list items...")
    result = run_search_test(store, items, store_id=store_id, verbose=True)
    if result["match_rate"] < 0.70:
        print("❌  Match rate too low — aborting cart fill.")
        return False

    match_rows = result["match_rows"]

    # ── 3. Cart fill (or dry-run simulation) ─────────────────────────────────
    mode_label = "DRY-RUN (no cart changes)" if dry_run else "LIVE CART FILL"
    print(f"\n[3/4] {mode_label}")
    print(f"  {'Item':<30} {'Product':<40} {'Price':>8}")
    print(f"  {'-'*30} {'-'*40} {'-'*8}")

    estimated_total = 0.0
    added = 0
    skipped_cart = 0

    if not dry_run:
        # Check what's already in cart (idempotency)
        existing_codes = adapter.get_cart_product_codes()
        if existing_codes:
            print(f"  ⚠️  Cart already has {len(existing_codes)} item(s) — skipping duplicates")
    else:
        existing_codes = set()

    for orig, query, product in match_rows:
        if not product.available:
            print(f"  ⏭  {orig:<30} [out of stock]")
            skipped_cart += 1
            continue

        price_str = f"{product.price_sek:.2f} kr" if product.price_sek else "—"
        print(f"  {'+':<2} {orig:<28} {product.name[:40]:<40} {price_str:>8}")
        estimated_total += product.price_sek or 0.0

        if not dry_run:
            if product.product_id in existing_codes:
                print(f"       (already in cart, skipping)")
                skipped_cart += 1
                continue
            ok = adapter.add_to_cart(product.product_id, qty=1)
            if ok:
                added += 1
            else:
                print(f"       ⚠️  add_to_cart failed for {product.product_id}")
        else:
            added += 1

    print(f"\n  {'DRY-RUN estimated' if dry_run else 'Items added'}: {added}")
    print(f"  Estimated total: {estimated_total:.2f} kr")

    # ── 4. Get real total and clear cart (live only) ──────────────────────────
    if not dry_run:
        real_total = adapter.get_cart_total()
        print(f"\n[4/4] Real cart total: {real_total:.2f} kr")
        print("  Clearing cart (test cleanup)...")
        cleared = adapter.clear_cart()
        if cleared:
            print("✅  Cart cleared successfully")
            # Verify it's actually empty
            time.sleep(1.0)
            remaining = adapter.get_cart_product_codes()
            if remaining:
                print(f"  ⚠️  Cart still has {len(remaining)} item(s) after clear — manual cleanup needed")
                return False
            print("✅  Cart confirmed empty")
        else:
            print("❌  Cart clear failed — MANUAL CLEAR NEEDED in Willys app")
            return False
    else:
        print(f"\n[4/4] Dry-run complete. No cart changes made.")

    print(f"\n{'='*65}")
    print(f"  Phase 2 cart test: {'DRY-RUN' if dry_run else 'LIVE'} — PASS ✅")
    print(f"{'='*65}\n")
    return True


# ── Phase 2: slot listing ─────────────────────────────────────────────────────

def run_list_slots(store: str, store_id: str | None) -> bool:
    """Login and list available pickup slots."""
    username, password = get_credentials(store)
    if not username or not password:
        print(f"\n❌  No credentials for {store.upper()}.")
        print(f"    Set {store.upper()}_USERNAME and {store.upper()}_PASSWORD env vars.")
        return False

    adapter = _make_adapter(store, store_id)

    print(f"\n[1/2] Logging in to {store.upper()}...")
    ok = adapter.login(username, password)
    if not ok:
        print("❌  Login failed.")
        return False
    print("✅  Login OK")

    sid = store_id or adapter.store_id
    if not sid:
        print("❌  No store_id — use --store-id or set store_id in adapter")
        return False

    print(f"\n[2/2] Fetching pickup slots for store {sid}...")
    slots = adapter.get_pickup_slots(sid)

    if not slots:
        print("  No slots returned. The API may require a different endpoint or auth.")
        print("  (This is not a blocking failure — slot booking is Phase 3)")
        return True  # Non-fatal: slots API may need session cookie from actual purchase flow

    available = [s for s in slots if s.available]
    print(f"\n  Total slots: {len(slots)}, Available: {len(available)}")
    print(f"\n  {'Slot ID':<20} {'Start':<25} {'End':<25} {'Avail':<6} {'Cap'}")
    print(f"  {'-'*20} {'-'*25} {'-'*25} {'-'*6} {'-'*6}")
    for s in slots[:20]:
        cap = str(s.capacity_remaining) if s.capacity_remaining is not None else "—"
        avail = "✅" if s.available else "❌"
        print(f"  {s.slot_id:<20} {s.starts_at:<25} {s.ends_at:<25} {avail:<6} {cap}")

    if slots:
        saturday_slots = [s for s in available if "Sat" in s.starts_at or "lör" in s.starts_at.lower()]
        if saturday_slots:
            print(f"\n  Saturday slots available: {len(saturday_slots)}")

    print(f"\n✅  Slot listing complete")
    return True


# ── Phase 5: multi-store comparison ──────────────────────────────────────────

def run_compare_test(items: list[dict], week_num: int, year: int) -> bool:
    """
    Phase 5: query Willys + Hemköp in parallel, print comparison table.
    Read-only — no login, no cart changes.
    """
    from order_agent.compare import compare_stores, format_comparison_table, pick_cheapest

    chains = ["willys", "hemkop"]
    print(f"\n{'='*65}")
    print(f"  Phase 5 — Multi-Store Comparison")
    print(f"  Week {week_num}/{year}, {len(items)} items, chains: {chains}")
    print(f"{'='*65}")

    import time
    t0 = time.monotonic()
    quotes = compare_stores(items, chains=chains)
    elapsed = time.monotonic() - t0

    print(f"\n  Completed in {elapsed:.1f}s\n")
    print(format_comparison_table(quotes))

    cheapest = pick_cheapest(quotes)
    if cheapest:
        print(f"\n  Cheapest: {cheapest.store_name} — {cheapest.total_sek:.0f} kr")
        if cheapest.checkout_url:
            print(f"  Checkout: {cheapest.checkout_url}")

    # Confirm: all chains below 90s, no errors, ≥ 70% match rate each
    ok = True
    for q in quotes:
        if q.error:
            print(f"  ❌ {q.store_name}: {q.error}")
            ok = False
            continue
        total = q.item_count + len(q.unavailable)
        rate = q.item_count / total if total > 0 else 0
        status = "✅" if rate >= 0.70 else "❌"
        print(f"  {status} {q.store_name}: {rate*100:.0f}% match rate")
        if rate < 0.70:
            ok = False

    if elapsed > 90:
        print(f"  ⚠️  Comparison took {elapsed:.0f}s > 90s limit")
        ok = False

    status_str = "PASS ✅" if ok else "FAIL ❌"
    print(f"\n  Phase 5 comparison test: {status_str}")
    print(f"{'='*65}\n")
    return ok


# ── Phase 3: full end-to-end run ─────────────────────────────────────────────

def run_full_run_test(
    store: str,
    items: list[dict],
    store_id: str | None,
    week_num: int,
    year: int,
    dry_run: bool = False,
) -> bool:
    """
    Phase 3 full-run test.
    Uses OrderAgent orchestrator (login → search → cart → slot → ntfy).
    Credentials from env vars. Config from config.json.
    """
    username, password = get_credentials(store)
    if not username or not password:
        print(f"\n❌  No credentials for {store.upper()}.")
        print(f"    Set {store.upper()}_USERNAME and {store.upper()}_PASSWORD env vars.")
        return False

    # Load config
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    try:
        with open(config_path) as f:
            import json as _json
            config = _json.load(f)
    except Exception as e:
        print(f"❌  Could not load config.json: {e}")
        return False

    # Override chain/store_id from CLI args
    if "order_agent" not in config:
        config["order_agent"] = {}
    config["order_agent"]["chain"] = store
    if store_id:
        config["order_agent"]["store_id"] = store_id

    from order_agent.core import OrderAgent

    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"\n{'='*65}")
    print(f"  Phase 3 Full Run — {store.upper()} — {mode}")
    print(f"  Week {week_num}/{year}, {len(items)} items")
    print(f"{'='*65}")

    agent = OrderAgent(config, username, password)
    result = agent.run(
        shopping_list=items,
        week_num=week_num,
        year=year,
        dry_run=dry_run,
    )

    print(f"\n  Success: {result.success}")
    print(f"  Chain: {result.chain}")
    print(f"  Items: {result.item_count}")
    print(f"  Total: {result.total_sek:.2f} kr")
    print(f"  Slot: {result.slot_time or '(none)'}")
    print(f"  Checkout: {result.checkout_url or '(none)'}")
    print(f"  Order history ID: {result.order_history_id}")
    if result.unavailable:
        print(f"  Unavailable ({len(result.unavailable)}): {', '.join(result.unavailable[:5])}")
    if result.error:
        print(f"  Error: {result.error}")

    ntfy_topic = config.get("order_agent", {}).get("ntfy_topic", "")
    if not ntfy_topic:
        print(f"\n  ⚠️  ntfy_topic not set in config.json — notification not sent")
        print(f"     Set order_agent.ntfy_topic in config.json to enable push notifications")

    status = "PASS ✅" if result.success else "FAIL ❌"
    print(f"\n  Phase 3 full run: {status}")
    print(f"{'='*65}\n")
    return result.success


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Order agent Phase 1+2 test")
    parser.add_argument("--store", default="willys",
                        choices=["willys", "hemkop", "citygross", "ica"],
                        help="Store chain (default: willys)")
    parser.add_argument("--store-id", default=None,
                        help="Store ID for store-specific pricing/slots")
    parser.add_argument("--week", type=int, default=None,
                        help="Week number (default: current week)")
    parser.add_argument("--year", type=int, default=None,
                        help="Year (default: current year)")
    parser.add_argument("--query", default=None,
                        help="Single item query (search test only)")
    # Phase 2 flags
    parser.add_argument("--fill-cart", action="store_true",
                        help="Phase 2: login and fill cart (requires credentials)")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --fill-cart: simulate without touching cart")
    parser.add_argument("--list-slots", action="store_true",
                        help="Phase 2: login and list pickup slots")
    parser.add_argument("--full-run", action="store_true",
                        help="Phase 3: full end-to-end (cart + slot + ntfy notification)")
    parser.add_argument("--all-stores", action="store_true",
                        help="Phase 5: compare all configured stores")
    parser.add_argument("--compare", action="store_true",
                        help="With --all-stores: print comparison table and exit (no cart)")
    args = parser.parse_args()

    # Ensure order-agent DB tables exist
    ensure_tables()

    if args.query:
        run_single_query(args.store, args.query)
        return 0

    week_num, year = get_current_week()
    if args.week:
        week_num = args.week
    if args.year:
        year = args.year

    # Slot listing doesn't need the shopping list
    if args.list_slots:
        ok = run_list_slots(args.store, args.store_id)
        return 0 if ok else 1

    print(f"Loading shopping list: week {week_num}/{year}")
    items = load_shopping_list(week_num, year)

    if not items:
        print(f"No unchecked items in shopping list for week {week_num}/{year}.")
        print("Tip: use --week / --year to specify a different week.")
        return 1

    print(f"Found {len(items)} items.")

    if args.dry_run and not args.fill_cart and not args.full_run:
        print("⚠️  --dry-run has no effect without --fill-cart or --full-run. Running search test instead.")

    if args.all_stores or args.compare:
        ok = run_compare_test(
            items=items,
            week_num=week_num,
            year=year,
        )
        return 0 if ok else 1

    if args.full_run:
        ok = run_full_run_test(
            store=args.store,
            items=items,
            store_id=args.store_id,
            week_num=week_num,
            year=year,
            dry_run=args.dry_run,
        )
        return 0 if ok else 1

    if args.fill_cart:
        ok = run_fill_cart_test(
            store=args.store,
            items=items,
            store_id=args.store_id,
            dry_run=args.dry_run,
        )
        return 0 if ok else 1

    # Default: Phase 1 search test
    result = run_search_test(
        store=args.store,
        items=items,
        store_id=args.store_id,
    )
    return 0 if result["match_rate"] >= 0.70 else 1


if __name__ == "__main__":
    sys.exit(main())
