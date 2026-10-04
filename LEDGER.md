# LEDGER — Matapp (github.com/bryn1/Matapp)

Grocery menu planner: ingests weekly offers from Swedish grocers, plans menus from
recipes against those offers. FastAPI app (`app/`) + ingest motor (`src/`).

STATUS: ACTIVE — audit + real-offers build landed 2026-09-24 (MC 1355).

## Dated entries
- 2026-09-24 (MC 1355.1, T1 audit): fresh suite 9 passed; findings P0×2 (no store-selection
  in source; two-way source/hosting divergence), P1×3 (canned menu data, profile accepted
  unknown/duplicate stores, mock-only motor), P2×4, P3×2. Artifact audit/T1-correctness.md.
- 2026-09-24 (MC 1355.2, T2 research): real offer data sources mapped. ICA erbjudanden page
  embeds complete weeklyOffers JSON server-side; Lidl campaign pages embed product JSON with
  deletedPrice; Coop has a real APIM gateway (paths need devtools capture); Willys OCC not
  URL-reachable. Ranking ICA > Lidl > Coop > Willys. Artifact audit/T2-data-sources.md.
- 2026-09-24 (MC 1355.3, T3 port-back, commit 5f3bc1d): stores router + config seam ported
  from the deployed copy into the source; profile store validation 422s unknown/duplicates.
- 2026-09-24 (MC 1355.4, T4a, commit 49f0914): real ICA+Lidl fetcher adapters behind the
  existing RawFeed contract (fail-tolerant); Lidl added to CHAIN_MAP + default catalog.
  Live sanity: ICA 11 entries, Lidl 132 entries parsed.
- 2026-09-24 (MC 1355.5, T4b, commit b06c309): /api/menu takes a validated week param
  (422 on impossible weeks — kills latent BUG-1/BUG-2); menu planned from offers-DB rows
  filtered to selected stores; boot-time ingest in lifespan; shared week-math helper
  src/planner/weeks.py (dedup).
- 2026-09-24 (MC 1355.6, T5 DA gate): VERDICT FIX — P1 silent total failure, P1 extrapris
  rule dead (no regular prices), P1 no user provisioning, P2 double prefix. Artifact
  audit/T5-DA-verdict.md.
- 2026-09-24 (MC 1355.7, T6 fixround, commits 77ff2ad + 966ddaa): /health offers signal +
  0-entry grocer warnings; Lidl deletedPrice → regular_price_cents (andel_extrapris now
  non-zero, 0.333 observed); single-prefix external ids; POST /api/auth/register
  (owner-ratified open registration). Suite 53 passed EXIT=0.
- 2026-09-24 (orchestrator e2e verification): boot → real ingest (71 offers: ica 11,
  lidl 60) → register → select ica+lidl → menu 2026-W39 with 9/15 days carrying real
  used_offer_ids. VERIFIED.

## Known limitations / open items
- Willys + Coop still serve 0 real offers (willys needs a devtools OCC capture; coop needs
  real APIM resource paths — see T2 §2/§3). They warn in logs and count 0 in /health.
- Ingest runs at boot only; no periodic refresh yet (later card).
- Menus for non-current weeks degrade to recipe-only (ingest writes the current week).
- Deployed copy /srv/workspace/hosting/apps/matapp is now BEHIND the source repo; deploy
  pending owner go.
- 2026-09-26 (MC 1355.8/1355.9, T7 research): OCR of veckoblad = last resort only; Willys+Coop
  offers wireable via Tjek squid API (dealer c371GA / 6c28SD, hotspots = structured JSON, no
  auth). Offers are STORE-GATED for ICA/Willys/Coop (ICA merchant-priced by design; Willys
  "från respektive butik"; Coop helpcenter confirms store variation); Lidl national (single
  region in regionsPrices). Per-store cart needs store-level data for 3 of 4 chains.
- 2026-09-26 (MC 1355.10, T8, commit c16d088): Tjek adapter wired for willys+coop behind the
  existing RawFeed contract. Live pull all four: willys 111, ica 11, coop 97, lidl 132 real
  offers. Orchestrator e2e: health offers_current_week=279; register→profile(willys,ica,lidl)
  →menu 2026-W39 with 15/15 days carrying real used_offer_ids, max andel_extrapris 0.333.
- 2026-09-26 (MC 1355.11, T9, hosting commit e4b3d03): framtidsversion DEPLOYED to live
  https://sibbamala.com/matapp/. Orchestrator live-verified: health 200 (app matapp,
  auth argon2id+session, offers_current_week 279, all four grocers populated), /api/stores
  returns willys/ica/coop/lidl. Rollback sha: 265b7cb (hosting repo).

## 2026-09-26 — T10 store-level selection (MC 1355.12–1355.18) — LIVE
- T10a locators VERIFIED (ICA/Willys/Coop/Lidl), T10b design REV2 pinned SHIP (2 DA gates), T10e build 793f991 + T10f fixes 2f44683/14dc068, DA c4 SHIP. 103 tests green.
- Live deploy: hosting c2cfdc8 + a9261c2 (coop cache state-dir fix, live Errno 30). Live VERIFIED: health 200 (279 offers), postal preview 401 anon, PUT profile 41451 → all 4 chains ok, menu 200 (255 offer_sources, 15/15 days with offers).
- Known: store-scoped offer rows populate at next boot ingest (design §6 step 5); ICA store-scoped ingest = named follow-up; Coop cold-start warm inside first profile save (stated POC limitation).

## 2026-10-03 — MC 10037 port P1-a0: /api/menu serves the DB recipes table
- Roster source flipped from the static Phase-6 ROSTER to the DB `recipes` table when non-empty; boot() seeds seed_starter when empty (fail-tolerant, boot-ingest precedent); ROSTER stays the empty-table fallback. app/models/recipes_db.py re-export shim (offers_db precedent) is now the ONLY app-side import path for the recipes mapping; the menu reads via c_rdb_list_all through the shim. Seed-module sibling import handled with the repo-sanctioned sys.modules alias (no src/ on sys.path — the double-map hazard stays retired). Tests: tests/test_db_recipe_roster.py (seed/idempotent/db-served/fallback); test_menu_kid_friendly fallback scenario emptied the table to keep pinning the ROSTER path.

## 2026-10-03 — MC 10037 port wave B: weekly shopping + purchase memory + staples (P1-b/P2-a/P2-b)
- Ports from matapp: shopping_text (normalize/category/combine, pure), per-user relational shopping_item/shopping_memory/staple, fresh-week due-habit + due-staple injection, toggle=buy (record_purchase + staple reset). build-from-accepted deferred to commit A (recipe_usage). 127 tests green (/tmp/matvenv).
- MC 10037 P1-a (same card): recipe_usage + recipe_rating tables (per-user, shared Base); POST /api/menu/accept recomputes the offered plan via the ONE shared assembly (menu._assemble_menu refactor — GET and accept share one implementation) and replace-alls the usage rows; GET /api/menu excludes titles accepted < 42 days ago for THIS user (router-side week math, plan_menu stays pure; relax oldest-used-first + one WARNING, GT-5rz lesson); POST /api/recipe/rate (1..7) + GET /api/recipe/ratings. New router file app/routers/plans.py (menu.py stays <400 lines at 371). NOTE: commit 358e97b (shopping child) swept app/main.py WITH my plans mounts while app/routers/plans.py was still untracked — this commit restores a bootable HEAD (the parallel-build collision the PORT-PLAN sequencing warned about, realized).
- Point 4 SHIPPED (wave B follow-up): POST /api/shopping/build?week= aggregates the accepted plan (recipe_usage b96555b) via title->recipes join, plan-sourced rows, merge-not-duplicate against manual/memory rows; 404 without accept. 139 tests green.

## 2026-10-03 — MC 10037 fix cycle 1 (DA gate): accept divergence + profile bounds
- P1-A: POST /api/menu/accept gains REQUIRED dishes echo; recompute mismatch -> 409, zero usage rows written. P2-B: ProfileBody bounded (persons 1..12, meal_days 1..7, kron_budget 0..10M) + _assemble_menu 409s an unsatisfiable profile — /api/menu can never 200 a zero-day week. Gate: 142 passed, exit 0.
