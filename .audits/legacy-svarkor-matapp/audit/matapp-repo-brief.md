# [seat:teddy] [type:code] matapp: populera bryn1/Matapp — framtidsversion + fixar + tests

Ägaren har skapat ett NYTT, TOMT publikt repo: https://github.com/bryn1/Matapp
(default branch main, skapat 2026-09-16, 0 refs). Uppgiften är att ge det sitt första
innehåll: matapp framtidsversionen MED fixrunda-2-fixarna och testsviten.

## Källa
Lokal klon: /srv/workspace/hosting, gren `matapp-fixround` (3 commits ovanpå 4d220cd):
- 8e4aa9b F1 fix — MATAPP_DB_URL env-var (audit 1188.9 F1)
- 57c5bd2 F2 login rate-limit/lockout + F3 timing-equalizer (audit 1188.9 F2/F3)
- 63818f9 make _mkuser idempotent (temp-DB reuse across tests)
Innehåll: apps/matapp/ (FastAPI + argon2id auth + profil + 3-förslags-meny + vendored
motor src/ + tests/ med 9 gröna tester).

## Uppgift
1. Klona /srv/workspace/hosting till ett rent arbetsdir (eller använd git worktree) på
   gren matapp-fixround.
2. Skapa nytt repo-innehåll: matapp-koden som repo-rot (dvs. innehållet i apps/matapp/
   blir roten i det nya repot — README.md, app/, src/, static/, templates/, tests/,
   requirements.txt, server.py, run_motor.py, DEPLOY.md). TA INTE med: --help/-katalogen
   (stray-artefakt, audit F12), __pycache__, .data/, .pytest_cache.
3. Skapa en README.md på svenska: vad matapp är (kvällsmats-planerare: profil med
   personer/måltider/budget, 3 veckomenyförslag, majoritet extrapris mot butiksfeeds),
   hur man kör lokalt (server.py, PORT/HOST/MATAPP_DB_URL env), testkommando
   (pytest tests/), och en kort statussektion (POC live på sibbamala.com/matapp,
   framtidsversion med auth/profil/3-förslag här, referenspris-ingest = öppen måldel).
4. Lägg till .gitignore (__pycache__, .data/, *.pyc, .pytest_cache, .venv).
5. Initiera nytt git-repo i arbetsdirt, branch main, commit (coherent: källkod+tests i
   en commit, README+gitignore i en annan eller samma — håll det rent), lägg till remote
   git@github.com:bryn1/Matapp.git och pusha main.
6. Verifiera: git ls-remote visar refs/heads/main; färsk pytest i den pushade koden
   (klona tillbaka till /tmp och kör) = 9/9 gröna.

## DoD / Acceptance
DoD: `git ls-remote git@github.com:bryn1/Matapp.git` visar refs/heads/main (exit 0,
utdata i evidence); färsk klon av bryn1/Matapp till /tmp + `pytest tests/` = 9 passed,
VERIFY_EXIT=0 i evidence; README.md finns i repots rot med svensk text; ingen --help/-
katalog, ingen .data/, ingen __pycache__ i det pushade trädet (git ls-tree-check i
evidence). Evidence sparad som /srv/workspace/svarkor-matapp/audit/matapp-repo-evidence.md.
