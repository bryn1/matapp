# matapp — architecture (kept true by the Architect; layout v2)

Single FastAPI web app + a vendored self-contained "motor" (`src/`). One sqlite
store. Deployed on vm106 under `/matapp/` (nginx strips the prefix; port 8141,
`DEPLOY.md`). Python deps pinned in `requirements.txt`: fastapi, uvicorn,
sqlalchemy, pydantic, argon2-cffi, httpx. No other runtime deps.

## Module map

### Web layer — `app/` (importable only upward into `src/`, never the reverse)
- `app/main.py` — FastAPI assembly; lifespan runs `db.boot()` + one weekly
  ingest pass (`src/scheduler/periodic.main`, fail-tolerant).
- `app/db.py` — engine/session singleton, `init_db`, and `ensure_columns()`:
  the guarded-ALTER migration list `_NEW_COLUMNS` (idempotent, concurrent-boot
  tolerant). New columns ride this mechanism — never a second migration path.
- `app/security.py`, `app/auth_service.py` — argon2id password hashing, opaque
  session tokens (`matapp_session` HttpOnly cookie), `SessionStore`,
  `LoginRateLimiter`.
- `app/routers/auth.py` — `POST /api/auth/register|login|logout`,
  `GET /api/auth/me`. Registration is open (MC 1355.7); register auto-login.
- `app/routers/profile.py` — `GET/PUT /api/profile`. PUT semantics:
  **absent field = unchanged, explicit null = clear** (`model_fields_set`),
  422 on out-of-range. Applies to `postal_code`, `num_children`,
  `prefer_kid_friendly`.
- `app/routers/menu.py` — `GET /api/menu?week=YYYY-Www` (auth-skyddad). Reads
  offers from the offers DB, filters by the user's selected stores, builds
  `FamilyPrefs` from the persisted profile, calls `src.planner.menu.plan_menu`
  once per seed (3 suggestions). Response models: `MenuResponse` →
  `Suggestion` → `MenuDay` (additive fields only; `offer_sources`,
  `kid_friendly`).
- `app/routers/stores.py` — store catalog from the ONE `PlannerConfig`
  (`app/config.py`).
- `app/profile_service.py` — profile CRUD (`ProfileData` value object, one row
  per user, upsert on save).
- `app/optimizer/` — `recipes.py` (the hand-curated `ROSTER` the planner
  actually consumes; mirrors the DB `Recipe` fields — two carriers of one
  entity), `optimizer.py` (`FamilyPrefs`, `DEFAULT_SEEDS = (101, 202, 303)`,
  `andel_extrapris`).

### Motor — `src/` (vendored, self-contained; never imported by anything outside)
- `src/fetcher/` + `src/fetcher/adapters/` — offer fetchers. Adapter files:
  `ica.py`, `lidl.py`, `tjek.py` (+ `_common.py`). Willys and Coop publish
  their veckoblad through Tjek, so ONE Tjek adapter serves both chains
  (dealer ids in `app/config.py`); there are no separate willys/coop adapter
  files. `aggregate.py` + `grocer.py` drive the pass.
- `src/normalizer/` — offer normalization to the `offers` row shape
  (`chain_mapper.py`, per-ingredient `is_extraprice`).
- `src/offers_db/store.py` — the SINGLE canonical `offers` table
  (UNIQUE `grocer_id, external_id, week_key` + store scope), `upsert_week`,
  `list_offers_in_week`.
- `src/planner/menu.py` — `plan_menu`: filter (`_allowed`) → rank by offer-hit
  count → seeded tiebreak → one dish per day, no repeats; pool exhaustion
  truncates the week (never crashes). MC 1355.18: kid-friendly wins TIES when
  `prefer_kid_friendly` is set (boost, not filter).
- `src/planner/weeks.py` — ISO week math (`week_to_monday`), the ONE week
  validator.
- `src/locator/` — postnummer → stores per chain (willys/ica/coop/lidl/geo).
- `src/recipes/` — recipe-db: `store.py` (ORM + `upsert_recipe`),
  `seed.py` (`seed_starter`, idempotent, run only from `run_motor.py` — NOT at
  app boot), `scraper.py` (`file://`/http ingest, drop-not-fail).
- `src/scheduler/periodic.py` — the weekly ingest pass wired at boot.

### Entrypoints
- `server.py` — vm106 service entrypoint: `$HOST`/`$PORT` (default
  127.0.0.1:8141), mounts `templates/index.html` at `/`, `/static`, `/health`,
  then serves `app.main:app`. Sqlite lives under `$STATE_DIRECTORY`
  (`MATAPP_DB_URL` override).
- `run_motor.py` — ops job: full ingest + `seed_starter`. Never imported by
  `app.main`.

## Data flow

1. Ingest (boot + ops): fetcher adapters → normalizer → `offers` DB rows
   (week-scoped, store-scoped).
2. `GET /api/menu`: offers DB → store-selection filter → `plan_menu` over the
   optimizer `ROSTER` → 3 seeded `Suggestion`s (+ `offer_sources`).
3. The planner NEVER reads the recipes DB; the seed/scraper `kid_friendly`
   values are bookkeeping/consistency only (planning reads the roster).

## Auth model

Open registration, argon2id hashes, opaque session cookie (`HttpOnly`),
`current_user` resolves every protected handler → 401 when absent/invalid.
No password reset, no e-mail verification, no lockout change.

## Offer sources (verified endpoints)

| Chain | Source | Endpoint |
|---|---|---|
| Willys | store locator | `https://www.willys.se/axfood/rest/v2/store` |
| ICA | offers + stores | `https://www.ica.se/erbjudanden/`, `https://www.ica.se/e11/public-access-token`, `https://apim-pub.gw.ica.se/sverige/digx/storesearch/v1` |
| Coop | offers + stores | `https://www.coop.se/butiker-erbjudanden/`, `https://proxy.api.coop.se/external/store/stores?api-version=v1` |
| Lidl | offers + stores | `https://www.lidl.se/c/erbjudanden`, `https://live.api.schwarz/odj/stores-api/v2/myapi` |
| Tjek (serves Willys + Coop) | offer data | `https://squid-api.tjek.com/v2/catalogs?dealer_id=<id>` via `src/fetcher/adapters/tjek.py` |
| Geocode | Nominatim | `https://nominatim.openstreetmap.org/search` |

## Store-level selection flow (MC 1355.16)

Profile `postal_code` → resolved once at save time (`src/locator`, per-chain
status persisted in `resolved_stores` JSON, never a 500) → `GET /api/menu`
keeps a chain-level offer row (`store_id` NULL = valid everywhere) or a row
scoped to one of the profile's resolved stores → dedup by (grocer_id,
normalized name) preferring the store-level row. No postal code / no resolved
stores → both steps are no-ops (behaviour = pre-T10b).

## Known limitations (stated, not hidden)

- **ICA store-scoped ingest is a named follow-up** — store-scoped offer rows
  currently populate for Willys only (`src/scheduler/periodic.py`
  `run_store_scoped_ingest`); ICA is chain-level until that follow-up lands.
- **Coop cold-start warm**: the Coop locator's store cache is file-backed; the
  first resolve after a cache wipe warms it lazily inside that resolve
  (`src/locator/coop.py` docstring) — a stated POC limitation.
- **`num_children` is informational-only in T11** (does not feed servings
  planning, `app/routers/profile.py`); servings-aware planning is the T11b
  follow-up.

## Data store

One sqlite file (`MATAPP_DB_URL` / `$STATE_DIRECTORY`). Tables on the shared
`database.Base` declared in the repo-root `database.py` (ORM models in
`app/models/`): `users`, `profile`, `offers`, `recipes`, `store_selection`
(sessions live in-memory in `auth_service.SessionStore`, not a table). Schema
changes = ORM column + `_NEW_COLUMNS` guarded-ALTER entry
(existing rows read NULL — every reader treats NULL as 0/absent).

## Tests

`tests/` pytest, offline (temp-DB `client` fixture in `conftest.py`, no
network). Run: `python -m pytest tests/ -q` from the repo root with the app's
deps installed. NOTE (MC 10037): the `client` fixture yields a TestClient
without entering its context manager, so the lifespan (boot ingest + recipe
boot-seed) never runs under pytest — the live-boot path is covered by runtime
acceptance, and menu tests default to the ROSTER fallback unless they seed the
table explicitly (see `tests/test_db_recipe_roster.py`).

## Ported features (from bryn1/matapp, MC 10037)

### Recipe roster: DB-served with ROSTER fallback (P1-a0)
- `app/models/recipes_db.py` re-exports the canonical `Recipe`/`c_rdb_list_all` from `src/recipes/store.py` (offers_db shim precedent; ONE mapping on the shared Base — app code must never import `src.recipes.store` directly).
- `app.db.boot()` → `seed_recipes_if_empty()` seeds the 18 starter recipes once (idempotent, fail-tolerant; motor failure ⇒ ROSTER fallback, never a crash).
- `/api/menu` roster = DB rows when non-empty, static ROSTER otherwise; both shapes carry `ingredients_json` et al., so planner/optimizer seams are unchanged. `src/planner/menu.py` stays pure and deterministic — no clock inside.

### Rotation + accept + ratings (P1-a)
- `POST /api/menu/accept {seed, week?, dishes}` recomputes the offered plan through the ONE shared assembly (`_assemble_menu`, identical inputs to the GET that offered it) and records `recipe_usage(user_id, title, week_key, seed)` — written ONLY here (single-clock rule; matapp's plan-time vs cook-time two-clock mess is the anti-pattern deliberately not ported). Re-accepting a week replaces its rows. **`dishes` is REQUIRED** (DA P1-A fix): the client echoes the dish ids the GET offered for that seed; if the recompute diverges (boot-ingest rewrote offers at a restart, profile moved) accept answers **409 and records NOTHING** — a divergence never lands usage rows, so the rotation clock and shopping/build only ever see plans the household was actually shown.
- `GET /api/menu` drops titles the user accepted < 42 days before the planned week (router-side week math, `ROTATION_WINDOW_DAYS = 42`; a usage exactly 42 days old may return); relax-oldest-first + one WARNING when the filtered roster can't fill `meal_days` (matapp GT-5rz contract). `ProfileBody` bounds are `persons 1..12`, `meal_days 1..7`, `kron_budget 0..10M` (DA P2-B — out-of-range 422), and `_assemble_menu` answers **409** when a profile leaves zero eligible recipes: `/api/menu` can never 200 a zero-day week.
- `POST /api/recipe/rate` (rating 1..7, upsert) + `GET /api/recipe/ratings` on `recipe_rating(user_id, title, rating)`. No ranking use yet — named follow-up.
- `GET /api/menu/accepted?week=` (MC 10349) — thin read over `recipe_usage` for one week (default current): `{week_key, seed, dishes[]}` in accept/render order; 404 with the shopping/build 404 detail shape when nothing is accepted. Writes nothing — the UI reads it on view load so the "Vald ✓" mark survives a reload.
- `GET /api/recipe/{title}` (MC 10349) — one `recipes` row through the `app.models.recipes_db` shim: `{title, category, servings, vegetarian, kid_friendly, ingredients:[{name,qty,unit}], allergens:[...]}`; 404 unknown title. Registered LAST on the `/api/recipe` prefix — FastAPI's registration-order matching keeps `/rate` + `/ratings` theirs.

### Weekly shopping (P1-b/P2)
- `app/services/shopping_text.py` — PURE port of matapp's normalize/longest-keyword categorize/quantity-merge (no db import; the pantry-coupling flaw fixed at the seam).
- `shopping_item(user_id, week_key, item, quantity, category, checked, added_manually, source∈{plan,memory,staple,manual})`, UNIQUE(user_id, week_key, item); GET/POST/DELETE `/api/shopping(+/{item})`, POST `/api/shopping/toggle` (check = purchase).
- `shopping_memory(user_id, item, last_bought, times_bought, avg_interval_days, bought_dates)` updated on toggle-check; due habitual items (times_bought>=3, interval due) auto-inject into a fresh week (matapp GT-6q4 logic), deduped.
- `staple(user_id, item, interval_days, last_bought)`; due staples inject into a fresh week (staple wins name collisions); buying resets `last_bought`; `/api/staples` CRUD.
- `POST /api/shopping/build?week=` aggregates the ACCEPTED plan's recipe ingredients into `source='plan'` rows (404 without an accepted plan for that week; 409 if an accepted title is missing from the recipes table), merging quantities into existing rows; building also makes the week non-fresh for due-injection.
- All shopping/accept/rate routes auth-gated via the shared 401 dependency; per-user isolation is the contract (matapp's plaintext-global tables deliberately NOT carried).

### Journey UI (MC 10349 — audit findings 2 + 7: the API journey was never in the UI)
- `templates/index.html` serves FOUR views (Konto/Profil/3 förslag/Handelslista) + a native `<dialog>` for recipe reading; vanilla modules bootstrap from `static/js/app.js` (window aliases, POC idiom). No new CSS: the journey reuses the `state-banner`/`empty-state`/`form-*`/`btn` classes.
- `static/js/ui/suggestions.js` — per-card "Välj detta förslag" → `POST /api/menu/accept` echoing the card's dishes in render order; 200 marks "Vald ✓" + status line, 409 auto-refetches `GET /api/menu` and says "Menyn uppdaterad — välj igen", other statuses an honest error banner. `GET /api/menu/accepted` on load keeps the mark across reloads. `render()` now lifts `#suggestions-loading` (the card-10064.1.3 stuck-loader fix; finding 7).
- `static/js/ui/shopping.js` — Handelslista: rows grouped by category, checkbox → toggle, add form → POST, "Ta bort" → DELETE (encodeURIComponent), "Bygg från veckans meny" → `POST /api/shopping/build` whose 404 answers "Velj ett förslag under 3 förslag först". DOM nodes + textContent only (item names are user data).
- `static/js/ui/recipe.js` — one shared dialog for dish buttons (suggestions + plan-sourced shopping rows; build writes INGREDIENT rows, so an unknown one honestly shows "Inget recept hittades"). Escape/close button native, focus returns to the opener via the `close` event.

### Deliberately NOT ported (decision record)
order_agent/auto-ordering (external-account credentials, PoC-grade); matapp scrapers/willys/campaigns/geo tables (Tjek store-scoped ingest supersedes); blob-encrypted `user_data` scheme (relational per-user tables instead); committed admin password, key-in-cookie sessions, shared creds file (port blockers — MATAPP audit §7). Deferred with reasons: pantry, price-watchlist, recipe-catalog scraper.
