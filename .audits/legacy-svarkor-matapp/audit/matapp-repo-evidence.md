# Matapp repo-populering — evidence (MC 1263.1, utförd av svarkor 2026-09-19)

Kort 1263.1 blockerades (3/3 NO_ARTIFACT: seat bernie saknar push-nyckel + färsk-klon-tester röda).
Svarkor tog över: fixade testbuggen, byggde repo-trädet, pushade, verifierade.

## Rotorsak till blockeringen
1. Första dispatchen dog på `mkdir: Permission denied` för /srv/workspace/Matapp (dir fanns ej vid dispatch).
2. Senare försök: seat bernie har INGEN forge-kredential (`git ls-remote` → exit 128 publickey) — per fleet-konvention håller svarkor push-nyckeln.
3. DoD-testet "fresh clone = 9 passed" kunde ALDRIG passera som skrivet: 2 tester (lockout, timing) använder `fresh_auth` utan `client`-fixture → default-engine pekar på `<repo>/.data/matapp.db` som inte finns i färsk träd → `sqlite3.OperationalError: unable to open database file`. Tidigare "9/9" var kört i källträd där .data/ redan fanns.

## Fix (commit 0a2c5d2 i hosting:matapp-fixround)
`database.make_engine()` skapar nu sqlite-filens föräldrakatalog (`os.makedirs(parent, exist_ok=True)`) för file-URL:er (inte :memory:).

## Verifiering (körda kommandon, denna session)
1. `git ls-remote git@github.com:bryn1/Matapp.git` →
   `43d1ecac70cdd801dc42905fdebfa18cd27b860f  refs/heads/main` (exit 0)
2. Färsk klon av bryn1/Matapp till /tmp + `pytest tests/` → **9 passed** (exit 0)
3. `git ls-tree -r --name-only HEAD` → 54 filer; junk-check: ingen `__pycache__`, ingen `.data/`, ingen `.pytest_cache`, ingen `--help/` i GIT-TRÄDET (.data/ skapas av testkörning, täckt av .gitignore)
4. README.md i repo-rot, svensk text (kvällsmats-planerare, kör-lokalt, tester, statussektion)
5. Källträd (hosting:matapp-fixround) efter fix: 9 passed — ingen regression

## Artefakter
- GitHub: https://github.com/bryn1/Matapp (main = 43d1eca)
- Fix-commit i hosting: 0a2c5d2 (pushad till origin/matapp-fixround)
- Byggdir: /tmp/matapp-repo-build/Matapp

VERIFY_EXIT=0
