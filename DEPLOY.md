# matapp — deploy & operations (MC 1355.11)

Public URL: https://sibbamala.com/matapp/
Manifest:  apps.yaml -> {name: matapp, type: service, root: apps/matapp,
           exec: server.py, port: 8141}
Unit:      vm106-app-matapp.service (renderer-generated from the manifest)
State:     $STATE_DIRECTORY = /var/lib/vm106-app-matapp (systemd StateDirectory)
           -> .data/kvallsmat.db lives there, never in this read-only repo tree.

## What is deployed (framtidsversion, MC 1355)
- FastAPI app (`app/`) + vendored motor (`src/`): fetcher -> normalizer -> offers DB
  -> planner. Auth: argon2id + session cookie; open registration (owner-ratified).
- Offer sources (all real, fail-tolerant): ICA page-embedded weeklyOffers JSON,
  Lidl campaign-page JSON, Willys + Coop via the Tjek squid API (per-store catalogs).
- Boot ingest runs in the app lifespan (current ISO week); /health carries
  offers_week / offers_current_week / offers_by_grocer.
- Menu: GET /api/menu?week=YYYY-Www (validated; 422 on impossible weeks), planned
  from offers-DB rows filtered to the profile's selected stores.

## Deploy flow
1. Mirror: /srv/workspace/hosting/apps/matapp (repo bryn1/hosting). Assemble by
   rsync from this repo, source wins on every conflict; exclude .git/.audits/.tmp/
   tests/__pycache__/.pytest_cache/LEDGER.md/README.md/.data. Never let the mirror
   carry files or hunks this repo lacks (pre-publish diff is mandatory).
2. Commit + push the hosting repo; the vm106 pull timer applies within 5 min.
3. Verify live: /health (offers signal), /api/stores (4 stores incl lidl),
   register + auth'd menu journey.
Rollback: `git revert <deploy-sha>` in the hosting repo (previous deploy sha is in
the LEDGER / MC card 1355.11: 265b7cb).
