"""
axfood.py — StoreAdapter for the Axfood platform (Willys, Hemköp, City Gross).

All three chains run on the same backend. Switch chain by setting BASE_URL
and CHAIN at instantiation.

Phase 1: search() — public endpoint, no auth.
Phase 2: login(), add_to_cart(), clear_cart(), get_cart_total(),
         get_pickup_slots() — requires valid store credentials.

Session (cookies + CSRF token) is persisted to
~/.config/matapp/sessions/<chain>_session.json and reused across runs.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests

from .base import Product, StoreAdapter, TimeSlot

logger = logging.getLogger(__name__)

# Axfood platform base URLs per chain
CHAIN_URLS: dict[str, str] = {
    "willys": "https://www.willys.se",
    "hemkop": "https://www.hemkop.se",
    "citygross": "https://www.citygross.se",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "sv-SE,sv;q=0.9",
}

# Where session files are stored
SESSION_DIR = Path.home() / ".config" / "matapp" / "sessions"

# Cart rate limit (seconds between add-to-cart requests)
CART_REQUEST_DELAY = 1.2

# Minimum name similarity to accept a search result (0–1)
MIN_SIMILARITY = 0.35

# How long to sleep between consecutive search requests (seconds)
REQUEST_DELAY = 0.6


class AxfoodAdapter(StoreAdapter):
    """
    Adapter for Willys / Hemköp / City Gross (Axfood platform).

    Usage:
        adapter = AxfoodAdapter("willys")
        products = adapter.search("nötfärs", "600 g")
    """

    def __init__(self, chain: str = "willys", store_id: Optional[str] = None):
        if chain not in CHAIN_URLS:
            raise ValueError(f"Unknown Axfood chain '{chain}'. Choose: {list(CHAIN_URLS)}")
        self.chain = chain
        self.display_name = chain.capitalize()
        self.store_id = store_id
        self._base_url = CHAIN_URLS[chain]
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._last_request_ts: float = 0.0
        self._csrf_token: str = ""

    # ── helpers ──────────────────────────────────────────────────────────────

    def _throttle(self) -> None:
        """Enforce minimum delay between requests."""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_ts = time.monotonic()

    def _get_json(self, url: str, params: dict) -> Optional[dict]:
        self._throttle()
        try:
            resp = self._session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            logger.warning(f"[{self.chain}] HTTP {e.response.status_code} for {url}: {e}")
        except requests.RequestException as e:
            logger.warning(f"[{self.chain}] Request error for {url}: {e}")
        except ValueError as e:
            logger.warning(f"[{self.chain}] JSON parse error for {url}: {e}")
        return None

    @staticmethod
    def _parse_package_grams(quantity_desc: str) -> Optional[float]:
        """
        Extract grams from a package description string.
        "500 g" → 500.0, "1 kg" → 1000.0, "2-pack 200g" → 400.0
        Returns None if not parseable.
        """
        # kg
        m = re.search(r'([\d.,]+)\s*kg', quantity_desc, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(',', '.')) * 1000
            except ValueError:
                pass
        # g
        m = re.search(r'([\d.,]+)\s*g\b', quantity_desc, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(',', '.'))
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_requested_grams(quantity_str: str) -> Optional[float]:
        """Parse requested quantity string: "600 g" → 600.0, "1 kg" → 1000.0."""
        return AxfoodAdapter._parse_package_grams(quantity_str)

    def _score_product(self, product_name: str, query: str,
                       quantity_desc: str, quantity_hint: str) -> float:
        """
        Score a search result 0–1.
        Higher = better match for the requested item and quantity.

        Components:
          - name similarity (60%): how closely the product name matches the query
          - quantity match (40%): how close the package size is to what we want
        """
        # Name similarity
        name_lower = product_name.lower()
        query_lower = query.lower()
        name_sim = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()

        # Boost if query is a substring of product name (exact word match)
        if query_lower in name_lower:
            name_sim = min(1.0, name_sim + 0.2)

        # Quantity proximity (only when both are parseable)
        qty_score = 0.5   # neutral when no quantity info
        req_g = AxfoodAdapter._parse_requested_grams(quantity_hint)
        pkg_g = AxfoodAdapter._parse_package_grams(quantity_desc)
        if req_g and pkg_g and req_g > 0:
            ratio = pkg_g / req_g
            # Perfect match at ratio=1.0, falls off toward 0 at ratio=0 or ratio=3+
            qty_score = max(0.0, 1.0 - abs(ratio - 1.0) * 0.5)

        return round(name_sim * 0.6 + qty_score * 0.4, 3)

    # ── Phase 1: public search ────────────────────────────────────────────────

    def search(self, query: str, quantity_hint: str = "") -> list[Product]:
        """
        Search Axfood product catalog.

        Uses /search — the same endpoint the storefront JS uses for the
        search bar. No authentication required. Falls back to /axfood/rest/c/searchV2.

        Returns up to 5 products sorted by score (best first).
        Returns [] on any error.
        """
        if not query.strip():
            return []

        # Primary: /search endpoint (storefront search bar, no auth required)
        url = f"{self._base_url}/search"
        params: dict = {"q": query, "size": 10, "type": "PRODUCT"}
        if self.store_id:
            params["storeId"] = self.store_id

        data = self._get_json(url, params)
        if data is None:
            # Fallback: try legacy searchV2 endpoint
            url_v2 = f"{self._base_url}/axfood/rest/c/searchV2"
            params_v2 = {"q": query, "size": 10, "page": 0, "type": "PRODUCT"}
            if self.store_id:
                params_v2["storeId"] = self.store_id
            data = self._get_json(url_v2, params_v2)

        if not data:
            logger.warning(f"[{self.chain}] No results for '{query}'")
            return []

        raw_results = (
            data.get("productSearchResult", {}).get("results")
            or data.get("results")
            or []
        )

        products = []
        for raw in raw_results:
            name = (raw.get("name") or "").strip()
            if not name:
                continue

            product_id = raw.get("code") or raw.get("productCode") or ""
            price_val = raw.get("priceValue") or 0.0
            try:
                price_val = float(str(price_val).replace(",", "."))
            except ValueError:
                price_val = 0.0

            compare_price = raw.get("comparePrice") or raw.get("comparePriceUnit") or ""
            unit = raw.get("salesUnit") or raw.get("unit") or "st"
            quantity_desc = raw.get("displayVolume") or raw.get("volumeDisplay") or ""
            available = not raw.get("outOfStock", False)

            score = self._score_product(name, query, quantity_desc, quantity_hint)
            if score < MIN_SIMILARITY:
                continue

            products.append(Product(
                product_id=product_id,
                name=name,
                price_sek=price_val,
                compare_price=str(compare_price),
                unit=unit,
                quantity_desc=quantity_desc,
                available=available,
                store_chain=self.chain,
                score=score,
            ))

        products.sort(key=lambda p: p.score, reverse=True)
        return products[:5]

    def find_store_id(self, store_name: str) -> Optional[str]:
        """Look up a Willys/Hemköp store ID by name."""
        url = f"{self._base_url}/axfood/rest/search/store"
        data = self._get_json(url, {"q": store_name.split()[0], "pageSize": 20})
        if not data:
            return None
        results = data.get("results", [])
        name_lower = store_name.lower()
        for s in results:
            if name_lower in s.get("name", "").lower():
                return s.get("storeId")
        if results:
            return results[0].get("storeId")
        return None

    # ── Phase 2: session persistence ─────────────────────────────────────────

    def _session_path(self) -> Path:
        return SESSION_DIR / f"{self.chain}_session.json"

    def _save_session(self) -> None:
        """Persist cookies and CSRF token to disk."""
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = self._session_path()
        data = {
            "cookies": dict(self._session.cookies),
            "csrf": self._csrf_token,
        }
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        logger.debug(f"[{self.chain}] Session saved to {path}")

    def _load_session(self) -> bool:
        """
        Load saved session from disk.
        Returns True if session file exists and was loaded.
        Does NOT verify that the session is still valid.
        """
        path = self._session_path()
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text())
            for k, v in data.get("cookies", {}).items():
                self._session.cookies.set(k, v)
            self._csrf_token = data.get("csrf", "")
            logger.debug(f"[{self.chain}] Session loaded from {path}")
            return True
        except Exception as e:
            logger.warning(f"[{self.chain}] Could not load session: {e}")
            return False

    def _is_logged_in(self) -> bool:
        """Quick check: can we reach the cart endpoint without a redirect?"""
        try:
            r = self._session.get(
                f"{self._base_url}/api/v2/cart",
                params={"fields": "BASIC"},
                timeout=10,
                allow_redirects=False,
            )
            return r.status_code == 200
        except Exception:
            return False

    # ── Phase 2: login ────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> bool:
        """
        Log in to the Axfood platform.

        Strategy:
          1. Try loading saved session — check if still valid.
          2. If not, POST to the v2 login endpoint with username + password.
          3. On success, save cookies + CSRF to session file.

        Returns True on success, False on failure.
        """
        self._csrf_token = ""

        # 1. Try cached session first
        if self._load_session() and self._is_logged_in():
            logger.info(f"[{self.chain}] Reused existing session")
            return True

        # 2. Fresh login — first fetch the homepage to get initial cookies/CSRF
        try:
            r = self._session.get(self._base_url, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"[{self.chain}] Cannot reach store homepage: {e}")
            return False

        # Extract CSRF from cookie or header
        csrf = (
            self._session.cookies.get("CSRF-TOKEN")
            or self._session.cookies.get("CSRFToken")
            or r.headers.get("X-CSRF-Token", "")
        )
        self._csrf_token = csrf

        # 3. POST login
        login_url = f"{self._base_url}/api/v2/customers/login"
        headers_extra = {}
        if self._csrf_token:
            headers_extra["CSRFToken"] = self._csrf_token
            headers_extra["X-CSRF-Token"] = self._csrf_token

        try:
            resp = self._session.post(
                login_url,
                json={"j_username": username, "j_password": password},
                headers=headers_extra,
                timeout=20,
            )
        except requests.RequestException as e:
            logger.error(f"[{self.chain}] Login request failed: {e}")
            return False

        if resp.status_code in (200, 201):
            # Refresh CSRF from response cookies
            new_csrf = (
                self._session.cookies.get("CSRF-TOKEN")
                or self._session.cookies.get("CSRFToken")
                or self._csrf_token
            )
            self._csrf_token = new_csrf
            self._save_session()
            logger.info(f"[{self.chain}] Login successful")
            return True

        # 4. If v2 endpoint failed, try legacy form-based login
        logger.warning(
            f"[{self.chain}] v2 login returned {resp.status_code}, "
            "trying legacy endpoint"
        )
        legacy_url = f"{self._base_url}/j_spring_security_check"
        try:
            resp2 = self._session.post(
                legacy_url,
                data={"j_username": username, "j_password": password},
                allow_redirects=True,
                timeout=20,
            )
            # Legacy login redirects to homepage on success
            if resp2.ok and "login" not in resp2.url.lower():
                self._csrf_token = self._session.cookies.get("CSRF-TOKEN", "")
                self._save_session()
                logger.info(f"[{self.chain}] Legacy login successful")
                return True
        except requests.RequestException as e:
            logger.error(f"[{self.chain}] Legacy login failed: {e}")

        logger.error(
            f"[{self.chain}] Login failed (status {resp.status_code}). "
            "Check credentials."
        )
        return False

    # ── Phase 2: cart ─────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict:
        h = {}
        if self._csrf_token:
            h["CSRFToken"] = self._csrf_token
            h["X-CSRF-Token"] = self._csrf_token
        return h

    def get_cart(self) -> dict:
        """Fetch current cart contents. Returns raw cart dict or {}."""
        url = f"{self._base_url}/api/v2/cart"
        data = self._get_json(url, {"fields": "FULL"})
        return data or {}

    def get_cart_product_codes(self) -> set[str]:
        """Return set of product codes currently in cart (for idempotency)."""
        cart = self.get_cart()
        entries = cart.get("entries", [])
        return {e.get("product", {}).get("code", "") for e in entries if e.get("product")}

    def add_to_cart(self, product_id: str, qty: int = 1) -> bool:
        """
        Add a product to cart. Rate-limited to 1 req/1.2s.
        Returns True on success.
        """
        self._throttle()
        # Extra delay for cart operations
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < CART_REQUEST_DELAY:
            time.sleep(CART_REQUEST_DELAY - elapsed)

        url = f"{self._base_url}/api/v2/cart/entries"
        try:
            resp = self._session.post(
                url,
                json={"product": {"code": product_id}, "quantity": qty},
                headers=self._auth_headers(),
                timeout=20,
            )
            if resp.status_code in (200, 201):
                logger.debug(f"[{self.chain}] Added {product_id} x{qty}")
                return True
            logger.warning(
                f"[{self.chain}] add_to_cart {product_id} → {resp.status_code}: "
                f"{resp.text[:120]}"
            )
            return False
        except requests.RequestException as e:
            logger.error(f"[{self.chain}] add_to_cart error: {e}")
            return False
        finally:
            self._last_request_ts = time.monotonic()

    def clear_cart(self) -> bool:
        """Delete all items from cart. Returns True on success."""
        url = f"{self._base_url}/api/v2/cart"
        try:
            resp = self._session.delete(
                url,
                headers=self._auth_headers(),
                timeout=20,
            )
            if resp.status_code in (200, 204):
                logger.info(f"[{self.chain}] Cart cleared")
                return True
            # Some Axfood versions don't support DELETE on /cart — try entry-by-entry
            cart = self.get_cart()
            entries = cart.get("entries", [])
            for entry in entries:
                entry_num = entry.get("entryNumber")
                if entry_num is not None:
                    self._session.delete(
                        f"{self._base_url}/api/v2/cart/entries/{entry_num}",
                        headers=self._auth_headers(),
                        timeout=10,
                    )
            logger.info(f"[{self.chain}] Cart cleared (entry-by-entry)")
            return True
        except requests.RequestException as e:
            logger.error(f"[{self.chain}] clear_cart error: {e}")
            return False

    def get_cart_total(self) -> float:
        """Return current cart total in SEK, or 0.0 on error."""
        cart = self.get_cart()
        try:
            return float(cart.get("totalPrice", {}).get("value", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def get_checkout_url(self) -> str:
        return f"{self._base_url}/checkout"

    # ── Phase 2: pickup slots ─────────────────────────────────────────────────

    def get_pickup_slots(self, store_id: str) -> list[TimeSlot]:
        """
        Fetch available pickup time slots for a store.

        Returns list of TimeSlot sorted by start time.
        Returns [] if store_id not set or API unavailable.
        """
        if not store_id:
            logger.warning(f"[{self.chain}] get_pickup_slots: no store_id provided")
            return []

        url = f"{self._base_url}/api/v2/stores/{store_id}/pickupslots"
        data = self._get_json(url, {})
        if not data:
            # Try alternative endpoint path
            url2 = f"{self._base_url}/axfood/rest/store/{store_id}/pickupslots"
            data = self._get_json(url2, {})

        if not data:
            logger.warning(f"[{self.chain}] No pickup slots found for store {store_id}")
            return []

        raw_slots = data if isinstance(data, list) else data.get("slots", data.get("pickupSlots", []))
        slots = []
        for s in raw_slots:
            slot_id = s.get("id") or s.get("slotId") or s.get("code", "")
            starts = s.get("startTime") or s.get("startsAt") or s.get("start", "")
            ends = s.get("endTime") or s.get("endsAt") or s.get("end", "")
            available = s.get("available", True) and not s.get("full", False)
            capacity = s.get("remainingCapacity") or s.get("capacity")
            if slot_id and starts:
                slots.append(TimeSlot(
                    slot_id=str(slot_id),
                    store_id=store_id,
                    starts_at=str(starts),
                    ends_at=str(ends),
                    available=bool(available),
                    capacity_remaining=int(capacity) if capacity is not None else None,
                ))

        slots.sort(key=lambda s: s.starts_at)
        return slots

    def select_slot(self, slot_id: str) -> bool:
        """
        Book a pickup slot.
        Returns True on success, False if booking fails or endpoint unavailable.
        """
        if not self.store_id:
            logger.warning(f"[{self.chain}] select_slot: no store_id configured")
            return False

        url = f"{self._base_url}/api/v2/stores/{self.store_id}/pickupslots/{slot_id}/reserve"
        try:
            resp = self._session.post(
                url,
                json={},
                headers=self._auth_headers(),
                timeout=20,
            )
            if resp.status_code in (200, 201, 204):
                logger.info(f"[{self.chain}] Slot {slot_id} booked successfully")
                return True
            # Try alternative endpoint schema
            url2 = f"{self._base_url}/api/v2/cart/pickupslot"
            resp2 = self._session.put(
                url2,
                json={"slotId": slot_id, "storeId": self.store_id},
                headers=self._auth_headers(),
                timeout=20,
            )
            if resp2.status_code in (200, 201, 204):
                logger.info(f"[{self.chain}] Slot {slot_id} booked via cart endpoint")
                return True
            logger.warning(
                f"[{self.chain}] select_slot: {resp.status_code} / {resp2.status_code}"
            )
            return False
        except requests.RequestException as e:
            logger.error(f"[{self.chain}] select_slot error: {e}")
            return False
