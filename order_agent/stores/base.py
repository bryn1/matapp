"""
base.py — StoreAdapter ABC and shared dataclasses.

All store adapters implement StoreAdapter. The orchestrator only ever
calls methods defined here — adapters are interchangeable.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Product:
    """A single product returned by a store's search API."""
    product_id: str
    name: str
    price_sek: float
    compare_price: str          # e.g. "39.90 kr/kg" — for display
    unit: str                   # e.g. "st", "kg", "förp"
    quantity_desc: str          # e.g. "500 g", "6-pack" — package description
    available: bool = True
    store_chain: str = ""
    score: float = 0.0          # match quality 0–1, set by normalizer


@dataclass
class TimeSlot:
    """A pickup time slot at a store."""
    slot_id: str
    store_id: str
    starts_at: str              # ISO datetime string
    ends_at: str
    available: bool = True
    capacity_remaining: Optional[int] = None


@dataclass
class CartItem:
    """An item placed in a store cart."""
    product: Product
    requested_quantity: int     # number of packages to add
    original_query: str         # the shopping list item that triggered this
    is_substitution: bool = False
    substitution_reason: str = ""


@dataclass
class CartQuote:
    """Result of a fill-cart run for one store — may be dry-run or real."""
    chain: str
    store_id: str
    store_name: str
    items: list[CartItem] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)   # original queries with no match
    substitutions: list[CartItem] = field(default_factory=list)
    total_sek: float = 0.0
    checkout_url: str = ""
    slot: Optional[TimeSlot] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def item_count(self) -> int:
        return len(self.items)


class StoreAdapter(abc.ABC):
    """
    Abstract base for all store adapters.

    Phase 1: only search() is required — no login, cart, or slot methods.
    Later phases add login(), add_to_cart(), get_pickup_slots(), select_slot().
    """

    chain: str = ""          # e.g. "willys", "ica", "hemkop"
    display_name: str = ""   # e.g. "Willys"

    # ── Phase 1: read-only search ──────────────────────────────────────────

    @abc.abstractmethod
    def search(self, query: str, quantity_hint: str = "") -> list[Product]:
        """
        Search for a product by name.

        Args:
            query:         normalized ingredient name, e.g. "nötfärs"
            quantity_hint: desired quantity string, e.g. "600 g" — used to
                           rank results by closest package size

        Returns:
            List of Products ordered by match quality (best first).
            Empty list if nothing found or on error.
        """

    # ── Phase 2+: authenticated operations (stubs raise NotImplementedError) ─

    def login(self, username: str, password: str) -> bool:
        raise NotImplementedError(f"{self.chain}: login not implemented yet")

    def add_to_cart(self, product_id: str, qty: int = 1) -> bool:
        raise NotImplementedError(f"{self.chain}: add_to_cart not implemented yet")

    def clear_cart(self) -> bool:
        raise NotImplementedError(f"{self.chain}: clear_cart not implemented yet")

    def get_cart_total(self) -> float:
        raise NotImplementedError(f"{self.chain}: get_cart_total not implemented yet")

    def get_pickup_slots(self, store_id: str) -> list[TimeSlot]:
        raise NotImplementedError(f"{self.chain}: get_pickup_slots not implemented yet")

    def select_slot(self, slot_id: str) -> bool:
        raise NotImplementedError(f"{self.chain}: select_slot not implemented yet")

    def get_checkout_url(self) -> str:
        raise NotImplementedError(f"{self.chain}: get_checkout_url not implemented yet")
