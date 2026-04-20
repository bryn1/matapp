#!/usr/bin/env python3
"""
db.py — SQLite database for matapp.

Tables:
- recipe_history: all generated recipes with household rating (1-7)
- recipe_catalog: ever-growing personal recipe catalog scraped from Swedish sites
- weekly_shopping: per-week shopping list (persistent across sessions)
- shopping_memory: tracks purchase frequency per item
- staples: household staples with restock intervals
"""

import json
import logging
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DEFAULT_DB = BASE_DIR / "data" / "matapp.db"


def get_connection(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DEFAULT_DB) -> None:
    """Create all tables if they don't exist."""
    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recipe_history (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                data_json   TEXT NOT NULL,
                source_url  TEXT,
                week_num    INTEGER,
                year        INTEGER,
                kid1_liked  INTEGER DEFAULT NULL,
                kid2_liked  INTEGER DEFAULT NULL,
                adult_liked INTEGER DEFAULT NULL,
                rating      INTEGER DEFAULT NULL,   -- 1-7 household scale
                key_ingredients TEXT DEFAULT NULL,  -- JSON array
                times_cooked INTEGER DEFAULT 0,
                last_cooked TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS recipe_catalog (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                url             TEXT UNIQUE NOT NULL,
                source          TEXT,
                diet_type       TEXT,
                site_rating     REAL,
                ingredients     TEXT,
                key_ingredients TEXT,
                instructions    TEXT,
                total_time_min  INTEGER,
                times_used      INTEGER DEFAULT 0,
                last_used       TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_catalog_diet ON recipe_catalog(diet_type);
            CREATE INDEX IF NOT EXISTS idx_catalog_rating ON recipe_catalog(site_rating);

            CREATE TABLE IF NOT EXISTS weekly_shopping (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                week_num     INTEGER NOT NULL,
                year         INTEGER NOT NULL,
                item         TEXT NOT NULL,
                quantity     TEXT,
                category     TEXT,
                on_sale      INTEGER DEFAULT 0,
                checked      INTEGER DEFAULT 0,
                added_manually INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(week_num, year, item)
            );

            CREATE TABLE IF NOT EXISTS shopping_memory (
                item              TEXT PRIMARY KEY,
                last_bought       TEXT,
                times_bought      INTEGER DEFAULT 0,
                avg_interval_days REAL,
                bought_dates      TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS staples (
                item         TEXT PRIMARY KEY,
                interval_days INTEGER NOT NULL,
                last_bought  TEXT
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                willys_code   TEXT,
                product_name  TEXT NOT NULL,
                normalized    TEXT NOT NULL,
                price         REAL,
                compare_price REAL,
                unit          TEXT,
                scraped_date  TEXT DEFAULT (date('now')),
                UNIQUE(normalized, scraped_date)
            );
            CREATE INDEX IF NOT EXISTS idx_price_history ON price_history(normalized);

            CREATE TABLE IF NOT EXISTS price_watchlist (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                query        TEXT NOT NULL,
                display_name TEXT NOT NULL,
                added_date   TEXT DEFAULT (date('now')),
                UNIQUE(query)
            );

            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                kdf_salt      TEXT NOT NULL,
                is_admin      INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS user_data (
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                data_type  TEXT NOT NULL,
                blob       TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, data_type)
            );

            CREATE TABLE IF NOT EXISTS store_geo_cache (
                zip_code    TEXT PRIMARY KEY,
                city        TEXT,
                lat         REAL,
                lon         REAL,
                stores_json TEXT NOT NULL,
                fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
                refresh_at  TEXT NOT NULL DEFAULT (datetime('now', '+7 days'))
            );

            CREATE TABLE IF NOT EXISTS zip_centroids (
                zip_code   TEXT PRIMARY KEY,
                place_name TEXT NOT NULL,
                lat        REAL NOT NULL,
                lon        REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS swedish_stores (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                osm_id     TEXT UNIQUE,
                chain      TEXT NOT NULL,
                name       TEXT NOT NULL,
                lat        REAL NOT NULL,
                lon        REAL NOT NULL,
                address        TEXT,
                zip_code       TEXT,
                city           TEXT,
                chain_store_id TEXT,
                fetched_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_swedish_stores_chain ON swedish_stores(chain);
            CREATE INDEX IF NOT EXISTS idx_swedish_stores_lat   ON swedish_stores(lat);
            CREATE INDEX IF NOT EXISTS idx_swedish_stores_lon   ON swedish_stores(lon);

            CREATE INDEX IF NOT EXISTS idx_recipe_history_week
                ON recipe_history(year, week_num);
            CREATE INDEX IF NOT EXISTS idx_weekly_shopping_week
                ON weekly_shopping(year, week_num);

            CREATE TABLE IF NOT EXISTS ingredient_standard_amounts (
                ingredient TEXT PRIMARY KEY,
                quantity_4p TEXT NOT NULL,
                unit TEXT NOT NULL DEFAULT ''
            );
        
            CREATE TABLE IF NOT EXISTS pantry (
                item          TEXT PRIMARY KEY COLLATE NOCASE,
                quantity      TEXT,
                unit          TEXT,
                in_stock      INTEGER NOT NULL DEFAULT 1,
                last_updated  TEXT NOT NULL DEFAULT (date('now'))
            );
""")
        _migrate(conn)
    logger.info(f"Database initialized at {db_path}")


def _seed_standard_amounts(conn: sqlite3.Connection) -> None:
    """Seed typical ingredient amounts for 4 servings (Swedish home cooking)."""
    amounts = [
        # Kött & fisk
        ("nötfärs", "600 g", "g"), ("köttfärs", "600 g", "g"),
        ("kycklingfilé", "600 g", "g"), ("kycklingbröst", "600 g", "g"),
        ("kycklinglår", "800 g", "g"), ("kyckling", "800 g", "g"),
        ("lax", "600 g", "g"), ("laxfilé", "600 g", "g"),
        ("torskfilé", "600 g", "g"), ("torsk", "600 g", "g"),
        ("fläskfilé", "600 g", "g"), ("fläskfärs", "600 g", "g"),
        ("lammfärs", "600 g", "g"), ("högrev", "800 g", "g"),
        ("bacon", "150 g", "g"), ("skinka", "200 g", "g"),
        ("falukorv", "500 g", "g"), ("korv", "500 g", "g"),
        ("räkor", "300 g", "g"), ("tonfisk", "2 burkar", "burkar"),
        # Pasta & ris & spannmål
        ("pasta", "400 g", "g"), ("spaghetti", "400 g", "g"),
        ("tagliatelle", "400 g", "g"), ("penne", "400 g", "g"),
        ("ris", "4 dl", "dl"), ("couscous", "3 dl", "dl"),
        ("bulgur", "3 dl", "dl"), ("quinoa", "3 dl", "dl"),
        ("nudlar", "300 g", "g"),
        # Grönsaker
        ("lök", "2 st", "st"), ("gul lök", "2 st", "st"),
        ("rödlök", "1 st", "st"), ("purjolök", "1 st", "st"),
        ("vitlök", "3 klyftor", "klyftor"),
        ("tomat", "4 st", "st"), ("tomater", "4 st", "st"),
        ("körsbärstomater", "250 g", "g"),
        ("paprika", "2 st", "st"), ("röd paprika", "2 st", "st"),
        ("gurka", "1 st", "st"), ("zucchini", "1 st", "st"),
        ("broccoli", "1 st", "st"), ("blomkål", "1 st", "st"),
        ("morot", "3 st", "st"), ("morötter", "3 st", "st"),
        ("potatis", "800 g", "g"), ("sötpotatis", "600 g", "g"),
        ("spenat", "100 g", "g"), ("majs", "1 burk", "burk"),
        ("selleri", "2 stjälkar", "stjälkar"),
        ("fänkål", "1 st", "st"), ("aubergine", "1 st", "st"),
        ("kål", "500 g", "g"), ("vitkål", "500 g", "g"),
        ("rödbeta", "3 st", "st"),
        # Mejeri & ägg
        ("ägg", "4 st", "st"), ("äggula", "2 st", "st"),
        ("smör", "50 g", "g"), ("margarin", "50 g", "g"),
        ("mjölk", "2 dl", "dl"), ("grädde", "2 dl", "dl"),
        ("vispgrädde", "2 dl", "dl"), ("crème fraiche", "200 g", "g"),
        ("crème fraîche", "200 g", "g"), ("gräddfil", "200 g", "g"),
        ("yoghurt", "2 dl", "dl"), ("filmjölk", "2 dl", "dl"),
        ("kvarg", "200 g", "g"), ("keso", "250 g", "g"),
        ("ost", "100 g", "g"), ("parmesanost", "50 g", "g"),
        ("parmesan", "50 g", "g"), ("mozzarella", "125 g", "g"),
        ("fetaost", "150 g", "g"), ("feta", "150 g", "g"),
        ("halloumi", "250 g", "g"), ("ricotta", "250 g", "g"),
        ("mascarpone", "250 g", "g"),
        # Konserver & torrvaror
        ("tomatsås", "400 g", "g"), ("tomatpuré", "2 msk", "msk"),
        ("krossade tomater", "400 g", "g"), ("hela tomater", "400 g", "g"),
        ("kokosmjölk", "400 ml", "ml"), ("kikärtor", "400 g", "g"),
        ("linser", "3 dl", "dl"), ("vita bönor", "400 g", "g"),
        ("svarta bönor", "400 g", "g"), ("kidney bönor", "400 g", "g"),
        ("kycklingfond", "2 msk", "msk"), ("köttfond", "2 msk", "msk"),
        ("fiskfond", "2 msk", "msk"), ("kalvfond", "2 msk", "msk"),
        ("grönsaksfond", "2 msk", "msk"), ("buljong", "1 tärning", "tärning"),
        ("olivolja", "3 msk", "msk"), ("rapsolja", "3 msk", "msk"),
        ("soja", "2 msk", "msk"), ("sojaost", "2 msk", "msk"),
        ("fisksås", "2 msk", "msk"), ("worcestershiresås", "1 msk", "msk"),
        ("ketchup", "2 msk", "msk"), ("senap", "1 msk", "msk"),
        ("majonnäs", "2 msk", "msk"), ("honung", "2 msk", "msk"),
        ("citron", "1 st", "st"), ("lime", "1 st", "st"),
        # Bröd & bakverk
        ("bröd", "1 limpa", "limpa"), ("tortilla", "4 st", "st"),
        ("pitabröd", "4 st", "st"), ("naanbröd", "4 st", "st"),
        # Frukt
        ("äpple", "2 st", "st"), ("päron", "2 st", "st"),
        ("banan", "2 st", "st"), ("mango", "1 st", "st"),
        ("avokado", "2 st", "st"), ("apelsin", "2 st", "st"),
        # Mjöl & bakning
        ("vetemjöl", "3 dl", "dl"), ("mjöl", "3 dl", "dl"),
        ("strösocker", "1 dl", "dl"), ("salt", "1 tsk", "tsk"),
        ("svartpeppar", "½ tsk", "tsk"), ("bakpulver", "2 tsk", "tsk"),
        ("vaniljsocker", "1 tsk", "tsk"), ("kakao", "3 msk", "msk"),
        ("havregryn", "2 dl", "dl"), ("ströbröd", "1 dl", "dl"),
        ("mandelmassa", "200 g", "g"), ("blockchoklad", "100 g", "g"),
        # Kryddor (pinch amounts)
        ("saffran", "½ g", "g"), ("spiskummin", "1 tsk", "tsk"),
        ("koriander", "1 tsk", "tsk"), ("paprikapulver", "1 tsk", "tsk"),
        ("oregano", "1 tsk", "tsk"), ("timjan", "1 tsk", "tsk"),
        ("rosmarin", "1 tsk", "tsk"), ("basilika", "1 tsk", "tsk"),
        ("dill", "1 kruka", "kruka"), ("persilja", "1 kruka", "kruka"),
        ("ingefära", "2 cm", "cm"), ("chili", "1 st", "st"),
        ("chilipeppar", "1 st", "st"), ("lagerblad", "2 st", "st"),
        ("curryblandning", "2 tsk", "tsk"), ("curry", "2 tsk", "tsk"),
        ("röd currypasta", "2 msk", "msk"), ("grön currypasta", "2 msk", "msk"),
        # Nötter & frön
        ("mandlar", "50 g", "g"), ("valnötter", "50 g", "g"),
        ("pinjenötter", "50 g", "g"), ("solrosfrön", "50 g", "g"),
        ("sesamfrön", "2 msk", "msk"),
        # Övrigt
        ("mat- och baksmör", "50 g", "g"),
        ("soltorkade tomater", "100 g", "g"),
        ("kapris", "2 msk", "msk"), ("oliver", "100 g", "g"),
        ("vitt vin", "1 dl", "dl"), ("rött vin", "1 dl", "dl"),
        ("vinäger", "2 msk", "msk"), ("balsamvinäger", "2 msk", "msk"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO ingredient_standard_amounts (ingredient, quantity_4p, unit) VALUES (?,?,?)",
        amounts
    )



INGREDIENT_CANONICAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredient_canonical (
    variant TEXT PRIMARY KEY COLLATE NOCASE,
    canonical TEXT NOT NULL
)
"""


_INGREDIENT_CANONICAL_SEED = [
    # Milk variants
    ("lättmjölk", "mjölk"),
    ("mellanmjölk", "mjölk"),
    ("standardmjölk", "mjölk"),
    ("laktosfri mjölk", "mjölk"),
    ("havremjölk", "mjölk"),
    ("sojamjölk", "mjölk"),
    # Feta
    ("feta", "fetaost"),
    ("fetaost i tärningar", "fetaost"),
    ("grekisk fetaost", "fetaost"),
    # Onion
    ("rödlök", "lök"),
    ("gul lök", "lök"),
    ("salladslök", "lök"),
    ("vitlöksklyfta", "vitlök"),
    # Tomato
    ("körsbärstomater", "tomat"),
    ("kvisttomater", "tomat"),
    ("cocktailtomat", "tomat"),
    ("tomat på burk", "krossade tomater"),
    # Cream/fraiche
    ("smetana", "creme fraiche"),
    ("gräddfil", "creme fraiche"),
    ("havrefraiche", "creme fraiche"),
    ("matlagningsgrädde", "grädde"),
    ("vispgrädde", "grädde"),
    ("kaffegrädde", "grädde"),
    ("havregrädde", "grädde"),
    ("sojagrädde", "grädde"),
    # Cheese variants
    ("parmesan", "parmesanost"),
    ("philadelphia", "färskost"),
    ("cream cheese", "färskost"),
    # Butter substitutes
    ("bregott", "smör"),
    ("lätta", "smör"),
    # Cucumber
    ("slanggurka", "gurka"),
    ("minigurka", "gurka"),
    # Eggs
    ("små ägg", "ägg"),
    ("stora ägg", "ägg"),
    ("medelstora ägg", "ägg"),
]


def _seed_ingredient_canonical(conn: sqlite3.Connection) -> None:
    """Seeds ingredient_canonical with common Swedish grocery variant → canonical mappings."""
    for variant, canonical in _INGREDIENT_CANONICAL_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO ingredient_canonical (variant, canonical) VALUES (?, ?)",
            (variant, canonical),
        )


_PACK_GRAMS_SEED = [
    ("fetaost", 200),
    ("halloumi", 250),
    ("smör", 250),
    ("mjöl", 500),
    ("pasta", 500),
    ("krossade tomater", 400),
    ("krossade tomat", 400),
    ("tomatpuré", 70),
    ("jäst", 50),
    ("bacon", 140),
    ("rökt skinka", 150),
    ("salami", 80),
    ("parmesanost", 80),
    ("färskost", 200),
    ("kokosmjölk", 400),
]


def _seed_pack_grams(conn: sqlite3.Connection) -> None:
    """Seeds pack_grams in ingredient_standard_amounts for common pack sizes."""
    for ingredient, pack_grams in _PACK_GRAMS_SEED:
        conn.execute(
            "UPDATE ingredient_standard_amounts SET pack_grams = ? WHERE ingredient = ? AND pack_grams IS NULL",
            (pack_grams, ingredient),
        )


def get_canonical_name(item: str, db_path: Path = DEFAULT_DB) -> str | None:
    """Return the canonical name for `item` if a mapping exists, else None."""
    if not item:
        return None
    key = item.lower().strip()
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT canonical FROM ingredient_canonical WHERE variant = ?",
            (key,),
        ).fetchone()
    return row["canonical"] if row else None


def resolve_pack_to_grams(item: str, count: float = 1.0,
                          db_path: Path = DEFAULT_DB) -> int | None:
    """
    Resolve an ingredient + pack count to grams.
    Looks up canonical name first, then reads pack_grams from
    ingredient_standard_amounts. Returns None if no mapping or no pack size.
    """
    if not item:
        return None
    canonical = get_canonical_name(item, db_path) or item.lower().strip()
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT pack_grams FROM ingredient_standard_amounts "
            "WHERE ingredient = ? AND pack_grams IS NOT NULL",
            (canonical,),
        ).fetchone()
    if not row:
        return None
    return int(count * row["pack_grams"])



def lookup_standard_amount(ingredient_name: str, servings: int = 4,
                           db_path: Path = DEFAULT_DB) -> str:
    """Return standard quantity string for ingredient scaled to servings.

    Tries exact match first, then partial match on key substring.
    Returns empty string if no match found.
    """
    import re
    name = ingredient_name.strip().lower()
    # Remove parentheticals like "kycklingfilé(er)"
    name = re.sub(r'\s*\([^)]*\)', '', name).strip()

    with get_connection(db_path) as conn:
        # 1. Exact match
        row = conn.execute(
            "SELECT quantity_4p FROM ingredient_standard_amounts WHERE ingredient = ?",
            (name,)
        ).fetchone()
        if not row:
            # 2. Substring match: ingredient table entry is substring of name or vice versa
            rows = conn.execute(
                "SELECT ingredient, quantity_4p FROM ingredient_standard_amounts"
            ).fetchall()
            for r in rows:
                key = r["ingredient"]
                if key in name or name in key:
                    row = r
                    break
        if not row:
            return ""

        qty_str = row["quantity_4p"]
        if servings == 4:
            return qty_str

        # Scale: parse number, multiply by servings/4, reformat
        m = re.match(r'^([\d½¼¾]+(?:[.,]\d+)?)\s*(.*)$', qty_str.strip())
        if not m:
            return qty_str
        num_str = m.group(1).replace(',', '.').replace('½', '0.5').replace('¼', '0.25').replace('¾', '0.75')
        try:
            num = float(num_str)
            scaled = num * servings / 4
            # Format nicely: integer if whole number
            if scaled == int(scaled):
                return f"{int(scaled)} {m.group(2)}".strip()
            else:
                return f"{scaled:.1f} {m.group(2)}".strip()
        except ValueError:
            return qty_str


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent migrations for existing databases."""
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(recipe_history)")}

    if "rating" not in existing_cols:
        conn.execute("ALTER TABLE recipe_history ADD COLUMN rating INTEGER DEFAULT NULL")
        conn.execute("""
            UPDATE recipe_history SET rating = 6
            WHERE kid1_liked=1 AND kid2_liked=1 AND rating IS NULL
        """)
        conn.execute("""
            UPDATE recipe_history SET rating = 2
            WHERE kid1_liked=0 AND kid2_liked=0 AND rating IS NULL
        """)
        conn.execute("""
            UPDATE recipe_history SET rating = 4
            WHERE kid1_liked != kid2_liked AND rating IS NULL
        """)
        logger.info("Migration: added rating column to recipe_history")

    if "key_ingredients" not in existing_cols:
        conn.execute("ALTER TABLE recipe_history ADD COLUMN key_ingredients TEXT DEFAULT NULL")
        logger.info("Migration: added key_ingredients column to recipe_history")

    mem_cols = {row[1] for row in conn.execute("PRAGMA table_info(shopping_memory)")}
    if "avg_interval_days" not in mem_cols:
        conn.execute("ALTER TABLE shopping_memory ADD COLUMN avg_interval_days REAL DEFAULT NULL")
        conn.execute("ALTER TABLE shopping_memory ADD COLUMN bought_dates TEXT DEFAULT '[]'")
        logger.info("Migration: added interval learning columns to shopping_memory")

    catalog_cols = {row[1] for row in conn.execute("PRAGMA table_info(recipe_catalog)")}
    if "total_time_min" not in catalog_cols:
        conn.execute("ALTER TABLE recipe_catalog ADD COLUMN total_time_min INTEGER DEFAULT NULL")
        logger.info("Migration: added total_time_min column to recipe_catalog")

    shop_cols = {row[1] for row in conn.execute("PRAGMA table_info(weekly_shopping)")}
    if "in_pantry" not in shop_cols:
        conn.execute("ALTER TABLE weekly_shopping ADD COLUMN in_pantry INTEGER DEFAULT 0")
        logger.info("Migration: added in_pantry column to weekly_shopping")

    # Create ingredient_standard_amounts table if missing (standalone table, no ALTER needed)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingredient_standard_amounts (
            ingredient TEXT PRIMARY KEY,
            quantity_4p TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT ''
        )
    """)
    # Seed standard amounts if table is empty
    if conn.execute("SELECT COUNT(*) FROM ingredient_standard_amounts").fetchone()[0] == 0:
        _seed_standard_amounts(conn)
        logger.info("Seeded ingredient_standard_amounts with defaults")

    # Phase 1 1b — canonical ingredient variants + pack_grams column
    conn.execute(INGREDIENT_CANONICAL_SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM ingredient_canonical").fetchone()[0] == 0:
        _seed_ingredient_canonical(conn)
        logger.info("Seeded ingredient_canonical with variant mappings")

    isa_cols = {row[1] for row in conn.execute("PRAGMA table_info(ingredient_standard_amounts)")}
    if "pack_grams" not in isa_cols:
        conn.execute("ALTER TABLE ingredient_standard_amounts ADD COLUMN pack_grams INTEGER")
        logger.info("Migration: added pack_grams column to ingredient_standard_amounts")
        _seed_pack_grams(conn)
        logger.info("Seeded pack_grams for common ingredients")


# ─── Recipe History ───────────────────────────────────────────────────────────

def save_recipes(recipes: list[dict], week_num: int, year: int,
                 db_path: Path = DEFAULT_DB) -> None:
    """Save generated recipes to history."""
    with get_connection(db_path) as conn:
        for recipe in recipes:
            recipe_id = f"{year}-w{week_num:02d}-{recipe.get('name', 'unknown')[:30]}"
            recipe_id = recipe_id.replace(" ", "-").replace("/", "-").lower()
            key_ing = recipe.get("key_ingredients")
            conn.execute("""
                INSERT OR REPLACE INTO recipe_history
                    (id, name, data_json, source_url, week_num, year, key_ingredients)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                recipe_id,
                recipe.get("name", ""),
                json.dumps(recipe, ensure_ascii=False),
                recipe.get("source_url"),
                week_num,
                year,
                json.dumps(key_ing, ensure_ascii=False) if key_ing else None,
            ))
    logger.info(f"Saved {len(recipes)} recipes for week {week_num}/{year}")


def get_recent_recipe_names(weeks_back: int = 8,
                             db_path: Path = DEFAULT_DB) -> list[str]:
    """Return recipe names cooked in the last N weeks (for avoiding repeats)."""
    today = date.today()
    cutoff = today - timedelta(weeks=weeks_back)
    cutoff_year, cutoff_week, _ = cutoff.isocalendar()

    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT name FROM recipe_history
            WHERE (year > ?) OR (year = ? AND week_num >= ?)
            ORDER BY year DESC, week_num DESC
        """, (cutoff_year, cutoff_year, cutoff_week)).fetchall()

    return [row["name"] for row in rows]


def get_recipes_for_week(week_num: int, year: int,
                          db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return recipes for a specific week."""
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM recipe_history
            WHERE week_num = ? AND year = ?
            ORDER BY created_at
        """, (week_num, year)).fetchall()

    result = []
    for row in rows:
        data = json.loads(row["data_json"])
        data["_db_id"] = row["id"]
        data["_kid1_liked"] = row["kid1_liked"]
        data["_kid2_liked"] = row["kid2_liked"]
        data["_adult_liked"] = row["adult_liked"]
        data["_rating"] = row["rating"]
        data["_times_cooked"] = row["times_cooked"]
        result.append(data)
    return result


def rate_recipe(recipe_id: str, rating: int, db_path: Path = DEFAULT_DB) -> None:
    """Save household rating (1-7) for a recipe."""
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE recipe_history
            SET rating = ?,
                times_cooked = times_cooked + 1,
                last_cooked = ?
            WHERE id = ?
        """, (rating, datetime.now().isoformat(), recipe_id))


def get_liked_recipes(db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return recipes rated 5+ (household liked)."""
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT name, rating, times_cooked
            FROM recipe_history
            WHERE rating >= 5
            ORDER BY rating DESC, times_cooked DESC
            LIMIT 20
        """).fetchall()
    return [dict(row) for row in rows]


def get_disliked_recipes(db_path: Path = DEFAULT_DB) -> list[str]:
    """Return names of recipes rated 1-2 (to avoid repeating)."""
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT name FROM recipe_history
            WHERE rating <= 2
        """).fetchall()
    return [row["name"] for row in rows]


def get_history(name_filter: str = None, min_rating: int = None,
                max_rating: int = None, db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return all rated recipe history entries, optionally filtered."""
    conditions = ["rating IS NOT NULL"]
    params: list = []
    if name_filter:
        conditions.append("LOWER(name) LIKE ?")
        params.append(f"%{name_filter.lower()}%")
    if min_rating is not None:
        conditions.append("rating >= ?")
        params.append(min_rating)
    if max_rating is not None:
        conditions.append("rating <= ?")
        params.append(max_rating)
    sql = f"""
        SELECT id, name, rating, times_cooked, last_cooked,
               week_num, year, key_ingredients, source_url, created_at
        FROM recipe_history
        WHERE {' AND '.join(conditions)}
        ORDER BY rating DESC, last_cooked DESC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        r = dict(row)
        if r.get("key_ingredients"):
            try:
                r["key_ingredients"] = json.loads(r["key_ingredients"])
            except Exception:
                r["key_ingredients"] = []
        result.append(r)
    return result


# ─── Recipe Catalog ───────────────────────────────────────────────────────────

def save_catalog_recipes(recipes: list[dict], db_path: Path = DEFAULT_DB) -> int:
    """Bulk insert catalog recipes. Returns number of new entries saved."""
    saved = 0
    with get_connection(db_path) as conn:
        for r in recipes:
            ingredients = r.get("ingredients", [])
            instructions = r.get("instructions", [])
            try:
                conn.execute("""
                    INSERT INTO recipe_catalog
                        (name, url, source, diet_type, site_rating, total_time_min,
                         ingredients, key_ingredients, instructions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        name = excluded.name,
                        source = excluded.source,
                        diet_type = excluded.diet_type,
                        site_rating = excluded.site_rating,
                        total_time_min = excluded.total_time_min,
                        ingredients = excluded.ingredients,
                        instructions = excluded.instructions
                """, (
                    r.get("name", ""),
                    r["url"],
                    r.get("source"),
                    r.get("diet_type"),
                    r.get("site_rating"),
                    r.get("total_time_min"),
                    json.dumps(ingredients, ensure_ascii=False),
                    json.dumps(r["key_ingredients"], ensure_ascii=False) if r.get("key_ingredients") else None,
                    json.dumps(instructions, ensure_ascii=False),
                ))
                saved += 1
            except Exception as e:
                logger.warning(f"Could not save catalog recipe {r.get('url')}: {e}")
    return saved


MEAT_KEYWORDS = [
    "nötfärs", "köttfärs", "fläskfärs", "lammfärs",
    "köttbullar", "fläsk", "bacon", "skinka", "korv",
    "falukorv", "biff", "oxfilé", "entrecôte",
    "revbensspjäll", "lamm", "älg", "vildsvin", "anka",
    "chorizo", "salsiccia", "pancetta", "prosciutto", "salami",
]

LAX_KEYWORDS = {"lax", "laxfilé", "gravlax", "rökt lax", "laxbiff", "salmon", "laxsida"}
CHICKEN_KEYWORDS = {"kyckling", "kycklingfilé", "kycklinglår", "kycklingbröst", "kycklingben",
                    "kycklingvinge", "kycklingfärs", "hel kyckling"}
FISH_KEYWORDS = {"fisk", "torsk", "abborre", "gös", "sej", "makrill", "tonfisk", "räkor",
                 "räka", "hummer", "krabba", "bläckfisk", "pilgrimsmussla", "mussla",
                 "sill", "strömming", "hälleflundra", "tilapia", "pangasius", "kolja",
                 "sardiner", "ansjovis", "tångräkor", "fiskfilé", "fisksoppa", "fiskpinnar",
                 "fiskbullar", "fiskgratäng", "fiskpaj"} | LAX_KEYWORDS


def _row_text(row) -> str:
    return ((row["name"] or "") + " " +
            (row["ingredients"] or "") + " " +
            (row["key_ingredients"] or "")).lower()


def _has_meat(row) -> bool:
    text = _row_text(row)
    return any(kw in text for kw in MEAT_KEYWORDS)


def _has_chicken(row) -> bool:
    return any(kw in _row_text(row) for kw in CHICKEN_KEYWORDS)


def _has_fish(row) -> bool:
    return any(kw in _row_text(row) for kw in FISH_KEYWORDS)


def classify_diet_type(row) -> str:
    """Classify a recipe row into a diet_type string based on ingredients."""
    if _has_chicken(row):
        return "chicken"
    if _has_fish(row):
        return "fish"
    if _has_meat(row):
        return "meat"
    return "vegetarian"


def backfill_diet_types(db_path: Path = DEFAULT_DB) -> int:
    """Populate diet_type for all rows where it is NULL. Returns count updated."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, ingredients, key_ingredients FROM recipe_catalog WHERE diet_type IS NULL"
        ).fetchall()
        updated = 0
        for row in rows:
            diet = classify_diet_type(row)
            conn.execute("UPDATE recipe_catalog SET diet_type=? WHERE id=?", (diet, row["id"]))
            updated += 1
    return updated


def get_catalog_candidates(offer_terms: list[str], exclude_names: list[str],
                            limit: int = 100, max_time_min: int = None,
                            allowed_diets: list[str] = None,
                            force_diets: list[str] = None,
                            db_path: Path = DEFAULT_DB) -> list[dict]:
    """
    Find catalog recipes matching offer terms, excluding recently used names.

    Scores every candidate by:
      - number of offer terms matched (primary — more = better)
      - site rating (secondary)
      - recency penalty (de-prioritise recently used)

    Diet filter (Phase 1 1c):
      allowed_diets=None  → no diet filter (legacy behavior)
      allowed_diets=[]    → returns [] immediately (no diet allowed)
      allowed_diets=[...] → SQL hard AND: LOWER(diet_type) IN (...); rows with
                            NULL diet_type are excluded. force_diets merges
                            into allowed_diets with a deprecation warning.

    Returns top `limit` results by score.
    """
    # Merge deprecated force_diets into allowed_diets
    if force_diets:
        logger.warning("get_catalog_candidates: force_diets is deprecated; merging into allowed_diets")
        if allowed_diets is None:
            allowed_diets = []
        allowed_diets = sorted({d.lower() for d in allowed_diets} | {d.lower() for d in force_diets})

    # Empty-list diet filter ⇒ nothing is allowed
    if allowed_diets is not None and len(allowed_diets) == 0:
        return []

    # Build SQL with optional diet AND time filters
    sql = "SELECT * FROM recipe_catalog WHERE (site_rating >= 3.5 OR site_rating IS NULL)"
    params: list = []

    if allowed_diets is not None:
        placeholders = ",".join("?" * len(allowed_diets))
        sql += f" AND LOWER(diet_type) IN ({placeholders})"
        params.extend([d.lower() for d in allowed_diets])

    if max_time_min is not None:
        sql += " AND (total_time_min IS NULL OR total_time_min <= ?)"
        params.append(max_time_min)

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()

    offer_terms_lower = [t.lower() for t in offer_terms]
    exclude_lower = {n.lower() for n in exclude_names}

    # Skip category pages / guide pages (no real ingredients)
    rows = [r for r in rows if r["ingredients"] and r["ingredients"] not in ('[]', '""', '')]

    # Belt-and-braces meat filter: SQL excludes rows with diet_type='meat' already,
    # but old rows may have NULL diet_type and still contain meat in ingredients.
    # When diet restricts to veg/fish only, drop anything with meat keywords.
    if allowed_diets is not None and "meat" not in allowed_diets:
        rows = [r for r in rows if not _has_meat(r)]

    scored = []
    for row in rows:
        if row["name"].lower() in exclude_lower:
            continue

        ingredients_text = (row["ingredients"] or "").lower()
        ki_text = (row["key_ingredients"] or "").lower()
        name_lower = row["name"].lower()
        combined = ingredients_text + " " + ki_text + " " + name_lower

        match_count = sum(1 for t in offer_terms_lower if t in combined)
        # No more force_diets bypass — diet_type is enforced in SQL
        if offer_terms_lower and match_count == 0:
            continue

        rating_score = float(row["site_rating"] or 3.5)
        times_used = int(row["times_used"] or 0)

        # Recency penalty: recently-used recipes rank lower. Never-used = 0
        # penalty. Decays linearly to 0 after 60 days so old picks become
        # eligible again.
        last_used_raw = row["last_used"]
        recency_penalty = 0.0
        if last_used_raw:
            try:
                days_ago = (date.today() - date.fromisoformat(last_used_raw)).days
                recency_penalty = max(0.0, 1.0 - days_ago / 60.0)
            except (ValueError, TypeError):
                pass

        # Small random jitter breaks deterministic ties so same-scored
        # recipes rotate across back-to-back plan generations.
        jitter = random.uniform(0.0, 0.5)

        score = (match_count * 10
                 + rating_score
                 - times_used * 0.5
                 - recency_penalty * 3.0
                 + jitter)

        r = dict(row)
        r["_match_count"] = match_count
        r["_score"] = score
        scored.append(r)

    scored.sort(key=lambda x: x["_score"], reverse=True)
    top = scored[:limit]

    # Deserialise JSON columns
    results = []
    for r in top:
        r["ingredients"] = json.loads(r["ingredients"]) if r["ingredients"] else []
        r["key_ingredients"] = json.loads(r["key_ingredients"]) if r["key_ingredients"] else []
        r["instructions"] = json.loads(r["instructions"]) if r["instructions"] else []
        results.append(r)

    return results

def mark_catalog_used(url: str, db_path: Path = DEFAULT_DB) -> None:
    """Bump times_used and set last_used to today for a catalog recipe."""
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE recipe_catalog
            SET times_used = times_used + 1,
                last_used = date('now')
            WHERE url = ?
        """, (url,))


def search_catalog_by_ingredients(terms: list[str],
                                   db_path: Path = DEFAULT_DB) -> list[dict]:
    """Search catalog by ingredient/key_ingredient LIKE terms."""
    if not terms:
        return []
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT id, name, url, source, diet_type, site_rating, key_ingredients
            FROM recipe_catalog
            ORDER BY site_rating DESC NULLS LAST
        """).fetchall()

    terms_lower = [t.lower() for t in terms]
    results = []
    for row in rows:
        ki = (row["key_ingredients"] or "").lower()
        name = row["name"].lower()
        if any(t in ki or t in name for t in terms_lower):
            r = dict(row)
            r["key_ingredients"] = json.loads(r["key_ingredients"]) if r["key_ingredients"] else []
            results.append(r)
    return results


# ─── Weekly Shopping List ─────────────────────────────────────────────────────

def save_shopping_list(shopping_list: list[dict], week_num: int, year: int,
                        db_path: Path = DEFAULT_DB) -> None:
    """Save the week's shopping list to DB (skip if already exists for this week)."""
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT COUNT(*) as c FROM weekly_shopping WHERE week_num=? AND year=? AND added_manually=0",
            (week_num, year)
        ).fetchone()["c"]

        if existing > 0:
            logger.info(f"Shopping list for week {week_num}/{year} already exists, skipping auto-items")
            return

        for cat in shopping_list:
            category = cat.get("category", "övrigt")
            for item in cat.get("items", []):
                conn.execute("""
                    INSERT OR IGNORE INTO weekly_shopping
                        (week_num, year, item, quantity, category, on_sale, checked, in_pantry)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """, (
                    week_num, year,
                    item.get("item", ""),
                    item.get("quantity", ""),
                    category,
                    1 if item.get("on_sale") else 0,
                    1 if item.get("in_pantry") else 0,
                ))


def get_shopping_list(week_num: int, year: int,
                       db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return the shopping list for a week, grouped by category."""
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM weekly_shopping
            WHERE week_num = ? AND year = ?
            ORDER BY category, item
        """, (week_num, year)).fetchall()

    by_cat: dict[str, list] = {}
    for row in rows:
        cat = row["category"] or "övrigt"
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append({
            "id": row["id"],
            "item": row["item"],
            "quantity": row["quantity"],
            "on_sale": bool(row["on_sale"]),
            "checked": bool(row["checked"]),
            "added_manually": bool(row["added_manually"]),
            "in_pantry": bool(row["in_pantry"]) if "in_pantry" in row.keys() else False,
        })

    category_order = ["frukt/grönt", "mejeri", "kött", "torrvaror", "övrigt"]
    result = []
    seen = set()
    for cat in category_order:
        if cat in by_cat:
            result.append({"category": cat, "items": by_cat[cat]})
            seen.add(cat)
    for cat, items in by_cat.items():
        if cat not in seen:
            result.append({"category": cat, "items": items})

    return result


def toggle_shopping_item(item_id: int, checked: bool,
                          db_path: Path = DEFAULT_DB) -> str | None:
    """Toggle checked state. Returns item name if it was manually added and checked."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE weekly_shopping SET checked = ? WHERE id = ?",
            (1 if checked else 0, item_id)
        )
        if checked:
            row = conn.execute(
                "SELECT item, added_manually FROM weekly_shopping WHERE id = ?", (item_id,)
            ).fetchone()
            if row and row["added_manually"]:
                return row["item"]
    return None


def add_shopping_item(item: str, quantity: str, week_num: int, year: int,
                       category: str = "övrigt", db_path: Path = DEFAULT_DB) -> int:
    """Add a custom item to the shopping list. Returns new row id."""
    with get_connection(db_path) as conn:
        cursor = conn.execute("""
            INSERT OR REPLACE INTO weekly_shopping
                (week_num, year, item, quantity, category, added_manually)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (week_num, year, item.strip(), quantity.strip(), category))
        return cursor.lastrowid


def remove_shopping_item(item_id: int, db_path: Path = DEFAULT_DB) -> None:
    """Remove an item from the shopping list."""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM weekly_shopping WHERE id = ?", (item_id,))


# ─── Shopping Memory ──────────────────────────────────────────────────────────

def record_purchase(item: str, db_path: Path = DEFAULT_DB) -> None:
    """Record a manual purchase and update the learned interval."""
    today = date.today().isoformat()
    item_key = item.strip().lower()

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT bought_dates, times_bought FROM shopping_memory WHERE item = ?",
            (item_key,)
        ).fetchone()

        if row:
            dates = json.loads(row["bought_dates"] or "[]")
        else:
            dates = []

        dates.append(today)
        # Keep last 20 purchases — enough to compute a stable interval
        dates = sorted(set(dates))[-20:]

        # Compute average interval from consecutive gaps
        avg_interval = None
        if len(dates) >= 2:
            gaps = [
                (date.fromisoformat(dates[i]) - date.fromisoformat(dates[i - 1])).days
                for i in range(1, len(dates))
                if (date.fromisoformat(dates[i]) - date.fromisoformat(dates[i - 1])).days > 0
            ]
            if gaps:
                avg_interval = sum(gaps) / len(gaps)

        conn.execute("""
            INSERT INTO shopping_memory (item, last_bought, times_bought, avg_interval_days, bought_dates)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(item) DO UPDATE SET
                last_bought       = ?,
                times_bought      = times_bought + 1,
                avg_interval_days = ?,
                bought_dates      = ?
        """, (
            item_key, today, avg_interval, json.dumps(dates),
            today, avg_interval, json.dumps(dates),
        ))


def get_smart_suggestions(warn_days_ahead: int = 3, db_path: Path = DEFAULT_DB) -> list[dict]:
    """
    Return items due for purchase based on learned intervals.
    Only items bought 2+ times with a known interval are suggested.
    Items with no interval yet are shown after 7 days from last purchase.
    """
    today = date.today()
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT item, last_bought, times_bought, avg_interval_days
            FROM shopping_memory
            WHERE times_bought >= 1
            ORDER BY item
        """).fetchall()

    suggestions = []
    for row in rows:
        last = row["last_bought"]
        interval = row["avg_interval_days"]
        times = row["times_bought"]

        if not last:
            continue

        lb = date.fromisoformat(last)

        # Need at least 2 purchases to have a learned interval
        if times < 2 or not interval:
            continue

        next_due = lb + timedelta(days=interval)
        days_until = (next_due - today).days
        days_overdue = -days_until

        if next_due <= today + timedelta(days=warn_days_ahead):
            suggestions.append({
                "item": row["item"],
                "interval_days": round(interval),
                "last_bought": last,
                "days_overdue": days_overdue,
                "urgent": days_overdue > 0,
            })

    return sorted(suggestions, key=lambda x: -x["days_overdue"])


# ─── Staples (kept for backwards compat, no-op) ───────────────────────────────

def init_staples(staples_config: list[dict], db_path: Path = DEFAULT_DB) -> None:
    pass  # Replaced by self-learning shopping_memory


def get_staple_suggestions(db_path: Path = DEFAULT_DB) -> list[dict]:
    return get_smart_suggestions(db_path=db_path)


def mark_staple_bought(item: str, db_path: Path = DEFAULT_DB) -> None:
    record_purchase(item, db_path)


# ─── Price History ────────────────────────────────────────────────────────────

def save_prices(prices: list[dict], db_path: Path = DEFAULT_DB) -> None:
    """Bulk insert price records, skip duplicates (same normalized+date)."""
    if not prices:
        return
    with get_connection(db_path) as conn:
        for p in prices:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO price_history
                        (willys_code, product_name, normalized, price, compare_price, unit)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    p.get("willys_code"),
                    p.get("product_name", ""),
                    p.get("normalized", ""),
                    p.get("price"),
                    p.get("compare_price"),
                    p.get("unit"),
                ))
            except Exception as e:
                logger.warning(f"Could not save price for {p.get('normalized')}: {e}")
    logger.info(f"Saved {len(prices)} price records")


def get_cheap_offers(offer_names: list[str], db_path: Path = DEFAULT_DB) -> set:
    """
    Return set of normalized offer names where today's price < 30-day avg × 0.90.
    Requires at least 3 data points.
    """
    if not offer_names:
        return set()
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT normalized,
                   AVG(price) as avg_price,
                   COUNT(*) as cnt
            FROM price_history
            WHERE scraped_date >= ?
            GROUP BY normalized
            HAVING cnt >= 3
        """, (cutoff,)).fetchall()

    avg_by_norm = {row["normalized"]: row["avg_price"] for row in rows}

    cheap = set()
    for name in offer_names:
        norm = _normalize_product_name(name)
        avg = avg_by_norm.get(norm)
        if avg is None:
            continue
        # Get today's price
        today = date.today().isoformat()
        with get_connection(db_path) as conn:
            row = conn.execute("""
                SELECT price FROM price_history
                WHERE normalized = ? AND scraped_date = ?
                ORDER BY id DESC LIMIT 1
            """, (norm, today)).fetchone()
        if row and row["price"] and row["price"] < avg * 0.90:
            cheap.add(name.lower())
    return cheap


# ─── Price Watchlist ──────────────────────────────────────────────────────────

def get_watchlist(db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return all watchlist items."""
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM price_watchlist ORDER BY display_name").fetchall()
    return [dict(row) for row in rows]


def add_to_watchlist(query: str, display_name: str, db_path: Path = DEFAULT_DB) -> None:
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO price_watchlist (query, display_name) VALUES (?, ?)
        """, (query.strip().lower(), display_name.strip()))


def remove_from_watchlist(query: str, db_path: Path = DEFAULT_DB) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM price_watchlist WHERE query = ?", (query.strip().lower(),))


def get_watchlist_alerts(db_path: Path = DEFAULT_DB) -> list[dict]:
    """
    Return watchlist items where current price is >10% below 30-day average.
    Requires at least 3 data points.
    """
    watchlist = get_watchlist(db_path)
    if not watchlist:
        return []

    cutoff = (date.today() - timedelta(days=30)).isoformat()
    today = date.today().isoformat()
    alerts = []

    with get_connection(db_path) as conn:
        for item in watchlist:
            norm = _normalize_product_name(item["query"])
            avg_row = conn.execute("""
                SELECT AVG(price) as avg_price, COUNT(*) as cnt
                FROM price_history
                WHERE normalized = ? AND scraped_date >= ?
            """, (norm, cutoff)).fetchone()

            if not avg_row or avg_row["cnt"] < 3 or not avg_row["avg_price"]:
                continue

            today_row = conn.execute("""
                SELECT price FROM price_history
                WHERE normalized = ? AND scraped_date = ?
                ORDER BY id DESC LIMIT 1
            """, (norm, today)).fetchone()

            if not today_row or not today_row["price"]:
                continue

            current = today_row["price"]
            avg = avg_row["avg_price"]
            if current < avg * 0.90:
                pct_below = round((1 - current / avg) * 100)
                alerts.append({
                    "query": item["query"],
                    "display_name": item["display_name"],
                    "current_price": round(current, 2),
                    "avg_price": round(avg, 2),
                    "pct_below": pct_below,
                })

    return alerts


def _normalize_product_name(name: str) -> str:
    """Lowercase, strip leading quantities/digits, remove common brand noise."""
    import re
    s = name.lower().strip()
    # Remove leading quantities like "2x", "500g", "1 l" etc.
    s = re.sub(r'^\d+[\s\.,xX]*(?:g|kg|ml|cl|dl|l|st|förp|paket)?\s*', '', s)
    return s.strip()


# ─── User management ──────────────────────────────────────────────────────────

def create_user(username: str, password_hash: str, kdf_salt: str,
                is_admin: bool = False, db_path: Path = DEFAULT_DB) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, kdf_salt, is_admin) VALUES (?,?,?,?)",
            (username, password_hash, kdf_salt, 1 if is_admin else 0),
        )
        return cur.lastrowid


def get_user_by_username(username: str, db_path: Path = DEFAULT_DB) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int, db_path: Path = DEFAULT_DB) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users(db_path: Path = DEFAULT_DB) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, username, is_admin, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_user(user_id: int, db_path: Path = DEFAULT_DB) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def user_count(db_path: Path = DEFAULT_DB) -> int:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ─── Per-user encrypted data blobs ────────────────────────────────────────────

def get_user_blob(user_id: int, data_type: str, db_path: Path = DEFAULT_DB) -> Optional[str]:
    """Return raw encrypted blob string, or None if not set."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT blob FROM user_data WHERE user_id=? AND data_type=?",
            (user_id, data_type),
        ).fetchone()
        return row[0] if row else None


def set_user_blob(user_id: int, data_type: str, blob: str, db_path: Path = DEFAULT_DB) -> None:
    """Upsert an encrypted blob for a user."""
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO user_data (user_id, data_type, blob, updated_at)
               VALUES (?,?,?,datetime('now'))
               ON CONFLICT(user_id, data_type) DO UPDATE SET blob=excluded.blob, updated_at=excluded.updated_at""",
            (user_id, data_type, blob),
        )


# ─── Price history helpers (per-user server.py usage) ─────────────────────────

def get_price_history(normalized: str, days: int = 30, db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return recent price history for a normalized product name."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT price, scraped_date FROM price_history WHERE normalized LIKE ? ORDER BY scraped_date DESC LIMIT ?",
            (f"%{normalized}%", days),
        ).fetchall()
        return [dict(r) for r in rows]


def get_watchlist_alerts_for(queries: list[str], db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return price alerts for a specific list of watchlist queries."""
    if not queries:
        return []
    alerts = []
    with get_connection(db_path) as conn:
        for query in queries:
            rows = conn.execute(
                "SELECT price, scraped_date FROM price_history WHERE normalized LIKE ? ORDER BY scraped_date DESC LIMIT 30",
                (f"%{query.lower()}%",),
            ).fetchall()
            if len(rows) < 3:
                continue
            prices = [r["price"] for r in rows if r["price"]]
            if not prices:
                continue
            current = prices[0]
            avg = sum(prices) / len(prices)
            if avg > 0:
                pct_below = round((avg - current) / avg * 100, 1)
                if pct_below > 10:
                    alerts.append({
                        "query": query,
                        "display_name": query,
                        "current_price": current,
                        "avg_price": round(avg, 2),
                        "pct_below": pct_below,
                    })
    return alerts


# ─── Store geo cache ──────────────────────────────────────────────────────────

_STORE_CACHE_FRESH_DAYS = 7    # serve from DB, no background refresh
_STORE_CACHE_STALE_DAYS = 30   # serve from DB but schedule background refresh
# older than 30 days → re-fetch synchronously


def geo_cache_get(zip_code: str, db_path: Path = DEFAULT_DB) -> dict | None:
    """Return cached entry or None.  Includes 'needs_refresh' flag."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT zip_code, city, lat, lon, stores_json, fetched_at, refresh_at "
            "FROM store_geo_cache WHERE zip_code=?",
            (zip_code,)
        ).fetchone()
    if not row:
        return None
    stores = json.loads(row["stores_json"])
    if not stores:  # don't serve empty cache entries
        return None
    age_days = (datetime.utcnow() - datetime.fromisoformat(row["fetched_at"])).days
    return {
        "zip_code":      row["zip_code"],
        "city":          row["city"],
        "lat":           row["lat"],
        "lon":           row["lon"],
        "stores":        stores,
        "fetched_at":    row["fetched_at"],
        "age_days":      age_days,
        "needs_refresh": age_days >= _STORE_CACHE_FRESH_DAYS,
        "force_refresh": age_days >= _STORE_CACHE_STALE_DAYS,
    }


def geo_cache_put(zip_code: str, city: str, lat: float, lon: float,
                  stores: list, db_path: Path = DEFAULT_DB) -> None:
    """Insert or replace a cache entry. Sets refresh_at to now + 7 days."""
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO store_geo_cache
               (zip_code, city, lat, lon, stores_json, fetched_at, refresh_at)
               VALUES (?,?,?,?,?,datetime('now'),datetime('now','+7 days'))
               ON CONFLICT(zip_code) DO UPDATE SET
                   city=excluded.city, lat=excluded.lat, lon=excluded.lon,
                   stores_json=excluded.stores_json,
                   fetched_at=excluded.fetched_at,
                   refresh_at=excluded.refresh_at""",
            (zip_code, city, lat, lon, json.dumps(stores, ensure_ascii=False))
        )


def geo_cache_all(db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return all cached zip codes with metadata (for admin view)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT zip_code, city, lat, lon, fetched_at, refresh_at, "
            "  json_array_length(stores_json) as store_count "
            "FROM store_geo_cache ORDER BY fetched_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Zip code centroids ───────────────────────────────────────────────────────

def zip_lookup(zip_code: str, db_path: Path = DEFAULT_DB) -> dict | None:
    """Return {zip_code, place_name, lat, lon} or None if not found."""
    z = zip_code.replace(" ", "")
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT zip_code, place_name, lat, lon FROM zip_centroids WHERE zip_code=?", (z,)
        ).fetchone()
    return dict(row) if row else None


def zip_upsert_bulk(rows: list[tuple], db_path: Path = DEFAULT_DB) -> int:
    """Bulk insert (zip_code, place_name, lat, lon) tuples. Returns row count after."""
    with get_connection(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO zip_centroids (zip_code, place_name, lat, lon) VALUES (?,?,?,?)",
            rows,
        )
        return conn.execute("SELECT COUNT(*) FROM zip_centroids").fetchone()[0]


# ─── Swedish stores (full national DB) ────────────────────────────────────────

_PRIORITY_CHAINS = {"willys", "ica", "coop", "lidl", "hemkop", "citygross", "netto"}


def stores_migrate(db_path: Path = DEFAULT_DB) -> None:
    """Run any pending schema migrations on swedish_stores."""
    with get_connection(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(swedish_stores)").fetchall()}
        if "chain_store_id" not in cols:
            conn.execute("ALTER TABLE swedish_stores ADD COLUMN chain_store_id TEXT")
            logger.info("Migrated swedish_stores: added chain_store_id column")


def store_set_chain_id(db_id: int, chain_store_id: str,
                       db_path: Path = DEFAULT_DB) -> None:
    """Store a resolved chain-specific ID for a store row."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE swedish_stores SET chain_store_id=? WHERE id=?",
            (chain_store_id, db_id),
        )


def stores_needing_chain_id(chain: str, db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return stores for a chain that don't yet have a chain_store_id."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, lat, lon, city FROM swedish_stores "
            "WHERE chain=? AND (chain_store_id IS NULL OR chain_store_id='')",
            (chain,),
        ).fetchall()
    return [dict(r) for r in rows]


def stores_upsert_bulk(stores: list[dict], db_path: Path = DEFAULT_DB) -> int:
    """Insert or update a list of store dicts. Returns number of rows affected."""
    with get_connection(db_path) as conn:
        conn.executemany(
            """INSERT INTO swedish_stores
               (osm_id, chain, name, lat, lon, address, zip_code, city, fetched_at)
               VALUES (:osm_id, :chain, :name, :lat, :lon, :address, :zip_code, :city, datetime('now'))
               ON CONFLICT(osm_id) DO UPDATE SET
                   chain=excluded.chain, name=excluded.name,
                   lat=excluded.lat, lon=excluded.lon,
                   address=excluded.address, zip_code=excluded.zip_code,
                   city=excluded.city, fetched_at=excluded.fetched_at""",
            stores,
        )
        return conn.execute("SELECT changes()").fetchone()[0]


def stores_nearest(lat: float, lon: float, limit: int = 10,
                   radius_km: float = 50, db_path: Path = DEFAULT_DB) -> list[dict]:
    """Return up to `limit` nearest stores within radius_km of (lat, lon)."""
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * 0.64)  # cos(60°N) for Sweden
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT osm_id, chain, name, lat, lon, address, zip_code, city, chain_store_id
               FROM swedish_stores
               WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?""",
            (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
        ).fetchall()
    if not rows:
        return []
    result = []
    for row in rows:
        dlat_m = (row["lat"] - lat) * 111_320
        dlon_m = (row["lon"] - lon) * 111_320 * 0.64
        dist_m = int((dlat_m ** 2 + dlon_m ** 2) ** 0.5)
        result.append({
            "id":            f"osm_{row['osm_id']}",
            "chain":         row["chain"],
            "name":          row["name"],
            "lat":           row["lat"],
            "lon":           row["lon"],
            "address":       row["address"] or "",
            "zip_code":      row["zip_code"] or "",
            "city":          row["city"] or "",
            "dist_m":        dist_m,
            "chain_store_id": row["chain_store_id"] or "",
        })
    result.sort(key=lambda s: (s["dist_m"] // 500,
                                0 if s["chain"] in _PRIORITY_CHAINS else 1,
                                s["dist_m"]))
    return result[:limit]


def stores_count(db_path: Path = DEFAULT_DB) -> int:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM swedish_stores").fetchone()[0]


def stores_last_updated(db_path: Path = DEFAULT_DB) -> str | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) FROM swedish_stores"
        ).fetchone()
    return row[0] if row else None


# ─── Store price catalog (CPI / cheapness scoring) ────────────────────────────

def store_price_catalog_migrate(db_path: Path = DEFAULT_DB) -> None:
    """Ensure store_price_catalog table exists."""
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS store_price_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                store_id TEXT NOT NULL,
                product_code TEXT NOT NULL,
                name TEXT NOT NULL,
                price REAL,
                compare_price TEXT,
                unit TEXT,
                scraped_date TEXT NOT NULL,
                UNIQUE(chain, store_id, product_code, scraped_date)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_price_catalog_chain_store
            ON store_price_catalog(chain, store_id, scraped_date)
        """)


def get_cheap_catalog_items(chain: str, store_id: str,
                             lookback_days: int = 60,
                             threshold: float = 0.90,
                             db_path: Path = DEFAULT_DB) -> list[dict]:
    """
    Return products whose latest price is at least 10% below their
    median price over the last `lookback_days` days (requires ≥2 data points).
    Used for CPI-based recipe ingredient scoring.
    """
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT product_code, name,
                   AVG(price) as avg_price,
                   MIN(price) as min_price,
                   MAX(price) as max_price,
                   COUNT(*) as data_points,
                   MAX(scraped_date) as latest_date
            FROM store_price_catalog
            WHERE chain=? AND store_id=? AND scraped_date >= ? AND price IS NOT NULL
            GROUP BY product_code
            HAVING data_points >= 2
        """, (chain, store_id, cutoff)).fetchall()

    today_str = date.today().isoformat()
    cheap = []
    for row in rows:
        with get_connection(db_path) as conn:
            latest = conn.execute("""
                SELECT price FROM store_price_catalog
                WHERE chain=? AND store_id=? AND product_code=? AND scraped_date=?
                ORDER BY id DESC LIMIT 1
            """, (chain, store_id, row["product_code"], today_str)).fetchone()
        if not latest or not latest["price"]:
            continue
        avg = row["avg_price"]
        if avg and latest["price"] < avg * threshold:
            cheap.append({
                "name": row["name"],
                "price": latest["price"],
                "avg_price": round(avg, 2),
                "pct_below": round((avg - latest["price"]) / avg * 100, 1),
            })

    return sorted(cheap, key=lambda x: -x["pct_below"])


def get_catalog_price(chain: str, store_id: str, product_name: str,
                      db_path: Path = DEFAULT_DB) -> Optional[float]:
    """Return today's catalog price for a product by fuzzy name match."""
    today_str = date.today().isoformat()
    name_lower = product_name.lower()
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT name, price FROM store_price_catalog
            WHERE chain=? AND store_id=? AND scraped_date=?
              AND price IS NOT NULL
            ORDER BY id DESC
        """, (chain, store_id, today_str)).fetchall()
    for row in rows:
        if any(w in row["name"].lower() for w in name_lower.split() if len(w) >= 4):
            return row["price"]
    return None


def lookup_store_product(chain: str, store_id: str, ingredient_name: str,
                          db_path: Path = DEFAULT_DB) -> Optional[dict]:
    """
    Find the best-matching store product for an ingredient.
    Returns {"name": str, "price": float, "compare_price": str, "unit": str} or None.
    Tries today's data first, falls back to most recent scrape.
    """
    name_lower = ingredient_name.lower()
    # Use longest ingredient word for the main search (avoids common words)
    words = [w for w in name_lower.split() if len(w) >= 4]
    if not words:
        return None

    with get_connection(db_path) as conn:
        # Find most recent scrape date for this store
        latest = conn.execute("""
            SELECT MAX(scraped_date) FROM store_price_catalog
            WHERE chain=? AND store_id=?
        """, (chain, store_id)).fetchone()[0]
        if not latest:
            return None

        rows = conn.execute("""
            SELECT name, price, compare_price, unit FROM store_price_catalog
            WHERE chain=? AND store_id=? AND scraped_date=? AND price IS NOT NULL
        """, (chain, store_id, latest)).fetchall()

    best_match = None
    best_score = 0
    for row in rows:
        product_lower = row["name"].lower()
        # Count how many ingredient words appear in the product name
        score = sum(1 for w in words if w in product_lower)
        if score > best_score:
            best_score = score
            best_match = dict(row)

    return best_match if best_score > 0 else None


def store_price_catalog_stats(chain: str, store_id: str,
                               db_path: Path = DEFAULT_DB) -> dict:
    """Return summary stats for a store's price catalog."""
    with get_connection(db_path) as conn:
        row = conn.execute("""
            SELECT COUNT(DISTINCT product_code) as products,
                   COUNT(DISTINCT scraped_date) as scrape_days,
                   MIN(scraped_date) as first_scrape,
                   MAX(scraped_date) as last_scrape
            FROM store_price_catalog
            WHERE chain=? AND store_id=?
        """, (chain, store_id)).fetchone()
    return dict(row) if row else {}


PANTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS pantry (
    item          TEXT PRIMARY KEY COLLATE NOCASE,
    quantity      TEXT,
    unit          TEXT,
    in_stock      INTEGER NOT NULL DEFAULT 1,
    last_updated  TEXT NOT NULL DEFAULT (date('now'))
);
"""


def pantry_seed_from_list(items: list[str], db_path: Path = DEFAULT_DB) -> int:
    """INSERT OR IGNORE each item into pantry with in_stock=1. Return rows inserted."""
    inserted = 0
    with get_connection(db_path) as conn:
        for item in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO pantry (item, in_stock) VALUES (?, 1)",
                (item,),
            )
            inserted += cur.rowcount
    return inserted


def pantry_list(in_stock: bool | None = None,
                db_path: Path = DEFAULT_DB) -> list[dict]:
    """List pantry rows. `in_stock`: True → only in_stock=1, False → only =0, None → all."""
    with get_connection(db_path) as conn:
        if in_stock is True:
            rows = conn.execute(
                "SELECT item, quantity, unit, in_stock, last_updated "
                "FROM pantry WHERE in_stock = 1 ORDER BY item"
            ).fetchall()
        elif in_stock is False:
            rows = conn.execute(
                "SELECT item, quantity, unit, in_stock, last_updated "
                "FROM pantry WHERE in_stock = 0 ORDER BY item"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT item, quantity, unit, in_stock, last_updated "
                "FROM pantry ORDER BY item"
            ).fetchall()
    return [dict(r) for r in rows]


def pantry_toggle(item: str, in_stock: bool,
                  db_path: Path = DEFAULT_DB) -> None:
    """Upsert in_stock for `item`. Preserves quantity/unit if row exists."""
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO pantry (item, in_stock, last_updated) "
            "VALUES (?, ?, date('now')) "
            "ON CONFLICT(item) DO UPDATE SET "
            "in_stock=excluded.in_stock, last_updated=excluded.last_updated",
            (item, 1 if in_stock else 0),
        )


def pantry_use(item: str, db_path: Path = DEFAULT_DB) -> None:
    """Set in_stock=0 for `item`. No-op if not present."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE pantry SET in_stock = 0, last_updated = date('now') "
            "WHERE item = ?",
            (item,),
        )


def pantry_restock(item: str, quantity: str | None = None,
                   unit: str | None = None,
                   db_path: Path = DEFAULT_DB) -> None:
    """Set in_stock=1 for `item`. If quantity/unit provided, update those too."""
    with get_connection(db_path) as conn:
        if quantity is None and unit is None:
            conn.execute(
                "INSERT INTO pantry (item, in_stock, last_updated) "
                "VALUES (?, 1, date('now')) "
                "ON CONFLICT(item) DO UPDATE SET "
                "in_stock=1, last_updated=date('now')",
                (item,),
            )
        else:
            conn.execute(
                "INSERT INTO pantry (item, quantity, unit, in_stock, last_updated) "
                "VALUES (?, ?, ?, 1, date('now')) "
                "ON CONFLICT(item) DO UPDATE SET "
                "quantity=COALESCE(excluded.quantity, pantry.quantity), "
                "unit=COALESCE(excluded.unit, pantry.unit), "
                "in_stock=1, last_updated=date('now')",
                (item, quantity, unit),
            )


def pantry_ensure_seeded(config_items: list[str],
                         db_path: Path = DEFAULT_DB) -> None:
    """If pantry is empty, seed from config_items list."""
    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM pantry").fetchone()[0]
    if count == 0:
        n = pantry_seed_from_list(config_items, db_path)
        logger.info("Seeded pantry with %d items.", n)
