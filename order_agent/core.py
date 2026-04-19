"""
core.py — Order agent orchestrator.

Coordinates the full flow:
  1. Load shopping list from DB
  2. Login to store
  3. Search for all items (normalizer → store API)
  4. Fill cart (with idempotency check)
  5. Book pickup slot (preferred: Saturday)
  6. Write order_history row
  7. Send ntfy notification (always — even on error)

Usage:
    from order_agent.core import OrderAgent, AgentResult
    agent = OrderAgent(config)
    result = agent.run(week_num=13, year=2026, dry_run=False)
"""
from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

import db as matapp_db
from order_agent.db_additions import ensure_tables
from order_agent.normalizer import normalize_shopping_list
from order_agent.notifier import (
    build_error_notification,
    build_success_notification,
    send as notify_send,
)
from order_agent.stores.axfood import AxfoodAdapter
from order_agent.stores.base import Product, StoreAdapter, TimeSlot
from order_agent.stores.ica import IcaAdapter

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result of one order agent run."""
    success: bool
    chain: str
    week_num: int
    year: int
    total_sek: float = 0.0
    item_count: int = 0
    unavailable: list[str] = field(default_factory=list)
    substitutions: list[str] = field(default_factory=list)
    checkout_url: str = ""
    slot_time: str = ""
    error: Optional[str] = None
    dry_run: bool = False
    order_history_id: Optional[int] = None


def _make_adapter(chain: str, store_id: Optional[str]) -> StoreAdapter:
    """Factory: return the correct StoreAdapter for the given chain."""
    chain_lower = chain.lower()
    if chain_lower == "ica":
        return IcaAdapter(store_id=store_id)
    # Default: Axfood platform (willys, hemkop, citygross)
    return AxfoodAdapter(chain=chain_lower, store_id=store_id)


def _pick_preferred_slot(
    slots: list[TimeSlot],
    preferred_weekday: int = 6,   # 6=Saturday (ISO weekday)
    preferred_hour: int = 11,
) -> Optional[TimeSlot]:
    """
    Pick the best available pickup slot.
    Prefers slots on preferred_weekday at preferred_hour.
    Falls back to any available slot if none found.
    """
    available = [s for s in slots if s.available]
    if not available:
        return None

    def slot_score(s: TimeSlot) -> float:
        try:
            # Parse ISO datetime or date string
            dt_str = s.starts_at.replace("Z", "+00:00")
            # Try full ISO first, then date-only
            try:
                dt = datetime.datetime.fromisoformat(dt_str)
            except ValueError:
                dt = datetime.datetime.strptime(s.starts_at[:19], "%Y-%m-%dT%H:%M:%S")
            weekday_match = 1.0 if dt.isoweekday() == preferred_weekday else 0.0
            hour_diff = abs(dt.hour - preferred_hour)
            hour_score = max(0.0, 1.0 - hour_diff * 0.1)
            return weekday_match * 2 + hour_score
        except Exception:
            return 0.0

    return max(available, key=slot_score)


def _format_slot_time(slot: Optional[TimeSlot]) -> str:
    if not slot:
        return ""
    try:
        dt_str = slot.starts_at.replace("Z", "+00:00")
        try:
            dt = datetime.datetime.fromisoformat(dt_str)
        except ValueError:
            dt = datetime.datetime.strptime(slot.starts_at[:19], "%Y-%m-%dT%H:%M:%S")
        days = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
        return f"{days[dt.weekday()]} {dt.strftime('%d/%m %H:%M')}"
    except Exception:
        return slot.starts_at


class OrderAgent:
    """
    Orchestrates a single store's weekly order flow.

    Config keys used:
        order_agent.chain              (default: "willys")
        order_agent.chains             list of chains for price comparison (Phase 5)
        order_agent.ntfy_url           (default: "http://localhost:8090")
        order_agent.ntfy_topic         (required for notifications)
        order_agent.store_id           store ID for slot booking
        order_agent.preferred_slot_weekday  (default: 6)
        order_agent.preferred_slot_hour     (default: 11)
    """

    def __init__(self, config: dict, username: str, password: str):
        """
        Args:
            config:   the full matapp config dict (from config.json)
            username: store login username
            password: store login password
        """
        oa_cfg = config.get("order_agent", {})
        self.chain = oa_cfg.get("chain", "willys")
        self.chains = oa_cfg.get("chains", [self.chain, "hemkop"])  # for comparison
        self.store_id = oa_cfg.get("store_id") or None
        self.ntfy_url = oa_cfg.get("ntfy_url", "http://localhost:8090")
        self.ntfy_topic = oa_cfg.get("ntfy_topic", "")
        self.preferred_weekday = int(oa_cfg.get("preferred_slot_weekday", 6))
        self.preferred_hour = int(oa_cfg.get("preferred_slot_hour", 11))
        self._username = username
        self._password = password

    def run(
        self,
        shopping_list: list[dict],
        week_num: int,
        year: int,
        dry_run: bool = False,
        user_id: Optional[int] = None,
    ) -> AgentResult:
        """
        Run the full order flow for one week's shopping list.

        dry_run=True → login + search + simulate, no cart changes, no slot booking.
        dry_run=False → full live run (cart filled, slot booked, notification sent).

        Always writes to order_history (even on error).
        Always sends ntfy notification if ntfy_topic is configured.
        """
        ensure_tables()
        result = AgentResult(
            success=False,
            chain=self.chain,
            week_num=week_num,
            year=year,
            dry_run=dry_run,
        )

        adapter = _make_adapter(self.chain, self.store_id)

        # ── Step 1: Login ──────────────────────────────────────────────────────
        logger.info(f"[core] Step 1: login to {self.chain}")
        ok = adapter.login(self._username, self._password)
        if not ok:
            result.error = "Login failed"
            self._finish(result, adapter, user_id, notify=True)
            return result

        # ── Step 2: Search ─────────────────────────────────────────────────────
        logger.info(f"[core] Step 2: searching {len(shopping_list)} items")
        normalized = normalize_shopping_list(shopping_list)
        searchable = [n for n in normalized if not n.skip]

        matched_items: list[tuple[str, Product]] = []  # (original_name, product)
        unavailable: list[str] = []

        for norm in searchable:
            try:
                results = adapter.search(norm.query, norm.quantity_str)
                if results:
                    best = results[0]
                    if best.available:
                        matched_items.append((norm.original, best))
                    else:
                        unavailable.append(f"{norm.original} [OOS]")
                else:
                    unavailable.append(norm.original)
            except Exception as e:
                logger.warning(f"[core] Search error for '{norm.query}': {e}")
                unavailable.append(f"{norm.original} [search error]")

        result.unavailable = unavailable
        logger.info(
            f"[core] Search complete: {len(matched_items)} matched, "
            f"{len(unavailable)} unavailable"
        )

        if not matched_items:
            result.error = "No products found — search returned empty"
            self._finish(result, adapter, user_id, notify=True)
            return result

        # ── Step 2b: Price comparison (read-only, parallel) ───────────────────
        comparison_quotes = None
        if len(self.chains) > 1 and not dry_run:
            logger.info(f"[core] Step 2b: comparing prices across {self.chains}")
            try:
                from order_agent.compare import compare_stores, pick_cheapest
                comparison_quotes = compare_stores(
                    shopping_list,
                    chains=self.chains,
                )
                cheapest = pick_cheapest(comparison_quotes)
                if cheapest and cheapest.chain != self.chain:
                    logger.info(
                        f"[core] Cheaper option available: {cheapest.chain} "
                        f"({cheapest.total_sek:.0f} kr vs using {self.chain})"
                    )
                    # Still fill the configured chain — user chooses after seeing notification
            except Exception as e:
                logger.warning(f"[core] Price comparison failed (non-fatal): {e}")

        # ── Step 3: Fill cart ──────────────────────────────────────────────────
        if not dry_run:
            logger.info(f"[core] Step 3: filling cart ({len(matched_items)} items)")
            existing_codes = adapter.get_cart_product_codes()
            added = 0
            cart_error = False

            for orig, product in matched_items:
                if product.product_id in existing_codes:
                    logger.debug(f"[core] {product.product_id} already in cart, skipping")
                    continue
                ok_add = adapter.add_to_cart(product.product_id, qty=1)
                if ok_add:
                    added += 1
                else:
                    logger.warning(f"[core] Failed to add {product.product_id} ({orig})")
                    cart_error = True

            if cart_error and added == 0:
                # Complete failure — clear and bail
                adapter.clear_cart()
                result.error = "All add_to_cart calls failed — cart cleared"
                self._finish(result, adapter, user_id, notify=True)
                return result

            result.item_count = added
            result.total_sek = adapter.get_cart_total()
            result.checkout_url = adapter.get_checkout_url()
        else:
            result.item_count = len(matched_items)
            result.total_sek = sum(p.price_sek or 0.0 for _, p in matched_items)
            result.checkout_url = adapter.get_checkout_url()

        # ── Step 4: Book pickup slot ───────────────────────────────────────────
        if not dry_run and self.store_id:
            logger.info(f"[core] Step 4: booking pickup slot")
            slots = adapter.get_pickup_slots(self.store_id)
            best_slot = _pick_preferred_slot(slots, self.preferred_weekday, self.preferred_hour)
            if best_slot:
                ok_slot = adapter.select_slot(best_slot.slot_id)
                if ok_slot:
                    result.slot_time = _format_slot_time(best_slot)
                    logger.info(f"[core] Slot booked: {result.slot_time}")
                else:
                    logger.warning("[core] Slot booking failed — user must book manually")
            else:
                logger.warning("[core] No available slots found")
        else:
            if not self.store_id:
                logger.info("[core] No store_id — skipping slot booking")

        result.success = True
        self._finish(result, adapter, user_id, notify=True, comparison_quotes=comparison_quotes)
        return result

    def _finish(
        self,
        result: AgentResult,
        adapter: StoreAdapter,
        user_id: Optional[int],
        notify: bool,
        comparison_quotes=None,
    ) -> None:
        """
        Post-run: write order_history, send notification.
        On error: clear cart first.
        """
        # Clear cart on error (don't leave partial carts behind)
        if not result.success and not result.dry_run:
            logger.info("[core] Error path — clearing cart")
            try:
                adapter.clear_cart()
            except Exception as e:
                logger.warning(f"[core] Cart clear on error failed: {e}")

        # Write order_history
        try:
            conn = matapp_db.get_connection()
            cur = conn.cursor()
            items_json = json.dumps([
                {"product_id": "", "name": "", "price": 0.0}
            ])  # placeholder — full detail in Phase 5
            cur.execute(
                """
                INSERT INTO order_history
                  (user_id, week_num, year, chain, store_id, total_sek,
                   items_json, pickup_slot, status, checkout_url, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    result.week_num,
                    result.year,
                    result.chain,
                    adapter.store_id or "",
                    result.total_sek,
                    items_json,
                    result.slot_time,
                    "pending" if result.success else "failed",
                    result.checkout_url,
                    result.error,
                ),
            )
            conn.commit()
            result.order_history_id = cur.lastrowid
            logger.info(f"[core] order_history row {result.order_history_id} written")
        except Exception as e:
            logger.error(f"[core] Failed to write order_history: {e}")

        # Send ntfy notification
        if notify and self.ntfy_topic:
            self._notify(result, comparison_quotes=comparison_quotes)

    def _notify(self, result: AgentResult, comparison_quotes=None) -> None:
        if result.success and comparison_quotes and len(comparison_quotes) > 1:
            from order_agent.notifier import build_comparison_notification
            payload = build_comparison_notification(
                quotes=comparison_quotes,
                chosen_chain=result.chain,
                chosen_checkout_url=result.checkout_url,
                week_num=result.week_num,
            )
        elif result.success:
            payload = build_success_notification(
                chain=result.chain,
                store_name=result.chain.capitalize(),
                total_sek=result.total_sek,
                item_count=result.item_count,
                checkout_url=result.checkout_url,
                slot_time=result.slot_time,
                substitutions=result.substitutions,
                week_num=result.week_num,
            )
        else:
            payload = build_error_notification(
                chain=result.chain,
                error_msg=result.error or "unknown error",
                week_num=result.week_num,
            )

        mode_suffix = " [DRY-RUN]" if result.dry_run else ""
        payload.title += mode_suffix

        ok = notify_send(self.ntfy_url, self.ntfy_topic, payload)
        if not ok:
            logger.warning("[core] ntfy notification failed to send")
