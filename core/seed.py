"""
First-run database seeding.

Creates the academic structure for **Government Girls Polytechnic, Shamli**
using the college's published details: its three diploma programmes with their
sanctioned intakes, and its real teaching-staff directory.

On top of that it generates a realistic body of demonstration data -- students,
timetable, roughly eight weeks of attendance history and a spread of leave
applications -- so every screen, report and chart has something meaningful to
show at a viva.

Two details worth noting:

*   The college is a **girls** polytechnic, so every generated student is
    female.  Getting this wrong would be immediately obvious to anyone from
    the institution.
*   **Sections are not created by default.**  The college does not publish
    sectioning, and the specification asks for sections to be optional, so
    students are seeded with no section.  An administrator can add sections
    later from Academic Setup and the whole system carries on working.

Seeding is idempotent: it runs only when the ``users`` table is empty.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from config.settings import (LEAVE_TYPES, ROLE_ADMIN, ROLE_FACULTY, ROLE_STUDENT,
                             config)
from core.auth import hash_password
from core.database import get_db
from core.logger import get_logger

logger = get_logger("core.seed")

# A fixed seed keeps the demo reproducible -- the same student is the same
# defaulter every time the project is presented.
RNG = random.Random(20260728)

DEFAULT_PASSWORDS = {
    ROLE_ADMIN: "admin123",
    ROLE_FACULTY: "faculty123",
    ROLE_STUDENT: "student123",
}

# ---------------------------------------------------------------------------
# Institution -- from https://www.ggpshamli.ac.in
# ---------------------------------------------------------------------------
COLLEGE = {
    "college_name": "Government Girls Polytechnic, Shamli",
    "college_short_name": "GGPS",
    "college_code": "GGPS-SHAMLI",
    "college_address": "Near New Mandi, Mundate Road, Shamli - 247776, Uttar Pradesh",
    "college_phone": "01398-251742",
    "college_email": "principal@ggpshamli.ac.in",
    "college_website": "www.ggpshamli.ac.in",
    "principal_name": "Jitendra Kumar",
    "college_established": "1989",
    "affiliation": ("Affiliated to Board of Technical Education, Uttar Pradesh "
                    "(BTEUP)  |  Approved by AICTE, New Delhi"),
}

BRANCHES = [
    # (name, code, head of department, sanctioned intake)
    ("Computer Science & Engineering", "CSE", "Mr. Sanjay Kumar", 60),
    ("Electronics Engineering", "EE", "Ms. Aditi Singh", 30),
    ("Fashion Designing & Garment Technology", "FD", "Dr. Monika", 60),
]

# The college's published teaching-staff directory.
FACULTY = [
    # (code, name, gender, qualification, designation, experience, branch, mobile)
    ("FAC001", "Mr. Sanjay Kumar", "Male", "M.Tech",
     "Head of Department", 15.0, "CSE", "9719804209"),
    ("FAC002", "Ms. Rashmi", "Female", "B.Tech",
     "Lecturer", 6.0, "CSE", "7983723945"),
    ("FAC003", "Mr. Sachin Vikal", "Male", "M.Tech",
     "Lecturer", 8.0, "CSE", "9639140955"),
    ("FAC004", "Ms. Swati Chauhan", "Female", "M.Tech",
     "Lecturer", 7.0, "CSE", "7060115758"),
    ("FAC005", "Ms. Aditi Singh", "Female", "M.Tech (Electronics & Communication)",
     "Head of Department", 9.0, "EE", "7017873310"),
    ("FAC006", "Mr. Sachin Saini", "Male", "M.Tech",
     "Lecturer", 10.0, "EE", "9971209799"),
    ("FAC007", "Ms. Vani Gupta", "Female", "M.Tech",
     "Lecturer", 6.5, "EE", "7599542338"),
    ("FAC008", "Ms. Monika Chauhan", "Female", "M.E. (Electronics & Communication)",
     "Lecturer", 8.5, "EE", "6395214136"),
    ("FAC009", "Dr. Monika", "Female", "Ph.D. (Physics)",
     "Lecturer", 12.0, "CSE", "8882657040"),
    ("FAC010", "Dr. Monika Sharma", "Female", "Ph.D. (Accountancy and Taxation)",
     "Head of Department", 11.0, "FD", "9250392232"),
    ("FAC011", "Mr. Tejpal Singh", "Male", "Diploma",
     "Workshop Instructor", 14.0, "EE", None),
    ("FAC012", "Mr. Rajendra Kumar", "Male", "Diploma",
     "Workshop Instructor", 13.0, "CSE", "9458292102"),
    ("FAC013", "Mr. Jitendra Kumar", "Male", "Diploma",
     "Workshop Instructor", 12.0, "EE", "9045124790"),
]

# BTEUP diploma curriculum for the three programmes.  The college does not
# publish subject codes, so these follow the conventional branch-prefix scheme.
SUBJECTS = [
    # ---- Computer Science & Engineering ----------------------------------
    ("CS301", "Data Structures Using C",        "CSE", 3, "Theory",    4, "FAC001"),
    ("CS302", "Object Oriented Programming",    "CSE", 3, "Theory",    4, "FAC003"),
    ("CS303", "Digital Electronics",            "CSE", 3, "Theory",    4, "FAC005"),
    ("CS304", "Data Structures Lab",            "CSE", 3, "Practical", 2, "FAC002"),
    ("CS305", "Applied Mathematics III",        "CSE", 3, "Theory",    3, "FAC009"),

    ("CS401", "Python Programming",             "CSE", 4, "Theory",    4, "FAC004"),
    ("CS402", "Database Management Systems",    "CSE", 4, "Theory",    4, "FAC001"),
    ("CS403", "Computer Networks",              "CSE", 4, "Theory",    4, "FAC003"),
    ("CS404", "Operating Systems",              "CSE", 4, "Theory",    4, "FAC002"),
    ("CS405", "Python Programming Lab",         "CSE", 4, "Practical", 2, "FAC004"),
    ("CS406", "Web Technology Lab",             "CSE", 4, "Practical", 2, "FAC012"),

    ("CS501", "Software Engineering",           "CSE", 5, "Theory",    4, "FAC001"),
    ("CS502", "Java Programming",               "CSE", 5, "Theory",    4, "FAC003"),
    ("CS503", "Computer Graphics",              "CSE", 5, "Theory",    3, "FAC004"),
    ("CS504", "Java Programming Lab",           "CSE", 5, "Practical", 2, "FAC002"),
    ("CS505", "Minor Project",                  "CSE", 5, "Project",   3, "FAC001"),

    ("CS601", "Mobile Application Development", "CSE", 6, "Theory",    4, "FAC003"),
    ("CS602", "Cloud Computing",                "CSE", 6, "Theory",    3, "FAC004"),
    ("CS603", "Major Project",                  "CSE", 6, "Project",   6, "FAC001"),

    # ---- Electronics Engineering ------------------------------------------
    ("EE301", "Electronic Devices & Circuits",  "EE", 3, "Theory",    4, "FAC005"),
    ("EE302", "Digital Electronics",            "EE", 3, "Theory",    4, "FAC008"),
    ("EE303", "Electronics Workshop",           "EE", 3, "Practical", 2, "FAC011"),

    ("EE401", "Analog Electronics",             "EE", 4, "Theory",    4, "FAC005"),
    ("EE402", "Microcontrollers",               "EE", 4, "Theory",    4, "FAC006"),
    ("EE403", "Communication Systems",          "EE", 4, "Theory",    4, "FAC008"),
    ("EE404", "Microcontroller Lab",            "EE", 4, "Practical", 2, "FAC007"),
    ("EE405", "Electrical Machines",            "EE", 4, "Theory",    3, "FAC006"),

    ("EE501", "Industrial Electronics",         "EE", 5, "Theory",    4, "FAC006"),
    ("EE502", "VLSI Design",                    "EE", 5, "Theory",    4, "FAC008"),
    ("EE503", "Minor Project",                  "EE", 5, "Project",   3, "FAC005"),

    # ---- Fashion Designing & Garment Technology ---------------------------
    ("FD301", "Textile Science",                "FD", 3, "Theory",    4, "FAC010"),
    ("FD302", "Pattern Making",                 "FD", 3, "Practical", 4, "FAC010"),
    ("FD303", "Fashion Illustration",           "FD", 3, "Practical", 3, "FAC010"),

    ("FD401", "Garment Construction",           "FD", 4, "Practical", 4, "FAC010"),
    ("FD402", "Apparel Design",                 "FD", 4, "Theory",    4, "FAC010"),
    ("FD403", "Fabric Studies",                 "FD", 4, "Theory",    3, "FAC010"),
    ("FD404", "Computer Aided Design Lab",      "FD", 4, "Practical", 2, "FAC012"),

    ("FD501", "Fashion Merchandising",          "FD", 5, "Theory",    4, "FAC010"),
    ("FD502", "Garment Production Technology",  "FD", 5, "Theory",    4, "FAC010"),
    ("FD503", "Minor Project",                  "FD", 5, "Project",   3, "FAC010"),
]

# This is a girls polytechnic -- every student is female.
FIRST_NAMES = [
    "Aarti", "Aditi", "Akanksha", "Alka", "Anjali", "Anju", "Ankita", "Annu",
    "Anshika", "Aradhana", "Archana", "Arti", "Ayesha", "Babita", "Bhavna",
    "Chhavi", "Deepa", "Deepika", "Divya", "Ekta", "Fatima", "Garima", "Gauri",
    "Geeta", "Heena", "Hema", "Isha", "Jyoti", "Kajal", "Kamini", "Kanchan",
    "Kavita", "Khushbu", "Komal", "Kriti", "Lakshmi", "Mamta", "Manisha",
    "Meenakshi", "Megha", "Monika", "Muskan", "Nazia", "Neelam", "Neha",
    "Nidhi", "Nikita", "Nisha", "Pooja", "Poonam", "Pratibha", "Preeti",
    "Priya", "Priyanka", "Rachna", "Radhika", "Rekha", "Renu", "Richa",
    "Ritu", "Sadhna", "Sakshi", "Sana", "Sangeeta", "Sanjana", "Sapna",
    "Seema", "Shalini", "Shikha", "Shivani", "Shweta", "Simran", "Sneha",
    "Sonam", "Sonia", "Suman", "Sunita", "Swati", "Tanu", "Tanvi", "Usha",
    "Vandana", "Vidhi", "Vinita", "Yamini", "Zoya",
]

SURNAMES = [
    "Sharma", "Verma", "Singh", "Chauhan", "Kumari", "Saini", "Tyagi", "Rana",
    "Panwar", "Malik", "Kashyap", "Goswami", "Tomar", "Pundir", "Bansal",
    "Garg", "Jain", "Agarwal", "Mittal", "Gupta", "Chaudhary", "Rathi",
    "Ahlawat", "Balyan", "Khan", "Ansari", "Siddiqui", "Kaur", "Yadav",
    "Pal", "Kashyap", "Prajapati", "Vishwakarma", "Rastogi", "Srivastava",
]

FATHER_NAMES = [
    "Rajesh", "Suresh", "Mahesh", "Dinesh", "Prakash", "Vijay", "Ashok",
    "Anil", "Sunil", "Mukesh", "Naresh", "Jitendra", "Satish", "Ramesh",
    "Om Prakash", "Krishan", "Devendra", "Rajendra", "Yogesh", "Sanjay",
]

MOTHER_NAMES = [
    "Sunita", "Anita", "Kavita", "Sarita", "Lata", "Rekha", "Usha", "Geeta",
    "Seema", "Nirmala", "Vandana", "Madhuri", "Shobha", "Asha", "Kamlesh",
    "Santosh", "Pushpa", "Saroj", "Manju", "Urmila",
]

# Localities in and around Shamli district.
AREAS = [
    "New Mandi", "Mundate Road", "Rail Bazar", "Gandhi Colony", "Adarsh Nagar",
    "Bhainswal Road", "Jhinjhana Road", "Kairana Road", "Civil Lines",
    "Krishna Puri", "Model Town",
]
TOWNS = [
    "Shamli", "Kairana", "Jhinjhana", "Un", "Thanabhawan", "Kandhla",
    "Garhi Pukhta", "Bhainswal",
]

HOLIDAYS = [
    ("2026-08-15", "Independence Day", "Public"),
    ("2026-09-05", "Teachers' Day", "Institutional"),
    ("2026-10-02", "Gandhi Jayanti", "Public"),
    ("2026-10-20", "Diwali", "Public"),
    ("2026-10-21", "Govardhan Puja", "Public"),
    ("2026-10-23", "Bhai Dooj", "Public"),
    ("2026-11-25", "Guru Nanak Jayanti", "Public"),
    ("2026-12-25", "Christmas", "Public"),
    ("2027-01-26", "Republic Day", "Public"),
    ("2027-03-04", "Holi", "Public"),
    ("2027-03-25", "Id-ul-Fitr", "Public"),
]

# Teaching periods used to build the weekly timetable.
PERIODS = [("09:30", "10:20"), ("10:20", "11:10"), ("11:20", "12:10"),
           ("12:10", "13:00"), ("13:40", "14:30"), ("14:30", "15:20")]


def _mobile() -> str:
    return f"{RNG.choice('6789')}{RNG.randint(100000000, 999999999)}"


def _address() -> str:
    return (f"{RNG.randint(1, 220)}, {RNG.choice(AREAS)}, "
            f"{RNG.choice(TOWNS)}, Shamli, Uttar Pradesh")


def _email(name: str, identifier: str) -> str:
    return f"{name.split()[0].lower()}.{identifier.lower()}@ggpshamli.ac.in"


def seed_database(force: bool = False) -> bool:
    """Populate a fresh database.  Returns True when seeding actually ran."""
    db = get_db()

    if not force and not db.is_empty():
        logger.info("Database already contains data; skipping seed.")
        return False

    logger.info("Seeding database for %s...", COLLEGE["college_name"])

    # ---------------------------------------------------------- branding
    config.update(COLLEGE)

    # ---------------------------------------------------------------- course
    course_id = db.insert("courses", {
        "course_name": "Diploma", "course_code": "DIP",
        "duration_years": 3, "total_semesters": 6,
        "description": ("Three-year diploma in engineering and technology, "
                        "admissions through BTEUP counselling"),
    })

    semester_ids: dict[int, int] = {}
    for number in range(1, 7):
        semester_ids[number] = db.insert("semesters", {
            "course_id": course_id, "semester_number": number,
            "semester_name": f"Semester {number}",
        })

    # -------------------------------------------------------------- branches
    branch_ids: dict[str, int] = {}
    for name, code, hod, intake in BRANCHES:
        branch_ids[code] = db.insert("branches", {
            "course_id": course_id, "branch_name": name, "branch_code": code,
            "hod_name": hod, "intake_capacity": intake,
        })

    # -------------------------------------------------------------- sections
    # Deliberately none.  Sections are optional; the administrator creates them
    # from Academic Setup if the college starts dividing its classes.
    section_ids: dict[str, int] = {}

    # -------------------------------------------------------------- sessions
    session_ids: dict[str, int] = {}
    for name, start, end, current in (
            ("2025-26", "2025-07-01", "2026-06-30", 0),
            ("2026-27", "2026-07-01", "2027-06-30", 1),
            ("2027-28", "2027-07-01", "2028-06-30", 0)):
        session_ids[name] = db.insert("academic_sessions", {
            "session_name": name, "start_date": start, "end_date": end,
            "is_current": current,
        })
    current_session_id = session_ids["2026-27"]
    config.set("current_academic_session", "2026-27")

    # --------------------------------------------------------------- batches
    batch_ids: dict[str, int] = {}
    for start_year in (2024, 2025, 2026, 2027):
        name = f"{start_year}-{start_year + 3}"
        batch_ids[name] = db.insert("batches", {
            "batch_name": name, "start_year": start_year, "end_year": start_year + 3,
        })

    # --------------------------------------------------------------- faculty
    faculty_ids: dict[str, int] = {}
    for code, name, gender, qualification, designation, experience, branch, mobile in FACULTY:
        faculty_ids[code] = db.insert("faculty", {
            "faculty_code": code, "full_name": name, "gender": gender,
            "qualification": qualification, "designation": designation,
            "experience_years": experience,
            "mobile": mobile or _mobile(),
            "email": _email(name.replace("Mr. ", "").replace("Ms. ", "")
                            .replace("Dr. ", ""), code),
            "address": _address(),
            "branch_id": branch_ids[branch],
            "joining_date": f"{2026 - int(experience)}-07-15",
            "status": "Active",
        })

    # -------------------------------------------------------------- subjects
    subject_ids: dict[str, int] = {}
    for code, name, branch, semester_number, kind, credits, faculty_code in SUBJECTS:
        subject_ids[code] = db.insert("subjects", {
            "subject_code": code, "subject_name": name, "course_id": course_id,
            "branch_id": branch_ids[branch],
            "semester_id": semester_ids[semester_number],
            "faculty_id": faculty_ids.get(faculty_code),
            "subject_type": kind, "credits": credits, "total_classes": 60,
        })

    # -------------------------------------------------------------- students
    students = _seed_students(db, course_id, branch_ids, semester_ids,
                              batch_ids, current_session_id)

    # -------------------------------------------------------- class teachers
    _seed_class_teachers(db, branch_ids, semester_ids, faculty_ids,
                         current_session_id)

    # ------------------------------------------------------------- timetable
    _seed_timetable(db, branch_ids, semester_ids, subject_ids, current_session_id)

    # -------------------------------------------------------------- holidays
    for holiday_date, name, kind in HOLIDAYS:
        db.insert("holidays", {
            "holiday_date": holiday_date, "holiday_name": name,
            "holiday_type": kind, "session_id": current_session_id,
        })

    # ----------------------------------------------------------------- users
    _seed_users(db, students, faculty_ids)

    # ------------------------------------------------------------ attendance
    _seed_attendance(db, students, subject_ids, branch_ids, semester_ids,
                     current_session_id)

    # ----------------------------------------------------------------- leave
    _seed_leaves(db, students, current_session_id)

    logger.info("Seeding complete: %d students, %d faculty, %d subjects",
                len(students), len(faculty_ids), len(subject_ids))
    return True


# ---------------------------------------------------------------------------
def _seed_students(db, course_id, branch_ids, semester_ids, batch_ids,
                   session_id) -> list[dict]:
    """Create students across the three branches, all female."""
    students: list[dict] = []
    used_names: set[str] = set()

    # (branch_code, semester_number, count) -- sized against sanctioned intake.
    cohorts = [
        ("CSE", 3, 42), ("CSE", 4, 45), ("CSE", 5, 38), ("CSE", 6, 33),
        ("EE",  3, 22), ("EE",  4, 24), ("EE",  5, 19),
        ("FD",  3, 34), ("FD",  4, 36), ("FD",  5, 28),
    ]

    serial_by_branch: dict[str, int] = {}

    for branch_code, semester_number, count in cohorts:
        admission_year = 2026 - ((semester_number - 1) // 2)
        batch_name = f"{admission_year}-{admission_year + 3}"

        for roll in range(1, count + 1):
            for _ in range(40):
                full_name = f"{RNG.choice(FIRST_NAMES)} {RNG.choice(SURNAMES)}"
                if full_name not in used_names:
                    used_names.add(full_name)
                    break

            serial_by_branch[branch_code] = serial_by_branch.get(branch_code, 0) + 1
            enrollment = (f"D{branch_code}{str(admission_year)[-2:]}"
                          f"{serial_by_branch[branch_code]:03d}")

            birth_year = admission_year - RNG.randint(17, 19)
            dob = date(birth_year, RNG.randint(1, 12), RNG.randint(1, 28))

            student_id = db.insert("students", {
                "enrollment_no": enrollment,
                "roll_no": str(roll),
                "full_name": full_name,
                "father_name": f"{RNG.choice(FATHER_NAMES)} {full_name.split()[-1]}",
                "mother_name": f"{RNG.choice(MOTHER_NAMES)} {full_name.split()[-1]}",
                "gender": "Female",
                "dob": dob.isoformat(),
                "mobile": _mobile(),
                "email": _email(full_name, enrollment),
                "address": _address(),
                "admission_date": f"{admission_year}-08-{RNG.randint(1, 20):02d}",
                "course_id": course_id,
                "branch_id": branch_ids[branch_code],
                "semester_id": semester_ids[semester_number],
                "section_id": None,          # sections are optional
                "batch_id": batch_ids.get(batch_name),
                "session_id": session_id,
                "face_registered": 0,
                "status": "Active",
            })

            students.append({
                "student_id": student_id, "enrollment_no": enrollment,
                "full_name": full_name, "branch_code": branch_code,
                "semester_number": semester_number,
                "branch_id": branch_ids[branch_code],
                "semester_id": semester_ids[semester_number],
            })

    return students


def _seed_class_teachers(db, branch_ids, semester_ids, faculty_ids,
                         session_id) -> None:
    """Give every taught class a class teacher, so leave always has a route."""
    assignments = [
        ("CSE", 3, "FAC002"), ("CSE", 4, "FAC004"),
        ("CSE", 5, "FAC003"), ("CSE", 6, "FAC001"),
        ("EE",  3, "FAC008"), ("EE",  4, "FAC007"), ("EE",  5, "FAC006"),
        ("FD",  3, "FAC010"), ("FD",  4, "FAC010"), ("FD",  5, "FAC010"),
    ]
    for branch_code, semester_number, faculty_code in assignments:
        db.insert("class_teachers", {
            "branch_id": branch_ids[branch_code],
            "semester_id": semester_ids[semester_number],
            "section_id": None,
            "faculty_id": faculty_ids[faculty_code],
            "session_id": session_id,
        })


def _seed_timetable(db, branch_ids, semester_ids, subject_ids, session_id) -> None:
    """Build a weekly schedule for every class that has subjects."""
    days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")

    # Group subjects by (branch, semester).
    by_class: dict[tuple[str, int], list[str]] = {}
    for code, _name, branch, semester_number, *_rest in SUBJECTS:
        by_class.setdefault((branch, semester_number), []).append(code)

    room_counter = 101
    for (branch_code, semester_number), codes in by_class.items():
        room = f"CR-{room_counter}"
        room_counter += 1

        for day_index, day in enumerate(days):
            # Saturday is a half day.
            slots = PERIODS[:4] if day == "Saturday" else PERIODS

            for period_index, (start, end) in enumerate(slots):
                # Rotate through the subject list so each gets a fair share.
                subject_code = codes[(period_index + day_index) % len(codes)]
                subject_id = subject_ids[subject_code]

                db.insert("timetable", {
                    "day_of_week": day, "start_time": start, "end_time": end,
                    "branch_id": branch_ids[branch_code],
                    "semester_id": semester_ids[semester_number],
                    "section_id": None,
                    "subject_id": subject_id,
                    "faculty_id": db.fetch_value(
                        "SELECT faculty_id FROM subjects WHERE subject_id = ?",
                        (subject_id,)),
                    "room_no": ("Lab-1" if "Lab" in subject_code
                                or period_index >= 4 else room),
                    "session_id": session_id,
                })


def _seed_users(db, students, faculty_ids) -> None:
    """Create login accounts: one admin, one per faculty, one per student."""
    db.insert("users", {
        "username": "admin",
        "password_hash": hash_password(DEFAULT_PASSWORDS[ROLE_ADMIN]),
        "role": ROLE_ADMIN, "full_name": "System Administrator",
        "email": config.get("college_email", ""), "must_change_pw": 0,
    })

    for code, faculty_id in faculty_ids.items():
        row = db.fetch_one("SELECT full_name, email FROM faculty WHERE faculty_id = ?",
                           (faculty_id,))
        db.insert("users", {
            "username": code.lower(),
            "password_hash": hash_password(DEFAULT_PASSWORDS[ROLE_FACULTY]),
            "role": ROLE_FACULTY, "full_name": row["full_name"],
            "email": row["email"], "linked_id": faculty_id, "must_change_pw": 0,
        })

    for student in students:
        db.insert("users", {
            "username": student["enrollment_no"].lower(),
            "password_hash": hash_password(DEFAULT_PASSWORDS[ROLE_STUDENT]),
            "role": ROLE_STUDENT, "full_name": student["full_name"],
            "linked_id": student["student_id"], "must_change_pw": 0,
        })


def _seed_attendance(db, students, subject_ids, branch_ids, semester_ids,
                     session_id) -> None:
    """Generate ~8 weeks of attendance history for the demo classes.

    Each student gets a persistent "diligence" score, so an individual is
    consistently strong or consistently weak across the whole term -- which is
    what makes the defaulter list and the trend charts believable.
    """
    holidays = {h["holiday_date"] for h in db.fetch_all("SELECT holiday_date FROM holidays")}

    diligence: dict[int, float] = {}
    for student in students:
        roll = RNG.random()
        if roll < 0.10:
            diligence[student["student_id"]] = RNG.uniform(0.45, 0.66)   # defaulters
        elif roll < 0.28:
            diligence[student["student_id"]] = RNG.uniform(0.68, 0.79)   # borderline
        else:
            diligence[student["student_id"]] = RNG.uniform(0.82, 0.98)   # regular

    # Classes with attendance history, and the subjects tracked for each.
    tracked = [
        ("CSE", 4, ["CS401", "CS402", "CS403", "CS404", "CS405"]),
        ("CSE", 3, ["CS301", "CS302", "CS304"]),
        ("CSE", 5, ["CS501", "CS502"]),
        ("EE",  4, ["EE401", "EE402", "EE404"]),
        ("EE",  3, ["EE301", "EE302"]),
        ("FD",  4, ["FD401", "FD402"]),
        ("FD",  3, ["FD301", "FD302"]),
    ]

    today = date.today()
    start_date = today - timedelta(days=56)

    for branch_code, semester_number, codes in tracked:
        roster = [s for s in students
                  if s["branch_code"] == branch_code
                  and s["semester_number"] == semester_number]
        if not roster:
            continue

        current = start_date
        while current <= today:
            # No classes on Sundays or configured holidays.
            if current.weekday() == 6 or current.isoformat() in holidays:
                current += timedelta(days=1)
                continue

            for period_index, subject_code in enumerate(codes):
                if RNG.random() < 0.28:          # not every subject meets daily
                    continue

                start_time, end_time = PERIODS[period_index % len(PERIODS)]
                subject_id = subject_ids[subject_code]
                faculty_id = db.fetch_value(
                    "SELECT faculty_id FROM subjects WHERE subject_id = ?", (subject_id,))

                att_session_id = db.insert("attendance_sessions", {
                    "subject_id": subject_id, "faculty_id": faculty_id,
                    "branch_id": branch_ids[branch_code],
                    "semester_id": semester_ids[semester_number],
                    "section_id": None,
                    "session_id": session_id,
                    "class_date": current.isoformat(),
                    "start_time": start_time, "end_time": end_time,
                    "mode": "Face Recognition" if RNG.random() < 0.75 else "Manual",
                    "total_students": len(roster),
                    "is_locked": 1 if (today - current).days > 7 else 0,
                    "created_by": 1,
                })

                rows, present_count = [], 0
                for student in roster:
                    score = diligence[student["student_id"]]
                    draw = RNG.random()

                    if draw < score * 0.93:
                        status, method = "Present", "Face Recognition"
                        present_count += 1
                    elif draw < score * 0.99:
                        status, method = "Late", "Face Recognition"
                        present_count += 1
                    elif draw < score * 0.99 + 0.035:
                        status, method = "Leave", "Leave System"
                    elif draw < score * 0.99 + 0.05:
                        status, method = "Medical Leave", "Leave System"
                    else:
                        status, method = "Absent", "Auto (Absent)"

                    confidence = (round(RNG.uniform(72, 97), 1)
                                  if method == "Face Recognition" else None)
                    marked_time = (f"{start_time}:{RNG.randint(10, 59):02d}"
                                   if status in ("Present", "Late") else None)

                    rows.append((att_session_id, student["student_id"], subject_id,
                                 current.isoformat(), status, method, confidence,
                                 marked_time))

                db.executemany(
                    """INSERT INTO attendance
                       (att_session_id, student_id, subject_id, class_date,
                        status, marked_method, confidence, marked_time)
                       VALUES (?,?,?,?,?,?,?,?)""", rows)

                db.execute(
                    "UPDATE attendance_sessions SET present_count = ?, absent_count = ? "
                    "WHERE att_session_id = ?",
                    (present_count, len(roster) - present_count, att_session_id))

            current += timedelta(days=1)


def _seed_leaves(db, students, session_id) -> None:
    """A spread of leave applications in every workflow state."""
    reasons = {
        "Casual Leave": "Attending a family function out of station.",
        "Medical Leave": "Suffering from viral fever; advised rest by the doctor.",
        "Sports Leave": "Selected for the inter-polytechnic athletics championship.",
        "Official Leave": "Representing the college at a technical symposium.",
        "Academic Leave": "Attending an industrial training workshop.",
        "Other Leave": "Required at home for an urgent personal matter.",
    }

    sample = RNG.sample(students, min(30, len(students)))
    today = date.today()

    for index, student in enumerate(sample):
        leave_type = RNG.choice(LEAVE_TYPES)
        total_days = RNG.randint(1, 6)
        from_date = today - timedelta(days=RNG.randint(-14, 45))
        to_date = from_date + timedelta(days=total_days - 1)

        # Match the workflow rules: long medical leave carries a certificate.
        needs_certificate = leave_type == "Medical Leave" and total_days > 3
        document_path = None
        if needs_certificate or (leave_type == "Medical Leave" and RNG.random() < 0.5):
            document_path = (f"storage/medical_certificates/"
                             f"{student['full_name'].replace(' ', '_')}_"
                             f"{student['enrollment_no']}_"
                             f"{from_date.isoformat()}_{to_date.isoformat()}.pdf")

        if index % 5 == 0:
            status, reviewed, remarks = "Pending", None, None
        elif index % 5 == 1:
            status, reviewed, remarks = "Rejected", 2, "Insufficient supporting documents."
        elif index % 5 == 2:
            status, reviewed, remarks = ("Returned for Correction", 2,
                                         "Please attach the medical certificate and resubmit.")
        else:
            status, reviewed, remarks = "Approved", 2, "Approved. Attendance updated."

        db.insert("leave_applications", {
            "student_id": student["student_id"], "leave_type": leave_type,
            "from_date": from_date.isoformat(), "to_date": to_date.isoformat(),
            "total_days": total_days,
            "reason": reasons.get(leave_type, "Personal reasons."),
            "document_path": document_path, "status": status,
            "applied_on": (from_date - timedelta(days=RNG.randint(1, 5))
                           ).strftime("%Y-%m-%d %H:%M:%S"),
            "reviewed_by": reviewed,
            "reviewed_on": (datetime.now() - timedelta(days=RNG.randint(1, 10))
                            ).strftime("%Y-%m-%d %H:%M:%S") if reviewed else None,
            "review_remarks": remarks,
            "attendance_applied": 1 if status == "Approved" else 0,
            "session_id": session_id,
        })
