"""
Subject repository.

Enforces the hierarchy required by the specification::

    Course -> Branch -> Semester -> Subject -> Faculty

A subject cannot be created against a branch that belongs to a different
course, nor against a semester outside that course -- those combinations are
rejected here rather than being caught later by a confusing foreign-key error.
"""

from __future__ import annotations

from core.audit import ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE, log_audit
from core.database import get_db
from core.logger import get_logger

logger = get_logger("models.subject")

SUBJECT_TYPES = ["Theory", "Practical", "Project", "Tutorial"]

_EDITABLE = {
    "subject_code", "subject_name", "course_id", "branch_id", "semester_id",
    "faculty_id", "subject_type", "credits", "total_classes", "is_active",
}


def _clean(data: dict) -> dict:
    return {k: v for k, v in data.items() if k in _EDITABLE}


_BASE_SELECT = """
    SELECT s.*, c.course_name, b.branch_name, b.branch_code,
           sem.semester_name, sem.semester_number,
           f.faculty_code, f.full_name AS faculty_name,
           (SELECT COUNT(*) FROM attendance_sessions a
            WHERE a.subject_id = s.subject_id) AS sessions_conducted
    FROM subjects s
    JOIN courses   c   ON c.course_id  = s.course_id
    JOIN branches  b   ON b.branch_id  = s.branch_id
    JOIN semesters sem ON sem.semester_id = s.semester_id
    LEFT JOIN faculty f ON f.faculty_id = s.faculty_id
"""


# ===========================================================================
# Retrieval
# ===========================================================================
def get_subject(subject_id: int):
    return get_db().fetch_one(f"{_BASE_SELECT} WHERE s.subject_id = ?", (subject_id,))


def search_subjects(course_id=None, branch_id=None, semester_id=None, faculty_id=None,
                    search: str = "", active_only: bool = True,
                    unassigned_only: bool = False) -> list:
    clauses, params = ["1=1"], []

    if course_id:
        clauses.append("s.course_id = ?")
        params.append(course_id)
    if branch_id:
        clauses.append("s.branch_id = ?")
        params.append(branch_id)
    if semester_id:
        clauses.append("s.semester_id = ?")
        params.append(semester_id)
    if faculty_id:
        clauses.append("s.faculty_id = ?")
        params.append(faculty_id)
    if unassigned_only:
        clauses.append("s.faculty_id IS NULL")
    if active_only:
        clauses.append("s.is_active = 1")
    if search:
        clauses.append("(s.subject_code LIKE ? OR s.subject_name LIKE ? "
                       "OR f.full_name LIKE ?)")
        params.extend([f"%{search}%"] * 3)

    return get_db().fetch_all(
        f"{_BASE_SELECT} WHERE {' AND '.join(clauses)} "
        f"ORDER BY sem.semester_number, s.subject_code", params)


def get_class_subjects(branch_id: int, semester_id: int) -> list:
    """Subjects taught to one class -- drives the attendance subject picker."""
    return get_db().fetch_all(
        f"{_BASE_SELECT} WHERE s.branch_id = ? AND s.semester_id = ? AND s.is_active = 1 "
        f"ORDER BY s.subject_name", (branch_id, semester_id))


def get_faculty_subjects(faculty_id: int) -> list:
    return get_db().fetch_all(
        f"{_BASE_SELECT} WHERE s.faculty_id = ? AND s.is_active = 1 "
        f"ORDER BY sem.semester_number, s.subject_name", (faculty_id,))


def count_subjects(active_only: bool = True) -> int:
    return get_db().count("subjects", "is_active = 1" if active_only else "1=1")


# ===========================================================================
# Validation of the hierarchy
# ===========================================================================
def _validate_hierarchy(course_id: int, branch_id: int, semester_id: int) -> tuple[bool, str]:
    db = get_db()

    branch = db.fetch_one("SELECT course_id, branch_name FROM branches WHERE branch_id = ?",
                          (branch_id,))
    if branch is None:
        return False, "The selected branch does not exist."
    if branch["course_id"] != course_id:
        return False, f"Branch '{branch['branch_name']}' does not belong to the selected course."

    semester = db.fetch_one("SELECT course_id, semester_name FROM semesters WHERE semester_id = ?",
                            (semester_id,))
    if semester is None:
        return False, "The selected semester does not exist."
    if semester["course_id"] != course_id:
        return False, f"'{semester['semester_name']}' does not belong to the selected course."

    return True, ""


# ===========================================================================
# Create / update / delete
# ===========================================================================
def add_subject(data: dict, user: dict | None = None) -> tuple[bool, str, int | None]:
    db = get_db()
    payload = _clean(data)

    for field in ("subject_code", "subject_name", "course_id", "branch_id", "semester_id"):
        if not payload.get(field):
            return False, f"'{field.replace('_', ' ').title()}' is required.", None

    ok, message = _validate_hierarchy(payload["course_id"], payload["branch_id"],
                                      payload["semester_id"])
    if not ok:
        return False, message, None

    payload["subject_code"] = str(payload["subject_code"]).strip().upper()
    if db.exists("subjects", "subject_code = ?", (payload["subject_code"],)):
        return False, f"Subject code '{payload['subject_code']}' is already in use.", None

    payload.setdefault("subject_type", "Theory")
    payload.setdefault("credits", 4)

    subject_id = db.insert("subjects", payload)
    log_audit(user, ACTION_CREATE, "Subjects", "subjects", subject_id,
              new_value={"subject_code": payload["subject_code"],
                         "subject_name": payload["subject_name"]})
    logger.info("Subject added: %s - %s", payload["subject_code"], payload["subject_name"])
    return True, f"Subject '{payload['subject_name']}' added.", subject_id


def update_subject(subject_id: int, data: dict, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    old = get_subject(subject_id)
    if old is None:
        return False, "Subject not found."

    payload = _clean(data)

    course_id = payload.get("course_id", old["course_id"])
    branch_id = payload.get("branch_id", old["branch_id"])
    semester_id = payload.get("semester_id", old["semester_id"])
    ok, message = _validate_hierarchy(course_id, branch_id, semester_id)
    if not ok:
        return False, message

    if "subject_code" in payload:
        payload["subject_code"] = str(payload["subject_code"]).strip().upper()
        if db.exists("subjects", "subject_code = ? AND subject_id <> ?",
                     (payload["subject_code"], subject_id)):
            return False, "Another subject already uses that code."

    # Moving a subject to a different class after attendance exists would
    # silently invalidate those records.
    if (branch_id != old["branch_id"] or semester_id != old["semester_id"]) \
            and db.count("attendance", "subject_id = ?", (subject_id,)):
        return False, ("This subject already has attendance records, so its branch "
                       "or semester cannot be changed. Create a new subject instead.")

    db.update("subjects", payload, "subject_id = ?", (subject_id,))
    changed = {k: v for k, v in payload.items()
               if str(old[k] if k in old.keys() else "") != str(v)}
    log_audit(user, ACTION_UPDATE, "Subjects", "subjects", subject_id,
              old_value={k: old[k] for k in changed if k in old.keys()},
              new_value=changed)
    return True, "Subject updated."


def delete_subject(subject_id: int, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    subject = get_subject(subject_id)
    if subject is None:
        return False, "Subject not found."

    attendance_rows = db.count("attendance", "subject_id = ?", (subject_id,))
    if attendance_rows:
        return False, (f"Cannot delete: {attendance_rows} attendance record(s) exist for "
                       "this subject. Deactivate it instead to preserve history.")

    timetable_rows = db.count("timetable", "subject_id = ?", (subject_id,))
    db.delete("subjects", "subject_id = ?", (subject_id,))

    log_audit(user, ACTION_DELETE, "Subjects", "subjects", subject_id,
              old_value={"subject_code": subject["subject_code"],
                         "subject_name": subject["subject_name"]},
              reason=f"{timetable_rows} timetable slot(s) removed")
    return True, f"Subject '{subject['subject_name']}' deleted."


def set_active(subject_id: int, active: bool, user: dict | None = None) -> tuple[bool, str]:
    """Deactivating preserves history while hiding the subject from pickers."""
    get_db().update("subjects", {"is_active": 1 if active else 0},
                    "subject_id = ?", (subject_id,))
    log_audit(user, ACTION_UPDATE, "Subjects", "subjects", subject_id,
              new_value={"is_active": active})
    return True, f"Subject {'activated' if active else 'deactivated'}."


def get_subject_stats(subject_id: int) -> dict:
    """Attendance summary for one subject -- used by reports and analytics."""
    db = get_db()
    row = db.fetch_one(
        """SELECT COUNT(DISTINCT a.att_session_id) AS sessions,
                  COUNT(a.attendance_id) AS records,
                  SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
                  SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent
           FROM attendance a WHERE a.subject_id = ?""", (subject_id,))

    records = (row["records"] or 0) if row else 0
    attended = (row["attended"] or 0) if row else 0
    return {
        "sessions": (row["sessions"] or 0) if row else 0,
        "records": records,
        "attended": attended,
        "absent": (row["absent"] or 0) if row else 0,
        "percentage": round(100.0 * attended / records, 2) if records else 0.0,
    }
