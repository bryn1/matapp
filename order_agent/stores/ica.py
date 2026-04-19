"""
ica.py — StoreAdapter for ICA (handla.ica.se / ICA mobile API).

ICA uses OAuth2 with username + password (no BankID for web/app).
The API is semi-public — used by handla.ica.se and the ICA app.

Key differences from Axfood:
  - OAuth2 Bearer token (not session cookies)
  - access_token valid 60 minutes; refresh_token valid 30 days
  - Token stored in session file alongside cookies
  - Product naming: ICA brands are "ICA Basic", "ICA I love eco" etc.
  - Better produce consistency than Willys; prefer ICA brand for commodities

⚠️  DEFERRED (Phase 7):
    The ICA product API lives at handla.api.ica.se which is a CDN-routed
    internal domain that only resolves from browser clients, not from server-side
    requests. Direct requests fail with DNS resolution errors.

    This adapter is structurally correct and ready to use, but requires either:
    (a) Playwright + browser automation to execute the JS-heavy SPA and extract
        the API token and correct endpoints, OR
    (b) ICA to expose a server-accessible product search endpoint.

    For now, Hemköp (same Axfood platform as Willys) provides the multi-store
    comparison capability without requiring browser automation.

    To activate ICA when Playwright is available:
      1. Install playwright: pip install playwright && playwright install chromium
      2. Add a login_playwright() method that drives the handla.ica.se SPA
      3. Extract the Bearer token from browser network requests
      4. Use the extracted token for subsequent API calls
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests

from .base import Product, StoreAdapter, TimeSlot

logger = logging.getLogger(__name__)

# ICA API base URLs
ICA_BASE = "https://handla.ica.se"
ICA_API = "https://handla.api.ica.se"

ICA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "sv-SE,sv;q=0.9",
    "Origin": ICA_BASE,
    "Referer": f"{ICA_BASE}/",
}

SESSION_DIR = Path.home() / ".config" / "matapp" / "sessions"
REQUEST_DELAY = 0.6
CART_REQUEST_DELAY = 1.2
MIN_SIMILARITY = 0.30

# ICA prefer own-brand for commodities
ICA_OWN_BRANDS = {"ica basic", "ica i love eco", "ica", "ica selection"}


class IcaAdapter(StoreAdapter):
    """
    Adapter for ICA (handla.ica.se).

    Usage:
        adapter = IcaAdapter(store_id="01234")
        ok = adapter.login("user@example.com", "password")
        products = adapter.search("nötfärs", "600 g")
    """

    chain = "ica"
    display_name = "ICA"

    def __init__(self, store_id: Optional[str] = None):
        self.store_id = store_id
        self._session = requests.Session()
        self._session.headers.update(ICA_HEADERS)
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_expires: float = 0.0
        self._last_request_ts: float = 0.0

    # ── session persistence ───────────────────────────────────────────────────

    def _session_path(self) -> Path:
        return SESSION_DIR / "ica_session.json"

    def _save_session(self) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = self._session_path()
        data = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "token_expires": self._token_expires,
        }
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        logger.debug(f"[ica] Session saved to {path}")

    def _load_session(self) -> bool:
        path = self._session_path()
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text())
            self._access_token = data.get("access_token", "")
            self._refresh_token = data.get("refresh_token", "")
            self._token_expires = float(data.get("token_expires", 0.0))
            return bool(self._access_token)
        except Exception as e:
            logger.warning(f"[ica] Could not load session: {e}")
            return False

    def _token_valid(self) -> bool:
        return bool(self._access_token) and time.time() < self._token_expires - 60

    def _set_auth_header(self) -> None:
        if self._access_token:
            self._session.headers["Authorization"] = f"Bearer {self._access_token}"
        else:
            self._session.headers.pop("Authorization", None)

    # ── throttle ──────────────────────────────────────────────────────────────

    def _throttle(self, delay: float = REQUEST_DELAY) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_ts = time.monotonic()

    def _get_json(self, url: str, params: dict = None) -> Optional[dict | list]:
        self._throttle()
        try:
            resp = self._session.get(url, params=params or {}, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            logger.warning(f"[ica] HTTP {e.response.status_code} for {url}")
        except requests.RequestException as e:
            logger.warning(f"[ica] Request error for {url}: {e}")
        except ValueError as e:
            logger.warning(f"[ica] JSON parse error for {url}: {e}")
        return None

    # ── login ─────────────────────────────────────────────────────────────────

    def _try_refresh(self) -> bool:
        """Use refresh_token to get new access_token. Returns True on success."""
        if not self._refresh_token:
            return False
        try:
            resp = self._session.post(
                f"{ICA_API}/api/login/generatetoken",
                json={"grantType": "refresh_token", "refreshToken": self._refresh_token},
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._access_token = data.get("accessToken", "")
                self._refresh_token = data.get("refreshToken", self._refresh_token)
                expires_in = int(data.get("expiresIn", 3600))
                self._token_expires = time.time() + expires_in
                self._set_auth_header()
                self._save_session()
                logger.info("[ica] Token refreshed successfully")
                return True
        except Exception as e:
            logger.warning(f"[ica] Token refresh failed: {e}")
        return False

    def login(self, username: str, password: str) -> bool:
        """
        Login to ICA using OAuth2 password flow.

        1. Load saved session — if access_token valid, reuse.
        2. If expired, try refresh_token.
        3. If no refresh or refresh fails, do fresh login.
        """
        if self._load_session():
            if self._token_valid():
                self._set_auth_header()
                logger.info("[ica] Reused existing session")
                return True
            if self._try_refresh():
                return True

        # Fresh login
        try:
            resp = self._session.post(
                f"{ICA_API}/api/login/generatetoken",
                json={
                    "grantType": "password",
                    "username": username,
                    "password": password,
                },
                timeout=20,
            )
        except requests.RequestException as e:
            logger.error(f"[ica] Login request failed: {e}")
            return False

        if resp.status_code == 200:
            data = resp.json()
            self._access_token = data.get("accessToken", "")
            self._refresh_token = data.get("refreshToken", "")
            expires_in = int(data.get("expiresIn", 3600))
            self._token_expires = time.time() + expires_in
            self._set_auth_header()
            self._save_session()
            logger.info("[ica] Login successful")
            return True

        logger.error(f"[ica] Login failed (status {resp.status_code}): {resp.text[:120]}")
        return False

    # ── search ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_grams(text: str) -> Optional[float]:
        if not text:
            return None
        m = re.search(r'([\d.,]+)\s*kg', text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(',', '.')) * 1000
            except ValueError:
                pass
        m = re.search(r'([\d.,]+)\s*g\b', text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(',', '.'))
            except ValueError:
                pass
        return None

    def _score_product(self, name: str, query: str, qty_desc: str, qty_hint: str) -> float:
        name_lower = name.lower()
        query_lower = query.lower()
        sim = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
        if query_lower in name_lower:
            sim = min(1.0, sim + 0.2)

        # Boost ICA own-brand for commodity items
        for brand in ICA_OWN_BRANDS:
            if brand in name_lower:
                sim = min(1.0, sim + 0.05)
                break

        qty_score = 0.5
        req_g = self._parse_grams(qty_hint)
        pkg_g = self._parse_grams(qty_desc)
        if req_g and pkg_g and req_g > 0:
            ratio = pkg_g / req_g
            qty_score = max(0.0, 1.0 - abs(ratio - 1.0) * 0.5)

        return round(sim * 0.6 + qty_score * 0.4, 3)

    def search(self, query: str, quantity_hint: str = "") -> list[Product]:
        """
        Search ICA product catalog.

        Uses the /api/products/search endpoint.
        Auth not strictly required for search, but set if available.
        """
        if not query.strip():
            return []

        params: dict = {"s": query, "size": 10}
        if self.store_id:
            params["storeId"] = self.store_id

        # Primary: handla.api.ica.se search
        data = self._get_json(f"{ICA_API}/api/products/search", params)

        # Fallback: handla.ica.se search
        if not data:
            data = self._get_json(f"{ICA_BASE}/api/products/search", params)

        if not data:
            logger.warning(f"[ica] No results for '{query}'")
            return []

        # ICA returns list directly or wrapped
        raw_results = data if isinstance(data, list) else (
            data.get("products") or data.get("results") or data.get("items") or []
        )

        products = []
        for raw in raw_results:
            name = (raw.get("name") or raw.get("productName") or "").strip()
            if not name:
                continue

            product_id = str(
                raw.get("id") or raw.get("productId") or raw.get("ean") or ""
            )
            price_val = 0.0
            price_data = raw.get("price") or raw.get("regularPrice") or {}
            if isinstance(price_data, dict):
                price_val = float(price_data.get("value", 0) or 0)
            elif isinstance(price_data, (int, float)):
                price_val = float(price_data)

            compare_price = str(
                raw.get("comparePrice") or raw.get("priceCompare") or ""
            )
            unit = str(raw.get("unit") or raw.get("salesUnit") or "st")
            qty_desc = str(
                raw.get("displayVolume") or raw.get("volumeDisplay") or
                raw.get("quantityDescription") or ""
            )
            available = not raw.get("outOfStock", False) and raw.get("status", "active") != "discontinued"

            score = self._score_product(name, query, qty_desc, quantity_hint)
            if score < MIN_SIMILARITY:
                continue

            products.append(Product(
                product_id=product_id,
                name=name,
                price_sek=price_val,
                compare_price=compare_price,
                unit=unit,
                quantity_desc=qty_desc,
                available=available,
                store_chain="ica",
                score=score,
            ))

        products.sort(key=lambda p: p.score, reverse=True)
        return products[:5]

    # ── cart ──────────────────────────────────────────────────────────────────

    def get_cart(self) -> dict:
        if not self.store_id:
            return {}
        data = self._get_json(f"{ICA_API}/api/cart", {"storeId": self.store_id})
        return data or {}

    def get_cart_product_codes(self) -> set[str]:
        cart = self.get_cart()
        items = cart.get("items") or cart.get("cartItems") or []
        return {str(i.get("productId") or i.get("id", "")) for i in items if i}

    def add_to_cart(self, product_id: str, qty: int = 1) -> bool:
        self._throttle(CART_REQUEST_DELAY)
        if not self.store_id:
            logger.warning("[ica] add_to_cart: no store_id configured")
            return False
        try:
            resp = self._session.post(
                f"{ICA_API}/api/cart/add",
                json={"productId": product_id, "quantity": qty, "storeId": self.store_id},
                timeout=20,
            )
            if resp.status_code in (200, 201):
                return True
            logger.warning(f"[ica] add_to_cart {product_id} → {resp.status_code}")
            return False
        except requests.RequestException as e:
            logger.error(f"[ica] add_to_cart error: {e}")
            return False
        finally:
            self._last_request_ts = time.monotonic()

    def clear_cart(self) -> bool:
        if not self.store_id:
            return False
        try:
            resp = self._session.delete(
                f"{ICA_API}/api/cart",
                params={"storeId": self.store_id},
                timeout=20,
            )
            if resp.status_code in (200, 204):
                logger.info("[ica] Cart cleared")
                return True
            # Fall back to clearing item by item
            cart = self.get_cart()
            items = cart.get("items") or cart.get("cartItems") or []
            for item in items:
                item_id = item.get("id") or item.get("cartItemId")
                if item_id:
                    self._session.delete(
                        f"{ICA_API}/api/cart/items/{item_id}",
                        timeout=10,
                    )
            logger.info("[ica] Cart cleared (item-by-item)")
            return True
        except requests.RequestException as e:
            logger.error(f"[ica] clear_cart error: {e}")
            return False

    def get_cart_total(self) -> float:
        cart = self.get_cart()
        try:
            total = cart.get("total") or cart.get("totalPrice") or {}
            if isinstance(total, dict):
                return float(total.get("value", 0) or 0)
            return float(total or 0)
        except (TypeError, ValueError):
            return 0.0

    def get_checkout_url(self) -> str:
        sid = self.store_id or ""
        return f"{ICA_BASE}/checkout{f'?storeId={sid}' if sid else ''}"

    # ── slots ─────────────────────────────────────────────────────────────────

    def get_pickup_slots(self, store_id: str) -> list[TimeSlot]:
        if not store_id:
            return []
        data = self._get_json(
            f"{ICA_API}/api/stores/{store_id}/slots",
            {"storeId": store_id},
        )
        if not data:
            return []

        raw_slots = data if isinstance(data, list) else (
            data.get("slots") or data.get("pickupSlots") or []
        )
        slots = []
        for s in raw_slots:
            slot_id = str(s.get("id") or s.get("slotId") or "")
            starts = str(s.get("startTime") or s.get("from") or s.get("start") or "")
            ends = str(s.get("endTime") or s.get("to") or s.get("end") or "")
            available = bool(s.get("available", True)) and not s.get("full", False)
            capacity = s.get("remainingCapacity") or s.get("capacity")
            if slot_id and starts:
                slots.append(TimeSlot(
                    slot_id=slot_id,
                    store_id=store_id,
                    starts_at=starts,
                    ends_at=ends,
                    available=available,
                    capacity_remaining=int(capacity) if capacity is not None else None,
                ))
        slots.sort(key=lambda s: s.starts_at)
        return slots

    def select_slot(self, slot_id: str) -> bool:
        if not self.store_id:
            return False
        try:
            resp = self._session.post(
                f"{ICA_API}/api/cart/checkout-url",
                json={"slotId": slot_id, "storeId": self.store_id},
                timeout=20,
            )
            if resp.status_code in (200, 201):
                return True
            # Try alternative
            resp2 = self._session.put(
                f"{ICA_API}/api/cart/slot",
                json={"slotId": slot_id},
                timeout=20,
            )
            return resp2.status_code in (200, 201, 204)
        except requests.RequestException as e:
            logger.error(f"[ica] select_slot error: {e}")
            return False

    # ── store lookup ──────────────────────────────────────────────────────────

    def find_store_id(self, store_name: str) -> Optional[str]:
        """Look up ICA store ID by name."""
        data = self._get_json(
            f"{ICA_API}/api/stores/search",
            {"q": store_name.split()[0], "pageSize": 20},
        )
        if not data:
            return None
        results = data if isinstance(data, list) else data.get("results", data.get("stores", []))
        name_lower = store_name.lower()
        for s in results:
            sname = s.get("name") or s.get("storeName") or ""
            if name_lower in sname.lower():
                return str(s.get("id") or s.get("storeId") or "")
        if results:
            s = results[0]
            return str(s.get("id") or s.get("storeId") or "")
        return None
