"""Timetable engine.

Pipeline:  build sessions -> feasibility pre-check -> backtracking search -> (fallback)
best-effort partial timetable + explanation of everything that could not be placed.

A *session* is one thing to place: a single period of a theory subject, or one
consecutive block (e.g. 2 periods) of a lab. Every session belongs to one
(division, subject, faculty) assignment.
"""
import math
import random
import time
from collections import Counter, defaultdict

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
NODE_LIMIT = 6000


# ---------------------------------------------------------------- loading
def get_settings(conn):
    s = {"days": 5, "periods": 8, "lunch_after": 4}
    for r in conn.execute("SELECT key, value FROM settings"):
        s[r["key"]] = int(r["value"])
    return s


def load_problem(conn):
    P = {"settings": get_settings(conn)}
    for t in ["departments", "divisions", "faculty", "rooms", "subjects", "assignments", "faculty_unavailability"]:
        P[t] = {r["id"]: dict(r) for r in conn.execute(f"SELECT * FROM {t}")}
    return P


def slot_name(d, p):
    return f"{DAY_NAMES[d]} P{p}"


def valid_starts(S, length):
    """Start periods for a block of `length` periods. Blocks may not cross the lunch break."""
    PP, la = S["periods"], S["lunch_after"]
    out = []
    for st in range(1, PP - length + 2):
        end = st + length - 1
        if length > 1 and 0 < la < PP and (st <= la) != (end <= la):
            continue
        out.append(st)
    return out


def build_sessions(P):
    days = P["settings"]["days"]
    out = []
    for a in P["assignments"].values():
        s = P["subjects"][a["subject_id"]]
        if s["is_lab"]:
            b = max(1, s["lab_block"] or 1)
            full, rem = divmod(s["weekly_periods"], b)
            lens = [b] * full + ([rem] if rem else [])
            cap = b  # at most one lab block per day
        else:
            lens = [1] * s["weekly_periods"]
            cap = max(2, math.ceil(s["weekly_periods"] / days))  # spread theory across days
        for i, L in enumerate(lens):
            out.append({"id": len(out), "division_id": a["division_id"], "subject_id": s["id"],
                        "faculty_id": a["faculty_id"], "length": L, "is_lab": bool(s["is_lab"]),
                        "cap": cap, "block": f"{a['division_id']}-{s['id']}-{i}"})
    return out


# ---------------------------------------------------------------- feasibility pre-check
def feasibility(P, sessions):
    """Cheap arithmetic checks that prove a problem impossible before searching."""
    S = P["settings"]
    D, PP = S["days"], S["periods"]
    total = D * PP
    issues = []

    def add(sev, code, msg, tip):
        issues.append({"severity": sev, "code": code, "message": msg, "suggestion": tip})

    div_load, fac_load = Counter(), Counter()
    for s in sessions:
        div_load[s["division_id"]] += s["length"]
        fac_load[s["faculty_id"]] += s["length"]
    for dv_id, dv in P["divisions"].items():
        if div_load[dv_id] == 0:
            add("warning", "division_empty", f"{dv['name']} has no subjects assigned.", "Add assignments for this division.")
        elif div_load[dv_id] > total:
            add("error", "division_overloaded",
                f"{dv['name']} needs {div_load[dv_id]} periods per week but only {total} slots exist.",
                f"Reduce weekly periods for {dv['name']} by at least {div_load[dv_id] - total}, or add days/periods.")
    unav_count = Counter(u["faculty_id"] for u in P["faculty_unavailability"].values())
    for f_id, load in fac_load.items():
        f = P["faculty"][f_id]
        free = total - unav_count[f_id]
        day_cap = D * (f["max_periods_per_day"] or PP)
        if load > free:
            add("error", "faculty_overloaded",
                f"{f['name']} is assigned {load} periods but only has {free} available slots.",
                f"Move {load - free} period(s) to another faculty or reduce their unavailability.")
        elif load > day_cap:
            add("error", "faculty_daily_limit",
                f"{f['name']} is assigned {load} periods but the daily limit ({f['max_periods_per_day']}) allows {day_cap} per week.",
                "Assign some subjects to another faculty or raise the daily limit.")
    lab_rooms = [r for r in P["rooms"].values() if r["room_type"] == "lab"]
    class_rooms = [r for r in P["rooms"].values() if r["room_type"] == "classroom"]
    need = {"lab": 0, "classroom": 0}
    checked = set()
    for s in sessions:
        typ = "lab" if s["is_lab"] else "classroom"
        need[typ] += s["length"]
        dv = P["divisions"][s["division_id"]]
        if (dv["id"], typ) in checked:
            continue
        checked.add((dv["id"], typ))
        pool = lab_rooms if typ == "lab" else class_rooms
        if not any(r["capacity"] >= dv["strength"] for r in pool):
            add("error", "no_suitable_room",
                f"No {typ} can hold {dv['name']} ({dv['strength']} students)."
                + ("" if pool else f" There are no {typ}s at all."),
                f"Add a {typ} with capacity of at least {dv['strength']}, or split the division.")
    for typ, pool in (("lab", lab_rooms), ("classroom", class_rooms)):
        if pool and need[typ] > len(pool) * total:
            add("error", "room_capacity",
                f"All divisions need {need[typ]} {typ} periods per week but {len(pool)} {typ}(s) offer only {len(pool) * total}.",
                f"Add {typ}s or reduce weekly periods.")
    for s in P["subjects"].values():
        if s["is_lab"]:
            b = max(1, s["lab_block"] or 1)
            if not valid_starts(S, b):
                add("error", "lab_block_too_long",
                    f"{s['name']} needs {b} consecutive periods but no stretch of {b} periods exists without crossing lunch.",
                    "Reduce the lab block length or move the lunch break.")
            if s["weekly_periods"] % b:
                add("warning", "lab_block_remainder",
                    f"{s['name']}: {s['weekly_periods']} weekly periods is not a multiple of the {b}-period block; the last block will be shorter.",
                    "Use a multiple of the block length.")
    return issues


# ---------------------------------------------------------------- search state
class State:
    def __init__(self, P):
        self.P = P
        S = P["settings"]
        self.D, self.PP, self.la = S["days"], S["periods"], S["lunch_after"]
        self.db, self.fb, self.rb = set(), set(), set()   # division / faculty / room busy: (id, day, period)
        self.sd = defaultdict(int)                        # (division, subject, day) -> periods
        self.fd = defaultdict(int)                        # (faculty, day) -> periods
        self.unav = {(u["faculty_id"], u["day"], u["period"]) for u in P["faculty_unavailability"].values()}
        self.starts = {}
        self.rooms_for = {}
        div_ids = sorted(P["divisions"])
        for typ in ("classroom", "lab"):
            for i, dv_id in enumerate(div_ids):
                dv = P["divisions"][dv_id]
                pool = sorted((r for r in P["rooms"].values() if r["room_type"] == typ and r["capacity"] >= dv["strength"]),
                              key=lambda r: (r["capacity"], r["id"]))
                ids = [r["id"] for r in pool]
                if typ == "classroom" and ids:            # give each division a "home" classroom
                    home = ids[i % len(ids)]
                    ids.remove(home)
                    ids.insert(0, home)
                self.rooms_for[(dv_id, typ)] = ids

    def _starts(self, L):
        if L not in self.starts:
            self.starts[L] = valid_starts(self.P["settings"], L)
        return self.starts[L]

    def _fmax(self, f_id):
        return self.P["faculty"][f_id]["max_periods_per_day"] or self.PP

    def _room(self, s, d, ps):
        for r in self.rooms_for[(s["division_id"], "lab" if s["is_lab"] else "classroom")]:
            if all((r, d, p) not in self.rb for p in ps):
                return r
        return None

    def options(self, s):
        dv, sub, fac, L = s["division_id"], s["subject_id"], s["faculty_id"], s["length"]
        res = []
        for d in range(self.D):
            if self.sd[(dv, sub, d)] + L > s["cap"] or self.fd[(fac, d)] + L > self._fmax(fac):
                continue
            for st in self._starts(L):
                ps = range(st, st + L)
                if any((dv, d, p) in self.db or (fac, d, p) in self.fb or (fac, d, p) in self.unav for p in ps):
                    continue
                room = self._room(s, d, ps)
                if room is not None:
                    res.append((d, st, room))
        return res

    def place(self, s, o):
        d, st, room = o
        for p in range(st, st + s["length"]):
            self.db.add((s["division_id"], d, p)); self.fb.add((s["faculty_id"], d, p)); self.rb.add((room, d, p))
        self.sd[(s["division_id"], s["subject_id"], d)] += s["length"]
        self.fd[(s["faculty_id"], d)] += s["length"]

    def unplace(self, s, o):
        d, st, room = o
        for p in range(st, st + s["length"]):
            self.db.discard((s["division_id"], d, p)); self.fb.discard((s["faculty_id"], d, p)); self.rb.discard((room, d, p))
        self.sd[(s["division_id"], s["subject_id"], d)] -= s["length"]
        self.fd[(s["faculty_id"], d)] -= s["length"]

    def score(self, s, o):
        """Lower is better: soft preferences (balanced days, spread subjects, labs after lunch)."""
        d, st, _ = o
        day_load = sum(1 for p in range(1, self.PP + 1) if (s["division_id"], d, p) in self.db)
        v = day_load + 4 * self.sd[(s["division_id"], s["subject_id"], d)] + 0.5 * self.fd[(s["faculty_id"], d)] + 0.05 * st
        if s["is_lab"] and st > self.la:
            v -= 1
        return v

    def diagnose(self, s):
        """Why can't this session be placed? Count the blocking reason for each candidate slot."""
        P = self.P
        dv, sub, fac, L = s["division_id"], s["subject_id"], s["faculty_id"], s["length"]
        typ = "lab" if s["is_lab"] else "classroom"
        why = Counter()
        starts = self._starts(L)
        for d in range(self.D):
            if self.sd[(dv, sub, d)] + L > s["cap"]:
                why["subject_daily_limit"] += len(starts); continue
            if self.fd[(fac, d)] + L > self._fmax(fac):
                why["faculty_daily_limit"] += len(starts); continue
            for st in starts:
                ps = range(st, st + L)
                if any((fac, d, p) in self.unav for p in ps): why["faculty_unavailable"] += 1
                elif any((fac, d, p) in self.fb for p in ps): why["faculty_busy"] += 1
                elif any((dv, d, p) in self.db for p in ps): why["division_busy"] += 1
                else: why["no_room"] += 1
        if not self.rooms_for[(dv, typ)]:
            why = Counter({"no_suitable_room": 1})
        names = {
            "subject_daily_limit": "subject already at its per-day limit",
            "faculty_daily_limit": "faculty at their daily limit",
            "faculty_unavailable": "faculty marked unavailable",
            "faculty_busy": "faculty already teaching",
            "division_busy": "division already has a class",
            "no_room": f"no free {typ}",
            "no_suitable_room": f"no {typ} large enough exists",
        }
        fn, dn = P["faculty"][fac]["name"], P["divisions"][dv]["name"]
        tips = {
            "no_room": f"Add another {typ} or reduce demand on {typ}s.",
            "no_suitable_room": f"Add a {typ} that fits {dn}.",
            "faculty_busy": f"{fn} is fully booked: assign another faculty or cut their load.",
            "faculty_unavailable": f"Relax {fn}'s unavailability.",
            "faculty_daily_limit": f"Raise {fn}'s daily limit or share their subjects.",
            "division_busy": f"{dn} is already full: reduce weekly periods.",
            "subject_daily_limit": "Reduce weekly periods for this subject.",
        }
        top = why.most_common(1)[0][0] if why else "no_room"
        detail = ", ".join(f"{n} slot(s): {names[k]}" for k, n in why.most_common())
        sn = P["subjects"][sub]["name"]
        return {"division": dn, "subject": sn, "faculty": fn, "length": L, "reasons": dict(why),
                "message": f"{sn} ({dn}, {fn}, {L} period{'s' if L > 1 else ''}) could not be placed. Blocked by: {detail}.",
                "suggestion": tips[top]}


class Timeout(Exception):
    pass


def search(st, sessions, deadline, rng):
    """Backtracking with MRV (most-constrained session first) + forward checking."""
    placed, nodes = [], [0]

    def rec(rem):
        if not rem:
            return True
        nodes[0] += 1
        if nodes[0] > NODE_LIMIT or time.time() > deadline:
            raise Timeout()
        best, seen = None, set()
        for s in rem:
            key = (s["division_id"], s["subject_id"], s["length"])
            if key in seen:            # identical sessions have identical options
                continue
            seen.add(key)
            o = st.options(s)
            if not o:
                return False           # forward check: dead end, backtrack now
            rank = (len(o), -s["length"])
            if best is None or rank < best[0]:
                best = (rank, s, o)
        _, s, o = best
        o.sort(key=lambda x: st.score(s, x) + rng.random() * 1.5)
        rest = [x for x in rem if x is not s]
        for opt in o:
            st.place(s, opt); placed.append((s, opt))
            if rec(rest):
                return True
            placed.pop(); st.unplace(s, opt)
        return False

    ok = rec(list(sessions))
    return ok, placed, nodes[0]


def greedy_partial(st, sessions, rng):
    """Best effort: place whatever fits, return what could not be placed."""
    placed, unplaced, rem = [], [], list(sessions)
    while rem:
        best = None
        for s in list(rem):
            o = st.options(s)
            if not o:
                unplaced.append(s); rem.remove(s); continue
            if best is None or (len(o), -s["length"]) < best[0]:
                best = ((len(o), -s["length"]), s, o)
        if best is None:
            break
        _, s, o = best
        opt = min(o, key=lambda x: st.score(s, x) + rng.random() * 0.1)
        st.place(s, opt); placed.append((s, opt)); rem.remove(s)
    return placed, unplaced


def to_entries(placed):
    rows = []
    for s, (d, st, room) in placed:
        for k in range(s["length"]):
            rows.append({"division_id": s["division_id"], "subject_id": s["subject_id"], "faculty_id": s["faculty_id"],
                         "room_id": room, "day": d, "period": st + k, "block_id": s["block"]})
    return rows


def generate(P, time_limit=10, seed=7):
    """Returns (entries, report)."""
    t0 = time.time()
    sessions = build_sessions(P)
    issues = feasibility(P, sessions)
    errors = [i for i in issues if i["severity"] == "error"]
    solved, attempts, exhausted = None, 0, False
    if sessions and not errors:
        deadline = t0 + time_limit
        while time.time() < deadline and solved is None and not exhausted:
            attempts += 1
            try:
                ok, placed, _ = search(State(P), sessions, min(deadline, time.time() + time_limit / 3), random.Random(seed + attempts))
                if ok:
                    solved = placed
                else:
                    exhausted = True   # complete search finished with no answer: provably impossible
            except Timeout:
                continue
    unplaced = []
    if solved is not None:
        placed, status = solved, "success"
    else:
        st = State(P)
        placed, unplaced_s = greedy_partial(st, sessions, random.Random(seed))
        unplaced = [st.diagnose(s) for s in unplaced_s]
        status = "partial" if placed else "infeasible"
        if not errors and sessions:
            issues.append({"severity": "error", "code": "no_solution",
                           "message": ("The search proved no complete timetable exists with these constraints."
                                       if exhausted else "No complete timetable found within the time limit."),
                           "suggestion": "See the unplaced classes below: the most-blocked ones show which constraint to relax."})
    report = {"status": status, "total_sessions": len(sessions), "placed_sessions": len(placed),
              "issues": issues, "unplaced": unplaced,
              "stats": {"attempts": attempts, "seconds": round(time.time() - t0, 2)}}
    return to_entries(placed), report


# ---------------------------------------------------------------- independent validator
def check_entries(P, entries, coverage=True):
    """Re-verifies every hard constraint from scratch. Used after generation, after manual edits
    and by /api/validate, so the scheduler never marks its own homework."""
    S = P["settings"]
    D, PP, la = S["days"], S["periods"], S["lunch_after"]
    v = []
    nm = lambda t, i: P[t][i]["name"]
    seen = {"division": {}, "faculty": {}, "room": {}}
    unav = {(u["faculty_id"], u["day"], u["period"]) for u in P["faculty_unavailability"].values()}
    fac_day = Counter()
    for e in entries:
        d, p = e["day"], e["period"]
        where = slot_name(d, p) if 0 <= d < len(DAY_NAMES) else f"day {d} P{p}"
        if not (0 <= d < D and 1 <= p <= PP):
            v.append({"type": "out_of_range", "message": f"{nm('subjects', e['subject_id'])} is scheduled outside the timetable ({where})."})
            continue
        for kind, key, tbl in (("division", "division_id", "divisions"), ("faculty", "faculty_id", "faculty"), ("room", "room_id", "rooms")):
            k = (e[key], d, p)
            if k in seen[kind]:
                v.append({"type": f"{kind}_conflict", "message": f"{nm(tbl, e[key])} is double-booked on {where}."})
            seen[kind][k] = e
        room, sub, dv = P["rooms"][e["room_id"]], P["subjects"][e["subject_id"]], P["divisions"][e["division_id"]]
        if bool(sub["is_lab"]) != (room["room_type"] == "lab"):
            v.append({"type": "room_type", "message": f"{sub['name']} ({dv['name']}) is in {room['name']}, a {room['room_type']}, on {where}."})
        if room["capacity"] < dv["strength"]:
            v.append({"type": "capacity", "message": f"{room['name']} (capacity {room['capacity']}) is too small for {dv['name']} ({dv['strength']}) on {where}."})
        if (e["faculty_id"], d, p) in unav:
            v.append({"type": "faculty_unavailable", "message": f"{nm('faculty', e['faculty_id'])} is marked unavailable on {where}."})
        fac_day[(e["faculty_id"], d)] += 1
    for (f, d), n in fac_day.items():
        cap = P["faculty"][f]["max_periods_per_day"] or PP
        if n > cap:
            v.append({"type": "faculty_daily_limit", "message": f"{nm('faculty', f)} teaches {n} periods on {DAY_NAMES[d]} (limit {cap})."})
    blocks = defaultdict(list)
    for e in entries:
        blocks[e["block_id"]].append(e)
    for es in blocks.values():
        if len(es) < 2:
            continue
        ps = sorted(x["period"] for x in es)
        sub, dvn = P["subjects"][es[0]["subject_id"]]["name"], nm("divisions", es[0]["division_id"])
        if len({x["day"] for x in es}) > 1 or ps != list(range(ps[0], ps[0] + len(ps))):
            v.append({"type": "block_split", "message": f"{sub} block for {dvn} is not consecutive."})
        elif 0 < la < PP and (ps[0] <= la) != (ps[-1] <= la):
            v.append({"type": "lunch_crossing", "message": f"{sub} block for {dvn} crosses the lunch break."})
    if coverage:
        have = Counter((e["division_id"], e["subject_id"]) for e in entries)
        for a in P["assignments"].values():
            need = P["subjects"][a["subject_id"]]["weekly_periods"]
            got = have[(a["division_id"], a["subject_id"])]
            if got != need:
                v.append({"type": "weekly_hours", "message": f"{nm('subjects', a['subject_id'])} for {nm('divisions', a['division_id'])} has {got} of {need} weekly periods."})
    return v
