"""
compare.py — Multi-store price comparison engine.

Queries multiple store adapters in parallel for the same shopping list
and returns a CartQuote per store, sorted by total cost.

Usage:
    from order_agent.compare import compare_stores, format_comparison_table
    quotes = compare_stores(shopping_list, chains=["willys", "hemkop"])
    print(format_comparison_table(quotes))
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from dataclasses import dataclass, field
from typing import Optional

from order_agent.normalizer import normalize_shopping_list
from order_agent.stores.axfood import AxfoodAdapter
from order_agent.stores.base import CartQuote, CartItem, StoreAdapter

logger = logging.getLogger(__name__)

# Maximum time to wait for a single store's search pass (seconds)
STORE_TIMEOUT = 60.0

# Maximum parallel store workers (search only — no cart changes)
MAX_WORKERS = 3


def _make_adapter(chain: str, store_id: Optional[str] = None) -> StoreAdapter:
    from order_agent.core import _make_adapter as core_make_adapter
    return core_make_adapter(chain, store_id)


def _quote_one_store(
    chain: str,
    store_id: Optional[str],
    shopping_list: list[dict],
) -> CartQuote:
    """
    Search all items at one store and build a CartQuote.
    Read-only — no cart changes, no login required.
    """
    adapter = _make_adapter(chain, store_id)
    normalized = normalize_shopping_list(shopping_list)
    searchable = [n for n in normalized if not n.skip]

    quote = CartQuote(
        chain=chain,
        store_id=store_id or "",
        store_name=chain.capitalize(),
        checkout_url=adapter.get_checkout_url() if hasattr(adapter, 'get_checkout_url') else "",
    )

    for norm in searchable:
        try:
            results = adapter.search(norm.query, norm.quantity_str)
        except Exception as e:
            logger.warning(f"[compare/{chain}] search error for '{norm.query}': {e}")
            quote.unavailable.append(norm.original)
            continue

        if not results:
            quote.unavailable.append(norm.original)
            continue

        best = results[0]
        if not best.available:
            quote.unavailable.append(f"{norm.original} [OOS]")
            continue

        cart_item = CartItem(
            product=best,
            requested_quantity=1,
            original_query=norm.original,
        )
        quote.items.append(cart_item)
        quote.total_sek += best.price_sek or 0.0

    return quote


def compare_stores(
    shopping_list: list[dict],
    chains: list[str] | None = None,
    store_ids: dict[str, str] | None = None,
) -> list[CartQuote]:
    """
    Query all requested store chains in parallel (search-only, no auth needed).

    Args:
        shopping_list:  list of {item, quantity} dicts
        chains:         chain names to query (default: ["willys", "hemkop"])
        store_ids:      optional {chain: store_id} for store-specific pricing

    Returns:
        List of CartQuote sorted by total_sek ascending (cheapest first).
        Stores that error are included with error field set.
    """
    if chains is None:
        chains = ["willys", "hemkop"]
    if store_ids is None:
        store_ids = {}

    quotes: list[CartQuote] = []
    start = time.monotonic()

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(chains))) as pool:
        futures = {
            pool.submit(
                _quote_one_store,
                chain,
                store_ids.get(chain),
                shopping_list,
            ): chain
            for chain in chains
        }

        for future in as_completed(futures, timeout=STORE_TIMEOUT * len(chains)):
            chain = futures[future]
            try:
                quote = future.result(timeout=STORE_TIMEOUT)
                quotes.append(quote)
                logger.info(
                    f"[compare] {chain}: {quote.item_count} items, "
                    f"{quote.total_sek:.0f} kr, "
                    f"{len(quote.unavailable)} unavailable"
                )
            except TimeoutError:
                logger.error(f"[compare] {chain} timed out")
                quotes.append(CartQuote(
                    chain=chain, store_id="", store_name=chain.capitalize(),
                    error=f"Timed out after {STORE_TIMEOUT:.0f}s",
                ))
            except Exception as e:
                logger.error(f"[compare] {chain} error: {e}")
                quotes.append(CartQuote(
                    chain=chain, store_id="", store_name=chain.capitalize(),
                    error=str(e),
                ))

    elapsed = time.monotonic() - start
    logger.info(f"[compare] All stores done in {elapsed:.1f}s")

    # Sort: errors last, then by total ascending (cheapest first)
    quotes.sort(key=lambda q: (q.error is not None, q.total_sek))
    return quotes


def format_comparison_table(quotes: list[CartQuote]) -> str:
    """
    Format a human-readable comparison table.

    Example:
      ──────────────────────────────────────────
      Store        Items   Unavail   Total
      ──────────────────────────────────────────
      ICA Nära       48       2     834 kr  ✅ billigast
      Willys         47       3     867 kr
      Hemköp         49       1     891 kr
      ──────────────────────────────────────────
    """
    if not quotes:
        return "(no results)"

    lines = [
        f"  {'─'*52}",
        f"  {'Butik':<18} {'Varor':>6}  {'Ej hittad':>10}  {'Total':>10}",
        f"  {'─'*52}",
    ]

    cheapest_idx = next(
        (i for i, q in enumerate(quotes) if q.ok and q.total_sek > 0), None
    )

    for i, q in enumerate(quotes):
        if q.error:
            lines.append(
                f"  {q.store_name:<18} {'—':>6}  {'—':>10}  {'FEL':>10}  ⚠️  {q.error[:30]}"
            )
            continue
        badge = "  ✅ billigast" if i == cheapest_idx else ""
        unavail_str = str(len(q.unavailable)) if q.unavailable else "0"
        total_str = f"{q.total_sek:.0f} kr"
        lines.append(
            f"  {q.store_name:<18} {q.item_count:>6}  {unavail_str:>10}  {total_str:>10}{badge}"
        )

    lines.append(f"  {'─'*52}")
    return "\n".join(lines)


def pick_cheapest(quotes: list[CartQuote]) -> Optional[CartQuote]:
    """Return the cheapest available (non-error) quote, or None."""
    available = [q for q in quotes if q.ok and q.total_sek > 0]
    if not available:
        return None
    return min(available, key=lambda q: q.total_sek)
