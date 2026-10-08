"""app.routers.plans — POST /api/menu/accept + recipe ratings (MC 10037 P1-a).

Split from app/routers/menu.py per the repo's size ceiling (menu.py stays the
GET/assembly owner, <400 lines); both new surfaces are thin HTTP wire-ups over
existing single implementations:

  * accept REUSES the one shared assembly ``menu._assemble_menu`` and RECOMPUTES
    the exact plan for the given seed — there is no second plan builder. It
    then writes the rotation clock (recipe_usage, replace-all per user+week).
  * rate upserts per-user ratings (recipe_rating), the conservative port of
    matapp's household-rating idea: this wave only records them.

Auth: every handler resolves the session cookie via the SAME dependency
``menu._current_user_or_401`` the menu GET uses — absent/invalid cookie is 401,
never 200 (gate-C6 line, one gate not two).

MC 10349 (finding 2: the owner's journey was API-only, unreachable in the UI)
adds the two THIN READS the UI needs, over the same single implementations:

  * ``GET /api/menu/accepted`` — the current week's accepted plan (a filter
    over ``recipe_usage`` via ``list_usage``; the same 404 answer
    POST /api/shopping/build gives), so the "Vald ✓" mark survives a reload.
  * ``GET /api/recipe/{title}`` — one recipe from the ``recipes`` table (via
    the ``app.models.recipes_db`` shim), 404 when unknown. Registered LAST on
    the /api/recipe prefix so the literal /rate + /ratings routes keep their
    own matches (FastAPI matches registrations in order).
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import db
from app.models.recipe_rating import list_ratings, upsert_rating
from app.models.recipe_usage import list_usage, replace_week_usage
from app.models.recipes_db import Recipe
from app.models.users import User
from app.optimizer.optimizer import DEFAULT_SEEDS
from app.routers.menu import (WEEK_PATTERN, _assemble_menu,
                              _current_user_or_401)
from src.planner.weeks import current_week_key, week_to_monday

accept_router = APIRouter(prefix="/api/menu", tags=["menu"])
recipe_router = APIRouter(prefix="/api/recipe", tags=["recipe"])


class AcceptBody(BaseModel):
    seed: int
    week: str | None = None
    # MC 10037 DA P1-A: the client MUST echo the dish ids the GET offered for
    # this seed, in order. REQUIRED on purpose — an optional field with a
    # "None = current behavior" fallback would keep the silent-recording
    # defect alive for every client that omits it, and that is exactly the
    # defect the DA verdict named. No client outside this repo called accept
    # yet (grep: tests only), so the API break is minimal and the guarantee
    # absolute: a divergent plan never lands usage rows.
    dishes: list[str]


@accept_router.post("/accept")
def accept_plan(body: AcceptBody,
                request: Request,
                user: User = Depends(_current_user_or_401),
                session=Depends(db.get_db)) -> dict:
    """Accept one of THIS week's three suggestions: recompute its exact plan,
    VERIFY it still matches what the client was offered, then record the
    rotation usage (replace-all for user+week) and return the dishes.

    DA P1-A guarantee: offers are rewritten by the boot-time ingest on every
    server restart, and profiles move — so the recompute can diverge from the
    GET the user acted on. When it does, answer 409 and record NOTHING (the
    client re-fetches and accepts the current plan); the rotation clock and
    shopping/build only ever see plans a household actually saw."""
    if body.week is not None and not re.fullmatch(WEEK_PATTERN, body.week):
        raise HTTPException(
            status_code=422,
            detail=f"week is not a well-formed ISO week key: {body.week!r}")
    week_key = body.week or current_week_key()
    menu = _assemble_menu(session, user, week_key)  # 422 on a fake week
    suggestion = next((s for s in menu.suggestions if s.seed == body.seed),
                      None)
    if suggestion is None:
        raise HTTPException(
            status_code=422,
            detail=f"seed {body.seed} is not one of the offered seeds "
                   f"{sorted(DEFAULT_SEEDS)}")
    dishes = [d.dish_id for d in suggestion.days]
    # DA P1-A: the plan recomputed NOW must be exactly what the client claims
    # it was offered. Offers can be rewritten by boot-ingest on a server
    # restart and profiles can move between GET and accept; a divergence means
    # the user would be recording a plan they never saw — refuse with 409 and
    # write NO usage rows (the rotation clock and shopping/build only ever see
    # plans the household actually saw). The client re-GETs and accepts the
    # current plan.
    if dishes != body.dishes:
        raise HTTPException(
            status_code=409,
            detail=("the offered plan changed since it was fetched — accept "
                    "records only the plan you were shown; re-fetch "
                    "GET /api/menu and accept with the current dishes"))
    replace_week_usage(session, user.user_id, week_key, body.seed, dishes)
    return {"ok": True, "week_key": week_key, "dishes": dishes}


def _week_or_422(week: str | None) -> str:
    """Optional ?week= gate: pattern enforced by FastAPI, REAL-week validity
    by the shared src.planner.weeks helper — the same 422 boundary as the menu
    and shopping routers (one gate contract, never a 500)."""
    week_key = week or current_week_key()
    try:
        week_to_monday(week_key)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"week is not a real ISO week: {week_key!r}") from None
    return week_key


@accept_router.get("/accepted")
def get_accepted_plan(request: Request,
                      week: str | None = Query(default=None,
                                               pattern=WEEK_PATTERN),
                      user: User = Depends(_current_user_or_401),
                      session=Depends(db.get_db)) -> dict:
    """The plan this user ACCEPTED for a week (default: the current one):
    {week_key, seed, dishes[]} in accept/render order.

    A thin read over the ONE rotation clock — rows are written only by
    POST /api/menu/accept, this never touches them. 404 with the same detail
    shape as POST /api/shopping/build's ("no accepted plan for week X"):
    "nothing accepted yet" is one honest answer, not two mechanisms. The UI
    calls it on view load so the "Vald ✓" mark survives a reload."""
    week_key = _week_or_422(week)
    rows = [row for row in list_usage(session, user.user_id)
            if row.week_key == week_key]
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"no accepted plan for week {week_key}")
    return {"week_key": week_key, "seed": rows[0].seed,
            "dishes": [row.title for row in rows]}


class RateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    rating: int = Field(ge=1, le=7)  # matapp's 1..7 household scale


@recipe_router.post("/rate")
def rate_recipe(body: RateBody,
                request: Request,
                user: User = Depends(_current_user_or_401),
                session=Depends(db.get_db)) -> dict:
    """Upsert the authenticated user's rating for one dish title."""
    upsert_rating(session, user.user_id, body.title, body.rating)
    return {"ok": True, "title": body.title, "rating": body.rating}


@recipe_router.get("/ratings")
def get_ratings(request: Request,
                user: User = Depends(_current_user_or_401),
                session=Depends(db.get_db)) -> list[dict]:
    """The authenticated user's ratings, title-ordered."""
    return [{"title": r.title, "rating": r.rating,
             "created_at": r.created_at.isoformat()}
            for r in list_ratings(session, user.user_id)]


def _json_list(raw) -> list:
    """Tolerant JSON-array read of a *_json column (shopping/build precedent:
    a broken row never 500s a read — it degrades to the honest empty list)."""
    try:
        parsed = json.loads(raw or "[]")
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


@recipe_router.get("/{title}")
def get_recipe(title: str,
               request: Request,
               user: User = Depends(_current_user_or_401),
               session=Depends(db.get_db)) -> dict:
    """Read one recipe from the ``recipes`` table (via the app.models shim —
    app code never imports src.recipes.store directly). 404 for an unknown
    title; the UI shows that state instead of faking a recipe.

    This catch-all path param MUST stay registered after /rate and /ratings:
    FastAPI matches routes in registration order, and GET /api/recipe/ratings
    is a literal path, not a title."""
    row = session.query(Recipe).filter_by(title=title).first()
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"no recipe for title: {title!r}")
    ingredients = []
    for ing in _json_list(row.ingredients_json):
        if not isinstance(ing, dict):
            continue
        name = str(ing.get("name", "")).strip()
        if not name:
            continue
        ingredients.append({"name": name, "qty": ing.get("qty"),
                            "unit": str(ing.get("unit") or "")})
    return {"title": row.title, "category": row.category,
            "servings": row.servings,
            "vegetarian": bool(row.vegetarian or 0),
            "kid_friendly": bool(row.kid_friendly or 0),
            "ingredients": ingredients,
            "allergens": [str(a) for a in _json_list(row.allergens_json)]}
