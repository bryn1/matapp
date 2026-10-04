# T3 — port-back: unify matapp source with the deployed hosting copy (MC 1355.3)

Coder profile, 2026-09-24. Repo: `/srv/workspace/svarkor-matapp-audit-2026`
(fresh clone of github bryn1/Matapp @43d1eca). Deployed copy read from
`/srv/workspace/hosting/apps/matapp` (read/copy only — hosting repo untouched).
NOTE: the clone's default branch is `main` (not `master`); the commit landed there.

## Commit

```
5f3bc1d matapp: port back stores router + config seam from deployed copy (MC 1355.3)
7 files changed, 360 insertions(+), 1 deletion(-)
```

## Diff summary

| File | Change |
|---|---|
| `app/config.py` | ADDED (ported). Store catalog wired to the motor's PlannerConfig (O1); `get_planner_config()` + `PLANNER` (ica/willys/coop), `KVALLSMATS_GROCERS` JSON override. Adaptation: `PlannerConfig` imported from the in-tree `src.config` package (the source repo IS the motor home — no external-repo sys.path seam); the DB-URL knob NOT ported because root `database.make_engine` already owns `MATAPP_DB_URL` (no dead second knob). |
| `app/models/store_selection.py` | ADDED (ported). `StoreSelection` table on the SAME root `database.Base` as users/profile/offers; `upsert_selection` (cap 3, unknown id → ValueError). Added `valid_store_ids(config)` — the one validation source shared by the stores router AND the profile router. |
| `app/routers/stores.py` | ADDED (ported near-verbatim). GET `/api/stores` (catalog read at request time), POST `/api/stores/select` (replace-all, cap 3, unknown → 422), GET `/api/stores/selected`. |
| `app/main.py` | include_router(stores.router) alongside auth/profile/menu. |
| `app/models/__init__.py` | registers `store_selection` so boot() creates the table (fix-2 idiom). |
| `app/routers/profile.py` | P1-2 fix: PUT /api/profile rejects DUPLICATE store ids (422) and UNKNOWN store ids (422) via `valid_store_ids(get_planner_config())` — the same catalog /api/stores serves. |
| `tests/test_stores_portback.py` | NEW, 8 regression tests (catalog content, select unknown/>3 → 422, profile unknown/duplicate → 422, positive control, O1 live-catalog pin via monkeypatched PLANNER). |
| `requirements.txt` | NO CHANGE needed: `argon2-cffi==25.1.0` + `argon2-cffi-bindings==21.2.0` are ALREADY pinned at HEAD 43d1eca (P2-1 was closed by the fixround-2 commit; the T1 audit text predates that pin). VERIFIED via `git show HEAD:requirements.txt`. |

Auth/profile/menu files untouched — the ported files coexist with auth.

## Fresh test run (VERIFIED, caches cleaned first)

```
$ find . -name __pycache__ -type d -exec rm -rf {} + ; rm -rf .pytest_cache
$ /srv/workspace/Hotell/.venv/bin/python -m pytest tests/ -q
17 passed, 1 warning in 4.09s        EXIT=0
```
(9 pre-existing + 8 new; first run of the new tests caught a real fixture issue —
the Secure session cookie is not replayed by httpx over plain-http testserver —
fixed in the test helper by setting the cookie from the token explicitly.)

## Probe table (VERIFIED, TestClient, logged in)

| Probe | Result |
|---|---|
| GET /api/stores | 200 — `['willys', 'ica', 'coop']` (the 3 grocers, read from PlannerConfig) |
| POST /api/stores/select unknown store `butiken-x` | 422 |
| POST /api/stores/select 4 stores | 422 |
| PUT /api/profile unknown store | 422 |
| PUT /api/profile duplicates `["ica","ica","ica"]` | 422 |
| PUT /api/profile valid `["ica","willys"]` | 200, saved + echoed |

Probe script output exit code: EXIT=0.

# VERDICT: PASS
