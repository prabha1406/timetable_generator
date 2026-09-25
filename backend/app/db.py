"""SQLite storage. The UNIQUE constraints on timetable_entries are the last line of
defence: even if the scheduler had a bug, the database itself refuses a teacher, room
or division being double-booked in the same slot."""
import os
import sqlite3

DB_PATH = os.environ.get("TT_DB", os.path.join(os.path.dirname(__file__), "..", "timetable.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS departments(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS divisions(
  id INTEGER PRIMARY KEY, department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
  name TEXT NOT NULL UNIQUE, year INTEGER DEFAULT 1, strength INTEGER DEFAULT 60);
CREATE TABLE IF NOT EXISTS faculty(
  id INTEGER PRIMARY KEY, department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
  name TEXT NOT NULL, email TEXT, max_periods_per_day INTEGER DEFAULT 6);
CREATE TABLE IF NOT EXISTS rooms(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, capacity INTEGER DEFAULT 60,
  room_type TEXT NOT NULL CHECK(room_type IN ('classroom','lab')));
CREATE TABLE IF NOT EXISTS subjects(
  id INTEGER PRIMARY KEY, department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
  name TEXT NOT NULL, code TEXT NOT NULL UNIQUE, is_lab INTEGER DEFAULT 0,
  weekly_periods INTEGER NOT NULL CHECK(weekly_periods > 0), lab_block INTEGER DEFAULT 2);
CREATE TABLE IF NOT EXISTS assignments(
  id INTEGER PRIMARY KEY, division_id INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
  faculty_id INTEGER NOT NULL REFERENCES faculty(id) ON DELETE CASCADE,
  UNIQUE(division_id, subject_id));
CREATE TABLE IF NOT EXISTS faculty_unavailability(
  id INTEGER PRIMARY KEY, faculty_id INTEGER NOT NULL REFERENCES faculty(id) ON DELETE CASCADE,
  day INTEGER NOT NULL, period INTEGER NOT NULL, UNIQUE(faculty_id, day, period));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS timetable_entries(
  id INTEGER PRIMARY KEY,
  division_id INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
  faculty_id INTEGER NOT NULL REFERENCES faculty(id) ON DELETE CASCADE,
  room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  day INTEGER NOT NULL, period INTEGER NOT NULL, block_id TEXT,
  UNIQUE(division_id, day, period),
  UNIQUE(faculty_id, day, period),
  UNIQUE(room_id, day, period));
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT, report TEXT);
"""


def get_conn(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path=None):
    conn = get_conn(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
