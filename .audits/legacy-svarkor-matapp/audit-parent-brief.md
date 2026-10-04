# matapp full audit — goal vs current state (owner order 2026-09-12)

## Goal (from PHASE0-map.md, /srv/workspace/svarkor-matapp/PHASE0-map.md)
Framtidsvisionen för matapp (Kvällsmats):
1. Inloggning (auth, argon2id + session-cookie)
2. Användarprofil: 3 butiker, kron-budget, måltider/vecka, personer — sparad per användare
3. Motorn ger 3 veckomenyförslag (inte 1)
4. Majoritet extrapris-ingredienser (ratio-tröskel >=50%, referenspris i offert-schema)
5. Valda butikers reklamblad (willys/ica/coop feeds)

## Current state (VERIFIED this session)
- Repo: bryn1/hosting, apps/matapp/ (ingen separat matapp-repo).
- LIVE på https://sibbamala.com/matapp/ = POC (revert 12e2e30): butiksväljare + deterministisk
  veckomeny. /health ok, / 200, /api/stores 200, /api/menu 422 utan params, /api/auth/* 404,
  /api/profile 404.
- Framtidsversionen = commit 4d220cd (auth + profil + 3-förslag, 37 filer) — publicerad 2026-09-09,
  gav 502 på vm106 inom ~4 min, revertad samma dag. Orsaken till 502: INTE fastställd ännu.
- Root-route-fix (e1ddcaf) är committad och live (tidigare lead om "ej committad" är STALE).

## Audit scope
- Inventory: fil-/modul-inventering av apps/matapp vid HEAD (POC) och vid 4d220cd (framtidsversion).
- Comparison: goal (ovan) vs båda tillstånden → diff-rapport: vad som är LIVE, vad som är BYGGT-EJ-LIVE,
  vad som SAKNAS helt.
- Vision + click-through: varje klick och länk på live-sajten, console-fel, render.
- Korrekthet: testsviten körd på POC-källan.
- Säkerhet: POC saknar auth med design (open-login); kolla injection, secrets, demo_guard, fel-läckage.

## Out of scope
- vm106-reconciler-armning (MC 2344 — separat, orkestrator-gated).
- Att fixa 502-orsaken (audit rapporterar, fix filas separat efter owner-beslut).

## DoD / Acceptance
Synthesiserad diff-rapport (goal vs live vs built-not-live) med severity-rankade fynd,
varje påstående märkt VERIFIED/UNVERIFIED, levererad till owner i chatten + sparad som fil.
