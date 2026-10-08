"""app.routers.stores — stores router for the web-api (ported from the deployed
hosting copy, MC 1355.3 T3; origin: hosting/apps/matapp app/routers/stores.py).

  GET  /api/stores        -> catalog READ from the motor's PlannerConfig grocers
                             at request time (O1 pin — no app-side second list).
                             MC 1355.16: optional ``?postal_code=`` resolve-
                             preview — AUTH-GATED (401 anonymous, same gate as
                             profile/menu, T10b §10-F5); anonymous calls without
                             the param keep today's byte-identical behavior.
  POST /api/stores/select -> AUTH-GATED (MC 10348, live-dogfood finding 1): the
                             authenticated user REPLACES their own selection,
                             capped at 3. >3, duplicates or unknown store_id ->
                             422. Persists to the user's profile row via the ONE
                             write path profile_service.save_selected_stores —
                             the global store_selection table is retired.
  GET  /api/stores/selected -> AUTH-GATED: the authenticated user's own
                             selection, read back from their profile (MC 10348).

Core selection rules live in profile_service (validation shared with
PUT /api/profile — one implementation); this router only marshals HTTP<->session.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import auth_service, profile_service
from app.config import get_planner_config
# MC 1355.17 (T10f DA P2-2): the preview param is validated + normalized by the
# SAME regex/normalization the profile PUT uses — one postnummer rule, no second.
from app.routers.profile import POSTAL_CODE_RE
from src.locator import resolve_stores_cached

router = APIRouter(prefix="/api/stores", tags=["stores"])


class StoreSelectIn(BaseModel):
    store_ids: list[str] = Field(default_factory=list)


def _catalog(config):
    """Map PlannerConfig grocers -> public store payload (READ at request time)."""
    return [
        {
            "store_id": g.grocer_id,
            "name": g.grocer_id,           # motor exposes no display name field
            "chain_type": g.chain,
            "enabled": True,               # motor has no disabled flag per grocer
        }
        for g in config.grocers
    ]


@router.get("")
def list_stores(request: Request,
                postal_code: str | None = Query(default=None)):
    """Store catalog; with ``?postal_code=`` an auth-gated resolve-preview.

    The preview param is honored ONLY for an authenticated user (401 when
    anonymous — T10b §10-F5: the locator pipeline, Nominatim 1 req/s policy
    included, is never reachable anonymously). The resolve result is served
    from the bounded per-process cache (TTL 24 h, max 128 entries — T10d N4).
    """
    cfg = get_planner_config()
    catalog = _catalog(cfg)
    if postal_code is None:
        return catalog  # anonymous behavior byte-identical to today
    auth_service.current_user_or_401(request)
    # MC 1355.17 (T10f DA P2-2): same validation + digits-only normalization as
    # PUT /api/profile — a malformed param is a 422, never a raw Nominatim query.
    if not POSTAL_CODE_RE.fullmatch(postal_code.strip()):
        raise HTTPException(
            status_code=422,
            detail="postal_code must be a Swedish postnummer, e.g. "
                   "'414 51' or '41451'")
    return {"stores": catalog,
            "nearby": resolve_stores_cached(re.sub(r"\s", "", postal_code))}


@router.post("/select")
def select_stores(payload: StoreSelectIn, request: Request) -> dict:
    """Replace the AUTHENTICATED USER's selected stores (MC 10348: per-user,
    persisted on their profile — never the retired global table, never 200
    without a valid session)."""
    user = auth_service.current_user_or_401(request)
    try:
        # ONE write path: profile_service upserts the user's own profile row
        profile_service.save_selected_stores(user, payload.store_ids)
    except ValueError as e:  # the shared rule: >3 / duplicate / unknown -> 422
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"selected": payload.store_ids}


@router.get("/selected")
def get_selected(request: Request) -> list[dict]:
    """The authenticated user's own selection, read from their profile."""
    user = auth_service.current_user_or_401(request)
    data = profile_service.load_profile(user)
    return [{"store_id": sid}
            for sid in (data.selected_stores if data else [])]
