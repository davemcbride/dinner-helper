"""Seed the dinners database from data/clean_meals.json.

Idempotent: refuses to touch a non-empty meals table unless --fresh is given.

Because the Keep list records *how often* a meal was written down, we use
those occurrence counts to backfill history so the app has meaningful stats,
favourites and "never had" signals on day one.  These rows are marked
`note = 'seed'` so they can be identified (and dropped) later.

Usage:
    python3 -m app.seed --fresh
"""

import argparse
import json
import random
from datetime import date, timedelta

from . import db
from .db import DB_PATH, get_conn

ROOT = db.ROOT
SEED_FILE = ROOT / "data" / "clean_meals.json"
SEED_DAYS = 365 * 2  # spread seed history over the last two years


def assign_seed_dates(meal_id: int, occurrences: int, rng: random.Random) -> list[str]:
    """Deterministic spread of `occurrences` ISO dates over the last two years."""
    if occurrences <= 0:
        return []
    today = date.today()
    days = list(range(0, SEED_DAYS, max(1, SEED_DAYS // (occurrences + 1))))
    selected = rng.sample(days, min(occurrences, len(days)))
    return [(today - timedelta(days=d)).isoformat() for d in sorted(selected)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="wipe and rebuild the DB")
    args = ap.parse_args()

    conn = get_conn()
    if args.fresh:
        with conn:
            conn.execute("DELETE FROM history")
            conn.execute("DELETE FROM meals")
    else:
        (n,) = conn.execute("SELECT COUNT(*) FROM meals").fetchone()
        if n:
            raise SystemExit(
                f"meals table already has {n} rows. Re-run with --fresh to rebuild."
            )

    if not SEED_FILE.exists():
        raise SystemExit(f"missing {SEED_FILE}; run scripts/clean_list.py first")

    payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    rng = random.Random("dinner-helper-seed-v1")

    # deterministic per-meal seed so re-runs don't reshuffle
    with conn:
        for m in payload["meals"]:
            cur = conn.execute(
                "INSERT INTO meals (id, name, aliases, enabled, flagged, flag_reasons, merge_hint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, aliases=excluded.aliases, enabled=excluded.enabled, "
                "flagged=excluded.flagged, flag_reasons=excluded.flag_reasons, "
                "merge_hint=excluded.merge_hint",
                (
                    m["id"],
                    m["name"],
                    json.dumps(m["aliases"]),
                    1 if m["enabled"] else 0,
                    1 if m["flagged"] else 0,
                    json.dumps(m["flag_reasons"]),
                    json.dumps(m.get("merge_hint")),
                ),
            )
            meal_id = cur.lastrowid
            for served_on in assign_seed_dates(
                meal_id, m["occurrences"], random.Random(f"{meal_id}-{m['name']}")
            ):
                conn.execute(
                    "INSERT INTO history (meal_id, served_on, note) VALUES (?, ?, 'seed')",
                    (meal_id, served_on),
                )

    (n_meals,) = conn.execute("SELECT COUNT(*) FROM meals").fetchone()
    (n_hist,) = conn.execute("SELECT COUNT(*) FROM history").fetchone()
    (n_seed,) = conn.execute("SELECT COUNT(*) FROM history WHERE note='seed'").fetchone()
    conn.close()
    print(f"DB seeded at {DB_PATH}")
    print(f"  meals:    {n_meals}")
    print(f"  history:  {n_hist}  ({n_seed} backfilled from the Keep list)")


if __name__ == "__main__":
    main()