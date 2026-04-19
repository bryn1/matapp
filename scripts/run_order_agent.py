#!/usr/bin/env python3
"""
run_order_agent.py — Entry point for the systemd order agent service.

Reads credentials from environment (loaded via EnvironmentFile in the service unit).
Reads config from config.json.
Runs the OrderAgent for the current week.
Sends ntfy notification on completion (success or failure).

Called by matapp-order-agent.service (Thursday 18:00 via timer).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys
from pathlib import Path

# Add matapp root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def main() -> int:
    # Load config
    config_path = ROOT / "config.json"
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Cannot load config.json: {e}")
        return 1

    oa_cfg = config.get("order_agent", {})
    chain = oa_cfg.get("chain", "willys")

    # Check credentials
    username = os.environ.get(f"{chain.upper()}_USERNAME", "")
    password = os.environ.get(f"{chain.upper()}_PASSWORD", "")
    if not username or not password:
        logger.warning(
            f"No credentials for {chain.upper()} — skipping. "
            f"Set {chain.upper()}_USERNAME and {chain.upper()}_PASSWORD in "
            f"~/.config/matapp/store_creds.env"
        )
        return 0  # Exit 0: not an error, just not configured

    # Get current week
    today = datetime.date.today()
    week_num, year = today.isocalendar()[1], today.year

    # Load shopping list from DB
    import db
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT item, quantity FROM weekly_shopping "
        "WHERE week_num=? AND year=? AND checked=0 AND in_pantry=0",
        (week_num, year),
    )
    shopping_list = [
        {"item": r["item"], "quantity": r["quantity"] or ""}
        for r in cur.fetchall()
    ]

    if not shopping_list:
        logger.warning(f"No shopping list for week {week_num}/{year} — skipping")
        return 0

    logger.info(
        f"Order agent starting: week {week_num}/{year}, "
        f"chain={chain}, {len(shopping_list)} items"
    )

    # Run agent
    from order_agent.core import OrderAgent
    agent = OrderAgent(config, username, password)
    result = agent.run(
        shopping_list=shopping_list,
        week_num=week_num,
        year=year,
        dry_run=False,
    )

    if result.success:
        logger.info(
            f"Order agent completed: {result.item_count} items, "
            f"{result.total_sek:.0f} kr, slot={result.slot_time}"
        )
        return 0
    else:
        logger.error(f"Order agent failed: {result.error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
