"""
Attendance repository -- the core of the system.

Model
-----
Taking a class creates one ``attendance_sessions`` row and pre-fills one
``attendance`` row per enrolled student with status ``Absent``.  Recognising a
face (or a manual click) *updates* that row rather than inserting a new one.
That single decision buys three things the specification asks for:

*   **duplicate prevention** -- the ``(att_session_id, student_id)`` unique
    constraint makes a second mark physically impossible;
*   **absentees for free** -- anyone never recognised simply keeps the default;
*   **a clean audit story** -- every later change is an UPDATE with a recorded
    before-value and a mandatory reason.

Locking freezes a session.  Faculty may lock; only an administrator may unlock.
"""

from __future__ import annotations

from datetime import datetime

from config.settings import (MARK_METHOD_FACE, MARK_METHOD_MANUAL,
                             PRESENT_LIKE, STATUS_ABSENT, STATUS_LATE,
                             STATUS_PRESENT, config)
from core.audit import (ACTION_ATTENDANCE_EDIT, ACTION_ATTENDANCE_LOCK,
                        ACTION_ATTENDANCE_MARK, ACTION_ATTENDANCE_UNLOCK,
                        ACTION_DELETE, log_audit)
from core.database import get_db
from core.logger import get_logger
from models.academic import is_holiday
from models.student import get_class_students

logger = get_logger("models.attendance")


def _now_time() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ===========================================================================
# Session lifecycle
# ===========================================================================
def open_session(subject_id: int, branch_id: int, semester_id: int,
                 section_id: int | None = None, faculty_id: int | None = None,
                 class_date: str | None = None, start_time: str | None = None,
                 end_time: str | None = None, timetable_id: int | None = None,
                 mode: str = MARK_METHOD_FACE, session_id: int | None = None,
                 user: dict | None = None,
                 allow_holiday: bool = False) -> tuple[bool, str, int | None]:
    """Open (or reopen) an attendance session and seed every student as Absent.

    Reopening an existing unlocked session is intentional -- a faculty who
    closes the camera by accident must be able to resume without losing the
    marks already made.
    """
    db = get_db()
    class_date = class_date or _today()
    start_time = start_time or datetime.now().strftime("%H:%M")

    holiday, holiday_name = is_holiday(class_date)
    if holiday and not allow_holiday:
        return False, f"{class_date} is a holiday ({holiday_name}). Attendance is disabled.", None

    existing = db.fetch_one(
        """SELECT * FROM attendance_sessions
           WHERE subject_id = ? AND class_date = ?
             AND IFNULL(section_id,0) = IFNULL(?,0) AND start_time = ?""",
        (subject_id, class_date, section_id, start_time))

    if existing:
        if existing["is_locked"]:
            return False, ("Attendance for this class is locked. "
                           "Ask an administrator to unlock it."), None
        return True, "Resuming the existing attendance session.", existing["att_session_id"]

    students = get_class_students(branch_id, semester_id, section_id)
    if not students:
        return False, "No active students found for this class.", None

    if session_id is None:
        from models.academic import get_current_session_id
        session_id = get_current_session_id()

    try:
        with db.transaction() as cur:
            cur.execute(
                """INSERT INTO attendance_sessions
                   (subject_id, faculty_id, branch_id, semester_id, section_id,
                    session_id, timetable_id, class_date, start_time, end_time,
                    mode, total_students, absent_count, created_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (subject_id, faculty_id, branch_id, semester_id, section_id,
                 session_id, timetable_id, class_date, start_time, end_time,
                 mode, len(students), len(students), (user or {}).get("user_id")))
            att_session_id = cur.lastrowid

            # Seed every student as Absent; recognition flips them to Present.
            cur.executemany(
                """INSERT INTO attendance
                   (att_session_id, student_id, subject_id, class_date,
                    status, marked_method)
                   VALUES (?,?,?,?,?,?)""",
                [(att_session_id, s["student_id"], subject_id, class_date,
                  STATUS_ABSENT, "Auto (Absent)") for s in students])
    except Exception as exc:                       # noqa: BLE001
        logger.error("Could not open attendance session: %s", exc)
        return False, f"Could not open the attendance session: {exc}", None

    log_audit(user, ACTION_ATTENDANCE_MARK, "Attendance", "attendance_sessions",
              att_session_id,
              new_value={"subject_id": subject_id, "date": class_date,
                         "students": len(students), "mode": mode},
              reason="Attendance session opened")
    logger.info("Attendance session %s opened for subject %s (%d students)",
                att_session_id, subject_id, len(students))
    return True, f"Attendance session opened for {len(students)} student(s).", att_session_id


def get_session(att_session_id: int):
    return get_db().fetch_one(
        """SELECT a.*, s.subject_code, s.subject_name, b.branch_name, b.branch_code,
                  sem.semester_name, sec.section_name, f.full_name AS faculty_name,
                  acs.session_name
           FROM attendance_sessions a
           JOIN subjects  s   ON s.subject_id  = a.subject_id
           JOIN branches  b   ON b.branch_id   = a.branch_id
           JOIN semesters sem ON sem.semester_id = a.semester_id
           LEFT JOIN sections sec ON sec.section_id = a.section_id
           LEFT JOIN faculty  f   ON f.faculty_id  = a.faculty_id
           LEFT JOIN academic_sessions acs ON acs.session_id = a.session_id
           WHERE a.att_session_id = ?""", (att_session_id,))


def get_session_records(att_session_id: int) -> list:
    """Every student in a session with their current mark, ordered by roll number."""
    return get_db().fetch_all(
        """SELECT a.*, s.enrollment_no, s.roll_no, s.full_name, s.photo_path,
                  s.face_registered
           FROM attendance a JOIN students s ON s.student_id = a.student_id
           WHERE a.att_session_id = ?
           ORDER BY CAST(s.roll_no AS INTEGER), s.roll_no""", (att_session_id,))


def close_session(att_session_id: int, lock: bool = False,
                  remarks: str = "", user: dict | None = None) -> tuple[bool, str]:
    """Finalise counts and optionally lock the session."""
    db = get_db()
    session_row = get_session(att_session_id)
    if session_row is None:
        return False, "Attendance session not found."

    counts = db.fetch_one(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status IN ('Present','Late') THEN 1 ELSE 0 END) AS present,
                  SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) AS absent
           FROM attendance WHERE att_session_id = ?""", (att_session_id,))

    updates = {
        "total_students": counts["total"] or 0,
        "present_count": counts["present"] or 0,
        "absent_count": counts["absent"] or 0,
        "end_time": datetime.now().strftime("%H:%M"),
        "remarks": remarks or None,
    }
    if lock:
        updates.update({
            "is_locked": 1,
            "locked_by": (user or {}).get("user_id"),
            "locked_at": _now(),
        })

    db.update("attendance_sessions", updates, "att_session_id = ?", (att_session_id,))

    log_audit(user, ACTION_ATTENDANCE_LOCK if lock else ACTION_ATTENDANCE_MARK,
              "Attendance", "attendance_sessions", att_session_id,
              new_value=updates, reason="Session closed" + (" and locked" if lock else ""))

    return True, (f"Attendance saved: {updates['present_count']} present, "
                  f"{updates['absent_count']} absent."
                  + (" Session locked." if lock else ""))


def lock_session(att_session_id: int, user: dict | None = None) -> tuple[bool, str]:
    return close_session(att_session_id, lock=True, user=user)


def unlock_session(att_session_id: int, reason: str,
                   user: dict | None = None) -> tuple[bool, str]:
    """Administrator-only.  A reason is mandatory and lands in the audit trail."""
    if not reason or not reason.strip():
        return False, "A reason is required to unlock attendance."

    get_db().update("attendance_sessions",
                    {"is_locked": 0, "locked_by": None, "locked_at": None},
                    "att_session_id = ?", (att_session_id,))
    log_audit(user, ACTION_ATTENDANCE_UNLOCK, "Attendance", "attendance_sessions",
              att_session_id, new_value={"is_locked": 0}, reason=reason.strip())
    return True, "Attendance session unlocked."


def is_locked(att_session_id: int) -> bool:
    return bool(get_db().fetch_value(
        "SELECT is_locked FROM attendance_sessions WHERE att_session_id = ?",
        (att_session_id,), 0))


def delete_session(att_session_id: int, reason: str,
                   user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    session_row = get_session(att_session_id)
    if session_row is None:
        return False, "Attendance session not found."
    if session_row["is_locked"]:
        return False, "Unlock the session before deleting it."

    removed = db.count("attendance", "att_session_id = ?", (att_session_id,))
    db.delete("attendance_sessions", "att_session_id = ?", (att_session_id,))

    log_audit(user, ACTION_DELETE, "Attendance", "attendance_sessions", att_session_id,
              old_value={"subject": session_row["subject_name"],
                         "date": session_row["class_date"], "records": removed},
              reason=reason)
    return True, f"Attendance session deleted ({removed} record(s))."


def search_sessions(subject_id=None, faculty_id=None, branch_id=None, semester_id=None,
                    section_id=None, from_date=None, to_date=None,
                    locked_only: bool = False, limit: int = 300,
                    enforce_scope: bool = True) -> list:
    """Conducted class sessions, restricted to what the caller may see.

    A student sees only sessions they were on the roster for; a lecturer only
    sessions for their own subjects.
    """
    clauses, params = ["1=1"], []

    if enforce_scope:
        from core.scope import current_scope
        clause, scope_params = current_scope().session_clause(
            subject_col="a.subject_id", faculty_col="a.faculty_id",
            session_id_col="a.att_session_id")
        clauses.append(clause)
        params.extend(scope_params)

    for column, value in (("a.subject_id", subject_id), ("a.faculty_id", faculty_id),
                          ("a.branch_id", branch_id), ("a.semester_id", semester_id),
                          ("a.section_id", section_id)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if from_date:
        clauses.append("a.class_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("a.class_date <= ?")
        params.append(to_date)
    if locked_only:
        clauses.append("a.is_locked = 1")

    return get_db().fetch_all(
        f"""SELECT a.*, s.subject_code, s.subject_name, b.branch_code,
                   sem.semester_name, sec.section_name, f.full_name AS faculty_name
            FROM attendance_sessions a
            JOIN subjects  s   ON s.subject_id  = a.subject_id
            JOIN branches  b   ON b.branch_id   = a.branch_id
            JOIN semesters sem ON sem.semester_id = a.semester_id
            LEFT JOIN sections sec ON sec.section_id = a.section_id
            LEFT JOIN faculty  f   ON f.faculty_id  = a.faculty_id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.class_date DESC, a.start_time DESC LIMIT ?""",
        params + [limit])


# ===========================================================================
# Marking
# ===========================================================================
def mark_by_face(att_session_id: int, student_id: int, confidence: float,
                 snapshot_path: str | None = None,
                 user: dict | None = None) -> tuple[bool, str]:
    """Mark a student from a face match.

    Returns ``(changed, message)``.  ``changed`` is False when the student was
    already marked -- the caller uses that to keep the live counter honest
    without treating a repeat detection as an error.
    """
    db = get_db()

    record = db.fetch_one(
        """SELECT a.*, s.full_name, s.roll_no FROM attendance a
           JOIN students s ON s.student_id = a.student_id
           WHERE a.att_session_id = ? AND a.student_id = ?""",
        (att_session_id, student_id))

    if record is None:
        return False, "This student is not enrolled in the class being marked."

    # Duplicate prevention: already recognised in this session.
    if record["marked_method"] == MARK_METHOD_FACE and record["status"] in PRESENT_LIKE:
        return False, f"{record['full_name']} is already marked."

    # A manual mark by the faculty outranks the camera.
    if record["marked_method"] == MARK_METHOD_MANUAL:
        return False, f"{record['full_name']} was already marked manually."

    status = _status_for_arrival(att_session_id)

    db.update("attendance", {
        "status": status,
        "marked_method": MARK_METHOD_FACE,
        "confidence": round(float(confidence), 2),
        "marked_time": _now_time(),
        "snapshot_path": snapshot_path,
    }, "attendance_id = ?", (record["attendance_id"],))

    _refresh_counts(att_session_id)
    return True, f"{record['full_name']} ({record['roll_no']}) marked {status}."


def _status_for_arrival(att_session_id: int) -> str:
    """Present, or Late when the student arrives after the grace period."""
    session_row = get_db().fetch_one(
        "SELECT start_time, class_date FROM attendance_sessions WHERE att_session_id = ?",
        (att_session_id,))
    if session_row is None or not session_row["start_time"]:
        return STATUS_PRESENT

    try:
        started = datetime.strptime(
            f"{session_row['class_date']} {session_row['start_time']}", "%Y-%m-%d %H:%M")
        minutes_late = (datetime.now() - started).total_seconds() / 60
        return STATUS_LATE if minutes_late > int(config.get("late_threshold_minutes", 10)) \
            else STATUS_PRESENT
    except (ValueError, TypeError):
        return STATUS_PRESENT


def mark_manual(att_session_id: int, student_id: int, status: str,
                reason: str = "", remarks: str = "",
                user: dict | None = None) -> tuple[bool, str]:
    """Faculty override.  Changing a face-marked record requires a reason."""
    db = get_db()

    if is_locked(att_session_id):
        return False, "This attendance session is locked and cannot be edited."

    record = db.fetch_one(
        """SELECT a.*, s.full_name, s.roll_no FROM attendance a
           JOIN students s ON s.student_id = a.student_id
           WHERE a.att_session_id = ? AND a.student_id = ?""",
        (att_session_id, student_id))
    if record is None:
        return False, "Attendance record not found."

    if record["status"] == status and record["marked_method"] == MARK_METHOD_MANUAL:
        return True, "No change."

    # Overriding the recogniser is allowed, but it must be justified.
    was_face_marked = record["marked_method"] == MARK_METHOD_FACE
    if was_face_marked and not reason.strip():
        return False, ("This record was marked by face recognition. "
                       "Please provide a reason for changing it.")

    db.update("attendance", {
        "status": status,
        "marked_method": MARK_METHOD_MANUAL,
        "marked_time": _now_time(),
        "is_modified": 1 if was_face_marked or record["is_modified"] else 0,
        "modified_by": (user or {}).get("user_id"),
        "modified_at": _now(),
        "modify_reason": reason.strip() or record["modify_reason"],
        "remarks": remarks.strip() or record["remarks"],
    }, "attendance_id = ?", (record["attendance_id"],))

    _refresh_counts(att_session_id)

    if was_face_marked or record["is_modified"]:
        log_audit(user, ACTION_ATTENDANCE_EDIT, "Attendance", "attendance",
                  record["attendance_id"],
                  old_value={"status": record["status"],
                             "method": record["marked_method"],
                             "confidence": record["confidence"]},
                  new_value={"status": status, "method": MARK_METHOD_MANUAL},
                  reason=reason.strip() or "Manual correction")

    return True, f"{record['full_name']} marked {status}."


def mark_all(att_session_id: int, status: str, only_unmarked: bool = True,
             reason: str = "", user: dict | None = None) -> tuple[bool, str, int]:
    """'Mark All Present' / 'Mark All Absent'.

    ``only_unmarked`` protects records the camera already resolved; turning it
    off is a bulk override and is audited as such.
    """
    db = get_db()

    if is_locked(att_session_id):
        return False, "This attendance session is locked and cannot be edited.", 0

    if only_unmarked:
        where = ("att_session_id = ? AND marked_method = 'Auto (Absent)'")
        params = (att_session_id,)
    else:
        if not reason.strip():
            return False, "A reason is required to overwrite already-marked records.", 0
        where = "att_session_id = ?"
        params = (att_session_id,)

    affected = db.execute(
        f"""UPDATE attendance
            SET status = ?, marked_method = ?, marked_time = ?,
                modified_by = ?, modified_at = ?, modify_reason = ?,
                is_modified = CASE WHEN marked_method = 'Auto (Absent)' THEN is_modified ELSE 1 END
            WHERE {where}""",
        (status, MARK_METHOD_MANUAL, _now_time(), (user or {}).get("user_id"),
         _now(), reason.strip() or None) + params)

    _refresh_counts(att_session_id)

    log_audit(user, ACTION_ATTENDANCE_EDIT, "Attendance", "attendance_sessions",
              att_session_id, new_value={"bulk_status": status, "rows": affected},
              reason=reason.strip() or f"Bulk mark all as {status}")

    return True, f"{affected} student(s) marked {status}.", affected


def apply_leave_status(student_id: int, from_date: str, to_date: str, status: str,
                       leave_id: int, user: dict | None = None) -> int:
    """Push an approved leave into attendance for the covered dates.

    Only ``Absent`` rows are touched -- a student physically recognised in
    class stays Present even if a leave was later approved for that day.
    """
    db = get_db()
    affected = db.execute(
        """UPDATE attendance
           SET status = ?, marked_method = 'Leave System',
               modified_by = ?, modified_at = ?, is_modified = 1,
               modify_reason = ?
           WHERE student_id = ? AND class_date BETWEEN ? AND ?
             AND status = 'Absent'
             AND att_session_id IN (SELECT att_session_id FROM attendance_sessions
                                    WHERE is_locked = 0)""",
        (status, (user or {}).get("user_id"), _now(),
         f"Approved leave #{leave_id}", student_id, from_date, to_date))

    for row in db.fetch_all(
            """SELECT DISTINCT att_session_id FROM attendance
               WHERE student_id = ? AND class_date BETWEEN ? AND ?""",
            (student_id, from_date, to_date)):
        _refresh_counts(row["att_session_id"])

    if affected:
        log_audit(user, ACTION_ATTENDANCE_EDIT, "Attendance", "attendance", None,
                  new_value={"status": status, "rows": affected},
                  reason=f"Leave #{leave_id} approved for {from_date} to {to_date}")
    return affected


def _refresh_counts(att_session_id: int) -> None:
    """Keep the denormalised counters on the session row accurate."""
    get_db().execute(
        """UPDATE attendance_sessions SET
             total_students = (SELECT COUNT(*) FROM attendance WHERE att_session_id = ?),
             present_count  = (SELECT COUNT(*) FROM attendance
                               WHERE att_session_id = ? AND status IN ('Present','Late')),
             absent_count   = (SELECT COUNT(*) FROM attendance
                               WHERE att_session_id = ? AND status = 'Absent')
           WHERE att_session_id = ?""",
        (att_session_id, att_session_id, att_session_id, att_session_id))


def refresh_counts(att_session_id: int) -> None:
    """Public alias for :func:`_refresh_counts`.

    Other modules (for example the self-attendance approval flow) change an
    attendance row directly and must resync the session counters afterwards.
    """
    _refresh_counts(att_session_id)


def record_unknown_face(att_session_id: int, snapshot_path: str | None,
                        best_match_id: int | None = None,
                        best_confidence: float | None = None) -> None:
    """Log a detected face that matched nobody, for later review."""
    get_db().insert("unknown_faces", {
        "att_session_id": att_session_id,
        "snapshot_path": snapshot_path,
        "best_match_id": best_match_id,
        "best_confidence": round(best_confidence, 2) if best_confidence else None,
    })


def get_unknown_faces(att_session_id: int | None = None, limit: int = 100) -> list:
    clause = "WHERE u.att_session_id = ?" if att_session_id else ""
    params = (att_session_id,) if att_session_id else ()
    return get_db().fetch_all(
        f"""SELECT u.*, s.full_name AS best_match_name, s.enrollment_no
            FROM unknown_faces u
            LEFT JOIN students s ON s.student_id = u.best_match_id
            {clause} ORDER BY u.unknown_id DESC LIMIT {int(limit)}""", params)


# ===========================================================================
# Queries & statistics
# ===========================================================================
def get_student_summary(student_id: int, session_id: int | None = None,
                        semester_id: int | None = None) -> dict:
    """Overall attendance figures for one student."""
    clauses, params = ["a.student_id = ?"], [student_id]
    if session_id:
        clauses.append("ats.session_id = ?")
        params.append(session_id)
    if semester_id:
        clauses.append("ats.semester_id = ?")
        params.append(semester_id)

    row = get_db().fetch_one(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
                   SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present,
                   SUM(CASE WHEN a.status = 'Absent'  THEN 1 ELSE 0 END) AS absent,
                   SUM(CASE WHEN a.status = 'Late'    THEN 1 ELSE 0 END) AS late,
                   SUM(CASE WHEN a.status = 'Leave'   THEN 1 ELSE 0 END) AS leave_count,
                   SUM(CASE WHEN a.status = 'Medical Leave' THEN 1 ELSE 0 END) AS medical
            FROM attendance a
            JOIN attendance_sessions ats ON ats.att_session_id = a.att_session_id
            WHERE {' AND '.join(clauses)}""", params)

    total = (row["total"] or 0) if row else 0
    attended = (row["attended"] or 0) if row else 0
    return {
        "total": total,
        "attended": attended,
        "present": (row["present"] or 0) if row else 0,
        "absent": (row["absent"] or 0) if row else 0,
        "late": (row["late"] or 0) if row else 0,
        "leave": (row["leave_count"] or 0) if row else 0,
        "medical": (row["medical"] or 0) if row else 0,
        "percentage": round(100.0 * attended / total, 2) if total else 0.0,
    }


def get_student_subject_summary(student_id: int) -> list:
    return get_db().fetch_all(
        "SELECT * FROM v_attendance_summary WHERE student_id = ? ORDER BY subject_name",
        (student_id,))


def get_student_trend(student_id: int, days: int = 30) -> list:
    """Day-by-day attendance percentage -- feeds the student trend chart."""
    return get_db().fetch_all(
        """SELECT class_date,
                  COUNT(*) AS total,
                  SUM(CASE WHEN status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended
           FROM attendance
           WHERE student_id = ? AND class_date >= date('now', ?)
           GROUP BY class_date ORDER BY class_date""",
        (student_id, f"-{int(days)} days"))


def get_today_stats(branch_id: int | None = None) -> dict:
    """Headline numbers for the admin dashboard."""
    db = get_db()
    clauses, params = ["a.class_date = ?"], [_today()]
    if branch_id:
        clauses.append("ats.branch_id = ?")
        params.append(branch_id)

    row = db.fetch_one(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present,
                   SUM(CASE WHEN a.status = 'Absent'  THEN 1 ELSE 0 END) AS absent,
                   SUM(CASE WHEN a.status = 'Late'    THEN 1 ELSE 0 END) AS late,
                   SUM(CASE WHEN a.status = 'Leave'   THEN 1 ELSE 0 END) AS leave_count,
                   SUM(CASE WHEN a.status = 'Medical Leave' THEN 1 ELSE 0 END) AS medical
            FROM attendance a
            JOIN attendance_sessions ats ON ats.att_session_id = a.att_session_id
            WHERE {' AND '.join(clauses)}""", params)

    total = (row["total"] or 0) if row else 0
    present = ((row["present"] or 0) + (row["late"] or 0)) if row else 0
    return {
        "total": total,
        "present": (row["present"] or 0) if row else 0,
        "absent": (row["absent"] or 0) if row else 0,
        "late": (row["late"] or 0) if row else 0,
        "leave": (row["leave_count"] or 0) if row else 0,
        "medical": (row["medical"] or 0) if row else 0,
        "percentage": round(100.0 * present / total, 2) if total else 0.0,
        "classes_held": db.count("attendance_sessions", "class_date = ?", (_today(),)),
    }


def get_defaulters(threshold: float | None = None, branch_id=None, semester_id=None,
                   section_id=None, session_id=None, subject_id=None) -> list:
    """Students below the attendance threshold -- the exam-eligibility list."""
    threshold = threshold if threshold is not None else float(config.get("attendance_threshold", 75.0))

    clauses, params = ["s.status = 'Active'"], []
    # Row-level restriction for the signed-in user (see core/scope.py).
    from core.scope import current_scope
    _scope_clause, _scope_params = current_scope().attendance_clause(
        student_col="s.student_id", subject_col="a.subject_id")
    clauses.append(_scope_clause)
    params.extend(_scope_params)

    if branch_id:
        clauses.append("s.branch_id = ?")
        params.append(branch_id)
    if semester_id:
        clauses.append("s.semester_id = ?")
        params.append(semester_id)
    if section_id:
        clauses.append("s.section_id = ?")
        params.append(section_id)
    if session_id:
        clauses.append("ats.session_id = ?")
        params.append(session_id)
    if subject_id:
        clauses.append("a.subject_id = ?")
        params.append(subject_id)

    return get_db().fetch_all(
        f"""SELECT s.student_id, s.enrollment_no, s.roll_no, s.full_name,
                   s.mobile, s.email, b.branch_name, b.branch_code,
                   sem.semester_name, sec.section_name,
                   COUNT(a.attendance_id) AS total_classes,
                   SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
                   ROUND(100.0 * SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(a.attendance_id),0), 2) AS percentage
            FROM students s
            JOIN attendance a ON a.student_id = s.student_id
            JOIN attendance_sessions ats ON ats.att_session_id = a.att_session_id
            JOIN branches  b   ON b.branch_id = s.branch_id
            JOIN semesters sem ON sem.semester_id = s.semester_id
            LEFT JOIN sections sec ON sec.section_id = s.section_id
            WHERE {' AND '.join(clauses)}
            GROUP BY s.student_id
            HAVING COUNT(a.attendance_id) > 0 AND percentage < ?
            ORDER BY percentage ASC""", params + [threshold])


def check_eligibility(student_id: int, threshold: float | None = None) -> dict:
    """Exam-eligibility verdict for one student, with the shortfall in classes."""
    threshold = threshold if threshold is not None else float(config.get("attendance_threshold", 75.0))
    summary = get_student_summary(student_id)

    eligible = summary["percentage"] >= threshold
    shortfall = 0
    if not eligible and summary["total"]:
        # Classes that must be attended consecutively to reach the threshold:
        #   (attended + x) / (total + x) >= t/100
        attended, total = summary["attended"], summary["total"]
        rate = threshold / 100.0
        if rate < 1.0:
            shortfall = max(0, int(-(-(rate * total - attended) / (1 - rate) // 1)))

    return {
        **summary,
        "threshold": threshold,
        "eligible": eligible,
        "shortfall_classes": shortfall,
        "verdict": "Eligible" if eligible else "Not Eligible",
    }


def get_attendance_records(from_date=None, to_date=None, branch_id=None, semester_id=None,
                           section_id=None, subject_id=None, faculty_id=None,
                           student_id=None, status=None, session_id=None,
                           search: str = "", limit: int = 5000,
                           enforce_scope: bool = True) -> list:
    """The general-purpose query behind every report and the search screen.

    Args:
        enforce_scope: apply the signed-in user's row-level restriction. A
            student therefore sees only their own rows and a lecturer only
            their own subjects, whatever filters the caller passes. Set False
            only for administrative jobs that legitimately span everyone.
    """
    clauses, params = ["1=1"], []

    if enforce_scope:
        from core.scope import current_scope
        clause, scope_params = current_scope().attendance_clause()
        clauses.append(clause)
        params.extend(scope_params)

    filters = [
        ("class_date >= ?", from_date), ("class_date <= ?", to_date),
        ("branch_id = ?", branch_id), ("semester_id = ?", semester_id),
        ("section_id = ?", section_id), ("subject_id = ?", subject_id),
        ("faculty_id = ?", faculty_id), ("student_id = ?", student_id),
        ("status = ?", status), ("session_id = ?", session_id),
    ]
    for clause, value in filters:
        if value:
            clauses.append(clause)
            params.append(value)

    if search:
        clauses.append("(enrollment_no LIKE ? OR roll_no LIKE ? OR student_name LIKE ? "
                       "OR subject_name LIKE ? OR faculty_name LIKE ?)")
        params.extend([f"%{search}%"] * 5)

    return get_db().fetch_all(
        f"""SELECT * FROM v_attendance_full WHERE {' AND '.join(clauses)}
            ORDER BY class_date DESC, roll_no LIMIT ?""", params + [limit])


def get_daily_series(from_date: str, to_date: str, branch_id=None,
                     semester_id=None, subject_id=None) -> list:
    """Daily attendance percentage over a range -- the trend chart source."""
    clauses, params = ["a.class_date BETWEEN ? AND ?"], [from_date, to_date]
    # Row-level restriction for the signed-in user (see core/scope.py).
    from core.scope import current_scope
    _scope_clause, _scope_params = current_scope().attendance_clause(
        student_col="a.student_id", subject_col="a.subject_id")
    clauses.append(_scope_clause)
    params.extend(_scope_params)

    if branch_id:
        clauses.append("ats.branch_id = ?")
        params.append(branch_id)
    if semester_id:
        clauses.append("ats.semester_id = ?")
        params.append(semester_id)
    if subject_id:
        clauses.append("a.subject_id = ?")
        params.append(subject_id)

    return get_db().fetch_all(
        f"""SELECT a.class_date,
                   COUNT(*) AS total,
                   SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
                   SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent,
                   ROUND(100.0 * SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(*),0), 2) AS percentage
            FROM attendance a
            JOIN attendance_sessions ats ON ats.att_session_id = a.att_session_id
            WHERE {' AND '.join(clauses)}
            GROUP BY a.class_date ORDER BY a.class_date""", params)


def get_monthly_series(year: int, branch_id=None, semester_id=None) -> list:
    clauses, params = ["strftime('%Y', a.class_date) = ?"], [str(year)]
    # Row-level restriction for the signed-in user (core/scope.py).
    from core.scope import current_scope
    _sc, _sp = current_scope().attendance_clause(
        student_col="a.student_id", subject_col="a.subject_id")
    clauses.append(_sc)
    params.extend(_sp)

    if branch_id:
        clauses.append("ats.branch_id = ?")
        params.append(branch_id)
    if semester_id:
        clauses.append("ats.semester_id = ?")
        params.append(semester_id)

    return get_db().fetch_all(
        f"""SELECT strftime('%m', a.class_date) AS month,
                   COUNT(*) AS total,
                   SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
                   ROUND(100.0 * SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(*),0), 2) AS percentage
            FROM attendance a
            JOIN attendance_sessions ats ON ats.att_session_id = a.att_session_id
            WHERE {' AND '.join(clauses)}
            GROUP BY month ORDER BY month""", params)


def get_comparison(group_by: str, from_date=None, to_date=None) -> list:
    """Comparison series for the analytics charts.

    ``group_by`` is one of ``branch``, ``subject``, ``faculty``, ``semester``.
    """
    columns = {
        "branch":   ("b.branch_name", "branches b ON b.branch_id = ats.branch_id"),
        "subject":  ("sub.subject_name", "subjects sub ON sub.subject_id = a.subject_id"),
        "faculty":  ("f.full_name", "faculty f ON f.faculty_id = ats.faculty_id"),
        "semester": ("sem.semester_name", "semesters sem ON sem.semester_id = ats.semester_id"),
    }
    if group_by not in columns:
        raise ValueError(f"Unsupported grouping: {group_by}")

    label_column, join = columns[group_by]

    clauses, params = ["1=1"], []

    # Comparison charts are the easiest place to leak another lecturer's
    # figures, so the row restriction is applied here rather than left to the
    # analytics screen.  A faculty comparing "faculty" therefore sees a chart
    # containing only themselves, which is the correct answer for them.
    from core.scope import current_scope
    _sc, _sp = current_scope().attendance_clause(
        student_col="a.student_id", subject_col="a.subject_id")
    clauses.append(_sc)
    params.extend(_sp)

    if from_date:
        clauses.append("a.class_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("a.class_date <= ?")
        params.append(to_date)

    return get_db().fetch_all(
        f"""SELECT {label_column} AS label,
                   COUNT(*) AS total,
                   SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
                   ROUND(100.0 * SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(*),0), 2) AS percentage
            FROM attendance a
            JOIN attendance_sessions ats ON ats.att_session_id = a.att_session_id
            JOIN {join}
            WHERE {' AND '.join(clauses)}
            GROUP BY label HAVING total > 0 ORDER BY percentage DESC""", params)


def get_status_distribution(from_date=None, to_date=None, branch_id=None) -> dict:
    """``{status: count}`` for the dashboard donut chart."""
    clauses, params = ["1=1"], []
    # Row-level restriction for the signed-in user (core/scope.py).
    from core.scope import current_scope
    _sc, _sp = current_scope().attendance_clause(
        student_col="a.student_id", subject_col="a.subject_id")
    clauses.append(_sc)
    params.extend(_sp)

    if from_date:
        clauses.append("a.class_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("a.class_date <= ?")
        params.append(to_date)
    if branch_id:
        clauses.append("ats.branch_id = ?")
        params.append(branch_id)

    rows = get_db().fetch_all(
        f"""SELECT a.status, COUNT(*) AS count FROM attendance a
            JOIN attendance_sessions ats ON ats.att_session_id = a.att_session_id
            WHERE {' AND '.join(clauses)} GROUP BY a.status""", params)
    return {row["status"]: row["count"] for row in rows}
