# matapp — audit + working stores/offers + Lidl (MC 1355)

Owner ask (2026-02-24 session): audit matapp; functionality must work; actual stores and
their commercial offers must actually work to put together menus; add Lidl as a choosable
store. Owner ratified via question: **real offers where feasible** (not mock-only).

## Confirmed state (VERIFIED this session)

- Source repo `github.com/bryn1/Matapp` @43d1eca is a SINGLE commit; the deployed
  hosting copy `/srv/workspace/hosting/apps/matapp` (in repo `bryn1/hosting`) has
  DIVERGED both ways:
  - hosting-only: `app/config.py` (KVALLSMATS_GROCERS seam + PLANNER), `app/models/store_selection.py`,
    `app/routers/stores.py`, `--help` junk file, `.data/`
  - source-only: `app/auth_service.py`, `app/models/users.py`, `app/models/profile.py`,
    `app/optimizer/`, `tests/` (hosting REVERTED the auth version, commit 12e2e30)
- Live probe https://sibbamala.com/matapp/: health 200; /api/stores returns willys/ica/coop;
  /api/menu returns `used_offer_ids: []` — menus are planned with ZERO offers.
- Root cause chain: fetcher expects fictional `{base}/veckans-extrapris` JSON API;
  configured endpoints (feeds.willys.se etc.) are not real; scheduler (`src/scheduler/periodic.py`)
  exists but nothing in `server.py` runs it; optimizer's offers are a hardcoded fixture
  (willys, week 2026-W37). The whole offer pipeline is mock.
- Ingredient→offer matching is naive word-token overlap (`_offer_extrapris_matches`).
- 18 starter recipes; zero Lidl references anywhere in the tree.
- Prior audit MC 1188.10: BUG-1 week overflow 500, BUG-2 silent wrong weeks, BUG-3 DB-missing
  crash — fix status in @43d1eca to be confirmed by T1.

## Build plan (after T1 verdict + T2 research)

1. **Port-back first (P0):** absorb the hosting-only files (app/config.py seam,
   store_selection model, stores router) into the source repo so source == deployable
   product again. One commit, no behaviour change.
2. **Fix audit P0/P1s** from T1 (week validation, DB boot, etc.).
3. **Real offer fetchers** per T2 feasibility ranking: one adapter per grocer behind the
   existing `pull_grocer` contract (fail-tolerant, cached, TTL). No new mechanism beside
   the fetcher — the fetcher contract is the seam.
4. **Scheduler wiring:** run the weekly ingest on app boot + periodic refresh in the
   deployed service (state-dir DB), so offers actually populate.
5. **Lidl:** add to CHAIN_MAP + PlannerConfig default + KVALLSMATS_GROCERS default list +
   store catalog; display name "Lidl". O1 honored: catalog still read from PlannerConfig.
6. **Menu-from-offers proof:** end-to-end runtime check — select stores, ingest real
   offers, plan a menu with non-empty `used_offer_ids`.
7. **Deploy:** hosting repo update after source is green; diff source vs mirror before
   publish (mirror must not carry hunks the source lacks).

## Evidence
- T1: audit/T1-correctness.md (child 1355.1)
- T2: audit/T2-data-sources.md (child 1355.2)
