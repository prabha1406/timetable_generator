"""Demo data. scenario='normal' is solvable; 'impossible' deliberately breaks several constraints
so you can demo the conflict report."""
TABLES = ["timetable_entries", "runs", "faculty_unavailability", "assignments", "subjects", "rooms", "faculty", "divisions", "departments", "settings"]

CSE = [("Database Management Systems", "CS301", 4, 0), ("Operating Systems", "CS302", 4, 0), ("Computer Networks", "CS303", 3, 0),
       ("Data Structures", "CS304", 3, 0), ("Software Engineering", "CS305", 3, 0), ("Discrete Mathematics", "CS306", 3, 0),
       ("DBMS Lab", "CS3L1", 2, 1), ("OS Lab", "CS3L2", 2, 1)]
IT = [("Web Technologies", "IT301", 4, 0), ("Java Programming", "IT302", 4, 0), ("Cloud Computing", "IT303", 3, 0),
      ("Data Mining", "IT304", 3, 0), ("Cyber Security", "IT305", 3, 0), ("Probability & Statistics", "IT306", 3, 0),
      ("Web Tech Lab", "IT3L1", 2, 1), ("Java Lab", "IT3L2", 2, 1)]
CSE_FAC = ["Dr. Priya", "Prof. Arun", "Dr. Meena", "Prof. Karthik", "Dr. Lakshmi", "Prof. Ravi", "Dr. Priya", "Prof. Arun"]
IT_FAC = ["Dr. Anand", "Prof. Divya", "Dr. Suresh", "Prof. Nisha", "Dr. Vikram", "Prof. Kavya", "Dr. Anand", "Prof. Divya"]


def seed(conn, scenario="normal"):
    for t in TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.executemany("INSERT INTO settings VALUES (?,?)", [("days", "5"), ("periods", "8"), ("lunch_after", "4")])
    dep = {}
    for name, code in [("Computer Science", "CSE"), ("Information Technology", "IT")]:
        dep[code] = conn.execute("INSERT INTO departments(name,code) VALUES (?,?)", (name, code)).lastrowid
    fac = {}
    for code, names in (("CSE", CSE_FAC), ("IT", IT_FAC)):
        for n in dict.fromkeys(names):
            fac[n] = conn.execute("INSERT INTO faculty(department_id,name,email,max_periods_per_day) VALUES (?,?,?,5)",
                                  (dep[code], n, n.split()[-1].lower() + "@college.edu")).lastrowid
    divs = {}
    for name, code, strength in [("CSE-A", "CSE", 60), ("CSE-B", "CSE", 58), ("IT-A", "IT", 55), ("IT-B", "IT", 50)]:
        divs[name] = (conn.execute("INSERT INTO divisions(department_id,name,year,strength) VALUES (?,?,3,?)", (dep[code], name, strength)).lastrowid, code)
    for i, n in enumerate(["A101", "A102", "A103", "A104"]):
        conn.execute("INSERT INTO rooms(name,capacity,room_type) VALUES (?,?,'classroom')", (n, 65))
    for n in ["Lab-1", "Lab-2"]:
        conn.execute("INSERT INTO rooms(name,capacity,room_type) VALUES (?,?,'lab')", (n, 65))
    subj = {}
    for code, subs, facs in (("CSE", CSE, CSE_FAC), ("IT", IT, IT_FAC)):
        for (name, c, hrs, lab), f in zip(subs, facs):
            sid = conn.execute("INSERT INTO subjects(department_id,name,code,is_lab,weekly_periods,lab_block) VALUES (?,?,?,?,?,2)",
                               (dep[code], name, c, lab, hrs)).lastrowid
            subj[c] = sid
            for dn, (did, dcode) in divs.items():
                if dcode == code:
                    conn.execute("INSERT INTO assignments(division_id,subject_id,faculty_id) VALUES (?,?,?)", (did, sid, fac[f]))
    # faculty preferences: Dr. Priya not free Friday afternoon, Prof. Arun not free Monday morning
    for p in (5, 6, 7, 8):
        conn.execute("INSERT INTO faculty_unavailability(faculty_id,day,period) VALUES (?,4,?)", (fac["Dr. Priya"], p))
    for p in (1, 2):
        conn.execute("INSERT INTO faculty_unavailability(faculty_id,day,period) VALUES (?,0,?)", (fac["Prof. Arun"], p))
    if scenario == "impossible":
        # 1) CSE-A gets a 20-period subject -> division overloaded (45 > 40 slots), Dr. Priya overloaded
        sid = conn.execute("INSERT INTO subjects(department_id,name,code,is_lab,weekly_periods,lab_block) VALUES (?,?,?,0,20,2)",
                           (dep["CSE"], "Extra Coaching", "CSX01")).lastrowid
        conn.execute("INSERT INTO assignments(division_id,subject_id,faculty_id) VALUES (?,?,?)", (divs["CSE-A"][0], sid, fac["Dr. Priya"]))
        # 2) A division of 120 students: no classroom or lab is big enough
        did = conn.execute("INSERT INTO divisions(department_id,name,year,strength) VALUES (?,?,3,120)", (dep["CSE"], "CSE-C (merged)")).lastrowid
        conn.execute("INSERT INTO assignments(division_id,subject_id,faculty_id) VALUES (?,?,?)", (did, subj["CS301"], fac["Dr. Priya"]))
        # 3) A 5-period lab block: impossible without crossing lunch
        conn.execute("INSERT INTO subjects(department_id,name,code,is_lab,weekly_periods,lab_block) VALUES (?,?,?,1,5,5)",
                     (dep["IT"], "Project Lab", "IT3L9"))
    conn.commit()
