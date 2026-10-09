# Matapp

Kvällsmats-planerare: skapa en profil med personer, måltider och budget och få
tre veckomenyförslag per vecka. Majoritetsröstning av förslagen jämförs mot
butiksfeeds för extrapriser.

## Status

- POC live på sibbamala.com/matapp
- Denna repo innehåller framtidsversionen: auth (argon2id + session), profil
  med personer/måltider/budget, 3 veckomenyförslag, flödet "Välj detta förslag"
  → Handelslista (byggd från veckans meny) → receptlasning i dialog
  (MC 10349), fixrunda-2-fixarna (rate-limit/lockout, timing-equalizer,
  `MATAPP_DB_URL` env-var) och testsvit.
- Referenspris-ingest mot butiksfeeds = öppen måldel.

## Kör lokalt

```bash
pip install -r requirements.txt
python server.py          # PORT/HOST/MATAPP_DB_URL styrs via env
```

- `PORT` (default 8141), `HOST` (default 127.0.0.1)
- `MATAPP_DB_URL` — SQLAlchemy-URL, default `sqlite:///<repo>/.data/matapp.db`
  (katalogen skapas automatiskt vid behov)

## Tester

```bash
pytest tests/
```

Hela testsviten (167 tester) är grön i en färsk klon (databaskatalog skapas
automatiskt).
