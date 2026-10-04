# [type:research] audit: inventory + goal-diff (matapp)

Du är GATE. Adversarial inventory + jämförelse för matapp (bryn1/hosting, apps/matapp/).

## Uppgift
1. FULL INVENTORY av apps/matapp/ vid HEAD (12e2e30, POC) och vid commit 4d220cd (framtidsversion):
   lista filer, moduler, routes (FastAPI), JS-vyer, CSS, templates, DB-tabeller, requirements-pins.
   Använd `git show 4d220cd --stat` och `git show 4d220cd:<path>` i /srv/workspace/hosting-klonen.
2. Jämför mot målet (PHASE0-map.md §1, /srv/workspace/svarkor-matapp/PHASE0-map.md):
   - auth (argon2id+session)
   - profil (3 butiker, kron-budget, måltider/vecka, personer)
   - 3 veckomenyförslag
   - majoritet extrapris (ratio >=50%, referenspris)
   - reklamblad per vald butik
3. Producera en DIFF-tabell: varje mål-del → status LIVE (på https://sibbamala.com/matapp/,
   verifiera med curl) / BYGGT-EJ-LIVE (finns i 4d220cd) / SAKNAS.
4. Notera vad som skiljer POC och framtidsversion i requirements.txt (httpx-pin) och server.py.

## DoD / Acceptance
DoD: Diff-tabellen komplett för alla 5 mål-delar, varje cell märkt VERIFIED (curl/git-bevis i evidence-filen) eller UNVERIFIED; evidence-fil med faktiska kommandoutdata och VERIFY_EXIT=0; rapport sparad som /srv/workspace/svarkor-matapp/audit/inventory-diff.md.
