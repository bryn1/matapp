# 1188.1 — matapp audit: inventory + goal-diff (GATE report)

**Card:** 1188.1 (parent 1188) · **Type:** research · **Agent:** kjell · **Date:** 2026-09-12 UTC
**Repo:** bryn1/hosting, apps/matapp/ · clone: /srv/workspace/hosting (HEAD = 12e2e30, clean)
**Evidence file:** /srv/workspace/hosting/kjell/1188.1-inventory-diff-evidence-20260912.md
**Report copy:** /srv/workspace/svarkor-matapp/audit/inventory-diff.md (per DoD)

All claims below are VERIFIED by commands run this session (outputs pasted in the evidence
file, sections E1–E14) unless marked otherwise.

---

## 1. FULL INVENTORY

### 1a. POC at HEAD 12e2e30 (what is LIVE)

- **Backend (app/):** FastAPI assembly `app/main.py` (mounts /static, root route, /health),
  `app/db.py` (boot + seed 18 starter recipes), `app/config.py` (DB_URL + motor path),
  routers: `stores.py` (GET /api/stores, POST /api/stores/select, GET /api/stores/selected),
  `menu.py` (GET /api/menu — single plan, query params week/meal_days/persons/vegetarian/
  budget_tier/allergens/seed).
- **Motor (src/, vendored):** config (CHAIN_MAP, GrocerConfig/PlannerConfig), database (Base),
  fetcher/grocer + aggregate, normalizer/chain_mapper (C4), offers_db/store (Offer table,
  upsert_week), planner/menu (plan_menu greedy, 1 plan per seed), recipes/store + seed +
  scraper, scheduler/periodic (ingest line M1→M4).
- **Frontend:** templates/index.html (2 views: Butiker, Veckomeny), static/js/app.js,
  ui/stores.js, ui/menu.js, utils/api.js (prefix-aware), css base/components/responsive.
- **DB tables:** offers (NO reference price), recipes, store_selection.
- **requirements.txt pins:** fastapi==0.141.1, uvicorn==0.52.4, sqlalchemy==2.0.52,
  pydantic==2.13.4. httpx explicitly NOT shipped ("test-only deps (pytest, httpx) are NOT
  shipped").
- **server.py:** hardcoded PORT=8141, HOST=127.0.0.1; $STATE_DIRECTORY rebase of the sqlite
  file; app.main itself serves static/root/health.

### 1b. Framtidsversion at 4d220cd (built, NOT live)

37 files changed, +1854/−1667 (git show 4d220cd --stat, E5). Adds:

- **Auth:** app/security.py (argon2-cffi PasswordHasher, Argon2id m=19MiB t=2 p=1; cookie
  matapp_session HttpOnly+Secure+SameSite=lax), app/auth_service.py (opaque in-memory
  SessionStore, token_urlsafe(32)), app/routers/auth.py (POST /api/auth/login, POST
  /api/auth/logout, GET /api/auth/me; 401 on bad creds / unauthenticated).
- **Profile:** app/models/profile.py (persons, meal_days, kron_budget REQUIRED, selected_stores
  ≤3, FK users.user_id UNIQUE), app/profile_service.py (upsert/load), app/routers/profile.py
  (GET/PUT /api/profile, auth-skyddad 401, 404 when no profile yet).
- **Menu-3 + ratio:** app/optimizer/optimizer.py (plan_menu with HARD ratio filter
  andel_extrapris >= 0.5 BEFORE greedy + 3 seeds DEFAULT_SEEDS=(101,202,303)),
  app/routers/menu.py (GET /api/menu auth+profil-skyddad, pydantic MenuResponse with
  suggestions[{week_key, seed, days[{date, dish_id, andel_extrapris}]}], SUGGESTION_COUNT=3).
- **DB:** app/models/users.py (users table), app/models/offers_db.py (offers table WITH
  regular_price_cents + savings_cents — closure-gap 1 closed in the NEW schema), root
  database.py (shared Base, MATAPP_DB_URL).
- **Frontend:** 3 views (auth, profil, 3 förslag), ui/auth.js, ui/profile.js,
  ui/suggestions.js, single css app.css (131 lines), favicon.ico.
- **server.py:** PORT/HOST from env; _wire_static_and_health() glue (app.main no longer
  serves static/root/health); local-dev MATAPP_DB_URL fallback.
- **requirements.txt:** adds argon2-cffi==25.1.0, argon2-cffi-bindings==21.2.0,
  httpx==0.28.1 (DA FIX-1 restored).
- **Unchanged:** src/ motor tree byte-identical to POC (E6), run_motor.py identical,
  DEPLOY.md identical.

---

## 2. DIFF-TABLE — goal (PHASE0-map §1) vs LIVE vs BUILT

| # | Mål-del | LIVE (sibbamala.com/matapp/) | BYGGT-EJ-LIVE (4d220cd) | SAKNAS |
|---|---|---|---|---|
| 1 | **Auth (argon2id + session)** | SAKNAS LIVE — VERIFIED: POST /api/auth/login → 404, GET /api/auth/me → 404 (E13) | BYGGT-EJ-LIVE — VERIFIED: routers/auth.py + security.py + auth_service.py + users table exist at 4d220cd (E9, E10) | — |
| 2 | **Profil (3 butiker, kron-budget, måltider/vecka, personer)** | SAKNAS LIVE — VERIFIED: GET /api/profile → 404 (E13). POC has only store_selection (≤3 butiker, no kron-budget/persons/meal_days per user) | BYGGT-EJ-LIVE — VERIFIED: models/profile.py + profile_service.py + routers/profile.py at 4d220cd; kron_budget REQUIRED, MAX 3 stores (E9, E10) | — |
| 3 | **3 veckomenyförslag** | SAKNAS LIVE — VERIFIED: live /api/menu returns ONE plan, no "suggestions" key (grep -c = 0, E13) | BYGGT-EJ-LIVE — VERIFIED: optimizer.plan_menu returns 3 Plans (DEFAULT_SEEDS 3 seeds), MenuResponse.suggestions, SUGGESTION_COUNT=3 (E9, E12) | — |
| 4 | **Majoritet extrapris (ratio >=50%, referenspris)** | SAKNAS LIVE — VERIFIED: live /api/menu has no andel_extrapris field (grep -c = 0); POC offers table has no regular_price (git grep 0 hits at 12e2e30, E11) | DELVIS BYGGT-EJ-LIVE — VERIFIED: ratio filter + andel_extrapris + regular_price_cents/savings_cents exist at 4d220cd (E10, E12). **BUT** the 4d220cd optimizer consumes a HARDCODED recorded offer set (app/optimizer/offers.py, 12 offers, willys 2026-W37) — the real ingest line (src/fetcher → normalizer → offers_db) is byte-identical to POC and still carries NO reference price. So "majoritet extrapris mot riktiga feeds" is SAKNAS even in 4d220cd | Real-feed reference-price throughput (RawOffer→NormalizedOffer→Offer) — SAKNAS i båda |
| 5 | **Reklamblad per vald butik (willys/ica/coop feeds)** | DELVIS LIVE — VERIFIED: /api/stores 200 (willys/ica/coop), /api/stores/select + /selected 200 (E13). BUT live /api/menu days all have used_offer_ids == [] — no offer data is actually flowing into the live menu (E13) | BYGGT (POC-arv) — VERIFIED: fetcher/normalizer/scheduler present and identical in both commits (E6) | Live offer ingest is not producing hits; ica/coop feed function remains PLAUSIBLE-UNCHECKED (token-gated per PHASE0-map §4) |

Cell status summary: every cell above is VERIFIED (curl/git evidence in the evidence file)
except the ica/coop feed-function sub-claim, which is PLAUSIBLE-UNCHECKED (inherited from
PHASE0-map §4; not re-testable without tokens).

---

## 3. POC vs framtidsversion — requirements.txt (httpx-pin) and server.py

- **requirements.txt:** POC ships 4 pins and explicitly does NOT ship httpx ("test-only deps
  (pytest, httpx) are NOT shipped"). 4d220cd adds argon2-cffi==25.1.0,
  argon2-cffi-bindings==21.2.0 and RESTORES httpx==0.28.1 as a runtime pin (DA FIX-1 /
  1164.9): src/fetcher/grocer.py + src/recipes/scraper.py import httpx at runtime, so
  run_motor.py (ingest-OPS) would die with ImportError after deploy without it. VERIFIED
  (E7).
- **server.py:** POC hardcodes PORT=8141/HOST=127.0.0.1 and relies on app.main for
  static/root/health. 4d220cd reads PORT/HOST from env and adds _wire_static_and_health()
  because the Phase-7 api-lager app.main ships no static/health routes. VERIFIED (E8).

---

## 4. Findings (severity-ranked)

1. **HIGH — the framtidsversion's "majoritet extrapris" is only proven against a recorded
   fixture, not real feeds.** app/optimizer/offers.py + recipes.py are hardcoded harness
   data (willys 2026-W37, 12 offers, 6 recipes). The real ingest line still lacks
   reference-price throughput (git grep regular_price in 4d220cd src/ = 0 hits). Goal part 4
   is therefore only partially built even at 4d220cd.
2. **HIGH — live site serves the POC only.** Live index.html is byte-identical to
   12e2e30's template (diff clean, E13); all future-only files 404. Every goal part 1–4 is
   absent live. (Parent brief records the 4d220cd publish gave 502 within ~4 min and was
   reverted; cause undetermined — out of scope for this card.)
3. **MEDIUM — live menu shows zero offer hits** (all used_offer_ids == [] on the live
   /api/menu response), so even the POC's "reklamblad" goal part is only structurally live
   (store selection works), not functionally (no offers flowing).
4. **LOW — 4d220cd sessions are in-memory** (auth_service.py docstring: "restarting the
   process drops them") — acceptable for the harness, noted for the fix round.
5. **LOW — POC tree contains a stray `apps/matapp/--help/` directory** (kvallsmat_motor.db
   at POC, plan_out.json at 4d220cd) — an artifact of a mis-invoked command, committed to
   the repo.

## 5. Method / verification level

- VERIFIED = git show/ls-tree/grep at pinned SHAs 12e2e30 and 4d220cd + live curl checks,
  all run this session; raw outputs in the evidence file (E1–E14).
- PLAUSIBLE-UNCHECKED = ica/coop feed function (token-gated; not re-tested).
- Negative claims (no regular_price in POC, no suggestions/andel_extrapris in live response)
  are backed by the instrument that would have shown them (git grep over the tracked tree;
  grep -c over the live response body).

VERIFY_EXIT=0
