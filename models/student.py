"""
Student repository.

Covers CRUD, paginated search, Excel import/export, face-dataset bookkeeping
and bulk semester promotion.

File-naming rules from the specification are enforced here so every caller
produces identical paths::

    student image  ->  EnrollmentNo_StudentName.jpg
    face dataset   ->  face_dataset/EnrollmentNo_StudentName/image001.jpg
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from config.settings import (FACE_DATASET_DIR, STUDENT_IMAGE_DIR, config)
from core.audit import (ACTION_CREATE, ACTION_DELETE, ACTION_PROMOTION,
                        ACTION_UPDATE, log_audit)
from core.database import get_db
from core.logger import get_logger
from core.validators import safe_filename

logger = get_logger("models.student")

# Columns the UI is allowed to write.  Anything else in a payload is ignored,
# which keeps a stray form field from corrupting a row.
_EDITABLE = {
    "enrollment_no", "roll_no", "full_name", "father_name", "mother_name",
    "gender", "dob", "mobile", "email", "address", "admission_date",
    "course_id", "branch_id", "semester_id", "section_id", "batch_id",
    "session_id", "photo_path", "dataset_path", "face_registered",
    "face_sample_count", "status",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(data: dict) -> dict:
    """Keep only writable columns and normalise blank strings to NULL."""
    out = {}
    for key, value in data.items():
        if key not in _EDITABLE:
            continue
        out[key] = None if isinstance(value, str) and not value.strip() else value
    return out


# ===========================================================================
# Naming helpers
# ===========================================================================
def student_folder_name(enrollment_no: str, full_name: str) -> str:
    """``DCS25001_Rahul_Verma`` -- used for both image and dataset naming."""
    return safe_filename(f"{enrollment_no}_{str(full_name).replace(' ', '_')}")


def student_image_path(enrollment_no: str, full_name: str, ext: str = ".jpg") -> Path:
    return STUDENT_IMAGE_DIR / f"{student_folder_name(enrollment_no, full_name)}{ext}"


def face_dataset_path(enrollment_no: str, full_name: str) -> Path:
    return FACE_DATASET_DIR / student_folder_name(enrollment_no, full_name)


# ===========================================================================
# Retrieval
# ===========================================================================
def get_student(student_id: int):
    return get_db().fetch_one("SELECT * FROM v_student_full WHERE student_id = ?",
                              (student_id,))


def get_student_by_enrollment(enrollment_no: str):
    return get_db().fetch_one("SELECT * FROM v_student_full WHERE enrollment_no = ?",
                              (enrollment_no,))


def _build_filter(branch_id=None, semester_id=None, section_id=None, batch_id=None,
                  session_id=None, status=None, search="", face_registered=None,
                  enforce_scope: bool = True) -> tuple[str, list]:
    """Shared WHERE clause for the student grid and its count query.

    ``enforce_scope`` restricts the result to the signed-in user's reach: a
    student sees only their own record, a lecturer only the classes they
    teach.  Administrative jobs that must span everyone pass False.
    """
    clauses, params = ["1=1"], []

    if enforce_scope:
        from core.scope import current_scope
        clause, scope_params = current_scope().student_clause()
        clauses.append(clause)
        params.extend(scope_params)

    if branch_id:
        clauses.append("branch_id = ?")
        params.append(branch_id)
    if semester_id:
        clauses.append("semester_id = ?")
        params.append(semester_id)
    if section_id:
        clauses.append("section_id = ?")
        params.append(section_id)
    if batch_id:
        clauses.append("batch_id = ?")
        params.append(batch_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if face_registered is not None:
        clauses.append("face_registered = ?")
        params.append(1 if face_registered else 0)
    if search:
        clauses.append("(enrollment_no LIKE ? OR roll_no LIKE ? OR full_name LIKE ? "
                       "OR mobile LIKE ? OR email LIKE ? OR father_name LIKE ?)")
        params.extend([f"%{search}%"] * 6)

    return " AND ".join(clauses), params


def search_students(branch_id=None, semester_id=None, section_id=None, batch_id=None,
                    session_id=None, status="Active", search="", face_registered=None,
                    page: int = 1, page_size: int | None = None,
                    order_by: str = "roll_no",
                    enforce_scope: bool = True) -> tuple[list, int]:
    """Paginated student search.

    Returns ``(rows, total_count)`` so the grid can render page controls.
    """
    page_size = page_size or int(config.get("rows_per_page", 25))
    where, params = _build_filter(branch_id, semester_id, section_id, batch_id,
                                  session_id, status, search, face_registered,
                                  enforce_scope=enforce_scope)

    total = int(get_db().fetch_value(
        f"SELECT COUNT(*) FROM v_student_full WHERE {where}", params, 0))

    safe_order = order_by if order_by in {
        "roll_no", "full_name", "enrollment_no", "semester_number", "branch_name",
        "student_id", "status",
    } else "roll_no"

    offset = max(0, (page - 1) * page_size)
    rows = get_db().fetch_all(
        f"SELECT * FROM v_student_full WHERE {where} "
        f"ORDER BY {safe_order} LIMIT ? OFFSET ?",
        params + [page_size, offset])
    return rows, total


def get_class_students(branch_id: int, semester_id: int, section_id: int | None = None,
                       active_only: bool = True) -> list:
    """Roster for one class -- the list a faculty marks attendance against."""
    clauses = ["branch_id = ?", "semester_id = ?"]
    params = [branch_id, semester_id]
    if section_id:
        clauses.append("section_id = ?")
        params.append(section_id)
    if active_only:
        clauses.append("status = 'Active'")
    return get_db().fetch_all(
        f"SELECT * FROM v_student_full WHERE {' AND '.join(clauses)} "
        f"ORDER BY CAST(roll_no AS INTEGER), roll_no", params)


def get_students_with_faces() -> list:
    """Registered students plus their stored encodings -- loaded by the recogniser."""
    return get_db().fetch_all(
        """SELECT s.student_id, s.enrollment_no, s.roll_no, s.full_name,
                  s.branch_id, s.semester_id, s.section_id, s.photo_path,
                  fe.encoding_blob, fe.backend, fe.sample_count
           FROM students s
           JOIN face_encodings fe ON fe.student_id = s.student_id
           WHERE s.status = 'Active' AND s.face_registered = 1""")


def count_students(status: str | None = "Active", **filters) -> int:
    where, params = _build_filter(status=status, **filters)
    return int(get_db().fetch_value(
        f"SELECT COUNT(*) FROM v_student_full WHERE {where}", params, 0))


def next_enrollment_no(branch_code: str, admission_year: int | None = None) -> str:
    """Suggest the next enrollment number, e.g. ``DCS26001``.

    Format: ``D`` + branch code (3 chars) + 2-digit year + 3-digit serial.
    """
    year = admission_year or datetime.now().year
    prefix = f"D{branch_code.upper()[:3]}{str(year)[-2:]}"
    last = get_db().fetch_value(
        "SELECT enrollment_no FROM students WHERE enrollment_no LIKE ? "
        "ORDER BY enrollment_no DESC LIMIT 1", (f"{prefix}%",))
    if last:
        try:
            return f"{prefix}{int(str(last)[len(prefix):]) + 1:03d}"
        except (ValueError, IndexError):
            pass
    return f"{prefix}001"


# ===========================================================================
# Create / update / delete
# ===========================================================================
def add_student(data: dict, photo_source: str | Path | None = None,
                user: dict | None = None) -> tuple[bool, str, int | None]:
    """Insert a student and file their photograph under the naming convention."""
    db = get_db()
    payload = _clean(data)

    for field in ("enrollment_no", "roll_no", "full_name", "branch_id", "semester_id"):
        if not payload.get(field):
            return False, f"'{field.replace('_', ' ').title()}' is required.", None

    if db.exists("students", "enrollment_no = ?", (payload["enrollment_no"],)):
        return False, f"Enrollment number '{payload['enrollment_no']}' already exists.", None

    # Roll numbers only need to be unique inside a class.
    if db.exists("students",
                 "roll_no = ? AND branch_id = ? AND semester_id = ? "
                 "AND IFNULL(section_id,0) = IFNULL(?,0)",
                 (payload["roll_no"], payload["branch_id"], payload["semester_id"],
                  payload.get("section_id"))):
        return False, f"Roll number '{payload['roll_no']}' already exists in this class.", None

    payload.setdefault("status", "Active")

    if photo_source:
        stored = _store_photo(photo_source, payload["enrollment_no"], payload["full_name"])
        if stored:
            payload["photo_path"] = str(stored)

    try:
        student_id = db.insert("students", payload)
    except Exception as exc:                       # noqa: BLE001
        logger.error("Failed to insert student: %s", exc)
        return False, f"Could not save the student: {exc}", None

    log_audit(user, ACTION_CREATE, "Students", "students", student_id,
              new_value={"enrollment_no": payload["enrollment_no"],
                         "full_name": payload["full_name"]})
    logger.info("Student added: %s (%s)", payload["full_name"], payload["enrollment_no"])
    return True, f"Student '{payload['full_name']}' added successfully.", student_id


def update_student(student_id: int, data: dict, photo_source: str | Path | None = None,
                   user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    old = get_student(student_id)
    if old is None:
        return False, "Student not found."

    payload = _clean(data)

    if "enrollment_no" in payload and db.exists(
            "students", "enrollment_no = ? AND student_id <> ?",
            (payload["enrollment_no"], student_id)):
        return False, "Another student already uses that enrollment number."

    if photo_source:
        stored = _store_photo(
            photo_source,
            payload.get("enrollment_no", old["enrollment_no"]),
            payload.get("full_name", old["full_name"]))
        if stored:
            payload["photo_path"] = str(stored)

    payload["updated_at"] = _now()
    db.update("students", payload, "student_id = ?", (student_id,))

    changed = {k: v for k, v in payload.items()
               if k != "updated_at" and str(old[k] if k in old.keys() else "") != str(v)}
    log_audit(user, ACTION_UPDATE, "Students", "students", student_id,
              old_value={k: old[k] for k in changed if k in old.keys()},
              new_value=changed)
    return True, "Student updated successfully."


def delete_student(student_id: int, remove_files: bool = True,
                   user: dict | None = None) -> tuple[bool, str]:
    """Delete a student.  Attendance rows cascade; files are removed on request."""
    db = get_db()
    student = get_student(student_id)
    if student is None:
        return False, "Student not found."

    attendance_rows = db.count("attendance", "student_id = ?", (student_id,))

    if remove_files:
        for path_value in (student["photo_path"], student["dataset_path"]):
            if not path_value:
                continue
            path = Path(path_value)
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.is_file():
                    path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove %s: %s", path, exc)

    db.delete("users", "role = 'Student' AND linked_id = ?", (student_id,))
    db.delete("students", "student_id = ?", (student_id,))

    log_audit(user, ACTION_DELETE, "Students", "students", student_id,
              old_value={"enrollment_no": student["enrollment_no"],
                         "full_name": student["full_name"]},
              reason=f"{attendance_rows} attendance record(s) removed with the student")
    return True, (f"Student '{student['full_name']}' deleted "
                  f"along with {attendance_rows} attendance record(s).")


def set_status(student_id: int, status: str, reason: str = "",
               user: dict | None = None) -> tuple[bool, str]:
    old = get_student(student_id)
    if old is None:
        return False, "Student not found."
    get_db().update("students", {"status": status, "updated_at": _now()},
                    "student_id = ?", (student_id,))
    log_audit(user, ACTION_UPDATE, "Students", "students", student_id,
              old_value={"status": old["status"]}, new_value={"status": status},
              reason=reason)
    return True, f"Status changed to {status}."


def _store_photo(source: str | Path, enrollment_no: str, full_name: str) -> Path | None:
    """Copy a chosen photo into storage under the naming convention."""
    source = Path(source)
    if not source.exists():
        logger.warning("Photo source does not exist: %s", source)
        return None
    STUDENT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = student_image_path(enrollment_no, full_name, source.suffix.lower() or ".jpg")
    try:
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return target
    except OSError as exc:
        logger.error("Could not store photo for %s: %s", enrollment_no, exc)
        return None


# ===========================================================================
# Face dataset bookkeeping
# ===========================================================================
def mark_face_registered(student_id: int, dataset_dir: str | Path, sample_count: int,
                         user: dict | None = None) -> None:
    get_db().update("students", {
        "dataset_path": str(dataset_dir),
        "face_registered": 1,
        "face_sample_count": sample_count,
        "updated_at": _now(),
    }, "student_id = ?", (student_id,))
    log_audit(user, "FACE_REGISTER", "Students", "students", student_id,
              new_value={"samples": sample_count})


def clear_face_data(student_id: int, user: dict | None = None) -> tuple[bool, str]:
    """Remove a student's dataset and encodings so it can be recaptured."""
    db = get_db()
    student = get_student(student_id)
    if student is None:
        return False, "Student not found."

    if student["dataset_path"]:
        shutil.rmtree(Path(student["dataset_path"]), ignore_errors=True)

    db.delete("face_encodings", "student_id = ?", (student_id,))
    db.update("students", {
        "face_registered": 0, "face_sample_count": 0,
        "dataset_path": None, "updated_at": _now(),
    }, "student_id = ?", (student_id,))

    log_audit(user, ACTION_DELETE, "Students", "face_encodings", student_id,
              reason="Face dataset cleared for re-capture")
    return True, "Face dataset cleared. You can capture it again."


# ===========================================================================
# Bulk promotion
# ===========================================================================
def promote_students(branch_id: int, from_semester_id: int, to_semester_id: int,
                     section_id: int | None = None, to_session_id: int | None = None,
                     student_ids: list[int] | None = None,
                     user: dict | None = None) -> tuple[bool, str, int]:
    """Move a cohort into the next semester.

    Attendance history is deliberately untouched -- rows stay bound to the
    ``attendance_sessions`` they were recorded against, so past percentages
    remain correct after promotion.
    """
    db = get_db()

    if from_semester_id == to_semester_id:
        return False, "Source and target semester must be different.", 0

    if student_ids:
        placeholders = ",".join("?" * len(student_ids))
        candidates = db.fetch_all(
            f"SELECT student_id, semester_id, session_id FROM students "
            f"WHERE student_id IN ({placeholders}) AND status = 'Active'", student_ids)
    else:
        clauses = ["branch_id = ?", "semester_id = ?", "status = 'Active'"]
        params = [branch_id, from_semester_id]
        if section_id:
            clauses.append("section_id = ?")
            params.append(section_id)
        candidates = db.fetch_all(
            f"SELECT student_id, semester_id, session_id FROM students "
            f"WHERE {' AND '.join(clauses)}", params)

    if not candidates:
        return False, "No active students matched the selection.", 0

    promoted = 0
    try:
        with db.transaction() as cur:
            for student in candidates:
                updates = {"semester_id": to_semester_id, "updated_at": _now()}
                if to_session_id:
                    updates["session_id"] = to_session_id

                assignments = ", ".join(f"{k} = ?" for k in updates)
                cur.execute(f"UPDATE students SET {assignments} WHERE student_id = ?",
                            tuple(updates.values()) + (student["student_id"],))

                cur.execute(
                    """INSERT INTO promotion_history
                       (student_id, from_semester_id, to_semester_id,
                        from_session_id, to_session_id, promoted_by, remarks)
                       VALUES (?,?,?,?,?,?,?)""",
                    (student["student_id"], student["semester_id"], to_semester_id,
                     student["session_id"], to_session_id or student["session_id"],
                     (user or {}).get("user_id"), "Bulk semester promotion"))
                promoted += 1
    except Exception as exc:                       # noqa: BLE001
        logger.error("Promotion failed: %s", exc)
        return False, f"Promotion failed and was rolled back: {exc}", 0

    log_audit(user, ACTION_PROMOTION, "Students", "students", None,
              old_value={"semester_id": from_semester_id},
              new_value={"semester_id": to_semester_id, "count": promoted},
              reason="Bulk semester promotion")
    logger.info("Promoted %d students to semester_id=%s", promoted, to_semester_id)
    return True, f"{promoted} student(s) promoted. Attendance history preserved.", promoted


def get_promotion_history(student_id: int | None = None, limit: int = 200) -> list:
    clause = "WHERE p.student_id = ?" if student_id else ""
    params = (student_id,) if student_id else ()
    return get_db().fetch_all(
        f"""SELECT p.*, s.enrollment_no, s.full_name,
                   fs.semester_name AS from_semester, ts.semester_name AS to_semester,
                   u.username AS promoted_by_name
            FROM promotion_history p
            JOIN students s ON s.student_id = p.student_id
            LEFT JOIN semesters fs ON fs.semester_id = p.from_semester_id
            LEFT JOIN semesters ts ON ts.semester_id = p.to_semester_id
            LEFT JOIN users u ON u.user_id = p.promoted_by
            {clause} ORDER BY p.promotion_id DESC LIMIT {int(limit)}""", params)
