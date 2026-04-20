# Matapp — Architecture

> Read this before any matapp work.
> Last updated: 2026-04-20

---

## Purpose

Swedish family meal planner. Generates weekly recipe suggestions based on Willys grocery store offers, manages a recipe catalog, shopping list, and pantry/staples. Per-user encrypted data storage.

**Household:** 2 adults, 2 kids (ages 3 and 6). Diet: vegetarian, chicken, fish.

---

## System Overview

```
Monday 07:00 cron
    │
    └── run.py ──► willys.py ──► Willys/Tjek API (weekly offers)
                │
                ├──► recipe_catalog (SQLite) — ingredient match
                ├──► search.py — fallback web scrape (ICA, Arla, tasteline...)
                └──► recipes.py ──► Ollama (mistral:7b) — generate recipes
                                └──► shopping.py ──► weekly_shopping (SQLite)

Browser
    │
    └── server.py (Flask, port 8765) ──► SQLite (matapp.db)
                                     ──► Cloudflare tunnel (mat.sibbamala.com)
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web server | Flask (port 8765) |
| Database | SQLite3 WAL mode |
| AI | Ollama (mistral:7b-instruct-q4_K_M) |
| Deployment | systemd user service + cron |
| Tunnel | Cloudflare (mat.sibbamala.com) |
| Notifications | ntfy |

---

## Database Schema (key tables)

| Table | Purpose |
|-------|---------|
| `recipe_history` | All generated recipes with per-person ratings (kid1, kid2, adult, 1–7 scale) |
| `recipe_catalog` | 1000+ scraped recipes from Swedish cooking sites |
| `weekly_shopping` | Current week shopping list (checked/unchecked, sale items) |
| `shopping_memory` | Purchase history for smart suggestions |
| `staples` | Pantry items with reorder intervals |
| `price_history` | Willys price tracking per product |
| `price_watchlist` | Monitored products |

---

## Key Files

| File | Role |
|------|------|
| `run.py` | Weekly pipeline: offers → match → generate → shopping list |
| `server.py` | Flask web server, auth, API routes |
| `db.py` | All SQLite operations (~1100 lines, 120 functions) |
| `willys.py` | Fetch weekly Willys offers (Tjek/Squid API) |
| `recipes.py` | Ollama recipe generation |
| `scrape.py` | Monthly bulk recipe catalog scraper |
| `search.py` | Fallback recipe web search |
| `shopping.py` | Build + deduplicate shopping list |
| `config.json` | All configuration (store, household, diet, model) |

---

## Configuration (config.json)

```json
{
  "willys_store": "Karlskrona Slottsbacken",
  "household": {"adults": 2, "kids": [3, 6]},
  "diet": ["vegetarian", "chicken", "fish"],
  "ollama_model": "mistral:7b-instruct-q4_K_M",
  "serve_port": 8765,
  "pantry_items": [...],
  "staples": [...],
  "order_agent": {"ntfy_url": "..."}
}
```

---

## Authentication Model

- Bcrypt password hashing
- PBKDF2 key derivation (AES-256-GCM per-user encryption)
- **Server-side session dict** — sessions invalidated on restart (ephemeral)
- Server cannot decrypt other users' data
- **Weakness:** in-memory sessions lost on restart/crash

---

## Deployment

```bash
# systemd user service
systemctl --user status matapp

# Cron jobs
# Weekly:  0 7 * * 1 → run.py
# Monthly: 0 3 1 * * → scrape.py

# Logs
tail -f ~/ai-sandbox/matapp/logs/server.log
tail -f ~/ai-sandbox/matapp/logs/run.log
```

---

## Devil's Advocate — Known Issues & Improvements

| Issue | Severity | Notes |
|-------|----------|-------|
| In-memory sessions lost on restart | MEDIUM | Fixed 2026-04-06: persistent secret key in `matapp.secret` + signed cookie (itsdangerous) embeds enc_key — sessions survive restart |
| Recipe deduplication by name only | MEDIUM | "pasta" vs "pappardelle" treated as different. Use ingredient-overlap similarity instead |
| Ollama model is mistral:7b | LOW | Weak at nuanced dietary reasoning. Upgrade to Qwen2.5-7B on VM200 via router for better quality |
| Web scrapers are fragile | HIGH | ICA/Arla/tasteline site structure changes without warning. Add scrape health check that alerts if catalog drops below threshold |
| No allergy/dislike tracking | MEDIUM | Household config has diet types but no per-person dislikes. Kid aged 3 likely has restrictions not modelled |
| Shopping deduplication may miss variants | MEDIUM | "mjölk" vs "oat milk" not recognized as alternatives. Add ingredient normalization dictionary |
| No multi-household support | LOW | Single config.json — fine for now but limits reuse |
| Price watchlist never acted on | LOW | `price_watchlist` table exists but no alert fires when watched item goes on sale |
| Catalog refresh overwrites ratings | LOW | Monthly scrape updates `recipe_catalog` — verify it doesn't clear user-submitted ratings |
| Willys API (Tjek/Squid) unofficial | HIGH | Could change or add auth at any time. Add fallback (hardcoded weekly staples list) |

---

## Recipe rotation (fixed 2026-04-20 — GT-24i)

`get_catalog_candidates` in `db.py` previously scored candidates by
`match_count*10 + rating - times_used*0.5` with deterministic sort — same
inputs produced the same ordering every week. Effective behavior: only
44/3858 catalog recipes ever used; same top 3 repeated 2+ weeks running.

**Fix (commit 5248d30 on `matapp-phase1-fixes`):**

- Parse `last_used` (ISO date) → recency_penalty (linear decay over 60 days)
- Add `random.uniform(0, 0.5)` jitter for tie-breaking
- New formula: `match_count*10 + rating - times_used*0.5 - recency_penalty*3.0 + jitter`

**Behavior:** recipes used within the last 60 days are demoted; the penalty
strength (max -3.0) exceeds the rating range (~1.5) but stays below the
match_count weight (10/term) so offer-match still dominates. Jitter
reshuffles same-scored recipes across back-to-back calls.

**5-week simulation** (isolated DB copy, `exclude_names` tracks last 2 weeks,
`mark_catalog_used` after commit): weeks 1-3 produce fully disjoint sets;
week 4 may reuse week-1 picks once their recency_penalty has decayed; week 5
diverges again. Acceptable for user intent ("don't repeat every week;
coming back after several weeks is OK").

**2026-04-20 PM follow-up (commit f9d863d):** the recency penalty depended on
\`db.mark_catalog_used()\` being called, but only \`run.py\` did so — never the web
endpoints. Result: web-generated plans never set \`last_used\`, so the penalty
stayed 0 and the same recipes stayed on top. Fixed in
\`/api/plan/next-week\`, \`/api/plan/swap-recipe\`, \`/api/plan/generate\` (server.py)
— each now marks the catalog rows it actually picked. Self-corrects within
1-2 generations.

**Not yet exhaustive:** jitter scale may need tuning if users still see stuck
winners. Next refinement (if needed): boost jitter to 1.0, or add weak jitter
inside `select_recipes._take` too.

---

## Phase 1 status (complete 2026-04-20)

All 7 user-reported bugs fixed on `matapp-phase1-fixes`:

| # | Bead | Commit | What |
|---|------|--------|------|
| 1a | GT-h4w | `b8bc0ed` | Sale detection: whole-word regex, `is_real_sale` flag |
| 1b | GT-x6e | `80364ce` | Ingredient canonicalisation + pack→grams fallback |
| 1c | GT-7l0 | `9c7f064` | Diet hard-AND filter; chicken/fish counts from `allowed_diets` |
| 1d | GT-myj | `7caaab2` | Category guess: longest keyword wins |
| 1e | GT-xqs | `6169546` | `/api/shopping/add`: normalise + categorise + dedupe |
| 1f | GT-9sv | (committed) | New `pantry` table + `/api/pantry/{toggle,use}` endpoints |
| 1g | GT-6q4 | `1d4fa07`+`88cf001` | Habit learning: `avg_interval_days`, `get_due_habitual_items`, run.py wires it into shopping_list |

Plus rotation fix `GT-24i` at `5248d30`.

**Branch:** `matapp-phase1-fixes` (origin set to `git@github.com-matapp:bryn1/matapp.git`). Not yet merged to `main`. Suggest opening a PR or fast-forwarding `main` once user has lived with the fixes for a week.
