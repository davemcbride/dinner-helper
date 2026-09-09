"""Dinner Helper - FastAPI backend.

Run (from the repo root):
    python3 -m app.main --host 0.0.0.0 --port 8000 --ssl
or simply:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 \
        --ssl-keyfile certs/dinner.dave.lan+2-key.pem \
        --ssl-certfile certs/dinner.dave.lan-chain.pem
"""

import argparse
import json
import sqlite3
import random
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .db import get_conn

ROOT = db.ROOT
STATIC_DIR = ROOT / "static"

app = FastAPI(title="Dinner Helper", version="0.1.0")


# --- pydantic bodies -------------------------------------------------------

class LogIn(BaseModel):
    served_on: str | None = None   # ISO date, defaults to today
    note: str = ""


class MergeIn(BaseModel):
    into: int


class ResolveIn(BaseModel):
    action: str                    # "keep" | "delete"


class MealIn(BaseModel):
    name: str


class PatchMealIn(BaseModel):
    name: str | None = None
    enabled: bool | None = None


class PlanIn(BaseModel):
    meal_id: int


# --- helpers ---------------------------------------------------------------

MEAL_FIELDS = """
m.id, m.name, m.aliases, m.enabled, m.flagged, m.flag_reasons, m.merge_hint,
(SELECT COUNT(*) FROM history h WHERE h.meal_id = m.id) AS count,
(SELECT MAX(h.served_on) FROM history h WHERE h.meal_id = m.id) AS last_served
"""


MEAL_KEYS = (
    "id", "name", "aliases", "enabled", "flagged", "flag_reasons",
    "merge_hint", "count", "last_served",
)


def to_meal(row: sqlite3.Row) -> dict:
    meal = {k: row[k] for k in MEAL_KEYS}
    meal["aliases"] = json.loads(meal["aliases"] or "[]")
    meal["flag_reasons"] = json.loads(meal["flag_reasons"] or "[]")
    hint = json.loads(meal["merge_hint"]) if meal["merge_hint"] else None
    meal["merge_hint"] = hint
    return meal


def get_meal(conn, meal_id: int) -> dict:
    row = conn.execute(
        f"SELECT {MEAL_FIELDS} FROM meals m WHERE m.id = ?", (meal_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="meal not found")
    return to_meal(row)


# --- meal listing / stats ---------------------------------------------------


@app.get("/api/meals")
def list_meals(
    q: str = "",
    filter: str = Query("all", pattern="^(all|never|rare|favourite|flagged|duplicates)$"),
    with_flagged: bool = False,
):
    """List meals with live stats.

    filter:
      all         every enabled meal
      never       meals cooked 0 times
      rare        meals cooked 0-1 times
      favourite   meals cooked at least once, ordered by count desc
      flagged     meals awaiting review
      duplicates  enabled meals with a suggested merge target (nearest match first)
    with_flagged: include flagged meals in non-flagged filters (for the Review tab)
    """
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        where = ["m.enabled = 1"]
        params: list = []
        if filter == "flagged":
            where = []
        if not with_flagged and filter != "flagged":
            where.append("m.flagged = 0")
        if q:
            where.append("(m.name LIKE ? OR m.aliases LIKE ?)")
            like = f"%{q}%"
            params += [like, like]

        sql_where = " AND ".join(where) if where else "1=1"
        base = (
            f"SELECT {MEAL_FIELDS}, "
            "(SELECT COUNT(*) FROM history h WHERE h.meal_id = m.id) AS mcount, "
            "m.flagged AS mflag "
            f"FROM meals m WHERE {sql_where}"
        )

        filter_where = ""
        order = "name COLLATE NOCASE"
        if filter == "flagged":
            filter_where = "mflag = 1"
        elif filter == "never":
            filter_where = "mcount = 0"
        elif filter == "rare":
            filter_where = "mcount <= 1"
        elif filter == "favourite":
            filter_where = "mcount >= 1"
            order = "mcount DESC, name COLLATE NOCASE"
        elif filter == "duplicates":
            filter_where = "mflag = 0 AND merge_hint IS NOT NULL AND merge_hint != 'null'"
            order = (
                "CAST(json_extract(merge_hint, '$.confidence') AS REAL) DESC, "
                "name COLLATE NOCASE"
            )

        if filter == "all" and not with_flagged:
            sql = f"{base} ORDER BY m.name COLLATE NOCASE"
        else:
            sql = f"SELECT * FROM ({base}) WHERE {filter_where} ORDER BY {order}"

        rows = conn.execute(sql, params).fetchall()
        return {"meals": [to_meal(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/meals/{meal_id}")
def meal(meal_id: int):
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return get_meal(conn, meal_id)
    finally:
        conn.close()


@app.post("/api/meals")
def create_meal(body: MealIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name required")
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id FROM meals WHERE name = ?", (name,)).fetchone()
        if row:
            return get_meal(conn, row["id"])
        cur = conn.execute("INSERT INTO meals (name) VALUES (?)", (name,))
        conn.commit()
        return get_meal(conn, cur.lastrowid)
    finally:
        conn.close()


@app.patch("/api/meals/{meal_id}")
def patch_meal(meal_id: int, body: PatchMealIn):
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        get_meal(conn, meal_id)
        fields, params = [], []
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=422, detail="name required")
            ex = conn.execute(
                "SELECT id FROM meals WHERE name = ? AND id != ?", (name, meal_id)
            ).fetchone()
            if ex:
                raise HTTPException(status_code=409, detail="meal name already exists")
            fields.append("name = ?")
            params.append(name)
        if body.enabled is not None:
            fields.append("enabled = ?")
            params.append(1 if body.enabled else 0)
        if fields:
            params.append(meal_id)
            conn.execute(
                f"UPDATE meals SET {', '.join(fields)} WHERE id = ?", params
            )
            conn.commit()
        return get_meal(conn, meal_id)
    finally:
        conn.close()


@app.delete("/api/meals/{meal_id}")
def delete_meal(meal_id: int):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        n = conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        raise HTTPException(status_code=404, detail="meal not found")
    return {"ok": True}


# --- picking ----------------------------------------------------------------


def _pick_weight(count: int, mode: str) -> float:
    if mode == "random":
        return 1.0
    if mode == "favourite":
        return 1.0 + count
    # default: favour never-had and rare meals
    if count == 0:
        return 6.0
    if count == 1:
        return 3.5
    if count <= 3:
        return 2.0
    return 1.0 / max(1, count)


@app.get("/api/pick")
def pick(
    mode: str = Query("rare", pattern="^(rare|random|favourite)$"),
    count: int = Query(1, ge=1, le=5),
    exclude: str = "",
):
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        excluded = {int(x) for x in exclude.split(",") if x.strip().isdigit()}
        rows = conn.execute(
            f"SELECT {MEAL_FIELDS} FROM meals m "
            "WHERE m.enabled = 1 AND m.flagged = 0"
        ).fetchall()
        candidates = [to_meal(r) for r in rows if r["id"] not in excluded]
        if not candidates:
            raise HTTPException(status_code=404, detail="no meals to choose from")
        weights = [_pick_weight(m["count"], mode) for m in candidates]
        picks = random.choices(candidates, weights=weights, k=min(count, len(candidates)))
        return {"picks": picks}
    finally:
        conn.close()


# --- logging / review / merge -----------------------------------------------


@app.post("/api/meals/{meal_id}/log")
def log_meal(meal_id: int, body: LogIn):
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        get_meal(conn, meal_id)
        served_on = body.served_on or date.today().isoformat()
        cur = conn.execute(
            "INSERT INTO history (meal_id, served_on, note) VALUES (?, ?, ?)",
            (meal_id, served_on, body.note),
        )
        conn.commit()
        item = {
            "id": cur.lastrowid,
            "meal_id": meal_id,
            "served_on": served_on,
            "note": body.note,
        }
        return {"logged": item, "meal": get_meal(conn, meal_id)}
    finally:
        conn.close()


@app.get("/api/history/recent")
def recent_history(limit: int = Query(20, ge=1, le=100)):
    """Most recent logged meals (newest first) including backfilled seed rows."""
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT h.id, h.meal_id, h.served_on, h.note, m.name AS meal_name "
            "FROM history h JOIN meals m ON m.id = h.meal_id "
            "ORDER BY h.served_on DESC, h.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {"entries": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.delete("/api/history/{hist_id}")
def undo_history(hist_id: int):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        cur = conn.execute("DELETE FROM history WHERE id = ?", (hist_id,))
        conn.commit()
    finally:
        conn.close()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="history entry not found")
    return {"ok": True}


@app.post("/api/history/reset")
def reset_seed_history():
    """Drop all backfilled (seed) history so counts start fresh from today."""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        n = conn.execute("DELETE FROM history WHERE note = 'seed'").rowcount
        conn.commit()
    finally:
        conn.close()
    return {"deleted": n}


@app.post("/api/meals/{meal_id}/merge")
def merge_meal(meal_id: int, body: MergeIn):
    """Fold meal_id into body.into: move history, fold aliases, delete source."""
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if meal_id == body.into:
            raise HTTPException(status_code=400, detail="cannot merge a meal into itself")
        src = get_meal(conn, meal_id)
        tgt = get_meal(conn, body.into)

        aliases = set(tgt["aliases"]) | set(src["aliases"]) | {src["name"]}
        if tgt["name"] in aliases:
            aliases.discard(tgt["name"])

        flagged = max(src["flagged"], tgt["flagged"])
        reasons = list(dict.fromkeys(src["flag_reasons"] + tgt["flag_reasons"]))

        with conn:
            conn.execute("UPDATE history SET meal_id = ? WHERE meal_id = ?", (body.into, meal_id))
            conn.execute(
                "UPDATE meals SET aliases = ?, flagged = ?, flag_reasons = ? WHERE id = ?",
                (json.dumps(sorted(aliases)), flagged, json.dumps(reasons), body.into),
            )
            conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        return {"merged": get_meal(conn, body.into)}
    finally:
        conn.close()


@app.post("/api/meals/{meal_id}/resolve")
def resolve_meal(meal_id: int, body: ResolveIn):
    """Review action: 'keep' unflags a meal, 'delete' removes it (and its history)."""
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        get_meal(conn, meal_id)
        action = body.action.strip().lower()
        if action == "keep":
            conn.execute(
                "UPDATE meals SET flagged = 0, flag_reasons = '[]' WHERE id = ?",
                (meal_id,),
            )
        elif action == "delete":
            conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        else:
            raise HTTPException(status_code=422, detail="action must be 'keep' or 'delete'")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# --- upcoming week ---------------------------------------------------------


def to_plan(row: sqlite3.Row) -> dict:
    plan = {
        "id": row["plan_id"],
        "planned_on": row["planned_on"],
        "note": row["note"],
        "meal": to_meal(row),
    }
    return plan


def get_plan(conn, planned_on: str) -> dict:
    row = conn.execute(
        "SELECT p.id AS plan_id, p.planned_on, p.note, "
        f"{MEAL_FIELDS} "
        "FROM plans p JOIN meals m ON m.id = p.meal_id "
        "WHERE p.planned_on = ?",
        (planned_on,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no plan for that date")
    return to_plan(row)


@app.get("/api/plans")
def list_plans(
    start: str = Query(..., description="ISO date, inclusive"),
    end: str = Query(..., description="ISO date, inclusive"),
):
    """Planned meals within [start, end] (inclusive)."""
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT p.id AS plan_id, p.planned_on, p.note, "
            f"{MEAL_FIELDS} "
            "FROM plans p JOIN meals m ON m.id = p.meal_id "
            "WHERE p.planned_on BETWEEN ? AND ? "
            "ORDER BY p.planned_on",
            (start, end),
        ).fetchall()
        return {"plans": [to_plan(r) for r in rows]}
    finally:
        conn.close()


@app.put("/api/plans/{planned_on}")
def put_plan(planned_on: str, body: PlanIn):
    """Assign a meal to a date (upsert)."""
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        get_meal(conn, body.meal_id)
        conn.execute(
            "INSERT INTO plans (meal_id, planned_on) VALUES (?, ?) "
            "ON CONFLICT(planned_on) DO UPDATE SET meal_id = excluded.meal_id, "
            "note = excluded.note",
            (body.meal_id, planned_on),
        )
        conn.commit()
        return get_plan(conn, planned_on)
    finally:
        conn.close()


@app.delete("/api/plans/{planned_on}")
def delete_plan(planned_on: str):
    """Clear the plan for a date."""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        n = conn.execute("DELETE FROM plans WHERE planned_on = ?", (planned_on,)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        raise HTTPException(status_code=404, detail="no plan for that date")
    return {"ok": True}


@app.post("/api/plans/{planned_on}/confirm")
def confirm_plan(planned_on: str):
    """Log a planned meal as eaten (only once the day has arrived) and clear it."""
    if planned_on > date.today().isoformat():
        raise HTTPException(status_code=400, detail="can't confirm a meal before its day")
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, meal_id, note FROM plans WHERE planned_on = ?",
            (planned_on,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no plan for that date")
        with conn:
            cur = conn.execute(
                "INSERT INTO history (meal_id, served_on, note) VALUES (?, ?, ?)",
                (row["meal_id"], planned_on, row["note"] or ""),
            )
            conn.execute("DELETE FROM plans WHERE id = ?", (row["id"],))
        item = {
            "id": cur.lastrowid,
            "meal_id": row["meal_id"],
            "served_on": planned_on,
            "note": row["note"],
        }
        return {"logged": item, "meal": get_meal(conn, row["meal_id"])}
    finally:
        conn.close()


# --- app / static -----------------------------------------------------------


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Dinner Helper")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument(
        "--ssl",
        action="store_true",
        help="serve HTTPS with certs/ (mkcert: dinner.dave.lan)",
    )
    args = ap.parse_args()

    ssl_opts = {}
    if args.ssl:
        ssl_opts = {
            "ssl_keyfile": ROOT / "certs" / "dinner.dave.lan+2-key.pem",
            "ssl_certfile": ROOT / "certs" / "dinner.dave.lan-chain.pem",
        }

    scheme = "https" if args.ssl else "http"
    print(f"\n  Dinner Helper -> {scheme}://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, **ssl_opts)


if __name__ == "__main__":
    main()