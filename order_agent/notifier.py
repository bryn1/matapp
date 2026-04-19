"""
notifier.py — ntfy push notifications for the order agent.

Sends a rich notification with:
  - Per-store cart totals (or single-store)
  - Checkout deeplink(s)
  - List of substitutions needing review
  - Pickup slot time
  - Error state if agent failed

Uses the ntfy HTTP API: POST to {ntfy_url}/{topic}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class NotifyPayload:
    """Everything needed to send one notification."""
    title: str
    body: str
    tags: list[str] = field(default_factory=list)
    priority: int = 3           # 1=min … 5=urgent
    click_url: str = ""         # opens when notification is tapped
    actions: list[dict] = field(default_factory=list)  # ntfy action buttons


def send(ntfy_url: str, topic: str, payload: NotifyPayload) -> bool:
    """
    Send a notification via ntfy HTTP API.
    Returns True on success.
    """
    url = f"{ntfy_url.rstrip('/')}/{topic}"
    data = {
        "title": payload.title,
        "message": payload.body,
        "tags": payload.tags,
        "priority": payload.priority,
    }
    if payload.click_url:
        data["click"] = payload.click_url
    if payload.actions:
        data["actions"] = payload.actions

    try:
        resp = requests.post(url, json=data, timeout=15)
        if resp.status_code in (200, 201):
            logger.info(f"[notifier] Notification sent to {url}")
            return True
        logger.warning(f"[notifier] ntfy returned {resp.status_code}: {resp.text[:120]}")
        return False
    except requests.RequestException as e:
        logger.error(f"[notifier] Failed to send notification: {e}")
        return False


def build_success_notification(
    chain: str,
    store_name: str,
    total_sek: float,
    item_count: int,
    checkout_url: str,
    slot_time: str,
    substitutions: list[str],
    week_num: int,
) -> NotifyPayload:
    """Build success notification for a completed cart fill."""
    sub_note = ""
    if substitutions:
        sub_note = f"\n\n⚠️ {len(substitutions)} substitution(er) att granska:\n"
        sub_note += "\n".join(f"  • {s}" for s in substitutions[:5])
        if len(substitutions) > 5:
            sub_note += f"\n  … och {len(substitutions) - 5} till"

    slot_note = f"  Upphämtning: {slot_time}" if slot_time else ""

    body = (
        f"Varukorgen är fylld för vecka {week_num}.\n"
        f"  Butik: {store_name}\n"
        f"  Antal varor: {item_count}\n"
        f"  Totalt: {total_sek:.0f} kr\n"
        f"{slot_note}"
        f"{sub_note}"
    )

    actions = []
    if checkout_url:
        actions.append({
            "action": "view",
            "label": f"Betala hos {chain.capitalize()}",
            "url": checkout_url,
            "clear": True,
        })

    return NotifyPayload(
        title=f"🛒 Veckans mat klar — {total_sek:.0f} kr",
        body=body,
        tags=["shopping_cart", "white_check_mark"],
        priority=3,
        click_url=checkout_url,
        actions=actions,
    )


def build_comparison_notification(
    quotes: list,         # list[CartQuote] — imported at call site to avoid circular
    chosen_chain: str,
    chosen_checkout_url: str,
    week_num: int,
) -> NotifyPayload:
    """Build multi-store comparison notification."""
    from order_agent.compare import format_comparison_table
    table = format_comparison_table(quotes)

    chosen_quote = next((q for q in quotes if q.chain == chosen_chain and q.ok), None)
    total = chosen_quote.total_sek if chosen_quote else 0.0

    actions = []
    for q in quotes:
        if q.ok and q.checkout_url:
            actions.append({
                "action": "view",
                "label": f"{q.store_name.capitalize()} ({q.total_sek:.0f} kr)",
                "url": q.checkout_url,
            })

    return NotifyPayload(
        title=f"🛒 Veckans mat — {total:.0f} kr ({chosen_chain.capitalize()} billigast)",
        body=f"Prisjämförelse vecka {week_num}:\n\n{table}\n\nGranska och betala:",
        tags=["shopping_cart", "scales"],
        priority=3,
        click_url=chosen_checkout_url,
        actions=actions[:3],
    )


def build_error_notification(
    chain: str,
    error_msg: str,
    week_num: int,
) -> NotifyPayload:
    """Build error notification when agent fails."""
    return NotifyPayload(
        title=f"❌ Varukorgen misslyckades — vecka {week_num}",
        body=(
            f"Order-agenten kunde inte fylla varukorgen.\n"
            f"  Butik: {chain}\n"
            f"  Fel: {error_msg}\n\n"
            f"Handla manuellt denna vecka."
        ),
        tags=["x", "warning"],
        priority=4,
    )
