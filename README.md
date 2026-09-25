# Intelligent Timetable Generator

Edumerge Solutions — Pre-Drive Product Engineering Assignment (Assignment 3)

A working web app that generates a weekly class timetable for a college with
multiple departments, divisions, faculty and rooms, respects hard scheduling
constraints, and explains itself in plain English whenever a full timetable
isn't possible.

## Quick start

```
pip install -r requirements.txt
./run.sh
```
Then open **http://localhost:8000**. The app ships with no data — use
**"Load demo data"** in the header for a ready-made scenario, or
**"Load impossible demo"** to see the conflict report in action. Data is
stored in `backend/timetable.db` (SQLite, created automatically).

Run the test suite: `pytest tests -q` (7 tests covering the scheduler, the
independent validator, and the database's own constraints).

## What it does

1. **Setup** — enter departments, divisions, faculty, rooms and subjects, and
   assign which faculty teaches which subject to which division. Set the
   weekly shape (days, periods/day, lunch period).
2. **Generate** — one click runs the scheduling engine. You get either a
   complete timetable, or a **conflict report**: exactly which classes could
   not be placed, why, and a concrete suggestion to fix it.
3. **Timetable** — view by division, faculty, or room. Drag a class onto an
   empty slot to reschedule it by hand; the app only allows moves that don't
   break a hard constraint, and explains why when it refuses one. Export any
   view as CSV.
4. **Insights** — faculty weekly workload and room utilisation, so an admin
   can see where the pressure points are.

## Assumptions (not specified by the brief, decided by us)

- A week has a configurable number of working days (default 5, Mon–Fri) and
  periods per day (default 8), with one lunch break after a configurable
  period. These are settings, not hard-coded, since different colleges differ.
- A single admin generates and edits the timetable; there's no multi-role
  login for this prototype (the brief doesn't ask for auth, and building a
  real auth system would take time away from the scheduling problem itself).
- A "division" is one student batch (e.g. CSE-A) with a fixed strength; a
  division attends every one of its assigned subjects as a whole.
- A subject has a weekly period count and is either theory (scheduled as
  single periods, spread across different days) or a lab (scheduled as one
  consecutive block per occurrence, e.g. a 2-period block, in a lab room, and
  never split across the lunch break).
- One faculty member is assigned to one subject for one division; a faculty
  member can teach several subjects/divisions but has a configurable maximum
  periods per day and can be marked unavailable for specific slots.
- Rooms are either classrooms or labs, each with a capacity; a division can
  only be placed in a room whose capacity covers its strength.
- Timetables are generated department-wide in a single pass rather than
  department-by-department, because faculty and rooms are frequently shared
  across departments in a real college, and solving them together produces a
  materially better (and honestly *correct*) schedule — solving each
  department in isolation can silently double-book a shared faculty member or
  room across departments.

## Architecture

- **Backend**: Python + FastAPI + SQLite (`backend/app/`). No heavy
  dependencies, so it runs anywhere `pip install fastapi uvicorn` works.
  - `db.py` — schema. Every hard constraint that can be expressed as a
    uniqueness rule (`UNIQUE(faculty_id, day, period)`, and the same for
    division and room) is enforced *at the database level* — a defence layer
    independent of the scheduling code.
  - `scheduler.py` — the engine (see below).
  - `main.py` — the REST API consumed by the frontend.
- **Frontend**: a single static HTML/CSS/vanilla-JS page (`frontend/index.html`),
  served by FastAPI. No build step, no framework — kept intentionally simple
  so the logic under evaluation is the scheduling engine, not frontend tooling.
- **Data model**: `departments → divisions/faculty/subjects`,
  `assignments (division, subject, faculty)`, `rooms`,
  `faculty_unavailability`, and `timetable_entries` (the generated result).

## The scheduling engine, in plain terms

1. **Break the problem into sessions.** Each (division, subject, faculty)
   assignment becomes one or more *sessions* to place: one session per period
   for theory, or one session per consecutive block for labs.
2. **Feasibility pre-check.** Before searching, cheap arithmetic checks
   compare demand against supply — total periods needed per division/faculty
   vs. slots available, room capacity vs. division size, whether a lab block
   even fits in the day without crossing lunch. Anything provably impossible
   is reported immediately, without wasting time searching.
3. **Backtracking search with forward checking.** If the numbers allow a
   solution, a constraint-satisfaction search places sessions one at a time,
   always picking the most-constrained session next (fewest legal slots
   left), and immediately backtracks if any remaining session has zero legal
   slots. A random restart with a time/node budget avoids getting stuck in
   one bad ordering.
4. **Soft preferences** guide *which* legal slot is picked, when several are
   legal: spread a subject's classes across different days, balance each
   division's and each faculty member's daily load, and prefer scheduling
   labs after lunch.
5. **If no complete timetable exists**, the engine falls back to a
   best-effort greedy placement (place everything you can) and, for every
   class it couldn't place, diagnoses *why* — counting how many candidate
   slots were blocked by each kind of conflict (faculty busy, no room, daily
   limit, etc.) and naming the most common blocker with a concrete fix.
6. **Independent validation.** After generation (and after every manual
   drag-and-drop edit), a *separate* function re-checks every hard
   constraint from scratch against the raw entries — the engine never
   grades its own homework. The database's own `UNIQUE` constraints are the
   final backstop if that somehow disagreed.

### Hard constraints enforced

- No faculty member teaches two classes at once.
- No room hosts two classes at once.
- No division attends two classes at once.
- Lab subjects only get lab rooms; room capacity must fit the division.
- Every subject receives its exact required weekly periods.
- A faculty member's daily period limit and unavailability are respected.
- A lab's periods stay consecutive and never straddle the lunch break.

### Trade-offs

- The search is exact and complete on small-to-medium colleges (the demo
  data — 2 departments, 4 divisions, 12 faculty, 16 subjects — solves well
  under a second). For very large inputs it falls back to a good-but-not-
  necessarily-optimal greedy solution within a fixed time budget rather than
  guaranteeing the mathematically best schedule — a deliberate trade-off:
  users need *a* usable timetable and a clear explanation quickly, not a
  perfect one after an unbounded wait.
- Manual edits only allow constraint-safe moves; there's no "override and
  flag it" mode. This was a product call: silently letting a coordinator
  create a conflict undermines the entire point of the tool.

## Edge cases handled

- Empty data (no divisions/faculty/subjects yet) — generates cleanly with an
  empty timetable rather than crashing.
- A division or faculty member with more weekly load than slots exist.
- A room class too small for the division's strength, or no room of the
  needed type at all.
- A lab block longer than the periods available before/after lunch.
- Two hard constraints failing simultaneously — every failure is reported,
  not just the first one found.
- A generation that's only partially possible — the schedulable part is
  still generated and shown, alongside the report on what couldn't be.

## AI usage

See `AI_USAGE_REPORT.md`.
