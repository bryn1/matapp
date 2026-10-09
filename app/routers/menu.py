"""app.routers.menu — auth-/profil-skyddad GET /api/menu (Phase 7 T6, MC 1355.5).

reason: MC 10351 adds the suggestion-diversity cascade to the ONE shared
assembly (_diverse_plans); splitting it out would fork the accept-recompute
seam plans.py imports, so the file stays whole past the 400-line target.

The API-lager's headline endpoint: a logged-in user asks for a week's menu and gets
THREE candidate plans (suggestions). MC 1355.5 rewires the data source: the offers
are read from the OFFERS DB (``src/offers_db/store.list_offers_in_week``), filtered
to the user's own selected stores (MC 10348: ``profile.selected_stores`` — the
global store_selection table is retired), and handed to the motor's real planner
``src.planner.menu.plan_menu`` — the hardcoded ``app/optimizer/offers.py`` fixture
list is no longer the menu's data source.

MC 10037 (PORT-PLAN P1-a0/P1-a): the recipe roster is the DB ``recipes`` table
when non-empty (ROSTER = empty-table fallback), and accepted dishes are excluded
for 42 days per user. The GET assembly lives in ``_assemble_menu`` — the ONE
implementation shared by POST /api/menu/accept (app/routers/plans.py), so an
accept recomputes byte-identically to the GET that offered the plan.

  * AUTH: every handler first resolves the opaque ``matapp_session`` cookie to a
    logged-in ``users.User`` via Phase 3 ``auth_service.current_user``; absent/invalid
    cookie -> **401, never 200** (the exact gate-C6 line "utan session 401").
  * PROFIL: the suggestion families are built from the authenticated user's own
    persisted profile (persons, meal_days) via Phase 5 ``profile_service.load_profile``
    — Phase-6 FamilyPrefs defaults (persons=4, meal_days=5) when nothing is saved.
  * WEEK: optional ``?week=YYYY-Www`` (default = current ISO week). The pattern is
    enforced by FastAPI (422 on a malformed key) and the key is validated as a REAL
    week via the ONE shared helper ``src.planner.weeks.week_to_monday`` — the audit's
    latent BUG-1/BUG-2 (``9999-W99`` OverflowError, ``2026-W54`` silent extrapolation)
    is fixed at this boundary with a 422, never a 500.
  * DEGRADATION (N5): an empty offers DB still returns 200 — every day degrades to
    ``used_offer_ids: []`` (the motor planner's recipe-only path), never a 500.

Response is pydantic-validated (gate C6 "pydantic-validering"): MenuResponse with a
list[Suggestion], each suggestion the {seed, week_key, days[]} shape.

Single concern: HTTP wire-up + planner invocation. Auth/persistence live in the
service modules; the week math lives in src/planner/weeks.py.
"""
from __future__ import annotations

import logging

from datetime import date

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app import auth_service, db, profile_service, security
from app.models.recipe_usage import list_usage
from app.models.users import User
from app.models.recipes_db import c_rdb_list_all
from app.optimizer.optimizer import DEFAULT_SEEDS, FamilyPrefs, andel_extrapris
from app.optimizer.recipes import recipes
from src.offers_db.store import list_offers_in_week
from src.planner.menu import plan_menu
from src.planner.weeks import current_week_key, week_to_monday

logger = logging.getLogger("kvallsmat.app.menu")

router = APIRouter(prefix="/api/menu", tags=["menu"])

# Gate C6 contract: exactly three suggestions (the Phase 6 DEFAULT_SEEDS = 3 seeds).
SUGGESTION_COUNT = 3
# Display week keys are ISO 8601 week dates, e.g. "2026-W34" (1-2 digit week is
# accepted by the pattern; real-week validity is checked by week_to_monday).
WEEK_PATTERN = r"^\d{4}-W\d{1,2}$"
# MC 10037 (P1-a, owner-quoted matapp rule): a recipe must not reappear for
# >= 6 weeks; a usage EXACTLY 42 days before the planned week may return.
ROTATION_WINDOW_DAYS = 42


# ---------------------------------------------------------------------------
# Pydantic response models (gate C6 "pydantic-validering")
# ---------------------------------------------------------------------------


class MenuDay(BaseModel):
    date: str
    dish_id: str
    andel_extrapris: float
    used_offer_ids: list[int] = Field(default_factory=list)
    # MC 1355.18 (T11): additive field (same precedent as offer_sources) —
    # absent/false renders nothing in the UI, old responses stay valid.
    kid_friendly: bool = False


class Suggestion(BaseModel):
    week_key: str
    seed: int
    days: list[MenuDay]


class OfferSource(BaseModel):
    """Which offer row (and store scope) fed the plan — MC 1355.16 (T10b §4).

    MC 1355.17 (T10f DA P3-2): ``store_name`` carries the resolved store's
    display name so the UI shows "butik: Willys Majorna", not a raw id.
    """

    offer_id: int
    grocer_id: str
    store_id: str | None = None
    store_name: str | None = None


class MenuResponse(BaseModel):
    week_key: str
    suggestions: list[Suggestion] = Field(default_factory=list)
    # MC 1355.16: optional additive field; used_offer_ids kept unchanged for
    # response-shape compatibility.
    offer_sources: list[OfferSource] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Auth dependency (mirrors app/routers/profile.py — 401, never 200)
# ---------------------------------------------------------------------------


def _current_user_or_401(request: Request) -> User:
    """Resolve the session cookie to a User; 401 when absent/invalid (auth-skyddad)."""
    token = request.cookies.get(security.SESSION_COOKIE)
    user = auth_service.current_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


@router.get("", response_model=MenuResponse)
def get_menu(request: Request,
             week: str | None = Query(default=None, pattern=WEEK_PATTERN),
             user: User = Depends(_current_user_or_401),
             session=Depends(db.get_db)) -> MenuResponse:
    """Plan the week's menu from the offers DB for the authenticated user's household."""
    # WEEK: default = current ISO week; validated as a REAL week (BUG-1/BUG-2
    # fix) inside the shared assembly, 422 — never 500.
    return _assemble_menu(session, user, week or current_week_key())


def _assemble_menu(session, user: User, week_key: str) -> MenuResponse:
    """THE menu assembly — one implementation for GET /api/menu and the accept
    recomputation in POST /api/menu/accept (MC 10037 P1-a): accept must see
    byte-identical inputs to the GET that offered the plan. Raises 422 on a
    week that is not a real ISO week."""
    try:
        week_to_monday(week_key)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"week is not a real ISO week: {week_key!r}") from None

    # PROFIL-skyddad: build the family from THIS user's saved profile.
    profile = profile_service.load_profile(user)
    # OFFERS from the DB, filtered to the USER'S OWN selected stores (MC 10348:
    # the single source of truth is profile.selected_stores — the global
    # store_selection table is retired; empty selection = no filter, same
    # semantics as before: the user has not chosen stores, week offered whole).
    offers = list_offers_in_week(session, week_key)
    selected = profile.selected_stores if profile is not None else []
    if selected:
        offers = [o for o in offers if o.grocer_id in selected]

    persons = profile.persons if profile is not None else FamilyPrefs().persons
    meal_days = profile.meal_days if profile is not None else FamilyPrefs().meal_days
    family = FamilyPrefs(meal_days=meal_days, persons=persons,
                         prefer_kid_friendly=bool(
                             profile.prefer_kid_friendly or 0)
                         if profile is not None else False)

    # MC 1355.16 (T10b §4): store-level selection — the store clause keeps a
    # chain-level row (store_id NULL = valid everywhere) or a row scoped to one
    # of the profile's resolved stores; then dedup by (grocer_id, normalized
    # name) preferring the store-level row. No resolved stores (or no
    # postal_code) -> both steps are a no-op and behavior is exactly today's.
    resolved = profile.resolved_stores if profile is not None else None
    offers = _apply_store_clause(offers, resolved)
    offers = _dedup_by_name(offers, resolved)
    # MC 1355.17 (T10f DA P3-2): store_id -> display name from the persisted
    # resolution, so offer_sources can show the store NAME in the UI.
    store_names = _resolved_store_names(resolved)
    offer_sources = [
        {"offer_id": o.offer_id, "grocer_id": o.grocer_id,
         "store_id": o.store_id,
         "store_name": store_names.get(str(o.store_id)) if o.store_id else None}
        for o in offers
    ]

    # The motor's real planner (src/planner/menu.py) — one plan per seed.
    # MC 10037 (P1-a0): the roster is the DB ``recipes`` table when it carries
    # rows (boot-seeded starter, app.db.seed_recipes_if_empty); the static
    # Phase-6 ROSTER stays the fallback for an empty table. Both shapes carry
    # the same C-RDB attributes _allowed/plan/andel_extrapris/MenuDay read, so
    # the planner seam needs no adapter.
    recipe_roster = _recipe_roster(session)
    # MC 10037 (P1-a): 42-day per-user hard no-repeat on ACCEPTED dishes.
    # Clock/week math lives HERE in the router, never inside plan_menu (N7).
    recipe_roster = _apply_rotation(session, user.user_id, week_key,
                                    recipe_roster, family.meal_days)
    by_title = {r.title: r for r in recipe_roster}
    # MC 10351 (dogfood 10064.7 finding 3): the seeds plan over EXCLUDING
    # pools (see _diverse_plans) — suggestion 1 is unchanged (the full
    # rotated roster), suggestions 2/3 drop earlier dishes while the pool
    # can still fill the week. Pool choice is a pure function of
    # roster+offers+seed-order, so accept recompute stays byte-identical.
    plans = _diverse_plans(week_key, offers, recipe_roster, family)

    # Pydantic validates + shapes the response (gate C6). ``andel_extrapris`` is
    # kept for response-shape compatibility; the offers DB carries no reference
    # price column, so the ratio is computed from what the rows actually hold.
    suggestions = [
        Suggestion(
            week_key=p["week_key"],
            seed=seed,
            days=[
                MenuDay(
                    date=d["date"],
                    dish_id=d["dish_id"],
                    andel_extrapris=(
                        round(andel_extrapris(by_title[d["dish_id"]], offers), 4)
                        if d["dish_id"] in by_title else 0.0
                    ),
                    used_offer_ids=d["used_offer_ids"],
                    kid_friendly=bool(
                        (getattr(by_title[d["dish_id"]], "kid_friendly", 0) or 0)
                    ) if d["dish_id"] in by_title else False,
                )
                for d in p["days"]
            ],
        )
        for seed, p in zip(DEFAULT_SEEDS, plans)
    ]
    # DA P2-B (MC 10037 fix cycle): /api/menu must NEVER 200 a zero-day week.
    # ProfileBody bounds stop absurd input, but an in-range profile can still
    # leave ZERO eligible recipes (persons=12 exceeds every roster serving
    # count) — a silent [0,0,0] week is the GT-5rz starvation UX, and accept +
    # shopping/build harden on top of it. That is an unsatisfiable-state
    # conflict, not a menu: 409 with the reason, and accept inherits the same
    # guard through this shared assembly.
    if all(not s.days for s in suggestions):
        raise HTTPException(
            status_code=409,
            detail=(f"no eligible recipes for this profile (persons={persons}, "
                    f"meal_days={meal_days}, week={week_key}) — nothing to plan"))
    return MenuResponse(week_key=week_key, suggestions=suggestions,
                        offer_sources=offer_sources)


# ---------------------------------------------------------------------------
# Recipe roster (MC 10037 P1-a0)
# ---------------------------------------------------------------------------


def _recipe_roster(session) -> list:
    """The served roster: DB ``recipes`` rows when the table carries any
    (boot-seeded starter), else the static Phase-6 ROSTER fallback. The DB read
    reuses the motor's C-RDB contract seam (c_rdb_list_all, title-ordered) —
    one implementation, imported via the app.models.recipes_db shim."""
    rows = c_rdb_list_all(session)
    return rows if rows else recipes()


# ---------------------------------------------------------------------------
# Rotation (MC 10037 P1-a): 42-day hard no-repeat per user
# ---------------------------------------------------------------------------


def _apply_rotation(session, user_id: int, week_key: str, roster: list,
                    meal_days: int) -> list:
    """Drop titles the user ACCEPTED within the last 42 days (matapp GT-5rz
    lesson applied: ONE clock — usage rows are written only by
    POST /api/menu/accept — and week math is done here, in the router).

    A usage recorded for week W blocks the title when planning week R iff
    0 < (monday(R) - monday(W)).days < ROTATION_WINDOW_DAYS: weeks R = W+1..W+5
    exclude it, R = W+6 (exactly 42 days) lets it return, and R = W keeps
    same-week re-plans stable (an accept never rewrites its own week's menu).

    When the strict pool cannot fill meal_days, the excluded titles come back
    OLDEST-USED-FIRST and one WARNING is logged — never silent starvation
    (matapp's relax-and-warn, the GT-5rz failure mode made impossible)."""
    rows = list_usage(session, user_id)
    if not rows:
        return roster
    req_monday = week_to_monday(week_key)
    excluded: set = set()
    last_use: dict = {}
    for row in rows:
        try:
            used_monday = week_to_monday(row.week_key)
        except ValueError:
            continue  # a stale/garbage week key never blocks the planner
        prev = last_use.get(row.title)
        if prev is None or used_monday > prev:
            last_use[row.title] = used_monday
        if 0 < (req_monday - used_monday).days < ROTATION_WINDOW_DAYS:
            excluded.add(row.title)
    kept = [r for r in roster if r.title not in excluded]
    if len(kept) < meal_days:
        logger.warning(
            "rotation pool too thin for user %s week %s (%d titles < meal_days"
            " %d) — relaxing oldest-used-first",
            user_id, week_key, len(kept), meal_days)
        kept_titles = {r.title for r in kept}
        for candidate in sorted(
                (r for r in roster if r.title not in kept_titles),
                key=lambda r: last_use.get(r.title, date.min)):
            if len(kept) >= meal_days:
                break
            kept.append(candidate)
    return kept


# ---------------------------------------------------------------------------
# Diversity (MC 10351): the three suggestions must actually differ
# ---------------------------------------------------------------------------


def _diverse_plans(week_key: str, offers: list, roster: list,
                   family) -> list:
    """One plan per DEFAULT_SEEDS, each over the ROTATED roster minus the
    dishes of earlier suggestions as far as the pool allows (MC 10351: greedy
    hit-count ranking left the seeds nothing to vary, so suggestion 1 and 2
    came out byte-identical on the live week).

    Suggestion k tries the exclusion sets strongest-first: minus ALL earlier
    dishes, then minus only the LATER ones (dropping the oldest exclusion —
    the immediately previous suggestion stays excluded, so adjacent cards
    cannot collide), down to the unchanged pool. The first exclusion set
    whose plan still fills the week wins: a plan never starves and never has
    fewer days than the full pool could give (the degradation the rotation
    relax already guarantees — never silent starvation). Determinism (N7) is
    untouched: the pool is a pure function of roster+offers+seed-order, no
    new randomness, so POST /api/menu/accept recomputes byte-identically."""
    plans: list = []
    prior_titles: list[set] = []
    full_days = 0
    for i, seed in enumerate(DEFAULT_SEEDS):
        if i == 0:
            plan = plan_menu(week_key, offers, roster, family, seed=seed)
            full_days = len(plan["days"])
        else:
            plan = None
            for start in range(len(prior_titles) + 1):
                excluded: set = set()
                for titles in prior_titles[start:]:
                    excluded |= titles
                pool = roster if not excluded else [
                    r for r in roster if r.title not in excluded]
                candidate = plan_menu(week_key, offers, pool, family,
                                      seed=seed)
                if len(candidate["days"]) >= full_days:
                    plan = candidate
                    break
        plans.append(plan)
        prior_titles.append({d["dish_id"] for d in plan["days"]})
    return plans


# ---------------------------------------------------------------------------
# Store-level selection helpers (MC 1355.16, T10b §4)
# ---------------------------------------------------------------------------


def _resolved_chain_ids(resolved: dict | None, chain: str) -> list[str]:
    """Store ids resolved for *chain*; [] when absent or the chain errored.

    An errored chain contributes chain-level rows only (T10b §4) — which the
    store clause below expresses by matching no store-scoped ids.
    """
    if not isinstance(resolved, dict):
        return []
    entry = (resolved.get("chains") or {}).get(chain) or {}
    if entry.get("status") != "ok":
        return []
    return [str(s.get("store_id")) for s in entry.get("stores", [])
            if s.get("store_id") is not None]


def _resolved_store_names(resolved: dict | None) -> dict:
    """store_id -> store_name across ALL resolved chains (T10f DA P3-2).

    Empty when unresolved/malformed — the UI then falls back to the raw id.
    """
    names: dict = {}
    if not isinstance(resolved, dict):
        return names
    for entry in (resolved.get("chains") or {}).values():
        if not isinstance(entry, dict) or entry.get("status") != "ok":
            continue
        for store in entry.get("stores") or []:
            sid = store.get("store_id")
            name = store.get("store_name")
            if sid is not None and name:
                names[str(sid)] = str(name)
    return names


def _apply_store_clause(offers: list, resolved: dict | None) -> list:
    """Keep an offer when chain-level (store_id NULL) or scoped to a resolved
    store of its own chain (T10b §4 step 1). No resolved stores at all -> a
    no-op: behavior is exactly today's (the regression guard)."""
    if not isinstance(resolved, dict) or not resolved.get("chains"):
        return offers
    kept = []
    for offer in offers:
        ids = _resolved_chain_ids(resolved, offer.grocer_id)
        if offer.store_id is None or str(offer.store_id) in ids:
            kept.append(offer)
    return kept


def _dedup_by_name(offers: list, resolved: dict | None) -> list:
    """Dedup (grocer_id, normalized name) preferring the store-level row
    (T10b §4 step 2). Normalized = casefold + strip (T10d N3). Chains without
    resolved stores are left untouched — the byte-identical no-op regression
    guard — so plan_menu's offer-hit counting and andel_extrapris never
    double-count the same physical product that exists both chain-level and
    store-level."""
    best: dict = {}
    ordered: list = []
    for offer in offers:
        if not _resolved_chain_ids(resolved, offer.grocer_id):
            ordered.append(offer)  # untouched chain: never deduped
            continue
        key = (offer.grocer_id, (offer.name or "").casefold().strip())
        current = best.get(key)
        if current is None:
            best[key] = offer
            ordered.append(offer)
        elif offer.store_id is not None and current.store_id is None:
            best[key] = offer  # store-level row wins; keep its position
            ordered[ordered.index(current)] = offer
    return ordered
