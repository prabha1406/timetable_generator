import os, sys, tempfile
os.environ["TT_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from app import db, scheduler
from app.seed import seed


@pytest.fixture()
def conn():
    db.init_db()
    c = db.get_conn()
    yield c
    c.close()


def solve(conn, scenario):
    seed(conn, scenario)
    P = scheduler.load_problem(conn)
    entries, report = scheduler.generate(P)
    return P, entries, report


def test_normal_scenario_fully_scheduled_and_valid(conn):
    P, entries, report = solve(conn, "normal")
    assert report["status"] == "success"
    assert scheduler.check_entries(P, entries) == []          # includes weekly-hours coverage
    assert report["placed_sessions"] == report["total_sessions"]


def test_labs_use_lab_rooms_and_never_cross_lunch(conn):
    P, entries, _ = solve(conn, "normal")
    for e in entries:
        if P["subjects"][e["subject_id"]]["is_lab"]:
            assert P["rooms"][e["room_id"]]["room_type"] == "lab"
            assert e["period"] in (1, 2, 3, 4, 5, 6, 7, 8)
    assert not [v for v in scheduler.check_entries(P, entries) if v["type"] == "lunch_crossing"]


def test_faculty_unavailability_respected(conn):
    P, entries, _ = solve(conn, "normal")
    unav = {(u["faculty_id"], u["day"], u["period"]) for u in P["faculty_unavailability"].values()}
    assert not [e for e in entries if (e["faculty_id"], e["day"], e["period"]) in unav]


def test_impossible_scenario_reports_reasons_instead_of_crashing(conn):
    P, entries, report = solve(conn, "impossible")
    assert report["status"] in ("partial", "infeasible")
    codes = {i["code"] for i in report["issues"]}
    assert {"division_overloaded", "no_suitable_room", "lab_block_too_long"} <= codes
    assert report["unplaced"], "unplaced classes must be explained"
    assert all(u["message"] and u["suggestion"] for u in report["unplaced"])
    assert scheduler.check_entries(P, entries, coverage=False) == []   # partial timetable is still conflict-free


def test_validator_catches_planted_conflicts(conn):
    P, entries, _ = solve(conn, "normal")
    a = entries[0]
    clash = dict(entries[1], faculty_id=a["faculty_id"], day=a["day"], period=a["period"])
    bad = scheduler.check_entries(P, entries[:1] + [clash], coverage=False)
    assert any(v["type"] == "faculty_conflict" for v in bad)


def test_database_unique_constraints_block_double_booking(conn):
    import sqlite3
    P, entries, _ = solve(conn, "normal")
    e = entries[0]
    conn.execute("INSERT INTO timetable_entries(division_id,subject_id,faculty_id,room_id,day,period,block_id) VALUES (?,?,?,?,?,?,?)",
                 (e["division_id"], e["subject_id"], e["faculty_id"], e["room_id"], e["day"], e["period"], "x"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO timetable_entries(division_id,subject_id,faculty_id,room_id,day,period,block_id) VALUES (?,?,?,?,?,?,?)",
                     (e["division_id"], e["subject_id"], e["faculty_id"], e["room_id"], e["day"], e["period"], "y"))
    conn.rollback()


def test_empty_data_does_not_crash(conn):
    for t in ["timetable_entries", "assignments", "subjects", "rooms", "faculty", "divisions", "departments"]:
        conn.execute(f"DELETE FROM {t}")
    P = scheduler.load_problem(conn)
    entries, report = scheduler.generate(P)
    assert entries == [] and report["total_sessions"] == 0
