# Dinner Helper

Mobile-first FastAPI + SQLite app for choosing and tracking dinners, shared
across two phones on a home LAN. No login by design.

## Development

Python 3.12, venv at `.venv/`. Deps (fastapi, uvicorn) in `requirements.txt`.
There is no test suite, no linter, no CI, and no frontend build step — don't
invent commands for them.

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
# HTTPS (needs mkcert certs in certs/): python -m app.main --ssl
```

## Data pipeline (order matters)

1. `python scripts/clean_list.py` — reads `docs/dinner_list.md` (never
   modifies it), normalizes and de-dupes into `data/clean_meals.json`.
   Meal IDs are assigned in sorted name order, so re-running after editing the
   list can reshuffle IDs.
2. `python -m app.seed` — builds `data/dinners.db` from `clean_meals.json`.
   Idempotent: exits if the `meals` table is non-empty unless `--fresh` is
   given (`--fresh` wipes and reseeds).
3. Run the server.

`data/*.db` and `certs/` are git-ignored. When `docs/dinner_list.md` changes,
re-run both steps. `--fresh` rebuilds from JSON only, discarding any merges or
edits made in the app.

## Architecture

- `app/main.py` — all FastAPI routes. Uses raw `sqlite3` (not SQLAlchemy);
  JSON columns (`aliases`, `flag_reasons`, `merge_hint`) are parsed by
  `to_meal()` and written with `json.dumps`.
- `app/db.py` — schema + `get_conn()`, which creates tables and runs an
  idempotent `_migrate()` for columns added later.
- `app/seed.py` — backfills history so stats are meaningful on day one; those
  rows are marked `note='seed'` and `POST /api/history/reset` deletes them.
- `static/` — vanilla JS/HTML/CSS mobile UI served by FastAPI, no build step.
- `docs/` — `dinner_list.md` is the raw source list; `features/` and `ideas/`
  hold planning notes.

## Conventions and gotchas

- `meals.name` is UNIQUE. PATCH returns 409 on a rename collision;
  POST `/api/meals` returns the existing meal instead of creating a duplicate.
- Merge (`POST /api/meals/{id}/merge`) folds history + aliases into the target
  and deletes the source; it cannot merge a meal into itself.
- `flagged` meals are excluded from `/api/pick` and normal listings. The
  Review tab uses `filter=flagged`; `merge_hint` JSON drives the duplicates
  filter.
- PATCH rename updates `name` only — aliases belong to a separate column.
- Serving surprises: no auth by design (LAN only), DB lives at
  `data/dinners.db` and must exist before the server is useful.