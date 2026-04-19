"""
db_additions.py — Additive DB migrations for the order agent.

Only adds new tables — never modifies existing ones.
Safe to call multiple times (idempotent via CREATE TABLE IF NOT EXISTS).

New tables:
  store_credentials  — encrypted store login per user
  product_cache      — ingredient → store SKU mapping (expires weekly)
  order_history      — record of each agent run
"""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


MIGRATIONS: list[str] = [
    # ── store credentials ──────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS store_credentials (
        user_id                INTEGER REFERENCES users(id) ON DELETE CASCADE,
        chain                  TEXT NOT NULL,
        username_enc           TEXT NOT NULL,
        password_enc           TEXT NOT NULL,
        store_id               TEXT,
        preferred_slot_weekday INTEGER DEFAULT 6,
        preferred_slot_hour    INTEGER DEFAULT 11,
        PRIMARY KEY (user_id, chain)
    )
    """,

    # ── product name → store SKU cache (expires weekly) ───────────────────
    """
    CREATE TABLE IF NOT EXISTS product_cache (
        chain         TEXT NOT NULL,
        store_id      TEXT NOT NULL DEFAULT '',
        query         TEXT NOT NULL,
        product_id    TEXT NOT NULL,
        product_name  TEXT NOT NULL,
        price_sek     REAL,
        unit          TEXT,
        quantity_desc TEXT,
        cached_at     TEXT DEFAULT (datetime('now')),
        expires_at    TEXT,
        PRIMARY KEY (chain, store_id, query)
    )
    """,

    # ── order run history ──────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS order_history (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER REFERENCES users(id),
        week_num     INTEGER NOT NULL,
        year         INTEGER NOT NULL,
        chain        TEXT NOT NULL,
        store_id     TEXT NOT NULL DEFAULT '',
        total_sek    REAL,
        items_json   TEXT,
        pickup_slot  TEXT,
        status       TEXT DEFAULT 'pending',
        checkout_url TEXT,
        error_msg    TEXT,
        created_at   TEXT DEFAULT (datetime('now'))
    )
    """,
]


def run_migrations(conn: sqlite3.Connection) -> int:
    """
    Apply all order-agent migrations to the given connection.

    Returns the number of statements executed (0 if all tables already exist).
    Safe to call multiple times.
    """
    cur = conn.cursor()
    applied = 0
    for sql in MIGRATIONS:
        try:
            cur.execute(sql.strip())
            applied += 1
        except sqlite3.Error as e:
            logger.error(f"Migration failed: {e}\nSQL: {sql.strip()[:80]}")
            raise
    conn.commit()
    logger.info(f"order_agent DB migrations applied ({applied} statements)")
    return applied


def ensure_tables(db_path: str | None = None) -> None:
    """
    Convenience: open DB at db_path (or default matapp DB) and run migrations.
    """
    if db_path is None:
        import db as matapp_db
        conn = matapp_db.get_connection()
    else:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

    run_migrations(conn)
