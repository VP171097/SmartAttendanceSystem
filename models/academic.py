"""
Academic structure repository -- courses, branches, semesters, sections,
academic sessions, batches and the holiday calendar.

Everything here is data, not code: the administrator can add a new course or a
seventh semester from the UI and the rest of the application picks it up
without a source change.  Deletes are refused when dependent records exist, so
the structure can never be left dangling.
"""

from __future__ import annotations

from datetime import date, datetime

from core.audit import ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE, log_audit
from core.database import get_db
from core.logger import get_logger

logger = get_logger("models.academic")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ===========================================================================
# Courses
# ===========================================================================
def get_courses(active_only: bool = True) -> list:
    where = "WHERE is_active = 1" if active_only else ""
    return get_db().fetch_all(f"SELECT * FROM courses {where} ORDER BY course_name")


def get_course(course_id: int):
    return get_db().fetch_one("SELECT * FROM courses WHERE course_id = ?", (course_id,))


def add_course(name: str, code: str, duration_years: int = 3,
               total_semesters: int = 6, description: str = "",
               sections: str = "", user: dict | None = None
               ) -> tuple[bool, str, int | None]:
    """Create a course and its semesters.

    Args:
        sections: optional comma-separated section names to create alongside
            the course, e.g. ``"A, B"``.  Sections are entirely optional -- a
            college that does not divide its classes leaves this blank and the
            rest of the system works with no section at all.
    """
    db = get_db()
    if db.exists("courses", "LOWER(course_name) = LOWER(?)", (name,)):
        return False, f"A course named '{name}' already exists.", None
    if db.exists("courses", "LOWER(course_code) = LOWER(?)", (code,)):
        return False, f"Course code '{code}' is already in use.", None

    course_id = db.insert("courses", {
        "course_name": name.strip(), "course_code": code.strip().upper(),
        "duration_years": duration_years, "total_semesters": total_semesters,
        "description": description.strip() or None,
    })

    # A course is useless without semesters -- generate them automatically.
    for number in range(1, int(total_semesters) + 1):
        db.insert("semesters", {
            "course_id": course_id, "semester_number": number,
            "semester_name": f"Semester {number}",
        })

    created_sections = create_sections_from_text(sections, user)

    log_audit(user, ACTION_CREATE, "Academic", "courses", course_id,
              new_value={"course_name": name, "course_code": code,
                         "sections": created_sections})

    message = f"Course '{name}' added with {total_semesters} semesters."
    if created_sections:
        message += f" Sections created: {', '.join(created_sections)}."
    return True, message, course_id


def create_sections_from_text(text: str, user: dict | None = None) -> list[str]:
    """Create any sections named in a comma-separated string.

    Existing sections are skipped silently, so re-running with "A, B" when A
    already exists simply adds B.
    """
    created = []
    for raw in str(text or "").replace("/", ",").split(","):
        name = raw.strip().upper()
        if not name:
            continue
        ok, _, _ = add_section(name, user=user)
        if ok:
            created.append(name)
    return created


def update_course(course_id: int, data: dict, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    old = get_course(course_id)
    if old is None:
        return False, "Course not found."
    if "course_name" in data and db.exists(
            "courses", "LOWER(course_name) = LOWER(?) AND course_id <> ?",
            (data["course_name"], course_id)):
        return False, "Another course already uses that name."

    db.update("courses", data, "course_id = ?", (course_id,))
    log_audit(user, ACTION_UPDATE, "Academic", "courses", course_id,
              old_value=dict(old), new_value=data)
    return True, "Course updated."


def delete_course(course_id: int, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    students = db.count("students", "course_id = ?", (course_id,))
    if students:
        return False, f"Cannot delete: {students} student(s) belong to this course."
    branches = db.count("branches", "course_id = ?", (course_id,))
    if branches:
        return False, f"Cannot delete: {branches} branch(es) belong to this course."

    old = get_course(course_id)
    db.delete("courses", "course_id = ?", (course_id,))
    log_audit(user, ACTION_DELETE, "Academic", "courses", course_id,
              old_value=dict(old) if old else None)
    return True, "Course deleted."


# ===========================================================================
# Branches
# ===========================================================================
def get_branches(course_id: int | None = None, active_only: bool = True,
                 search: str = "") -> list:
    clauses, params = ["1=1"], []
    if course_id:
        clauses.append("b.course_id = ?")
        params.append(course_id)
    if active_only:
        clauses.append("b.is_active = 1")
    if search:
        clauses.append("(b.branch_name LIKE ? OR b.branch_code LIKE ? OR b.hod_name LIKE ?)")
        params.extend([f"%{search}%"] * 3)

    return get_db().fetch_all(
        f"""SELECT b.*, c.course_name,
                   (SELECT COUNT(*) FROM students s
                    WHERE s.branch_id = b.branch_id AND s.status = 'Active') AS student_count,
                   (SELECT COUNT(*) FROM faculty f WHERE f.branch_id = b.branch_id) AS faculty_count
            FROM branches b
            JOIN courses c ON c.course_id = b.course_id
            WHERE {' AND '.join(clauses)}
            ORDER BY b.branch_name""", params)


def get_branch(branch_id: int):
    return get_db().fetch_one(
        "SELECT b.*, c.course_name FROM branches b "
        "JOIN courses c ON c.course_id = b.course_id WHERE b.branch_id = ?", (branch_id,))


def add_branch(course_id: int, name: str, code: str, hod_name: str = "",
               intake: int = 60, user: dict | None = None) -> tuple[bool, str, int | None]:
    db = get_db()
    if db.exists("branches", "course_id = ? AND LOWER(branch_code) = LOWER(?)",
                 (course_id, code)):
        return False, f"Branch code '{code}' already exists for this course.", None
    if db.exists("branches", "course_id = ? AND LOWER(branch_name) = LOWER(?)",
                 (course_id, name)):
        return False, f"Branch '{name}' already exists for this course.", None

    branch_id = db.insert("branches", {
        "course_id": course_id, "branch_name": name.strip(),
        "branch_code": code.strip().upper(), "hod_name": hod_name.strip() or None,
        "intake_capacity": intake,
    })
    log_audit(user, ACTION_CREATE, "Academic", "branches", branch_id,
              new_value={"branch_name": name, "branch_code": code})
    return True, f"Branch '{name}' added.", branch_id


def update_branch(branch_id: int, data: dict, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    old = get_branch(branch_id)
    if old is None:
        return False, "Branch not found."
    if "branch_code" in data and db.exists(
            "branches", "course_id = ? AND LOWER(branch_code) = LOWER(?) AND branch_id <> ?",
            (old["course_id"], data["branch_code"], branch_id)):
        return False, "Another branch already uses that code."

    db.update("branches", data, "branch_id = ?", (branch_id,))
    log_audit(user, ACTION_UPDATE, "Academic", "branches", branch_id,
              old_value=dict(old), new_value=data)
    return True, "Branch updated."


def delete_branch(branch_id: int, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    students = db.count("students", "branch_id = ?", (branch_id,))
    if students:
        return False, f"Cannot delete: {students} student(s) are enrolled in this branch."
    subjects = db.count("subjects", "branch_id = ?", (branch_id,))
    if subjects:
        return False, f"Cannot delete: {subjects} subject(s) are mapped to this branch."

    old = get_branch(branch_id)
    db.delete("branches", "branch_id = ?", (branch_id,))
    log_audit(user, ACTION_DELETE, "Academic", "branches", branch_id,
              old_value=dict(old) if old else None)
    return True, "Branch deleted."


# ===========================================================================
# Semesters
# ===========================================================================
def get_semesters(course_id: int | None = None, active_only: bool = True) -> list:
    clauses, params = ["1=1"], []
    if course_id:
        clauses.append("course_id = ?")
        params.append(course_id)
    if active_only:
        clauses.append("is_active = 1")
    return get_db().fetch_all(
        f"SELECT * FROM semesters WHERE {' AND '.join(clauses)} "
        f"ORDER BY course_id, semester_number", params)


def get_semester(semester_id: int):
    return get_db().fetch_one("SELECT * FROM semesters WHERE semester_id = ?", (semester_id,))


def add_semester(course_id: int, number: int, name: str = "",
                 sections: str = "",
                 user: dict | None = None) -> tuple[bool, str, int | None]:
    """Add a semester, optionally creating sections at the same time."""
    db = get_db()
    if db.exists("semesters", "course_id = ? AND semester_number = ?", (course_id, number)):
        return False, f"Semester {number} already exists for this course.", None

    semester_id = db.insert("semesters", {
        "course_id": course_id, "semester_number": number,
        "semester_name": name.strip() or f"Semester {number}",
    })
    # Keep the parent course's declared total in step.
    db.execute(
        "UPDATE courses SET total_semesters = "
        "(SELECT COUNT(*) FROM semesters WHERE course_id = ?) WHERE course_id = ?",
        (course_id, course_id))

    created_sections = create_sections_from_text(sections, user)

    log_audit(user, ACTION_CREATE, "Academic", "semesters", semester_id,
              new_value={"semester_number": number, "sections": created_sections})

    message = f"Semester {number} added."
    if created_sections:
        message += f" Sections created: {', '.join(created_sections)}."
    return True, message, semester_id


def delete_semester(semester_id: int, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    students = db.count("students", "semester_id = ?", (semester_id,))
    if students:
        return False, f"Cannot delete: {students} student(s) are in this semester."
    subjects = db.count("subjects", "semester_id = ?", (semester_id,))
    if subjects:
        return False, f"Cannot delete: {subjects} subject(s) belong to this semester."

    old = get_semester(semester_id)
    db.delete("semesters", "semester_id = ?", (semester_id,))
    log_audit(user, ACTION_DELETE, "Academic", "semesters", semester_id,
              old_value=dict(old) if old else None)
    return True, "Semester deleted."


# ===========================================================================
# Sections
# ===========================================================================
def get_sections(active_only: bool = True) -> list:
    where = "WHERE is_active = 1" if active_only else ""
    return get_db().fetch_all(
        f"""SELECT s.*,
                   (SELECT COUNT(*) FROM students st
                    WHERE st.section_id = s.section_id AND st.status='Active') AS student_count
            FROM sections s {where} ORDER BY s.section_name""")


def add_section(name: str, capacity: int = 60,
                user: dict | None = None) -> tuple[bool, str, int | None]:
    db = get_db()
    name = name.strip().upper()
    if db.exists("sections", "UPPER(section_name) = ?", (name,)):
        return False, f"Section '{name}' already exists.", None
    section_id = db.insert("sections", {"section_name": name, "capacity": capacity})
    log_audit(user, ACTION_CREATE, "Academic", "sections", section_id,
              new_value={"section_name": name})
    return True, f"Section '{name}' added.", section_id


def delete_section(section_id: int, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    students = db.count("students", "section_id = ?", (section_id,))
    if students:
        return False, f"Cannot delete: {students} student(s) are in this section."
    db.delete("sections", "section_id = ?", (section_id,))
    log_audit(user, ACTION_DELETE, "Academic", "sections", section_id)
    return True, "Section deleted."


# ===========================================================================
# Academic sessions
# ===========================================================================
def get_sessions(active_only: bool = True) -> list:
    where = "WHERE is_active = 1" if active_only else ""
    return get_db().fetch_all(
        f"SELECT * FROM academic_sessions {where} ORDER BY start_date DESC")


def get_current_session():
    """The session flagged current, falling back to the most recent one."""
    db = get_db()
    row = db.fetch_one("SELECT * FROM academic_sessions WHERE is_current = 1 LIMIT 1")
    if row is None:
        row = db.fetch_one(
            "SELECT * FROM academic_sessions ORDER BY start_date DESC LIMIT 1")
    return row


def get_current_session_id() -> int | None:
    row = get_current_session()
    return row["session_id"] if row else None


def add_session(name: str, start_date: str, end_date: str, make_current: bool = False,
                user: dict | None = None) -> tuple[bool, str, int | None]:
    db = get_db()
    if db.exists("academic_sessions", "session_name = ?", (name,)):
        return False, f"Academic session '{name}' already exists.", None

    session_id = db.insert("academic_sessions", {
        "session_name": name.strip(), "start_date": start_date,
        "end_date": end_date, "is_current": 0,
    })
    if make_current:
        set_current_session(session_id, user)
    log_audit(user, ACTION_CREATE, "Academic", "academic_sessions", session_id,
              new_value={"session_name": name})
    return True, f"Academic session '{name}' added.", session_id


def set_current_session(session_id: int, user: dict | None = None) -> tuple[bool, str]:
    """Exactly one session may be current at a time."""
    db = get_db()
    row = db.fetch_one("SELECT * FROM academic_sessions WHERE session_id = ?", (session_id,))
    if row is None:
        return False, "Academic session not found."

    with db.transaction() as cur:
        cur.execute("UPDATE academic_sessions SET is_current = 0")
        cur.execute("UPDATE academic_sessions SET is_current = 1 WHERE session_id = ?",
                    (session_id,))

    from config.settings import config
    config.set("current_academic_session", row["session_name"])

    log_audit(user, ACTION_UPDATE, "Academic", "academic_sessions", session_id,
              new_value={"is_current": True}, reason="Current session changed")
    return True, f"'{row['session_name']}' is now the current academic session."


def delete_session(session_id: int, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    if db.count("attendance_sessions", "session_id = ?", (session_id,)):
        return False, "Cannot delete: attendance records are linked to this session."
    if db.count("students", "session_id = ?", (session_id,)):
        return False, "Cannot delete: students are linked to this session."
    db.delete("academic_sessions", "session_id = ?", (session_id,))
    log_audit(user, ACTION_DELETE, "Academic", "academic_sessions", session_id)
    return True, "Academic session deleted."


# ===========================================================================
# Batches
# ===========================================================================
def get_batches(active_only: bool = True) -> list:
    where = "WHERE is_active = 1" if active_only else ""
    return get_db().fetch_all(
        f"""SELECT b.*,
                   (SELECT COUNT(*) FROM students s WHERE s.batch_id = b.batch_id) AS student_count
            FROM batches b {where} ORDER BY b.start_year DESC""")


def add_batch(name: str, start_year: int, end_year: int,
              user: dict | None = None) -> tuple[bool, str, int | None]:
    db = get_db()
    if db.exists("batches", "batch_name = ?", (name,)):
        return False, f"Batch '{name}' already exists.", None
    batch_id = db.insert("batches", {
        "batch_name": name.strip(), "start_year": start_year, "end_year": end_year,
    })
    log_audit(user, ACTION_CREATE, "Academic", "batches", batch_id,
              new_value={"batch_name": name})
    return True, f"Batch '{name}' added.", batch_id


def delete_batch(batch_id: int, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    if db.count("students", "batch_id = ?", (batch_id,)):
        return False, "Cannot delete: students are assigned to this batch."
    db.delete("batches", "batch_id = ?", (batch_id,))
    log_audit(user, ACTION_DELETE, "Academic", "batches", batch_id)
    return True, "Batch deleted."


# ===========================================================================
# Class teachers
# ===========================================================================
def get_class_teacher(branch_id: int, semester_id: int,
                      section_id: int | None = None):
    """Resolve who is responsible for a class.

    Falls back sensibly so a student is never told "pending with nobody":

    1. An explicitly assigned class teacher for that exact class.
    2. A class teacher assigned to the branch + semester with no section.
    3. The faculty teaching the most subjects to that class.
    4. The head of department.
    """
    db = get_db()

    row = db.fetch_one(
        """SELECT f.faculty_id, f.full_name, f.faculty_code, f.email, f.mobile,
                  'Class Teacher' AS source
           FROM class_teachers ct JOIN faculty f ON f.faculty_id = ct.faculty_id
           WHERE ct.branch_id = ? AND ct.semester_id = ?
             AND IFNULL(ct.section_id, -1) = IFNULL(?, -1)
           LIMIT 1""", (branch_id, semester_id, section_id))
    if row:
        return row

    row = db.fetch_one(
        """SELECT f.faculty_id, f.full_name, f.faculty_code, f.email, f.mobile,
                  'Class Teacher' AS source
           FROM class_teachers ct JOIN faculty f ON f.faculty_id = ct.faculty_id
           WHERE ct.branch_id = ? AND ct.semester_id = ? AND ct.section_id IS NULL
           LIMIT 1""", (branch_id, semester_id))
    if row:
        return row

    row = db.fetch_one(
        """SELECT f.faculty_id, f.full_name, f.faculty_code, f.email, f.mobile,
                  'Subject Teacher' AS source
           FROM subjects s JOIN faculty f ON f.faculty_id = s.faculty_id
           WHERE s.branch_id = ? AND s.semester_id = ? AND s.is_active = 1
           GROUP BY f.faculty_id
           ORDER BY COUNT(*) DESC LIMIT 1""", (branch_id, semester_id))
    if row:
        return row

    return db.fetch_one(
        """SELECT f.faculty_id, f.full_name, f.faculty_code, f.email, f.mobile,
                  'Head of Department' AS source
           FROM faculty f WHERE f.branch_id = ? AND f.status = 'Active'
           ORDER BY f.experience_years DESC LIMIT 1""", (branch_id,))


def get_class_teachers() -> list:
    """Every explicit assignment, for the management grid."""
    return get_db().fetch_all(
        """SELECT ct.*, b.branch_name, b.branch_code, sem.semester_name,
                  sec.section_name, f.full_name AS faculty_name, f.faculty_code,
                  f.mobile, f.email
           FROM class_teachers ct
           JOIN branches  b   ON b.branch_id = ct.branch_id
           JOIN semesters sem ON sem.semester_id = ct.semester_id
           LEFT JOIN sections sec ON sec.section_id = ct.section_id
           JOIN faculty   f   ON f.faculty_id = ct.faculty_id
           ORDER BY b.branch_name, sem.semester_number, sec.section_name""")


def assign_class_teacher(branch_id: int, semester_id: int, section_id: int | None,
                         faculty_id: int, user: dict | None = None
                         ) -> tuple[bool, str, int | None]:
    db = get_db()

    existing = db.fetch_one(
        "SELECT class_teacher_id FROM class_teachers WHERE branch_id = ? "
        "AND semester_id = ? AND IFNULL(section_id,-1) = IFNULL(?,-1)",
        (branch_id, semester_id, section_id))

    if existing:
        db.update("class_teachers",
                  {"faculty_id": faculty_id, "session_id": get_current_session_id(),
                   "assigned_by": (user or {}).get("user_id")},
                  "class_teacher_id = ?", (existing["class_teacher_id"],))
        log_audit(user, ACTION_UPDATE, "Academic", "class_teachers",
                  existing["class_teacher_id"], new_value={"faculty_id": faculty_id})
        return True, "Class teacher updated.", existing["class_teacher_id"]

    class_teacher_id = db.insert("class_teachers", {
        "branch_id": branch_id, "semester_id": semester_id, "section_id": section_id,
        "faculty_id": faculty_id, "session_id": get_current_session_id(),
        "assigned_by": (user or {}).get("user_id"),
    })
    log_audit(user, ACTION_CREATE, "Academic", "class_teachers", class_teacher_id,
              new_value={"branch_id": branch_id, "semester_id": semester_id,
                         "faculty_id": faculty_id})
    return True, "Class teacher assigned.", class_teacher_id


def remove_class_teacher(class_teacher_id: int,
                         user: dict | None = None) -> tuple[bool, str]:
    get_db().delete("class_teachers", "class_teacher_id = ?", (class_teacher_id,))
    log_audit(user, ACTION_DELETE, "Academic", "class_teachers", class_teacher_id)
    return True, "Class teacher assignment removed."


# ===========================================================================
# Holiday calendar
# ===========================================================================
def get_holidays(session_id: int | None = None, year: int | None = None) -> list:
    clauses, params = ["1=1"], []
    if session_id:
        clauses.append("(h.session_id = ? OR h.session_id IS NULL)")
        params.append(session_id)
    if year:
        clauses.append("strftime('%Y', h.holiday_date) = ?")
        params.append(str(year))
    return get_db().fetch_all(
        f"""SELECT h.*, a.session_name FROM holidays h
            LEFT JOIN academic_sessions a ON a.session_id = h.session_id
            WHERE {' AND '.join(clauses)} ORDER BY h.holiday_date""", params)


def add_holiday(holiday_date: str, name: str, holiday_type: str = "Public",
                session_id: int | None = None, description: str = "",
                user: dict | None = None) -> tuple[bool, str, int | None]:
    db = get_db()
    if db.exists("holidays", "holiday_date = ?", (holiday_date,)):
        return False, f"A holiday is already recorded for {holiday_date}.", None
    holiday_id = db.insert("holidays", {
        "holiday_date": holiday_date, "holiday_name": name.strip(),
        "holiday_type": holiday_type, "session_id": session_id,
        "description": description.strip() or None,
    })
    log_audit(user, ACTION_CREATE, "Academic", "holidays", holiday_id,
              new_value={"date": holiday_date, "name": name})
    return True, f"Holiday '{name}' added for {holiday_date}.", holiday_id


def delete_holiday(holiday_id: int, user: dict | None = None) -> tuple[bool, str]:
    get_db().delete("holidays", "holiday_id = ?", (holiday_id,))
    log_audit(user, ACTION_DELETE, "Academic", "holidays", holiday_id)
    return True, "Holiday removed."


def is_holiday(check_date: str | date) -> tuple[bool, str]:
    """Attendance is blocked on holidays -- and on Sundays by convention."""
    if isinstance(check_date, date):
        check_date = check_date.isoformat()

    row = get_db().fetch_one(
        "SELECT holiday_name FROM holidays WHERE holiday_date = ?", (check_date,))
    if row:
        return True, row["holiday_name"]

    try:
        if datetime.strptime(check_date, "%Y-%m-%d").weekday() == 6:
            return True, "Sunday"
    except ValueError:
        pass
    return False, ""


# ===========================================================================
# Lookup helpers for form dropdowns
# ===========================================================================
def dropdown_map(rows, id_key: str, label_key: str) -> dict[str, int]:
    """Build ``{"display label": id}`` for a CTkComboBox."""
    return {str(row[label_key]): row[id_key] for row in rows}


def get_academic_lookups(course_id: int | None = None) -> dict:
    """One call that fills every academic dropdown on a form."""
    courses = get_courses()
    if course_id is None and courses:
        course_id = courses[0]["course_id"]
    return {
        "courses":   dropdown_map(courses, "course_id", "course_name"),
        "branches":  dropdown_map(get_branches(course_id), "branch_id", "branch_name"),
        "semesters": dropdown_map(get_semesters(course_id), "semester_id", "semester_name"),
        "sections":  dropdown_map(get_sections(), "section_id", "section_name"),
        "batches":   dropdown_map(get_batches(), "batch_id", "batch_name"),
        "sessions":  dropdown_map(get_sessions(), "session_id", "session_name"),
    }
