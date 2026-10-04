# matapp — deploy & operations (MC 573.2)

Public URL: https://sibbamala.com/matapp/
Manifest:  apps.yaml -> {name: matapp, type: service, root: apps/matapp,
           exec: server.py, port: 8141}
Unit:      vm106-app-matapp.service (renderer-generated from the manifest)
State:     $STATE_DIRECTORY = /var/lib/vm106-app-matapp (systemd StateDirectory)
           -> .data/kvallsmat.db lives there, never in this read-only repo tree.

## How it runs
- server.py is a $STATE_DIRECTORY-aware uvicorn entrypoint (same idiom as apps/hotell):
  * KVALLMATS_REPO=<app root>  -> the vendored motor src/ (self-contained) is the
    single motor source; no external /srv path is needed.
  * KVALLSMATS_DB_URL=sqlite:///$STATE_DIRECTORY/.data/kvallsmat.db -> sqlite is
    rebased under the writable state dir so boot (schema + idempotent 18-recipe
    seed) survives the renderer's read-only unit.
  * The state dir is created before app.main is imported.
- requirements.txt holds exact pins (fastapi 0.141.1, uvicorn 0.52.4,
  sqlalchemy 2.0.52, pydantic 2.13.4) verified against the assembled stack.

## Deploy
1. Commit apps/matapp + the apps.yaml entry, push to github.com/bryn1/hosting
   (origin). vm106's pull timer (<=5 min) applies it.
2. Port 8141 is a service port in range 8100-8199, unused by any other app here.

## Verify (after the <=5 min pull window)
  curl -s https://sibbamala.com/matapp/health    -> 200 {"status":"ok",...,
                                                   "motor_resolved":true}
  curl -s https://sibbamala.com/matapp/api/stores -> 200 [willys, ica, coop]
(cf-cache-status: DYNAMIC confirms live origin, not an edge cache artifact.)

## Rollback
Revert/remove the matapp entry + apps/matapp dir, push -> renderer drops the unit
and nginx proxy on the next pull.
