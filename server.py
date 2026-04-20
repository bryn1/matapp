#!/usr/bin/env python3
"""
server.py — Flask web server för matapp med per-användare krypterad data.

Auth model:
- Bcrypt password hashing
- PBKDF2 key derivation (AES-256-GCM)
- Server-side session dict; key never stored to disk
- Admin cannot decrypt other users' data
"""

import base64
import datetime
import json
import logging
import secrets
import sys
import time
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, make_response, redirect, render_template, request, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import auth
import db
import shopping

BASE_DIR = Path(__file__).parent

# ─── Persistent secret key ────────────────────────────────────────────────────
# Generated once, stored on disk so sessions survive server restarts.
# enc_key (derived from user password) is embedded in the signed cookie payload.
# The cookie is httponly+samesite — not accessible to JS. HTTPS protects in transit.
_SECRET_FILE = BASE_DIR / "matapp.secret"
if _SECRET_FILE.exists():
    _PERSISTENT_SECRET = _SECRET_FILE.read_text().strip()
else:
    _PERSISTENT_SECRET = secrets.token_hex(32)
    _SECRET_FILE.write_text(_PERSISTENT_SECRET)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False
app.config["SECRET_KEY"] = _PERSISTENT_SECRET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "logs" / "server.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ─── Session store (signed cookie, survives restart) ─────────────────────────
# enc_key is embedded in the signed token — not stored on disk, not accessible
# to server unless it has the cookie. Server restart does NOT invalidate sessions.
_SESSION_TTL = 7 * 86_400          # 7 days
_signer = URLSafeTimedSerializer(_PERSISTENT_SECRET, salt="mat-session")


def _create_session(user_id: int, username: str, enc_key: bytes, is_admin: bool) -> str:
    payload = {
        "uid": user_id,
        "usr": username,
        "key": base64.b64encode(enc_key).decode(),
        "adm": int(is_admin),
    }
    return _signer.dumps(payload)


def _get_session(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = _signer.loads(token, max_age=_SESSION_TTL)
        return {
            "user_id":  payload["uid"],
            "username": payload["usr"],
            "enc_key":  base64.b64decode(payload["key"]),
            "is_admin": bool(payload["adm"]),
            "ts":       time.time(),
        }
    except (BadSignature, SignatureExpired, KeyError):
        return None


def _delete_session(token: str) -> None:
    pass  # Sessions are stateless — logout is handled by clearing the cookie


def current_session() -> dict | None:
    return _get_session(request.cookies.get("mat_token"))


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        sess = current_session()
        if not sess:
            if request.path.startswith("/api/"):
                return jsonify(error="Ej inloggad"), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        sess = current_session()
        if not sess or not sess["is_admin"]:
            return jsonify(error="Kräver admin"), 403
        return f(*args, **kwargs)
    return wrapper


# ─── Per-user data helpers ────────────────────────────────────────────────────

def _load(sess: dict, data_type: str) -> dict | list:
    blob = db.get_user_blob(sess["user_id"], data_type)
    if not blob:
        return _default_data(data_type)
    try:
        return auth.decrypt_json(blob, sess["enc_key"])
    except Exception:
        logger.warning(f"Decrypt failed for user {sess['user_id']} type {data_type}")
        return _default_data(data_type)


def _save(sess: dict, data_type: str, data) -> None:
    blob = auth.encrypt_json(data, sess["enc_key"])
    db.set_user_blob(sess["user_id"], data_type, blob)


def _default_data(data_type: str) -> dict | list:
    defaults = {
        "config":      {"setup_complete": False, "store_name": "", "store_id": None,
                        "store_address": "", "store_chain": "other",
                        "diet": ["vegetarian", "chicken", "fish"],
                        "household": {"adults": 2, "kids": [3, 6]},
                        "adults": 2, "kids_count": 2,
                        "recipes_per_week": 5, "budget_sek": None,
                        "fast_days": 2, "medium_days": 2, "long_days": 1,
                        "include_dessert": False, "include_starter": False,
                        "festive_meals": False,
                        "allergies": [], "exclude_items": [],
                        "occasion": None, "occasion_week": None},
        "weekly_plan": {"week_key": "", "recipes": [], "offers": []},
        "shopping":    {"week_num": 0, "year": 0, "items": [], "_next_id": 1},
        "staples":     {"items": []},
        "watchlist":   {"items": []},
        "memory":      {"items": {}},
    }
    return defaults.get(data_type, {})


def _current_week() -> tuple[int, int]:
    iso = datetime.date.today().isocalendar()
    return iso[1], iso[0]   # week_num, year


def _flatten_and_save_shopping(sess: dict, shopping_items: list[dict],
                                week_num: int, year: int) -> list[dict]:
    """Flatten [{category, items:[]}] from build_shopping_list, assign IDs, save."""
    shop = _load(sess, "shopping")
    next_id = shop.get("_next_id", 1)
    flat = []
    for cat_group in shopping_items:
        category = cat_group.get("category", "övrigt")
        for item in cat_group.get("items", []):
            flat.append({**item, "category": category, "id": next_id, "checked": False})
            next_id += 1
    _save(sess, "shopping", {"week_num": week_num, "year": year,
                              "items": flat, "_next_id": next_id})
    return flat


def _week_key(week_num: int, year: int) -> str:
    return f"{year}-W{week_num:02d}"


# ─── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET"])
def login_page():
    if current_session():
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify(error="Användarnamn och lösenord krävs"), 400
    if len(password) < 6:
        return jsonify(error="Lösenord måste vara minst 6 tecken"), 400
    if db.get_user_by_username(username):
        return jsonify(error="Användarnamnet är redan taget"), 409

    kdf_salt  = auth.new_salt()
    pw_hash   = auth.hash_password(password)
    user_id   = db.create_user(username, pw_hash, kdf_salt)
    enc_key   = auth.derive_key(password, kdf_salt)
    token     = _create_session(user_id, username, enc_key, False)

    # Seed default staples for new user (from global config)
    try:
        cfg = _load_config()
        sess = {"user_id": user_id, "enc_key": enc_key}
        staples_data = {"items": cfg.get("staples", [])}
        _save(sess, "staples", staples_data)
    except Exception:
        pass

    resp = make_response(jsonify(ok=True, username=username, id=user_id))
    resp.set_cookie("mat_token", token, httponly=True, samesite="Lax", max_age=_SESSION_TTL)
    return resp


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data     = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = db.get_user_by_username(username)
    if not user or not auth.verify_password(password, user["password_hash"]):
        return jsonify(error="Fel användarnamn eller lösenord"), 401

    enc_key = auth.derive_key(password, user["kdf_salt"])
    token   = _create_session(user["id"], user["username"], enc_key, bool(user["is_admin"]))

    resp = make_response(jsonify(ok=True, username=user["username"],
                                 id=user["id"], is_admin=bool(user["is_admin"])))
    resp.set_cookie("mat_token", token, httponly=True, samesite="Lax", max_age=_SESSION_TTL)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    token = request.cookies.get("mat_token")
    if token:
        _delete_session(token)
    resp = make_response(jsonify(ok=True))
    resp.delete_cookie("mat_token")
    return resp


@app.route("/api/auth/me")
def api_auth_me():
    sess = current_session()
    if not sess:
        return jsonify(logged_in=False)
    cfg = _load(sess, "config")
    return jsonify(logged_in=True, username=sess["username"], is_admin=sess["is_admin"],
                   setup_complete=cfg.get("setup_complete", False))


# ─── Store geo cache (DB-backed, in-memory L1 for hot zips) ──────────────────

# L1 memory cache — avoids DB hit on repeated searches within a session
_zip_mem_cache: dict[str, dict] = {}
_ZIP_MEM_TTL = 300  # 5 min — after this, re-read from DB

# Background refresh set — zip codes currently being refreshed
_refreshing: set[str] = set()


_PRIORITY_CHAINS = {"willys", "ica", "coop", "lidl", "hemkop", "citygross", "netto", "mathem"}
_GAS_KEYWORDS    = {"shell", "circle k", "preem", "ok ", "st1", "ingo", "tesla", "q8",
                    "statoil", "tanka", "bensin", "macken"}

def _chain_from_name(name: str) -> str:
    n = name.lower()
    if "willys" in n or "willy:" in n: return "willys"
    if "ica" in n:        return "ica"
    if "coop" in n:       return "coop"
    if "lidl" in n:       return "lidl"
    if "hemköp" in n or "hemkop" in n: return "hemkop"
    if "city gross" in n or "citygross" in n: return "citygross"
    if "netto" in n:      return "netto"
    if "mathem" in n:     return "mathem"
    if "maxi" in n:       return "ica"      # ICA Maxi
    if "kvantum" in n:    return "ica"      # ICA Kvantum
    return "other"

def _is_gas_station(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in _GAS_KEYWORDS)


def _zip_coarse_coords(zip_code: str, _req=None) -> tuple[float, float, str] | None:
    """Precise zip centroid from local GeoNames DB (~0ms).
    Falls back to zippopotam.us if not found locally.
    """
    row = db.zip_lookup(zip_code)
    if row:
        return row["lat"], row["lon"], row["place_name"]
    # Fallback: zippopotam.us API
    import requests as _rq
    try:
        r = _rq.get(f"https://api.zippopotam.us/se/{zip_code}", timeout=8)
        if r.status_code == 200:
            places = r.json().get("places", [])
            if places:
                p = places[0]
                return float(p["latitude"]), float(p["longitude"]), p.get("place name", "")
    except Exception:
        pass
    return None




_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def _overpass_query(query: str) -> list[dict]:
    """Run an Overpass query against all mirrors in parallel; return first success."""
    import concurrent.futures
    import requests as _rq

    def _try(endpoint: str):
        r = _rq.post(endpoint, data={"data": query}, timeout=12)
        if not r.text.strip():
            raise ValueError("empty body")
        return r.json().get("elements", [])

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_try, ep): ep for ep in _OVERPASS_ENDPOINTS}
        for fut in concurrent.futures.as_completed(futs, timeout=14):
            ep = futs[fut]
            try:
                els = fut.result()
                logger.info(f"Overpass OK from {ep} ({len(els)} elements)")
                for f in futs:
                    f.cancel()
                return els
            except Exception as e:
                logger.warning(f"Overpass {ep}: {e}")
    return []


def _zip_precise_centroid(zip_osm: str, city_lat: float, city_lon: float) -> tuple[float, float]:
    """Refine postal code centroid using addr:postcode nodes from OSM.
    Returns refined (lat, lon) or original city coords if no data found.
    """
    bbox = (city_lat - 0.5, city_lon - 0.5, city_lat + 0.5, city_lon + 0.5)
    q = (
        f'[out:json][timeout:10];'
        f'node["addr:postcode"="{zip_osm}"]'
        f'({bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f});'
        f'out 30;'
    )
    els = _overpass_query(q)
    if els:
        avg_lat = sum(e["lat"] for e in els) / len(els)
        avg_lon = sum(e["lon"] for e in els) / len(els)
        logger.info(f"Centroid from {len(els)} addr nodes: {avg_lat:.4f},{avg_lon:.4f}")
        return avg_lat, avg_lon
    return city_lat, city_lon


def _overpass_stores(lat: float, lon: float, radius_m: int) -> list[dict]:
    """Grocery stores within radius_m metres of (lat, lon).
    Filters gas stations; prioritises known supermarket chains; returns up to 10.
    """
    query = (
        f"[out:json][timeout:12];"
        f"(node[\"shop\"~\"supermarket|convenience|grocery\"][\"name\"]"
        f"(around:{radius_m},{lat},{lon});"
        f"way[\"shop\"~\"supermarket|convenience|grocery\"][\"name\"]"
        f"(around:{radius_m},{lat},{lon}););"
        f"out center;"
    )
    elements = _overpass_query(query)

    stores = []
    for el in elements:
        tags   = el.get("tags", {})
        name   = tags.get("name", "")
        if not name or _is_gas_station(name):
            continue
        lat_el = el.get("lat") or (el.get("center") or {}).get("lat")
        lon_el = el.get("lon") or (el.get("center") or {}).get("lon")
        addr_parts = [
            tags.get("addr:street", ""),
            tags.get("addr:housenumber", ""),
            tags.get("addr:postcode", ""),
            tags.get("addr:city", ""),
        ]
        address = " ".join(p for p in addr_parts if p).strip()
        chain   = _chain_from_name(name)
        dlat    = (lat_el - lat) * 111_320 if lat_el else 0
        dlon    = (lon_el - lon) * 111_320 * 0.64 if lon_el else 0
        dist    = int((dlat**2 + dlon**2) ** 0.5)
        stores.append({
            "id":      f"osm_{el['id']}",
            "name":    name,
            "chain":   chain,
            "address": address,
            "dist_m":  dist,
        })

    # Sort: priority chains bubble up within same 500m bucket
    def _sort_key(s):
        return (s["dist_m"] // 500, 0 if s["chain"] in _PRIORITY_CHAINS else 1, s["dist_m"])

    stores.sort(key=_sort_key)
    return stores[:10]


def _fetch_stores_for_zip(zip_code: str) -> list[dict]:
    """Resolve zip → coords via zippopotam, query local swedish_stores DB for nearest stores.
    Falls back to live Overpass if the local DB is empty (not yet populated).
    No Overpass centroid call — city-level coords are sufficient with a 50km radius.
    """
    coarse = _zip_coarse_coords(zip_code)
    if not coarse:
        raise ValueError(f"Okänt postnummer: {zip_code}")
    city_lat, city_lon, city = coarse

    # Primary: local stores DB (sub-millisecond)
    if db.stores_count() > 0:
        stores = db.stores_nearest(city_lat, city_lon, limit=10)
        if not stores:
            stores = db.stores_nearest(city_lat, city_lon, limit=10, radius_km=150)
    else:
        # Fallback: live Overpass (DB not yet populated — run populate_stores.py)
        logger.warning("swedish_stores DB is empty — falling back to live Overpass")
        stores = _overpass_stores(city_lat, city_lon, 8000)
        if not stores:
            stores = _overpass_stores(city_lat, city_lon, 20_000)

    db.geo_cache_put(zip_code, city, city_lat, city_lon, stores)
    logger.info(f"{zip_code}: {len(stores)} stores cached (lat={city_lat:.4f}, lon={city_lon:.4f})")
    return stores


def _background_refresh(zip_code: str) -> None:
    """Run in a daemon thread — refresh stale zip without blocking the request."""
    if zip_code in _refreshing:
        return
    _refreshing.add(zip_code)
    try:
        _fetch_stores_for_zip(zip_code)
        _zip_mem_cache.pop(zip_code, None)  # invalidate L1 so next request reads fresh DB
    except Exception as e:
        logger.warning(f"Background refresh {zip_code}: {e}")
    finally:
        _refreshing.discard(zip_code)


@app.route("/api/stores/search")
@login_required
def api_stores_search():
    """Search stores by postal code.
    - L1 memory cache (5 min): instant
    - DB cache fresh (<7 days): instant, no background work
    - DB cache stale (7-30 days): instant, trigger background refresh
    - DB cache expired (>30 days) or miss: fetch synchronously, store in DB
    """
    import threading

    zip_code = request.args.get("zip", "").strip().replace(" ", "")
    if not zip_code:
        return jsonify([])

    # L1: in-memory (hot path)
    mem = _zip_mem_cache.get(zip_code)
    if mem and (time.time() - mem["ts"]) < _ZIP_MEM_TTL:
        return jsonify(mem["stores"])

    # L2: DB cache
    cached = db.geo_cache_get(zip_code)
    if cached:
        stores = cached["stores"]
        # Populate L1
        _zip_mem_cache[zip_code] = {"stores": stores, "ts": time.time()}

        if cached["force_refresh"]:
            # >30 days stale — still serve from cache but must refresh soon
            logger.info(f"{zip_code}: DB cache expired ({cached['age_days']}d) — serving stale, background refresh")
            threading.Thread(target=_background_refresh, args=(zip_code,), daemon=True).start()
        elif cached["needs_refresh"] and zip_code not in _refreshing:
            # 7-30 days — serve instantly, refresh quietly in background
            logger.info(f"{zip_code}: DB cache stale ({cached['age_days']}d) — background refresh")
            threading.Thread(target=_background_refresh, args=(zip_code,), daemon=True).start()
        else:
            logger.info(f"{zip_code}: DB cache hit ({cached['age_days']}d old)")

        return jsonify(stores)

    # Cache miss — fetch synchronously
    try:
        stores = _fetch_stores_for_zip(zip_code)
        _zip_mem_cache[zip_code] = {"stores": stores, "ts": time.time()}
        return jsonify(stores)
    except ValueError as e:
        return jsonify(error=str(e)), 404
    except Exception as e:
        logger.exception(f"stores/search error for {zip_code}")
        return jsonify(error=str(e)), 502


@app.route("/api/setup/store", methods=["POST"])
@login_required
def api_setup_store():
    sess = current_session()
    data = request.get_json() or {}
    store_id        = data.get("store_id")
    store_name      = (data.get("store_name") or "").strip()
    store_addr      = (data.get("store_address") or "").strip()
    store_chain     = (data.get("chain") or "other").strip()
    chain_store_id  = (data.get("chain_store_id") or "").strip() or None
    if not store_id or not store_name:
        return jsonify(error="store_id och store_name krävs"), 400

    cfg = _load(sess, "config")
    cfg.update({
        "setup_complete":  True,
        "store_id":        store_id,
        "store_name":      store_name,
        "store_address":   store_addr,
        "store_chain":     store_chain,
        "chain_store_id":  chain_store_id,
    })
    _save(sess, "config", cfg)
    return jsonify(ok=True)


# ─── Settings ─────────────────────────────────────────────────────────────────

_SETTINGS_FIELDS = {
    "adults", "kids_count", "recipes_per_week", "budget_sek",
    "fast_days", "medium_days", "long_days",
    "include_dessert", "include_starter", "festive_meals",
    "diet", "allergies", "exclude_items",
}

@app.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get():
    sess = current_session()
    cfg  = _load(sess, "config")
    defaults = _default_data("config")
    result = {k: cfg.get(k, defaults.get(k)) for k in _SETTINGS_FIELDS}
    return jsonify(result)


@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings_post():
    sess = current_session()
    data = request.get_json() or {}
    cfg  = _load(sess, "config")
    for k in _SETTINGS_FIELDS:
        if k in data:
            cfg[k] = data[k]
    _save(sess, "config", cfg)
    return jsonify(ok=True)


# ─── Plan swap / occasion ──────────────────────────────────────────────────────

@app.route("/api/plan/next-week", methods=["POST"])
@login_required
def api_plan_next_week():
    """Generate plan for next week (week_key = current+1)."""
    sess = current_session()
    cfg  = _load(sess, "config")
    if not cfg.get("setup_complete"):
        return jsonify(error="Sätt upp din butik först"), 400

    import datetime as _dt
    today      = _dt.date.today()
    next_monday = today + _dt.timedelta(days=(7 - today.weekday()))
    iso        = next_monday.isocalendar()
    week_num, year = iso[1], iso[0]
    wkey = _week_key(week_num, year)

    try:
        from campaigns import fetch_campaigns
        from recipes import select_recipes
        from shopping import build_shopping_list

        offers = fetch_campaigns(
            store_name=cfg.get("store_name", ""),
            chain=cfg.get("store_chain", "willys"),
            chain_store_id=cfg.get("chain_store_id"),
        )
        offer_terms = list({
            w for o in offers
            for w in o["name"].lower().split() if len(w) >= 4
        })
        recent_names = db.get_recent_recipe_names(weeks_back=8)
        catalog_candidates = db.get_catalog_candidates(
            offer_terms=offer_terms, exclude_names=recent_names, limit=150,
        )
        if not catalog_candidates:
            return jsonify(error="Receptkatalogen är tom"), 500

        global_cfg = _load_config()
        recipes = select_recipes(catalog_candidates, offers, settings=cfg, allowed_diets=cfg.get("diet"))

        if not recipes:
            return jsonify(error="Inga recept hittades"), 500

        shopping_items = build_shopping_list(recipes, offers, global_cfg.get("pantry_items", []), chain=cfg.get("store_chain"), store_id=cfg.get("chain_store_id"))
        plan_data = {"week_key": wkey, "recipes": recipes, "offers": offers}
        _save(sess, "weekly_plan", plan_data)

        items = _flatten_and_save_shopping(sess, shopping_items, week_num, year)

        # Bump times_used + last_used for the catalog rows we picked, so the
        # rotation penalty in get_catalog_candidates kicks in next time.
        try:
            picked = {r.get("name", "").lower() for r in recipes}
            for cand in catalog_candidates:
                if cand.get("name", "").lower() in picked and cand.get("url"):
                    db.mark_catalog_used(cand["url"])
        except Exception:
            logger.exception("mark_catalog_used failed (non-fatal)")

        return jsonify(ok=True, recipe_count=len(recipes))
    except Exception as e:
        logger.exception("next-week plan failed")
        return jsonify(error=str(e)), 500


@app.route("/api/plan/swap-recipe", methods=["POST"])
@login_required
def api_plan_swap_recipe():
    """Replace one recipe in the current week's plan."""
    sess = current_session()
    cfg  = _load(sess, "config")
    data = request.get_json() or {}
    idx  = data.get("recipe_index")
    if idx is None:
        return jsonify(error="recipe_index krävs"), 400

    week_num, year = _current_week()
    wkey = _week_key(week_num, year)
    plan = _load(sess, "weekly_plan")
    if plan.get("week_key") != wkey or not plan.get("recipes"):
        return jsonify(error="Ingen plan för denna vecka"), 404

    recipes = plan["recipes"]
    if not (0 <= idx < len(recipes)):
        return jsonify(error="Ogiltigt recept-index"), 400

    try:
        from campaigns import fetch_campaigns
        from recipes import select_recipes
        from shopping import build_shopping_list

        offers = fetch_campaigns(
            store_name=cfg.get("store_name", ""),
            chain=cfg.get("store_chain", "willys"),
            chain_store_id=cfg.get("chain_store_id"),
        )
        offer_terms = list({
            w for o in offers
            for w in o["name"].lower().split() if len(w) >= 4
        })
        existing_names = [r.get("name", "") for i, r in enumerate(recipes) if i != idx]
        global_cfg = _load_config()
        catalog_candidates = db.get_catalog_candidates(
            offer_terms=offer_terms, exclude_names=existing_names, limit=50,
        )
        new_recipes = select_recipes(catalog_candidates, offers, settings=cfg,
                                     exclude_names=existing_names, count=1,
                                     allowed_diets=cfg.get("diet"))
        if not new_recipes:
            return jsonify(error="Kunde inte hitta nytt recept"), 500

        recipes[idx] = new_recipes[0]
        plan["recipes"] = recipes
        _save(sess, "weekly_plan", plan)

        # Rebuild shopping list
        shopping_items = build_shopping_list(recipes, offers, global_cfg.get("pantry_items", []), chain=cfg.get("store_chain"), store_id=cfg.get("chain_store_id"))
        _flatten_and_save_shopping(sess, shopping_items, week_num, year)

        # Mark the swapped-in recipe as used.
        try:
            swapped_name = (new_recipes[0].get("name") or "").lower()
            for cand in catalog_candidates:
                if cand.get("name", "").lower() == swapped_name and cand.get("url"):
                    db.mark_catalog_used(cand["url"])
                    break
        except Exception:
            logger.exception("mark_catalog_used failed (non-fatal)")

        return jsonify(ok=True, new_recipe=new_recipes[0].get("name"))
    except Exception as e:
        logger.exception("swap-recipe failed")
        return jsonify(error=str(e)), 500


@app.route("/api/plan/set-occasion", methods=["POST"])
@login_required
def api_plan_set_occasion():
    sess = current_session()
    data = request.get_json() or {}
    description = (data.get("description") or "").strip()
    weeks_ahead = int(data.get("weeks_ahead") or 0)
    if not description:
        return jsonify(error="Beskrivning saknas"), 400

    import datetime as _dt
    target = _dt.date.today() + _dt.timedelta(weeks=weeks_ahead)
    iso = target.isocalendar()
    occasion_week = _week_key(iso[1], iso[0])

    cfg = _load(sess, "config")
    cfg["occasion"]      = description
    cfg["occasion_week"] = occasion_week
    _save(sess, "config", cfg)
    return jsonify(ok=True, week=occasion_week)


# ─── Weekly plan generation ───────────────────────────────────────────────────

@app.route("/api/plan/generate", methods=["POST"])
@login_required
def api_plan_generate():
    sess = current_session()
    cfg  = _load(sess, "config")
    if not cfg.get("setup_complete"):
        return jsonify(error="Sätt upp din butik först"), 400

    week_num, year = _current_week()
    wkey = _week_key(week_num, year)

    force = (request.get_json(silent=True) or {}).get("force", False)
    plan = _load(sess, "weekly_plan")
    if not force and plan.get("week_key") == wkey and plan.get("recipes"):
        return jsonify(ok=True, message="Planen för veckan finns redan")

    try:
        from campaigns import fetch_campaigns
        from recipes import select_recipes
        from shopping import build_shopping_list

        offers = fetch_campaigns(
            store_name=cfg.get("store_name", ""),
            chain=cfg.get("store_chain", "willys"),
            chain_store_id=cfg.get("chain_store_id"),
        )

        # Build offer terms for catalog scoring
        offer_terms = list({
            w for o in offers
            for w in o["name"].lower().split() if len(w) >= 4
        })

        # Fetch catalog candidates scored by offer overlap
        recent_names = db.get_recent_recipe_names(weeks_back=8)
        catalog_candidates = db.get_catalog_candidates(
            offer_terms=offer_terms,
            exclude_names=recent_names,
            limit=150,
        )

        if not catalog_candidates:
            return jsonify(error="Receptkatalogen är tom — kör scrape.py för att fylla den"), 500

        global_cfg = _load_config()
        recipes = select_recipes(catalog_candidates, offers,
                                  week_num=week_num, settings=cfg,
                                  allowed_diets=cfg.get("diet"))

        if not recipes:
            return jsonify(error="Inga recept hittades"), 500

        shopping_items = build_shopping_list(recipes, offers, global_cfg.get("pantry_items", []), chain=cfg.get("store_chain"), store_id=cfg.get("chain_store_id"))

        # Save weekly plan
        plan_data = {"week_key": wkey, "recipes": recipes, "offers": offers}
        _save(sess, "weekly_plan", plan_data)

        # Save shopping list
        items = _flatten_and_save_shopping(sess, shopping_items, week_num, year)

        # Bump times_used + last_used for the catalog rows we picked.
        try:
            picked = {r.get("name", "").lower() for r in recipes}
            for cand in catalog_candidates:
                if cand.get("name", "").lower() in picked and cand.get("url"):
                    db.mark_catalog_used(cand["url"])
        except Exception:
            logger.exception("mark_catalog_used failed (non-fatal)")

        return jsonify(ok=True, recipe_count=len(recipes), item_count=len(items))
    except Exception as e:
        logger.exception("Plan generation failed")
        return jsonify(error=str(e)), 500


# ─── KPI / Deals ──────────────────────────────────────────────────────────────

@app.route("/api/deals")
@login_required
def api_deals():
    """Return current week's deals with KPI scoring (discount %, value score)."""
    sess = current_session()
    cfg  = _load(sess, "config")
    if not cfg.get("store_name"):
        return jsonify([])
    try:
        from willys import fetch_offers
        offers = fetch_offers(cfg["store_name"])

        # Enrich with price history KPI
        result = []
        for o in offers[:30]:
            price   = o.get("price") or 0
            orig    = o.get("original_price") or price
            disc_pct = round((orig - price) / orig * 100, 1) if orig and orig > price else 0

            # Price history from shared table
            norm = o.get("name", "").lower().split()[0]
            history = db.get_price_history(norm)
            avg_price  = (sum(h["price"] for h in history) / len(history)) if history else None
            below_avg  = round((avg_price - price) / avg_price * 100, 1) if avg_price and avg_price > price else 0
            value_score = round(disc_pct * 0.6 + max(below_avg, 0) * 0.4, 1)

            result.append({**o, "disc_pct": disc_pct, "below_avg_pct": below_avg,
                           "value_score": value_score, "historically_cheap": below_avg > 8})

        result.sort(key=lambda x: x["value_score"], reverse=True)
        return jsonify(result)
    except Exception as e:
        return jsonify(error=str(e)), 500


# ─── Main page ────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        with open(BASE_DIR / "config.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.route("/")
@login_required
def index():
    sess    = current_session()
    cfg     = _load(sess, "config")
    week_num, year = _current_week()

    plan     = _load(sess, "weekly_plan")
    wkey     = _week_key(week_num, year)
    recipes  = []
    offers   = []
    if plan.get("week_key") == wkey:
        recipes = plan.get("recipes", [])
        offers  = plan.get("offers", [])

    # Enrich recipes with DB rating from plan blob
    for r in recipes:
        r.setdefault("_rating", r.get("rating"))
        r.setdefault("_db_id", r.get("id") or r.get("name", "").replace(" ", "-").lower()[:30])

    shopping    = _build_shopping_display(sess)
    suggestions = _build_staple_suggestions(sess)

    # Watchlist alerts
    watchlist_data = _load(sess, "watchlist")
    watchlist_alerts = db.get_watchlist_alerts_for(
        [w["query"] for w in watchlist_data.get("items", [])]
    ) if watchlist_data.get("items") else []

    return render_template(
        "index.html",
        username=sess["username"],
        is_admin=sess["is_admin"],
        setup_complete=cfg.get("setup_complete", False),
        store_name=cfg.get("store_name", ""),
        week_num=week_num,
        year=year,
        recipes=recipes,
        offers=offers,
        shopping=shopping,
        smart_suggestions=suggestions,
        watchlist_alerts=watchlist_alerts,
        has_data=bool(recipes),
        cache_bust=int(time.time()),
    )


def _build_shopping_display(sess: dict) -> list[dict]:
    """Convert flat shopping items into category-grouped list for template."""
    shop = _load(sess, "shopping")
    items = shop.get("items", [])
    cats: dict[str, list] = {}
    for item in items:
        cat = item.get("category") or "övrigt"
        cats.setdefault(cat, []).append(item)
    return [{"category": cat, "items": items} for cat, items in sorted(cats.items())]


def _build_staple_suggestions(sess: dict) -> list[dict]:
    staples_data = _load(sess, "staples")
    today = datetime.date.today()
    suggestions = []
    for s in staples_data.get("items", []):
        last = s.get("last_bought")
        interval = s.get("interval_days", 30)
        if last:
            last_date = datetime.date.fromisoformat(last)
            due = last_date + datetime.timedelta(days=interval)
            days_over = (today - due).days
            if days_over >= -3:  # due within 3 days or overdue
                suggestions.append({"item": s["item"], "days_overdue": days_over, "urgent": days_over > 0})
        else:
            # Never bought — suggest
            suggestions.append({"item": s["item"], "days_overdue": 0, "urgent": False})
    return suggestions[:5]


# ─── Shopping list routes ─────────────────────────────────────────────────────

@app.route("/api/state")
@login_required
def api_state():
    sess = current_session()
    week_num, year = _current_week()
    plan = _load(sess, "weekly_plan")
    wkey = _week_key(week_num, year)
    recipes = plan.get("recipes", []) if plan.get("week_key") == wkey else []
    shopping = _build_shopping_display(sess)
    suggestions = _build_staple_suggestions(sess)
    return jsonify(week_num=week_num, year=year, recipes=recipes,
                   shopping=shopping, smart_suggestions=suggestions)


@app.route("/api/shopping/toggle", methods=["POST"])
@login_required
def api_shopping_toggle():
    sess = current_session()
    data    = request.get_json() or {}
    item_id = data.get("id")
    checked = data.get("checked", False)
    if item_id is None:
        return jsonify(error="id required"), 400

    shop = _load(sess, "shopping")
    item_name = None
    for it in shop.get("items", []):
        if it["id"] == int(item_id):
            it["checked"] = checked
            item_name = it.get("item")
            break
    _save(sess, "shopping", shop)

    if checked and item_name:
        mem = _load(sess, "memory")
        items = mem.setdefault("items", {})
        rec = items.setdefault(item_name, {"times_bought": 0, "last_bought": None, "dates": []})
        today = datetime.date.today().isoformat()
        rec["times_bought"] += 1
        rec["last_bought"] = today
        rec["dates"] = (rec.get("dates") or [])[-29:] + [today]
        _save(sess, "memory", mem)

    return jsonify(ok=True)


@app.route("/api/shopping/add", methods=["POST"])
@login_required
def api_shopping_add():
    sess = current_session()
    data = request.get_json() or {}
    item = (data.get("item") or "").strip()
    quantity = (data.get("quantity") or "").strip()
    if not item:
        return jsonify(error="item required"), 400

    week_num, year = _current_week()
    shop = _load(sess, "shopping")
    new_id = shop.get("_next_id", 1)
    normalized_item = shopping._normalize_item_name(item)
    category = shopping._guess_category(item)

    for existing in shop.get("items", []):
        if shopping._normalize_item_name(existing['item']) == normalized_item:
            existing['quantity'] = shopping._combine_quantities(existing['quantity'], quantity)
            existing['category'] = category
            return jsonify(ok=True, id=existing['id'], category=category, quantity=existing['quantity'], merged=True)

    shop.setdefault("items", []).append({
        "id": new_id, "item": item, "quantity": quantity,
        "category": category, "on_sale": False, "checked": False,
        "in_pantry": False, "added_manually": True,
    })
    shop["_next_id"] = new_id + 1
    shop["week_num"] = week_num
    shop["year"] = year
    _save(sess, "shopping", shop)
    return jsonify(ok=True, id=new_id, category=category, quantity=quantity, merged=False)


@app.route("/api/shopping/remove", methods=["POST"])
@login_required
def api_shopping_remove():
    sess    = current_session()
    data    = request.get_json() or {}
    item_id = data.get("id")
    if item_id is None:
        return jsonify(error="id required"), 400

    shop = _load(sess, "shopping")
    shop["items"] = [it for it in shop.get("items", []) if it["id"] != int(item_id)]
    _save(sess, "shopping", shop)
    return jsonify(ok=True)


# ─── Recipe rating ────────────────────────────────────────────────────────────

@app.route("/api/recipe/rate", methods=["POST"])
@login_required
def api_recipe_rate():
    sess = current_session()
    data      = request.get_json() or {}
    recipe_id = data.get("recipe_id")
    rating    = data.get("rating")
    if not recipe_id:
        return jsonify(error="recipe_id required"), 400
    try:
        rating = int(rating)
        if not 1 <= rating <= 7:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify(error="rating must be 1-7"), 400

    plan = _load(sess, "weekly_plan")
    for r in plan.get("recipes", []):
        if (r.get("id") or r.get("name", "").replace(" ", "-").lower()[:30]) == recipe_id:
            r["rating"] = rating
            r["_rating"] = rating
            break
    _save(sess, "weekly_plan", plan)
    return jsonify(ok=True)


# ─── Recipe history ───────────────────────────────────────────────────────────

@app.route("/api/recipes/history")
@login_required
def api_recipes_history():
    name    = request.args.get("name", "").strip() or None
    min_r   = request.args.get("min_rating", type=int)
    max_r   = request.args.get("max_rating", type=int)
    rows    = db.get_history(name_filter=name, min_rating=min_r, max_rating=max_r)
    return jsonify(rows)


# ─── Recipe search (shared catalog) ──────────────────────────────────────────

@app.route("/api/recipes/search")
@login_required
def api_recipes_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    terms = [t.strip() for t in q.replace(",", " ").split() if t.strip()]
    results = db.search_catalog_by_ingredients(terms)
    return jsonify(results)


# ─── Watchlist ────────────────────────────────────────────────────────────────

@app.route("/api/watchlist")
@login_required
def api_watchlist():
    sess = current_session()
    data = _load(sess, "watchlist")
    return jsonify(data.get("items", []))


@app.route("/api/watchlist/add", methods=["POST"])
@login_required
def api_watchlist_add():
    sess = current_session()
    data         = request.get_json() or {}
    query        = (data.get("query") or "").strip()
    display_name = (data.get("display_name") or query).strip()
    if not query:
        return jsonify(error="query required"), 400

    wl = _load(sess, "watchlist")
    wl.setdefault("items", [])
    if not any(it["query"] == query for it in wl["items"]):
        wl["items"].append({"query": query, "display_name": display_name,
                             "added_date": datetime.date.today().isoformat()})
    _save(sess, "watchlist", wl)
    return jsonify(ok=True)


@app.route("/api/watchlist/remove", methods=["POST"])
@login_required
def api_watchlist_remove():
    sess  = current_session()
    data  = request.get_json() or {}
    query = (data.get("query") or "").strip()
    wl    = _load(sess, "watchlist")
    wl["items"] = [it for it in wl.get("items", []) if it["query"] != query]
    _save(sess, "watchlist", wl)
    return jsonify(ok=True)


# ─── Staples ──────────────────────────────────────────────────────────────────

@app.route("/api/staples/bought", methods=["POST"])
@login_required
def api_staples_bought():
    sess = current_session()
    data = request.get_json() or {}
    item = data.get("item")
    if not item:
        return jsonify(error="item required"), 400

    st = _load(sess, "staples")
    today = datetime.date.today().isoformat()
    for s in st.get("items", []):
        if s["item"].lower() == item.lower():
            s["last_bought"] = today
            break
    _save(sess, "staples", st)
    return jsonify(ok=True)


@app.route("/api/staples/suggestions")
@login_required
def api_staples_suggestions():
    sess = current_session()
    return jsonify(_build_staple_suggestions(sess))

@app.route("/api/pantry", methods=["GET"])
@login_required
def api_pantry_list():
    """Return all pantry items (shared across household)."""
    items = db.pantry_list()
    return jsonify(items=items)


@app.route("/api/pantry/toggle", methods=["POST"])
@login_required
def api_pantry_toggle():
    """Set in_stock to 0 or 1 for `item`. Creates row if absent."""
    data = request.get_json(silent=True) or {}
    item = (data.get("item") or "").strip()
    if not item:
        return jsonify(error="item required"), 400
    in_stock = bool(data.get("in_stock", False))
    db.pantry_toggle(item, in_stock)
    return jsonify(ok=True, item=item, in_stock=in_stock)


@app.route("/api/pantry/use", methods=["POST"])
@login_required
def api_pantry_use():
    """Mark `item` as consumed (in_stock=0). No-op if absent."""
    data = request.get_json(silent=True) or {}
    item = (data.get("item") or "").strip()
    if not item:
        return jsonify(error="item required"), 400
    db.pantry_use(item)
    return jsonify(ok=True, item=item, in_stock=False)


@app.route("/api/pantry/restock", methods=["POST"])
@login_required
def api_pantry_restock():
    """Set in_stock=1 for `item`. Optional quantity/unit update."""
    data = request.get_json(silent=True) or {}
    item = (data.get("item") or "").strip()
    if not item:
        return jsonify(error="item required"), 400
    quantity = data.get("quantity")
    unit = data.get("unit")
    db.pantry_restock(item, quantity, unit)
    return jsonify(ok=True, item=item, in_stock=True)


# ─── Admin panel ──────────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_panel():
    sess  = current_session()
    users = db.list_users()
    return render_template("admin.html", users=users, username=sess["username"])


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    return jsonify(db.list_users())


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_user(user_id: int):
    sess = current_session()
    if user_id == sess["user_id"]:
        return jsonify(error="Kan inte ta bort dig själv"), 400
    db.delete_user(user_id)
    return jsonify(ok=True)


@app.route("/api/admin/geo-cache")
@admin_required
def api_admin_geo_cache():
    """List all cached postal codes with age and store count."""
    return jsonify(db.geo_cache_all())


@app.route("/api/admin/geo-cache/<zip_code>", methods=["DELETE"])
@admin_required
def api_admin_geo_cache_delete(zip_code: str):
    """Force-expire a zip so next search re-fetches from Overpass."""
    from pathlib import Path
    with db.get_connection() as conn:
        conn.execute("DELETE FROM store_geo_cache WHERE zip_code=?", (zip_code,))
    _zip_mem_cache.pop(zip_code, None)
    return jsonify(ok=True, message=f"{zip_code} removed — next search will re-fetch")


# ─── Order Agent ──────────────────────────────────────────────────────────────

_order_jobs: dict[str, dict] = {}   # job_id → {status, result, started_at}


@app.route("/api/order/fill-carts", methods=["POST"])
@login_required
def api_order_fill_carts():
    """
    Trigger the order agent manually.

    POST body (JSON, all optional):
        dry_run   bool   (default false) — simulate without touching cart
        week_num  int    (default current week)
        year      int    (default current year)

    Requires env vars WILLYS_USERNAME / WILLYS_PASSWORD (or configured chain prefix).
    Returns: {job_id, status: "started"|"error", message}
    """
    import os
    import threading
    import datetime as dt_mod

    body = request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run", False))
    week_num = body.get("week_num") or _current_week()[0]
    year = body.get("year") or _current_week()[1]
    sess = current_session()

    cfg = _load_config()
    oa_cfg = cfg.get("order_agent", {})
    chain = oa_cfg.get("chain", "willys")

    username = os.environ.get(f"{chain.upper()}_USERNAME", "")
    password = os.environ.get(f"{chain.upper()}_PASSWORD", "")
    if not username or not password:
        return jsonify(
            job_id=None,
            status="error",
            message=f"No credentials — set {chain.upper()}_USERNAME and {chain.upper()}_PASSWORD",
        ), 400

    # Load shopping list
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT item, quantity FROM weekly_shopping "
        "WHERE week_num=? AND year=? AND checked=0 AND in_pantry=0",
        (week_num, year),
    )
    shopping_list = [{"item": r["item"], "quantity": r["quantity"] or ""} for r in cur.fetchall()]
    if not shopping_list:
        return jsonify(
            job_id=None,
            status="error",
            message=f"No shopping list for week {week_num}/{year}",
        ), 400

    job_id = f"job_{int(time.time())}"
    _order_jobs[job_id] = {"status": "running", "result": None, "started_at": time.time()}

    def _run():
        from order_agent.core import OrderAgent
        agent = OrderAgent(cfg, username, password)
        result = agent.run(
            shopping_list=shopping_list,
            week_num=week_num,
            year=year,
            dry_run=dry_run,
            user_id=sess.get("user_id"),
        )
        _order_jobs[job_id]["status"] = "done"
        _order_jobs[job_id]["result"] = {
            "success": result.success,
            "total_sek": result.total_sek,
            "item_count": result.item_count,
            "error": result.error,
            "slot_time": result.slot_time,
            "checkout_url": result.checkout_url,
        }

    threading.Thread(target=_run, daemon=True).start()
    logger.info(f"Order agent job {job_id} started (dry_run={dry_run}, week={week_num})")
    return jsonify(job_id=job_id, status="started", message="Order agent running in background")


@app.route("/api/order/status/<job_id>")
@login_required
def api_order_status(job_id: str):
    """Poll order agent job progress."""
    job = _order_jobs.get(job_id)
    if not job:
        return jsonify(error="Job not found"), 404
    return jsonify(job_id=job_id, **job)


@app.route("/api/order/history")
@login_required
def api_order_history():
    """Return past order runs for the current user."""
    sess = current_session()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM order_history WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
        (sess.get("user_id"),),
    )
    rows = [dict(r) for r in cur.fetchall()]
    return jsonify(rows)


@app.route("/api/order/settings", methods=["GET"])
@login_required
def api_order_settings_get():
    """Return current order agent settings (credentials never returned)."""
    import os
    cfg = _load_config()
    oa = cfg.get("order_agent", {})
    chain = oa.get("chain", "willys")
    return jsonify({
        "chain": chain,
        "chains": oa.get("chains", [chain, "hemkop"]),
        "store_id": oa.get("store_id", ""),
        "ntfy_topic": oa.get("ntfy_topic", ""),
        "preferred_slot_weekday": oa.get("preferred_slot_weekday", 6),
        "preferred_slot_hour": oa.get("preferred_slot_hour", 11),
        "has_credentials": bool(os.environ.get(f"{chain.upper()}_USERNAME")),
    })


@app.route("/api/order/settings", methods=["POST"])
@login_required
def api_order_settings_post():
    """
    Update order agent settings.
    Credentials saved to ~/.config/matapp/store_creds.env (600 perms, never config.json).
    """
    import os
    from pathlib import Path as PPath

    body = request.get_json(silent=True) or {}
    cfg = _load_config()
    if "order_agent" not in cfg:
        cfg["order_agent"] = {}
    oa = cfg["order_agent"]

    for key in ("chain", "store_id", "ntfy_topic", "preferred_slot_weekday", "preferred_slot_hour"):
        if key in body:
            oa[key] = body[key]

    chain = oa.get("chain", "willys")
    compare_chains = [chain] if chain == "hemkop" else [chain, "hemkop"]
    oa["chains"] = compare_chains

    config_path = BASE_DIR / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    if username:
        creds_path = PPath.home() / ".config" / "matapp" / "store_creds.env"
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        existing_lines = []
        if creds_path.exists():
            existing_lines = [
                line for line in creds_path.read_text().splitlines()
                if line.strip() and not line.startswith(f"{chain.upper()}_")
            ]
        new_lines = existing_lines + [f"{chain.upper()}_USERNAME={username}"]
        if password:
            new_lines.append(f"{chain.upper()}_PASSWORD={password}")
        creds_path.write_text("\n".join(new_lines) + "\n")
        creds_path.chmod(0o600)

    logger.info(f"Order settings updated by {current_session().get('username')}")
    return jsonify(ok=True, message="Inställningar sparade")


# ─── Startup ──────────────────────────────────────────────────────────────────

def _ensure_admin():
    """Create admin account on first run."""
    if db.user_count() == 0:
        kdf_salt = auth.new_salt()
        pw_hash  = auth.hash_password("alst3836")
        db.create_user("admin", pw_hash, kdf_salt, is_admin=True)
        logger.info("Skapade admin-användare (lösenord: alst3836)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    db.init_db()
    _ensure_admin()

    global_cfg = _load_config()
    logger.info(f"Startar matapp server på http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
