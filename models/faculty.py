"""
Faculty repository.

A faculty member's "assigned subjects" are not a stored list -- they are
derived from the ``subjects.faculty_id`` mapping, so a subject can never be
assigned in one place and missing in another.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from config.settings import FACULTY_IMAGE_DIR, config
from core.audit import ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE, log_audit
from core.database import get_db
from core.logger import get_logger
from core.validators import safe_filename

logger = get_logger("models.faculty")

_EDITABLE = {
    "faculty_code", "full_name", "gender", "dob", "qualification", "designation",
    "experience_years", "mobile", "email", "address", "branch_id", "department",
    "joining_date", "photo_path", "status",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(data: dict) -> dict:
    return {k: (None if isinstance(v, str) and not v.strip() else v)
            for k, v in data.items() if k in _EDITABLE}


def faculty_image_path(faculty_code: str, full_name: str, ext: str = ".jpg") -> Path:
    """``FacultyID_FacultyName.jpg`` per the naming specification."""
    return FACULTY_IMAGE_DIR / f"{safe_filename(f'{faculty_code}_{str(full_name).replace(chr(32), chr(95))}')}{ext}"


def display_department(row) -> str:
    """The department label to show for a faculty row.

    Most staff belong to a diploma branch (Computer Science & Engineering,
    Electronics Engineering) and that name is shown. A handful teach a
    general subject that is not tied to any one branch -- Applied Science
    (Physics), Accountancy & Taxation -- and for them ``branch_id`` is
    intentionally left unset; the free-text ``department`` column carries
    their department instead. Workshop instructors have neither on the
    published staff directory, so both fall through to "-".
    """
    branch_name = row["branch_name"] if "branch_name" in row.keys() else None
    if branch_name:
        return branch_name
    department = row["department"] if "department" in row.keys() else None
    return department or "-"


# ===========================================================================
# Retrieval
# ===========================================================================
def get_faculty(faculty_id: int):
    return get_db().fetch_one(
        """SELECT f.*, b.branch_name, b.branch_code,
                  (SELECT COUNT(*) FROM subjects s WHERE s.faculty_id = f.faculty_id) AS subject_count
           FROM faculty f LEFT JOIN branches b ON b.branch_id = f.branch_id
           WHERE f.faculty_id = ?""", (faculty_id,))


def get_faculty_by_code(code: str):
    return get_db().fetch_one("SELECT * FROM faculty WHERE faculty_code = ?", (code,))


def search_faculty(branch_id: int | None = None, status: str | None = "Active",
                   search: str = "", page: int = 1,
                   page_size: int | None = None,
                   enforce_scope: bool = True) -> tuple[list, int]:
    """Paginated faculty search.

    Scoped by default: a lecturer sees only their own record, never a
    colleague's contact details or workload.
    """
    page_size = page_size or int(config.get("rows_per_page", 25))
    clauses, params = ["1=1"], []

    if enforce_scope:
        from core.scope import current_scope
        clause, scope_params = current_scope().faculty_clause("f.faculty_id")
        clauses.append(clause)
        params.extend(scope_params)

    if branch_id:
        clauses.append("f.branch_id = ?")
        params.append(branch_id)
    if status:
        clauses.append("f.status = ?")
        params.append(status)
    if search:
        clauses.append("(f.faculty_code LIKE ? OR f.full_name LIKE ? OR f.mobile LIKE ? "
                       "OR f.email LIKE ? OR f.qualification LIKE ? OR f.designation LIKE ?)")
        params.extend([f"%{search}%"] * 6)

    where = " AND ".join(clauses)
    total = int(get_db().fetch_value(
        f"SELECT COUNT(*) FROM faculty f WHERE {where}", params, 0))

    offset = max(0, (page - 1) * page_size)
    rows = get_db().fetch_all(
        f"""SELECT f.*, b.branch_name, b.branch_code,
                   (SELECT COUNT(*) FROM subjects s WHERE s.faculty_id = f.faculty_id) AS subject_count
            FROM faculty f LEFT JOIN branches b ON b.branch_id = f.branch_id
            WHERE {where} ORDER BY f.full_name LIMIT ? OFFSET ?""",
        params + [page_size, offset])
    return rows, total


def get_all_faculty(active_only: bool = True) -> list:
    where = "WHERE f.status = 'Active'" if active_only else ""
    return get_db().fetch_all(
        f"""SELECT f.*, b.branch_name FROM faculty f
            LEFT JOIN branches b ON b.branch_id = f.branch_id
            {where} ORDER BY f.full_name""")


def get_assigned_subjects(faculty_id: int) -> list:
    """Subjects this faculty teaches, with class context."""
    return get_db().fetch_all(
        """SELECT s.*, b.branch_name, b.branch_code, sem.semester_name,
                  sem.semester_number, c.course_name
           FROM subjects s
           JOIN branches  b   ON b.branch_id  = s.branch_id
           JOIN semesters sem ON sem.semester_id = s.semester_id
           JOIN courses   c   ON c.course_id  = s.course_id
           WHERE s.faculty_id = ? AND s.is_active = 1
           ORDER BY sem.semester_number, s.subject_name""", (faculty_id,))


def get_assigned_semesters(faculty_id: int) -> list:
    return get_db().fetch_all(
        """SELECT DISTINCT sem.semester_id, sem.semester_name, sem.semester_number
           FROM subjects s JOIN semesters sem ON sem.semester_id = s.semester_id
           WHERE s.faculty_id = ? ORDER BY sem.semester_number""", (faculty_id,))


def next_faculty_code() -> str:
    """Suggest the next code in the ``FAC001`` series."""
    last = get_db().fetch_value(
        "SELECT faculty_code FROM faculty WHERE faculty_code LIKE 'FAC%' "
        "ORDER BY faculty_code DESC LIMIT 1")
    if last:
        try:
            return f"FAC{int(str(last)[3:]) + 1:03d}"
        except ValueError:
            pass
    return "FAC001"


def count_faculty(status: str | None = "Active") -> int:
    where, params = ("status = ?", (status,)) if status else ("1=1", ())
    return get_db().count("faculty", where, params)


# ===========================================================================
# Create / update / delete
# ===========================================================================
def add_faculty(data: dict, photo_source: str | Path | None = None,
                user: dict | None = None) -> tuple[bool, str, int | None]:
    db = get_db()
    payload = _clean(data)

    for field in ("faculty_code", "full_name"):
        if not payload.get(field):
            return False, f"'{field.replace('_', ' ').title()}' is required.", None

    if db.exists("faculty", "faculty_code = ?", (payload["faculty_code"],)):
        return False, f"Faculty code '{payload['faculty_code']}' already exists.", None

    payload.setdefault("status", "Active")

    if photo_source:
        stored = _store_photo(photo_source, payload["faculty_code"], payload["full_name"])
        if stored:
            payload["photo_path"] = str(stored)

    faculty_id = db.insert("faculty", payload)
    log_audit(user, ACTION_CREATE, "Faculty", "faculty", faculty_id,
              new_value={"faculty_code": payload["faculty_code"],
                         "full_name": payload["full_name"]})
    logger.info("Faculty added: %s (%s)", payload["full_name"], payload["faculty_code"])
    return True, f"Faculty '{payload['full_name']}' added successfully.", faculty_id


def update_faculty(faculty_id: int, data: dict, photo_source: str | Path | None = None,
                   user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    old = get_faculty(faculty_id)
    if old is None:
        return False, "Faculty member not found."

    payload = _clean(data)
    if "faculty_code" in payload and db.exists(
            "faculty", "faculty_code = ? AND faculty_id <> ?",
            (payload["faculty_code"], faculty_id)):
        return False, "Another faculty member already uses that code."

    if photo_source:
        stored = _store_photo(photo_source,
                              payload.get("faculty_code", old["faculty_code"]),
                              payload.get("full_name", old["full_name"]))
        if stored:
            payload["photo_path"] = str(stored)

    payload["updated_at"] = _now()
    db.update("faculty", payload, "faculty_id = ?", (faculty_id,))

    changed = {k: v for k, v in payload.items()
               if k != "updated_at" and str(old[k] if k in old.keys() else "") != str(v)}
    log_audit(user, ACTION_UPDATE, "Faculty", "faculty", faculty_id,
              old_value={k: old[k] for k in changed if k in old.keys()},
              new_value=changed)
    return True, "Faculty record updated."


def delete_faculty(faculty_id: int, user: dict | None = None) -> tuple[bool, str]:
    """Delete a faculty member; their subjects are unassigned, not deleted."""
    db = get_db()
    faculty = get_faculty(faculty_id)
    if faculty is None:
        return False, "Faculty member not found."

    subject_count = db.count("subjects", "faculty_id = ?", (faculty_id,))

    if faculty["photo_path"]:
        Path(faculty["photo_path"]).unlink(missing_ok=True)

    db.execute("UPDATE subjects SET faculty_id = NULL WHERE faculty_id = ?", (faculty_id,))
    db.execute("UPDATE timetable SET faculty_id = NULL WHERE faculty_id = ?", (faculty_id,))
    db.delete("users", "role = 'Faculty' AND linked_id = ?", (faculty_id,))
    db.delete("faculty", "faculty_id = ?", (faculty_id,))

    log_audit(user, ACTION_DELETE, "Faculty", "faculty", faculty_id,
              old_value={"faculty_code": faculty["faculty_code"],
                         "full_name": faculty["full_name"]},
              reason=f"{subject_count} subject(s) left unassigned")
    return True, (f"Faculty '{faculty['full_name']}' deleted. "
                  f"{subject_count} subject(s) are now unassigned.")


def assign_subject(faculty_id: int, subject_id: int,
                   user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    subject = db.fetch_one("SELECT * FROM subjects WHERE subject_id = ?", (subject_id,))
    if subject is None:
        return False, "Subject not found."

    db.update("subjects", {"faculty_id": faculty_id}, "subject_id = ?", (subject_id,))
    log_audit(user, ACTION_UPDATE, "Faculty", "subjects", subject_id,
              old_value={"faculty_id": subject["faculty_id"]},
              new_value={"faculty_id": faculty_id}, reason="Subject assignment")
    return True, "Subject assigned."


def unassign_subject(subject_id: int, user: dict | None = None) -> tuple[bool, str]:
    get_db().update("subjects", {"faculty_id": None}, "subject_id = ?", (subject_id,))
    log_audit(user, ACTION_UPDATE, "Faculty", "subjects", subject_id,
              new_value={"faculty_id": None}, reason="Subject unassigned")
    return True, "Subject unassigned."


def _store_photo(source: str | Path, faculty_code: str, full_name: str) -> Path | None:
    source = Path(source)
    if not source.exists():
        return None
    FACULTY_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = faculty_image_path(faculty_code, full_name, source.suffix.lower() or ".jpg")
    try:
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target
    except OSError as exc:
        logger.error("Could not store faculty photo: %s", exc)
        return None


# ===========================================================================
# Workload
# ===========================================================================
def get_workload(faculty_id: int) -> dict:
    """Teaching load summary shown on the faculty dashboard."""
    db = get_db()
    return {
        "subjects": db.count("subjects", "faculty_id = ? AND is_active = 1", (faculty_id,)),
        "weekly_classes": db.count("timetable", "faculty_id = ? AND is_active = 1", (faculty_id,)),
        "students": int(db.fetch_value(
            """SELECT COUNT(DISTINCT s.student_id) FROM students s
               JOIN subjects sub ON sub.branch_id = s.branch_id
                                AND sub.semester_id = s.semester_id
               WHERE sub.faculty_id = ? AND s.status = 'Active'""", (faculty_id,), 0)),
        "sessions_taken": db.count("attendance_sessions", "faculty_id = ?", (faculty_id,)),
    }
