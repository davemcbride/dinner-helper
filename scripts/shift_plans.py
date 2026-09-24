#!/usr/bin/env python3
"""Shift every planned meal date by a whole number of days.

The Upcoming-week planner stores one row per planned date in the `plans`
table. If a week (or several) gets entered against the wrong dates, say a
week early, this moves every plan by the same offset so the whole schedule
lands on the intended days.

Dry-run by default: pass --apply to write. A timestamped snapshot of the
database is taken before any change.

Usage:
    python3 scripts/shift_plans.py -7           # preview shifting back a week
    python3 scripts/shift_plans.py -7 --apply   # perform it
"""

import argparse
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "dinners.db"


def shift_day(iso_day: str, days: int) -> str:
    y, m, d = (int(part) for part in iso_day.split("-"))
    return (date(y, m, d) + timedelta(days=days)).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shift all planned meals by N days (dry-run unless --apply)."
    )
    parser.add_argument("days", type=int,
                        help="signed number of days to shift every plan, e.g. -7")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default is a dry run)")
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help="database file to edit")
    args = parser.parse_args()

    if args.days == 0:
        parser.error("days must be non-zero")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT p.id, p.meal_id, p.planned_on, p.note, m.name "
            "FROM plans p JOIN meals m ON m.id = p.meal_id "
            "ORDER BY p.planned_on"
        ).fetchall()
        if not rows:
            print("No plans to shift.")
            return

        moved = [
            (r["id"], r["meal_id"], shift_day(r["planned_on"], args.days),
             r["note"], r["planned_on"], r["name"])
            for r in rows
        ]

        for _, _, dst, _, src, name in moved:
            print(f"  {src} -> {dst}  {name}")
        print(f"\n{len(moved)} plan(s) would shift by {args.days:+d} day(s).")

        if not args.apply:
            print("Dry run. Re-run with --apply to write.")
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = Path(args.db).with_name(f"{Path(args.db).stem}-pre-shift-{stamp}.db")
        with sqlite3.connect(backup) as bck:
            conn.backup(bck)

        # Replace the whole table in one transaction so the unique
        # planned_on index can never trip on a half-moved set.
        with conn:
            conn.execute("DELETE FROM plans")
            conn.executemany(
                "INSERT INTO plans (id, meal_id, planned_on, note) VALUES (?, ?, ?, ?)",
                [(pid, meal_id, dst, note) for pid, meal_id, dst, note, _, _ in moved],
            )

        print(f"Backed up to {backup}")
        print(f"Shifted {len(moved)} plan(s) by {args.days:+d} day(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
