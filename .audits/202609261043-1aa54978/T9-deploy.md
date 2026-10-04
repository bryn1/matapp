# T9 — deploy matapp framtidsversion to live (MC 1355.11, parent 1355)

Owner-ratified (verbatim): "driftsättning till live, kontrollera vad som är live just nu
och se till att senaste versionen hostas".

Source: `/srv/workspace/matapp` @32c7963 (branch main). Mirror: `/srv/workspace/hosting/apps/matapp`
(repo `bryn1/hosting`). Executed by the `infra` profile, 2026-09-26.

## 1. Pre-publish diff verdict (mirror doctrine)

`diff -rq source mirror` (excluding .git/.audits/.tmp/.data) before assembly found:

- Mirror-only files: `static/css/base.css`, `static/css/components.css`,
  `static/css/responsive.css`, `static/js/ui/menu.js`, `static/js/ui/stores.js`,
  junk file `--help`, plus untracked `.pytest_cache/`.
- **Judgment on `static/js/ui/stores.js`** (mirror-only, NOT in the task's retired list —
  checked before deleting): it is the OLD frontend's store-picker view — it fetches
  `/api/stores` + `/api/stores/selected` and POSTs `/api/stores/select` with a hard
  3-store cap, and is referenced only by the OLD `templates/index.html` (lines 8-10, 213-214,
  same generation as the retired `menu.js`/old css). The NEW frontend handles store
  selection through the profile: `static/js/ui/profile.js` reads/writes
  `selected_stores` via GET/PUT `/api/profile` (Endpoints registry in
  `static/js/utils/api.js` has no stores-picker endpoints). → retired old-frontend file,
  **not a live fix**. Deleted.
- The other mirror-only files are exactly the retired files the task listed.
- **No mirror-only hunk that is a live fix the source lacks was found.** VERDICT: safe to publish.

## 2. Mirror assembly

`rsync -a --delete` source → mirror, excluding `.git/ .audits/ .tmp/ tests/ __pycache__
.pypytest_cache LEDGER.md README.md`, plus `.data/` (runtime state, `*.db` gitignored in
hosting — the mirror's local `.data/kvallsmat.db` left untouched, source dev DB not copied).
rsync's `--exclude` also protects receiver-side dirs from `--delete`, so `tests/` and
nested `__pycache__` were removed manually afterwards.

- Added (from source): auth stack (`app/security.py`, `app/auth_service.py`,
  `app/routers/auth.py`, `app/models/users.py`), profile (`app/profile_service.py`,
  `app/routers/profile.py`, `app/models/profile.py`), optimizer (`app/optimizer/*`),
  offers_db model, fetcher adapters (`tjek.py`, `ica.py`, `lidl.py`, `_common.py`),
  `src/planner/weeks.py`, new frontend (`static/css/app.css`, `ui/auth.js`,
  `ui/profile.js`, `ui/suggestions.js`, favicon), root `database.py`, `.gitignore`.
- Removed (retired): `static/css/{base,components,responsive}.css`,
  `static/js/ui/{menu,stores}.js`, `--help`, `.pytest_cache/`.
- Updated: `DEPLOY.md` — now describes auth + registration, 4 stores
  (willys/ica/coop/lidl per CHAIN_MAP in `src/config.py`), Tjek (willys+coop) +
  ICA + Lidl offer adapters, and the fail-tolerant boot ingest (MC 1355.5).
- Post-assembly check: `diff -rq source mirror` (modulo the intended exclusions)
  → **zero differences** (exit 0). The mirror carries nothing the source lacks.
- `tests/` and `.audits/` absent from the mirror (deploy-layer convention kept).

## 3. Mirror boot sanity (before push)

`HOST=127.0.0.1 PORT=39417 STATE_DIRECTORY=<tmpdir> /srv/workspace/hotell/.venv/bin/python
server.py` — server booted standalone ("Application startup complete"):

- `GET /health` → 200 `{"status":"ok","app":"matapp","auth":"argon2id+session",
  "profile":"auth-protected","menu":"auth+profile-protected","offers_week":"2026-W39",
  "offers_current_week":279,"offers_by_grocer":{"coop":97,"ica":11,"lidl":60,"willys":111}}`
  — boot ingest populated offers from ALL FOUR grocers.
- `GET /api/stores` → 200, 4 stores: willys, ica, coop, lidl (all enabled).
- `POST /api/auth/register` → 200.
- Process killed after the check (port 39417 confirmed closed).

## 4. Commit + push

- Commit: `e4b3d038c3000d8a8b9b430a5d22d09aba0348d1` — "matapp: deploy framtidsversion
  to live (MC 1355.11 T9)", author `svarkor (matapp-audit) <svarkor@agent-town.local>`
  (set one-shot via `git -c`; the repo's standing identity `svarkor-infra (MC 1335.9)`
  was left untouched). 50 files changed, 2722 insertions, 1543 deletions.
- Push: `6a524d5..e4b3d03 main -> main`, exit 0.
- **Rollback: the previous hosting commit sha is `265b7cbd5cd62b4060c837d7fce8b8a188482791`**
  (my commit's parent; `git revert e4b3d03` is the equivalent forward-rollback). Note: the
  rocket cron commits every ~5 min, so HEAD was `b2729a79` at task start and `265b7cbd`
  by commit time — the cron commits are unrelated to matapp.

## 5. Live verification (after the ≤5 min vm106 pull timer)

Waited 300 s after push, then (all VERIFIED this session, raw output quoted):

- `curl https://sibbamala.com/matapp/health` → 200:
  `{"status":"ok","app":"matapp","auth":"argon2id+session","profile":"auth-protected","menu":"auth+profile-protected","offers_week":"2026-W39","offers_current_week":279,"offers_by_grocer":{"coop":97,"ica":11,"lidl":60,"willys":111}}`
  — offers signal present, all four grocers populated. The old live version said
  `"app":"kvallsmats"` with no auth fields; this is the new version.
- `curl https://sibbamala.com/matapp/api/stores` → 200, 4 stores incl lidl:
  `[{"store_id":"willys",...},{"store_id":"ica",...},{"store_id":"coop",...},{"store_id":"lidl","name":"lidl","chain_type":"lidl","enabled":true}]`
- `POST /api/auth/register` → **200** (was 404 on the old live version).
  Follow-up `GET /api/auth/me` with the session cookie →
  `{"user_id":1,"username":"t9livecheck"}` — session auth works live.
- Menu fetch (auth'd) → 200 with **non-empty `used_offer_ids`** on every day, e.g.
  day 1: `"used_offer_ids":[139,136,167,127,267,258,231,252,46,81]`,
  day 2: `[149]`, day 3: `[234,233,90]` — real offers are used by the planner.

## 5b. Follow-up sync (added during the resume cycle)

After the initial deploy, source commit `1b641a5e` (2026-09-26 11:49, MC 1355.11 follow-up)
resolved the two open items below: it rewrote the source DEPLOY.md for the framtidsversion
and dropped the stale base.css comment in `templates/index.html`. Mirror doctrine (source
wins) required a sync: commits `ba05925` (sync; mistakenly placed index.html at the app
root) and `9eb23a4` (correction: index.html into templates/) were pushed. Post-fix
source-vs-mirror diff: clean. Live re-verified after a 300 s pull window — served
index.html carries the new comment line, all four live checks green again (fresh user
dacheck8607, user_id 3). Full adversarial re-check: `.audits/202609261043-1aa54978/T9-review.md`.

## 6. Verdict

All DoD lines met: pre-publish diff clean (no mirror-only live fixes lost), mirror
assembled from source with retired files removed and tests/.audits excluded, mirror
boot sanity green, hosting commit pushed with rollback sha stated, live verification
green on all four checks.

# VERDICT: PASS
