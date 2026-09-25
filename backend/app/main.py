import csv
import io
import json
import os
import sqlite3

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import db, scheduler
from .seed import TABLES, seed

app = FastAPI(title="Timetable Generator")
db.init_db()

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "index.html")

# table -> editable columns (whitelist keeps the generic CRUD safe)
ENTITIES = {
    "departments": ["name", "code"],
    "divisions": ["department_id", "name", "year", "strength"],
    "faculty": ["department_id", "name", "email", "max_periods_per_day"],
    "rooms": ["name", "capacity", "room_type"],
    "subjects": ["department_id", "name", "code", "is_lab", "weekly_periods", "lab_block"],
    "assignments": ["division_id", "subject_id", "faculty_id"],
    "faculty_unavailability": ["faculty_id", "day", "period"],
}


def conn():
    return db.get_conn()


@app.get("/")
def index():
    return FileResponse(FRONTEND)


# ---------------------------------------------------------------- settings
@app.get("/api/settings")
def get_settings():
    c = conn()
    s = scheduler.get_settings(c)
    c.close()
    return s


@app.put("/api/settings")
def put_settings(body: dict):
    days, periods, lunch = int(body.get("days", 5)), int(body.get("periods", 8)), int(body.get("lunch_after", 4))
    if not (1 <= days <= 7 and 1 <= periods <= 12 and 0 <= lunch < periods):
        raise HTTPException(400, "days must be 1-7, periods 1-12 and lunch_after between 0 and periods-1.")
    c = conn()
    for k, v in (("days", days), ("periods", periods), ("lunch_after", lunch)):
        c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    c.commit(); c.close()
    return {"days": days, "periods": periods, "lunch_after": lunch}


# ---------------------------------------------------------------- generic CRUD
def _check(t):
    if t not in ENTITIES:
        raise HTTPException(404, "Unknown table")


@app.get("/api/data/{t}")
def list_rows(t: str):
    _check(t)
    c = conn()
    rows = [dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")]
    c.close()
    return rows


@app.post("/api/data/{t}")
def add_row(t: str, body: dict):
    _check(t)
    cols = [k for k in ENTITIES[t] if k in body and body[k] not in ("", None)]
    c = conn()
    try:
        cur = c.execute(f"INSERT INTO {t}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [body[k] for k in cols])
        c.commit()
    except sqlite3.Error as e:
        raise HTTPException(400, f"Could not save: {e}")
    finally:
        c.close()
    return {"id": cur.lastrowid}


@app.delete("/api/data/{t}/{row_id}")
def delete_row(t: str, row_id: int):
    _check(t)
    c = conn()
    c.execute(f"DELETE FROM {t} WHERE id=?", (row_id,))
    c.commit(); c.close()
    return {"ok": True}


# ---------------------------------------------------------------- demo data
@app.post("/api/seed")
def seed_demo(scenario: str = "normal"):
    if scenario not in ("normal", "impossible"):
        raise HTTPException(400, "scenario must be normal or impossible")
    c = conn()
    seed(c, scenario)
    c.close()
    return {"ok": True, "scenario": scenario}


@app.post("/api/reset")
def reset():
    c = conn()
    for t in TABLES:
        c.execute(f"DELETE FROM {t}")
    c.execute("INSERT INTO settings VALUES ('days','5'),('periods','8'),('lunch_after','4')")
    c.commit(); c.close()
    return {"ok": True}


# ---------------------------------------------------------------- generation
@app.post("/api/generate")
def generate():
    c = conn()
    P = scheduler.load_problem(c)
    entries, report = scheduler.generate(P)
    # trust, but verify: the independent validator re-checks the engine's output
    report["violations"] = scheduler.check_entries(P, entries, coverage=False)
    try:
        c.execute("DELETE FROM timetable_entries")
        c.executemany("INSERT INTO timetable_entries(division_id,subject_id,faculty_id,room_id,day,period,block_id) "
                      "VALUES (:division_id,:subject_id,:faculty_id,:room_id,:day,:period,:block_id)", entries)
        c.execute("INSERT INTO runs(status, report) VALUES (?,?)", (report["status"], json.dumps(report)))
        c.commit()
    except sqlite3.IntegrityError as e:   # DB constraints caught something the engine missed
        c.rollback()
        raise HTTPException(500, f"Database rejected the timetable: {e}")
    finally:
        c.close()
    return report


@app.get("/api/report")
def last_report():
    c = conn()
    r = c.execute("SELECT report FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    c.close()
    return json.loads(r["report"]) if r else None


# ---------------------------------------------------------------- viewing
def _entries(c, **flt):
    where, args = [], []
    for k, v in flt.items():
        if v is not None:
            where.append(f"e.{k}=?"); args.append(v)
    sql = ("SELECT e.*, d.name AS division, s.name AS subject, s.code AS subject_code, s.is_lab, f.name AS faculty, r.name AS room "
           "FROM timetable_entries e JOIN divisions d ON d.id=e.division_id JOIN subjects s ON s.id=e.subject_id "
           "JOIN faculty f ON f.id=e.faculty_id JOIN rooms r ON r.id=e.room_id"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY e.day, e.period")
    return [dict(r) for r in c.execute(sql, args)]


@app.get("/api/timetable")
def timetable(division_id: int | None = None, faculty_id: int | None = None, room_id: int | None = None):
    c = conn()
    rows = _entries(c, division_id=division_id, faculty_id=faculty_id, room_id=room_id)
    c.close()
    return rows


@app.get("/api/export.csv", response_class=PlainTextResponse)
def export_csv(division_id: int | None = None):
    c = conn()
    rows = _entries(c, division_id=division_id)
    c.close()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Division", "Day", "Period", "Subject", "Faculty", "Room"])
    for r in rows:
        w.writerow([r["division"], scheduler.DAY_NAMES[r["day"]], r["period"], r["subject"], r["faculty"], r["room"]])
    return PlainTextResponse(out.getvalue(), headers={"Content-Disposition": "attachment; filename=timetable.csv"})


@app.get("/api/validate")
def validate():
    c = conn()
    P = scheduler.load_problem(c)
    rows = [dict(r) for r in c.execute("SELECT * FROM timetable_entries")]
    c.close()
    v = scheduler.check_entries(P, rows, coverage=True)
    return {"valid": not v, "entries": len(rows), "violations": v}


class Move(BaseModel):
    entry_id: int
    day: int
    period: int
    room_id: int | None = None


@app.post("/api/timetable/move")
def move(m: Move):
    """Manual edit. Moves a class (the whole block for labs) to a new slot, but only if every hard
    constraint still holds; otherwise refuses and explains why."""
    c = conn()
    P = scheduler.load_problem(c)
    rows = [dict(r) for r in c.execute("SELECT * FROM timetable_entries")]
    e = next((r for r in rows if r["id"] == m.entry_id), None)
    if not e:
        c.close(); raise HTTPException(404, "Entry not found")
    block = sorted((r for r in rows if r["block_id"] == e["block_id"]), key=lambda r: r["period"])
    ids = {r["id"] for r in block}
    others = [r for r in rows if r["id"] not in ids]
    moved = [dict(r, day=m.day, period=m.period + i) for i, r in enumerate(block)]
    typ = "lab" if P["subjects"][e["subject_id"]]["is_lab"] else "classroom"
    cands = ([m.room_id] if m.room_id else []) + [block[0]["room_id"]] + [r["id"] for r in P["rooms"].values() if r["room_type"] == typ]
    first_error = None
    for room in dict.fromkeys(cands):
        trial = [dict(r, room_id=room) for r in moved]
        viol = scheduler.check_entries(P, others + trial, coverage=False)
        if not viol:
            c.execute(f"DELETE FROM timetable_entries WHERE id IN ({','.join('?' * len(ids))})", list(ids))
            c.executemany("INSERT INTO timetable_entries(division_id,subject_id,faculty_id,room_id,day,period,block_id) "
                          "VALUES (:division_id,:subject_id,:faculty_id,:room_id,:day,:period,:block_id)", trial)
            c.commit(); c.close()
            return {"ok": True}
        first_error = first_error or viol
    c.close()
    raise HTTPException(409, {"message": "That move breaks hard constraints.", "violations": first_error})


# ---------------------------------------------------------------- insights
@app.get("/api/stats")
def stats():
    c = conn()
    P = scheduler.load_problem(c)
    rows = [dict(r) for r in c.execute("SELECT * FROM timetable_entries")]
    c.close()
    S = P["settings"]
    total = S["days"] * S["periods"]
    fac = {f["id"]: {"name": f["name"], "periods": 0, "per_day": [0] * S["days"]} for f in P["faculty"].values()}
    room = {r["id"]: {"name": r["name"], "type": r["room_type"], "used": 0} for r in P["rooms"].values()}
    for e in rows:
        fac[e["faculty_id"]]["periods"] += 1
        fac[e["faculty_id"]]["per_day"][e["day"]] += 1
        room[e["room_id"]]["used"] += 1
    for r in room.values():
        r["utilisation"] = round(100 * r["used"] / total) if total else 0
    return {"total_slots": total, "faculty": list(fac.values()), "rooms": list(room.values())}
