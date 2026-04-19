# Matapp

Swedish family meal planner for mat.sibbamala.com. Generates 5 weekly recipes based on Willys offers, manages a personal recipe catalog, and serves an interactive shopping list.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally with `mistral:7b` pulled
- Cloudflare tunnel pointing to `localhost:8765`

## Install dependencies

```bash
cd /home/lektove/ai-sandbox/matapp
pip install flask requests beautifulsoup4 lxml
```

## First-time setup

### 1. Initialize the database

```bash
python3 -c "import db; db.init_db()"
```

This creates `data/matapp.db` with all tables including `recipe_catalog`.

### 2. Pull the Ollama model

```bash
ollama pull mistral:7b
```

### 3. Seed the recipe catalog (optional but recommended)

Scrapes Swedish recipe sites (tasteline, ica, arla, receptfavoriter, koket) for vegetarian, chicken, and fish recipes rated ≥ 3.5:

```bash
python3 scrape.py --dry-run     # Preview without saving
python3 scrape.py --limit 50   # Save first 50 found
python3 scrape.py               # Full seed (no limit)
```

By category:
```bash
python3 scrape.py --category vegetarian
python3 scrape.py --category chicken
python3 scrape.py --category fisk
```

## Running the weekly pipeline

Fetches Willys offers → picks recipes from catalog (falls back to web scraping) → generates 5 recipes via Ollama → saves to DB:

```bash
python3 run.py
```

Options:
```
--force       Re-run even if this week already exists in the DB
--no-search   Skip web scraping fallback (catalog only)
```

## Starting the web server

```bash
python3 server.py
```

Serves on `http://localhost:8765`. Access via Cloudflare tunnel at `mat.sibbamala.com`.

With debug mode:
```bash
python3 server.py --debug
```

## Cron setup

Add to crontab (`crontab -e`):

```cron
# Generate weekly recipes every Monday at 07:00
0 7 * * 1 cd /home/lektove/ai-sandbox/matapp && python3 run.py >> logs/run.log 2>&1

# Refresh recipe catalog on the 1st of every month at 03:00
0 3 1 * * cd /home/lektove/ai-sandbox/matapp && python3 scrape.py >> logs/scrape.log 2>&1
```

## Systemd service (for the Flask server)

Create `/etc/systemd/system/matapp.service` (or `~/.config/systemd/user/matapp.service` for user-level):

```ini
[Unit]
Description=Matapp Flask server
After=network.target

[Service]
WorkingDirectory=/home/lektove/ai-sandbox/matapp
ExecStart=/usr/bin/python3 /home/lektove/ai-sandbox/matapp/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Enable and start:
```bash
systemctl --user enable matapp
systemctl --user start matapp
systemctl --user restart matapp   # after code changes
```

## File overview

| File | Purpose |
|------|---------|
| `run.py` | Weekly pipeline (Willys → catalog → Ollama → DB) |
| `server.py` | Flask web server (port 8765) |
| `scrape.py` | Bulk recipe catalog builder |
| `db.py` | SQLite layer — recipes, catalog, shopping list, staples |
| `recipes.py` | Ollama recipe generation |
| `search.py` | Fallback web scraper for live recipe search |
| `willys.py` | Fetches Willys weekly offers via Tjek/Squid API |
| `shopping.py` | Builds and deduplicates shopping list from recipes |
| `config.json` | App config (store, model, diet, catalog settings) |
| `data/matapp.db` | SQLite database (created automatically) |
| `logs/` | Run and server logs |

## Key config options (`config.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `ollama_model` | `mistral:7b` | Ollama model to use |
| `ollama_url` | `http://localhost:11434` | Ollama server URL |
| `diet` | `["vegetarian","chicken","fish"]` | Allowed diet categories |
| `catalog_min_matches` | `10` | Min catalog hits before falling back to web scraping |
| `min_catalog_rating` | `3.5` | Minimum site rating for catalog recipes |
| `serve_port` | `8765` | Flask server port |
| `willys_store` | `Slottsbacken` | Willys store name |

## Recipe rating

Recipes are rated on a 1–7 household scale in the web UI:
- 1–2: disliked (avoided in future weeks)
- 3–4: neutral
- 5–6: liked (can be revisited)
- 7: favourite

## Searching the catalog

Use the "Sök efter recept" section at the bottom of the page. Enter ingredients you have at home (comma-separated) to find matching catalog recipes with links to the original sites.

## API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Main page |
| GET | `/api/state` | Full state as JSON |
| POST | `/api/recipe/rate` | Rate a recipe `{recipe_id, rating: 1-7}` |
| GET | `/api/recipes/search?q=...` | Search catalog by ingredients |
| POST | `/api/shopping/toggle` | Check/uncheck a shopping item |
| POST | `/api/shopping/add` | Add item to shopping list |
| POST | `/api/shopping/remove` | Remove item from shopping list |
| POST | `/api/staples/bought` | Mark a staple as purchased |
| GET | `/api/staples/suggestions` | Get staple restock suggestions |
