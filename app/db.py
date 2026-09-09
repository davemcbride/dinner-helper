import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "dinners.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    aliases     TEXT NOT NULL DEFAULT '[]',   -- JSON list of known spellings
    enabled     INTEGER NOT NULL DEFAULT 1,
    flagged     INTEGER NOT NULL DEFAULT 0,
    flag_reasons TEXT NOT NULL DEFAULT '[]',  -- JSON list of why it was flagged
    merge_hint  TEXT NOT NULL DEFAULT 'null'  -- JSON {"into": id, "into_name": str, "confidence": float}
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_id    INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    served_on  TEXT NOT NULL,                 -- ISO date (YYYY-MM-DD)
    note       TEXT NOT NULL DEFAULT ''       -- 'seed' for imported entries
);
CREATE INDEX IF NOT EXISTS idx_history_meal ON history(meal_id);
CREATE INDEX IF NOT EXISTS idx_history_on  ON history(served_on);

CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_id    INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
    planned_on TEXT NOT NULL,                 -- ISO date (YYYY-MM-DD)
    note       TEXT NOT NULL DEFAULT ''       -- for future 'eat out' / skip markers
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_on  ON plans(planned_on);
CREATE INDEX IF NOT EXISTS idx_plans_meal ON plans(meal_id);
"""


def get_conn(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Open (creating schema) a connection to the app database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add any columns introduced after the very first schema version."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(meals)")}
    if "merge_hint" not in cols:
        conn.execute("ALTER TABLE meals ADD COLUMN merge_hint TEXT NOT NULL DEFAULT 'null'")
        conn.commit()


def dict_rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


def fetch_one(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def fetch_all(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return dict_rows(conn.execute(sql, params).fetchall())