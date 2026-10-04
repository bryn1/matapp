# [seat:teddy] [type:code] matapp fixrunda: F1 + BUG-1/2 + F2/F3 (på 4d220cd-basis)

Du fixar de audit-fynd som blockerar nästa publiceringsförsök av matapp framtidsversion.
Repo: /srv/workspace/hosting (clone av bryn1/hosting). HEAD = 12e2e30 (revert av
framtidsversionen). Framtidsversionens innehåll finns på commit 4d220cd.

## Bakgrund (audit MC 1188, rapporter i /srv/workspace/svarkor-matapp/audit/)
- security.md: F1 (HIGH) env-var-mismatch; F2 (HIGH) ingen rate-limit på login;
  F3 (MEDIUM) username-enumeration via timing.
- correctness.md: BUG-1 (week 9999-W99/0000-W01 → OverflowError → 500);
  BUG-2 (icke-existerande veckonummer W54/W00 accepteras tyst → fel datum).

## Uppgift — arbeta på en gren baserad på 4d220cd:s apps/matapp-innehåll
1. Skapa gren `matapp-fixround` från 4d220cd. Återställ apps/matapp från 4d220cd på grenen
   (t.ex. via git checkout 4d220cd -- apps/matapp eller motsvarande) så fixarna landar på
   framtidsversionen, inte POC:n.
2. **F1:** server.py sätter KVALLSMATS_DB_URL men database.py/app/db.py läser MATAPP_DB_URL.
   Ena variabelnamnet (välj MATAPP_DB_URL konsekvent, uppdatera DEPLOY.md om den nämner
   det gamla namnet). Bevisa med test att state-dir-override fungerar.
3. **BUG-1 + BUG-2:** validera week-key så att (a) ogiltiga datum-intervall ger 422 med
   begripligt meddelande, (b) W00/W54-liknande icke-existerande veckor avvisas istället för
   att tyst extrapoleras. ISO-vecka-regel: vecka 1 = veckan med årets första torsdag.
4. **F2:** rate-limit/lockout på POST /api/auth/login — t.ex. per-username+IP räknare,
   lockout efter N fel på T minuter (in-memory räcker, samma scope som SessionStore).
5. **F3:** jämna timing-oraclet — kör argon2-verify (dummy-hash) även för obefintliga
   användarnamn så att svarstiden är konstant.
6. Kör POC-testsviten (68 tester) + nya tester för varje fix. Allt grönt.

## DoD / Acceptance
DoD: gren matapp-fixround i /srv/workspace/hosting med fixarna för F1, BUG-1, BUG-2, F2, F3;
varje fix har minst ett nytt test som misslyckas före och passerar efter; full svit grön
(färsk körning, utdata i evidence); DEPLOY.md uppdaterad om env-var-namn ändrats; evidence
sparad som /srv/workspace/svarkor-matapp/audit/fixround-evidence.md med VERIFY_EXIT=0.
