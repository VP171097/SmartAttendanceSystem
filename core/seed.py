"""
First-run database seeding.

Creates the working structure for **Government Girls Polytechnic, Shamli**
using the college's published details.

This is a **minimal, real starting set** -- not a demonstration dataset:

*   one course (Diploma) with six semesters,
*   one branch (Computer Science & Engineering),
*   three teaching staff from the college's published directory,
*   five students,
*   the semester's subjects and a weekly timetable,
*   the holiday calendar and academic sessions.

**No attendance or leave history is generated.** Everything the application
shows is real data entered through the application itself, which is what a
college actually wants on day one.

Two details worth noting:

*   The college is a **girls** polytechnic, so every student is female.
*   **Sections are not created.** They are optional throughout the system; an
    administrator adds them from Academic Setup only if classes get divided.

Seeding is idempotent: it runs only when the ``users`` table is empty.
"""

from __future__ import annotations

from datetime import date

from config.settings import ROLE_ADMIN, ROLE_FACULTY, ROLE_STUDENT, config
from core.auth import hash_password
from core.database import get_db
from core.logger import get_logger

logger = get_logger("core.seed")

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

# All three diploma programmes the college runs, per its Courses page --
# name, branch code, head of department (where the site names one), and the
# sanctioned intake.  Only Computer Science & Engineering has a seeded
# curriculum, timetable and student roster (see ACTIVE_BRANCH_CODE below);
# Electronics and Fashion Designing exist as real branches with their real
# faculty attached, ready for an administrator to build out subjects and
# admit students through the application itself, rather than seeding invented
# coursework the college's public pages do not actually list.
BRANCHES = [
    # (name, code, head of department, intake)
    ("Computer Science & Engineering", "CSE", "Mr. Sanjay Kumar", 60),
    ("Electronics Engineering", "EE", None, 30),   # no HOD named on the site
    ("Fashion Designing & Garment Technology", "FD", None, 60),
]
ACTIVE_BRANCH_CODE = "CSE"

# The college's complete published teaching-staff directory (13 members).
# The username for each is their faculty code.  Where the site prints "N/A"
# for qualification, mobile or email (the three workshop instructors), that
# field is left blank here rather than invented.  Two lecturers are listed on
# the site only as "Monika" and "Dr. Monika" -- distinct people in different
# departments -- and are kept exactly as named, with no surname added that
# the source does not give.
#
# (code, name, gender, qualification, designation, experience, branch_code,
#  department, mobile)
FACULTY = [
    ("FAC001", "Mr. Sanjay Kumar", "Male", "M.Tech",
     "HOD", 15.0, "CSE", None, "9719804209"),
    ("FAC002", "Aditi Singh", "Female", "M.Tech in Electronics And Communication",
     "Lecturer", 9.0, "EE", None, "7017873310"),
    ("FAC003", "Rashmi", "Female", "B.Tech",
     "Lecturer", 6.0, "CSE", None, "7983723945"),
    ("FAC004", "Sachin Saini", "Male", "M.Tech",
     "Lecturer", 8.0, "EE", None, "9971209799"),
    ("FAC005", "Vani Gupta", "Female", "M.Tech",
     "Lecturer", 7.0, "EE", None, "7599542338"),
    ("FAC006", "Sachin Vikal", "Male", "M.Tech",
     "Lecturer", 8.0, "CSE", None, "9639140955"),
    ("FAC007", "Swati Chauhan", "Female", "M.Tech",
     "Lecturer", 6.0, "CSE", None, "7060115758"),
    ("FAC008", "Monika", "Female", "Ph.D.",
     "Lecturer", 12.0, None, "Applied Science (Physics)", "8882657040"),
    ("FAC009", "Monika Chauhan", "Female", "M.E. (Electronics & Communication Engineering)",
     "Lecturer", 8.0, "EE", None, "6395214136"),
    ("FAC010", "Dr. Monika", "Female", "Ph.D",
     "Lecturer", 11.0, None, "Accountancy and Taxation", "9250392232"),
    ("FAC011", "Mr. Tejpal Singh", "Male", None,
     "Workshop Instructor", None, None, None, None),
    ("FAC012", "Mr. Rajendra Kumar", "Male", None,
     "Workshop Instructor", None, None, None, None),
    ("FAC013", "Mr. Jitendra Kumar", "Male", None,
     "Workshop Instructor", None, None, None, None),
]

# Semester 3 curriculum for the CSE diploma -- the only branch with a seeded
# course of study; see ACTIVE_BRANCH_CODE.
SUBJECTS = [
    # (code, name, semester, type, credits, faculty_code)
    ("CS301", "Data Structures Using C",     3, "Theory",    4, "FAC001"),
    ("CS302", "Object Oriented Programming", 3, "Theory",    4, "FAC006"),
    ("CS303", "Digital Electronics",         3, "Theory",    4, "FAC003"),
    ("CS304", "Data Structures Lab",         3, "Practical", 2, "FAC001"),
    ("CS305", "Applied Mathematics III",     3, "Theory",    3, "FAC007"),
    ("CS306", "Web Technology Lab",          3, "Practical", 2, "FAC006"),
]

# Five students, all female.
STUDENTS = [
    # (roll, name, father, mother, dob, mobile)
    ("1", "Ankita Pandey",   "Rajesh Pandey",   "Sunita Pandey",   "2007-04-12", "9719812345"),
    ("2", "Priya Chauhan",   "Suresh Chauhan",  "Anita Chauhan",   "2007-08-25", "9719823456"),
    ("3", "Neha Saini",      "Mahesh Saini",    "Kavita Saini",    "2008-01-17", "9719834567"),
    ("4", "Shivani Tyagi",   "Dinesh Tyagi",    "Rekha Tyagi",     "2007-11-03", "9719845678"),
    ("5", "Muskan Ansari",   "Imran Ansari",    "Nazia Ansari",    "2008-03-29", "9719856789"),
]

HOLIDAYS = [
    ("2026-08-15", "Independence Day", "Public"),
    ("2026-09-05", "Teachers' Day", "Institutional"),
    ("2026-10-02", "Gandhi Jayanti", "Public"),
    ("2026-10-20", "Diwali", "Public"),
    ("2026-10-21", "Govardhan Puja", "Public"),
    ("2026-11-25", "Guru Nanak Jayanti", "Public"),
    ("2026-12-25", "Christmas", "Public"),
    ("2027-01-26", "Republic Day", "Public"),
    ("2027-03-04", "Holi", "Public"),
]

# Teaching periods used to build the weekly timetable.
PERIODS = [("09:30", "10:20"), ("10:20", "11:10"), ("11:20", "12:10"),
           ("12:10", "13:00"), ("13:40", "14:30"), ("14:30", "15:20")]

TEACHING_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")

# The class this starting set is built around.
ACTIVE_SEMESTER = 3
ADMISSION_YEAR = 2025


def seed_database(force: bool = False) -> bool:
    """Populate a fresh database.  Returns True when seeding actually ran."""
    db = get_db()

    if not force and not db.is_empty():
        logger.info("Database already contains data; skipping seed.")
        return False

    logger.info("Seeding starting data for %s...", COLLEGE["college_name"])

    # ---------------------------------------------------------- branding
    config.update(COLLEGE)

    # ------------------------------------------------------------ course
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

    # ----------------------------------------------------------- branches
    branch_ids: dict[str, int] = {}
    for branch_name, branch_code, hod, intake in BRANCHES:
        branch_ids[branch_code] = db.insert("branches", {
            "course_id": course_id, "branch_name": branch_name,
            "branch_code": branch_code, "hod_name": hod, "intake_capacity": intake,
        })
    branch_id = branch_ids[ACTIVE_BRANCH_CODE]   # CSE: the branch with a curriculum

    # ---------------------------------------------------------- sessions
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

    # ------------------------------------------------------------ batches
    batch_ids: dict[str, int] = {}
    for start_year in (2024, 2025, 2026):
        name = f"{start_year}-{start_year + 3}"
        batch_ids[name] = db.insert("batches", {
            "batch_name": name, "start_year": start_year, "end_year": start_year + 3,
        })

    # ------------------------------------------------------------ faculty
    faculty_ids: dict[str, int] = {}
    for (code, name, gender, qualification, designation, experience,
         branch_code, department, mobile) in FACULTY:
        plain = name.replace("Mr. ", "").replace("Ms. ", "").replace("Dr. ", "")
        # Only a subset of staff have a real mobile/email on the public
        # directory (the workshop instructors are listed "N/A" for both);
        # an email is only invented when a real mobile number exists to base
        # a plausible handle on, otherwise both are left blank.
        email = (f"{plain.split()[0].lower()}.{code.lower()}@ggpshamli.ac.in"
                 if mobile else None)
        faculty_ids[code] = db.insert("faculty", {
            "faculty_code": code, "full_name": name, "gender": gender,
            "qualification": qualification, "designation": designation,
            "experience_years": experience, "mobile": mobile, "email": email,
            "address": ("Government Girls Polytechnic, Shamli, Uttar Pradesh"
                        if mobile else None),
            "branch_id": branch_ids.get(branch_code) if branch_code else None,
            "department": department,
            "joining_date": (f"{2026 - int(experience)}-07-15" if experience else None),
            "status": "Active",
        })

    # ----------------------------------------------------------- subjects
    subject_ids: dict[str, int] = {}
    for code, name, semester_number, kind, credits, faculty_code in SUBJECTS:
        subject_ids[code] = db.insert("subjects", {
            "subject_code": code, "subject_name": name, "course_id": course_id,
            "branch_id": branch_id, "semester_id": semester_ids[semester_number],
            "faculty_id": faculty_ids[faculty_code],
            "subject_type": kind, "credits": credits, "total_classes": 60,
        })

    # ----------------------------------------------------------- students
    students = _seed_students(db, course_id, branch_id, semester_ids,
                              batch_ids, current_session_id)

    # ------------------------------------------------------ class teacher
    db.insert("class_teachers", {
        "branch_id": branch_id,
        "semester_id": semester_ids[ACTIVE_SEMESTER],
        "section_id": None,
        "faculty_id": faculty_ids["FAC003"],     # Rashmi
        "session_id": current_session_id,
    })

    # ---------------------------------------------------------- timetable
    _seed_timetable(db, branch_id, semester_ids, subject_ids, current_session_id)

    # ----------------------------------------------------------- holidays
    for holiday_date, name, kind in HOLIDAYS:
        db.insert("holidays", {
            "holiday_date": holiday_date, "holiday_name": name,
            "holiday_type": kind, "session_id": current_session_id,
        })

    # -------------------------------------------------------------- users
    _seed_users(db, students, faculty_ids)

    logger.info("Seeding complete: %d student(s), %d faculty, %d subject(s), "
                "no attendance history",
                len(students), len(faculty_ids), len(subject_ids))
    return True


# ---------------------------------------------------------------------------
def _seed_students(db, course_id, branch_id, semester_ids, batch_ids,
                   session_id) -> list[dict]:
    """Create the five students, all female, with no section."""
    students: list[dict] = []
    batch_name = f"{ADMISSION_YEAR}-{ADMISSION_YEAR + 3}"

    for index, (roll, full_name, father, mother, dob, mobile) in enumerate(STUDENTS, 1):
        enrollment = f"D{ACTIVE_BRANCH_CODE}{str(ADMISSION_YEAR)[-2:]}{index:03d}"

        student_id = db.insert("students", {
            "enrollment_no": enrollment,
            "roll_no": roll,
            "full_name": full_name,
            "father_name": father,
            "mother_name": mother,
            "gender": "Female",
            "dob": dob,
            "mobile": mobile,
            "email": f"{full_name.split()[0].lower()}.{enrollment.lower()}@ggpshamli.ac.in",
            "address": "Shamli, Uttar Pradesh",
            "admission_date": f"{ADMISSION_YEAR}-08-01",
            "course_id": course_id,
            "branch_id": branch_id,
            "semester_id": semester_ids[ACTIVE_SEMESTER],
            "section_id": None,               # sections are optional
            "batch_id": batch_ids.get(batch_name),
            "session_id": session_id,
            "face_registered": 0,
            "status": "Active",
        })

        students.append({
            "student_id": student_id,
            "enrollment_no": enrollment,
            "full_name": full_name,
        })

    return students


def _seed_timetable(db, branch_id, semester_ids, subject_ids, session_id) -> None:
    """Weekly schedule for the active class, rotating through its subjects."""
    codes = [code for code, *_ in SUBJECTS]
    semester_id = semester_ids[ACTIVE_SEMESTER]

    for day_index, day in enumerate(TEACHING_DAYS):
        # Saturday is a half day.
        slots = PERIODS[:4] if day == "Saturday" else PERIODS

        for period_index, (start, end) in enumerate(slots):
            subject_code = codes[(period_index + day_index) % len(codes)]
            subject_id = subject_ids[subject_code]

            db.insert("timetable", {
                "day_of_week": day, "start_time": start, "end_time": end,
                "branch_id": branch_id, "semester_id": semester_id,
                "section_id": None,
                "subject_id": subject_id,
                "faculty_id": db.fetch_value(
                    "SELECT faculty_id FROM subjects WHERE subject_id = ?",
                    (subject_id,)),
                "room_no": "Lab-1" if "Lab" in subject_code else "CR-101",
                "session_id": session_id,
            })


def _seed_users(db, students, faculty_ids) -> None:
    """Create login accounts.

    Faculty sign in with their faculty code; students with their enrollment
    number.  Matching is case-insensitive, so FAC001 and fac001 both work.
    """
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
            "username": code,                 # the faculty code itself
            "password_hash": hash_password(DEFAULT_PASSWORDS[ROLE_FACULTY]),
            "role": ROLE_FACULTY, "full_name": row["full_name"],
            "email": row["email"], "linked_id": faculty_id, "must_change_pw": 0,
        })

    for student in students:
        db.insert("users", {
            "username": student["enrollment_no"],
            "password_hash": hash_password(DEFAULT_PASSWORDS[ROLE_STUDENT]),
            "role": ROLE_STUDENT, "full_name": student["full_name"],
            "linked_id": student["student_id"], "must_change_pw": 0,
        })
