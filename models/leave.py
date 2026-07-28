"""
Leave management repository.

Workflow::

    Student applies  ->  Pending
                          |
        Faculty reviews  -+-> Approved  -> attendance updated automatically
                          +-> Rejected
                          +-> Returned for Correction -> student edits & resubmits
                          |
        Admin may override any decision, or delete the application entirely.

Certificate policy (from the specification): medical leave of up to
``medical_cert_mandatory_days`` days may attach a certificate; longer than that
and it becomes mandatory.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from config.settings import (LEAVE_APPROVED, LEAVE_PENDING, LEAVE_REJECTED,
                             LEAVE_RETURNED, MEDICAL_CERT_DIR, STATUS_LEAVE,
                             STATUS_MEDICAL, config)
from core.audit import (ACTION_DELETE, ACTION_LEAVE_APPLY, ACTION_LEAVE_OVERRIDE,
                        ACTION_LEAVE_REVIEW, log_audit)
from core.database import get_db
from core.logger import get_logger
from core.validators import parse_date, safe_filename, validate_document
from models.attendance import apply_leave_status

logger = get_logger("models.leave")

MEDICAL_LEAVE = "Medical Leave"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def certificate_filename(student_name: str, enrollment_no: str,
                         from_date: str, to_date: str, extension: str) -> str:
    """``Rahul_Verma_DCS25001_2026-08-01_2026-08-05.pdf`` per the naming rules."""
    stem = safe_filename(
        f"{str(student_name).replace(' ', '_')}_{enrollment_no}_{from_date}_{to_date}")
    return f"{stem}{extension}"


def requires_certificate(leave_type: str, total_days: int) -> bool:
    """True when policy makes a supporting document mandatory."""
    limit = int(config.get("medical_cert_mandatory_days", 3))
    return leave_type == MEDICAL_LEAVE and total_days > limit


# ===========================================================================
# Retrieval
# ===========================================================================
_BASE_SELECT = """
    SELECT l.*, s.enrollment_no, s.roll_no, s.full_name AS student_name,
           s.mobile, s.email, b.branch_name, b.branch_code,
           sem.semester_name, sec.section_name,
           u.full_name AS reviewer_name, u.role AS reviewer_role,
           uo.full_name AS override_by_name
    FROM leave_applications l
    JOIN students   s   ON s.student_id  = l.student_id
    JOIN branches   b   ON b.branch_id   = s.branch_id
    JOIN semesters  sem ON sem.semester_id = s.semester_id
    LEFT JOIN sections sec ON sec.section_id = s.section_id
    LEFT JOIN users u  ON u.user_id  = l.reviewed_by
    LEFT JOIN users uo ON uo.user_id = l.override_by
"""


def get_leave(leave_id: int):
    return get_db().fetch_one(f"{_BASE_SELECT} WHERE l.leave_id = ?", (leave_id,))


def search_leaves(student_id=None, status=None, leave_type=None, branch_id=None,
                  semester_id=None, section_id=None, from_date=None, to_date=None,
                  faculty_id=None, search: str = "", limit: int = 500) -> list:
    clauses, params = ["1=1"], []

    for clause, value in (
            ("l.student_id = ?", student_id), ("l.status = ?", status),
            ("l.leave_type = ?", leave_type), ("s.branch_id = ?", branch_id),
            ("s.semester_id = ?", semester_id), ("s.section_id = ?", section_id)):
        if value:
            clauses.append(clause)
            params.append(value)

    if from_date:
        clauses.append("l.to_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("l.from_date <= ?")
        params.append(to_date)

    # Faculty only see leave from students in classes they actually teach.
    if faculty_id:
        clauses.append(
            """EXISTS (SELECT 1 FROM subjects sub
                       WHERE sub.faculty_id = ?
                         AND sub.branch_id = s.branch_id
                         AND sub.semester_id = s.semester_id)""")
        params.append(faculty_id)

    if search:
        clauses.append("(s.enrollment_no LIKE ? OR s.full_name LIKE ? OR l.reason LIKE ?)")
        params.extend([f"%{search}%"] * 3)

    return get_db().fetch_all(
        f"{_BASE_SELECT} WHERE {' AND '.join(clauses)} "
        f"ORDER BY CASE l.status WHEN 'Pending' THEN 0 ELSE 1 END, "
        f"l.leave_id DESC LIMIT ?", params + [limit])


def count_pending(faculty_id: int | None = None) -> int:
    if faculty_id:
        return int(get_db().fetch_value(
            """SELECT COUNT(*) FROM leave_applications l
               JOIN students s ON s.student_id = l.student_id
               WHERE l.status = 'Pending' AND EXISTS (
                   SELECT 1 FROM subjects sub WHERE sub.faculty_id = ?
                     AND sub.branch_id = s.branch_id
                     AND sub.semester_id = s.semester_id)""", (faculty_id,), 0))
    return get_db().count("leave_applications", "status = 'Pending'")


def get_leave_stats(student_id: int | None = None) -> dict:
    where, params = ("WHERE student_id = ?", (student_id,)) if student_id else ("", ())
    rows = get_db().fetch_all(
        f"SELECT status, COUNT(*) AS count FROM leave_applications {where} GROUP BY status",
        params)
    stats = {row["status"]: row["count"] for row in rows}
    stats["total"] = sum(stats.values())
    return stats


# ===========================================================================
# Apply
# ===========================================================================
def apply_leave(student_id: int, leave_type: str, from_date: str, to_date: str,
                reason: str, document_source: str | Path | None = None,
                user: dict | None = None) -> tuple[bool, str, int | None]:
    """Submit a leave application, storing any supporting document."""
    db = get_db()

    start, end = parse_date(from_date), parse_date(to_date)
    if start is None or end is None:
        return False, "Please enter valid From and To dates.", None
    if end < start:
        return False, "The 'To' date cannot be before the 'From' date.", None

    reason = (reason or "").strip()
    if len(reason) < 10:
        return False, "Please give a reason of at least 10 characters.", None

    total_days = (end - start).days + 1

    if requires_certificate(leave_type, total_days) and not document_source:
        limit = int(config.get("medical_cert_mandatory_days", 3))
        return False, (f"Medical leave longer than {limit} days requires a medical "
                       f"certificate. This request covers {total_days} days."), None

    if document_source:
        ok, message = validate_document(document_source, allow_blank=False)
        if not ok:
            return False, message, None

    # Overlapping requests create ambiguous attendance updates.
    overlap = db.fetch_one(
        """SELECT leave_id, from_date, to_date FROM leave_applications
           WHERE student_id = ? AND status IN ('Pending','Approved')
             AND from_date <= ? AND to_date >= ?""",
        (student_id, end.isoformat(), start.isoformat()))
    if overlap:
        return False, (f"You already have a leave request covering "
                       f"{overlap['from_date']} to {overlap['to_date']}."), None

    stored_path = None
    if document_source:
        stored_path = _store_document(document_source, student_id,
                                      start.isoformat(), end.isoformat())

    from models.academic import get_current_session_id
    leave_id = db.insert("leave_applications", {
        "student_id": student_id,
        "leave_type": leave_type,
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "total_days": total_days,
        "reason": reason,
        "document_path": str(stored_path) if stored_path else None,
        "status": LEAVE_PENDING,
        "session_id": get_current_session_id(),
    })

    log_audit(user, ACTION_LEAVE_APPLY, "Leave", "leave_applications", leave_id,
              new_value={"type": leave_type, "from": start.isoformat(),
                         "to": end.isoformat(), "days": total_days})
    logger.info("Leave #%s applied by student %s (%s, %d days)",
                leave_id, student_id, leave_type, total_days)
    return True, f"Leave application submitted for {total_days} day(s).", leave_id


def update_leave(leave_id: int, leave_type: str, from_date: str, to_date: str,
                 reason: str, document_source: str | Path | None = None,
                 user: dict | None = None) -> tuple[bool, str]:
    """Resubmit an application that was returned for correction."""
    db = get_db()
    leave = get_leave(leave_id)
    if leave is None:
        return False, "Leave application not found."
    if leave["status"] not in (LEAVE_PENDING, LEAVE_RETURNED):
        return False, f"A {leave['status'].lower()} application can no longer be edited."

    start, end = parse_date(from_date), parse_date(to_date)
    if start is None or end is None or end < start:
        return False, "Please enter a valid date range."

    total_days = (end - start).days + 1
    has_document = bool(document_source or leave["document_path"])
    if requires_certificate(leave_type, total_days) and not has_document:
        return False, "A medical certificate is required for this duration."

    updates = {
        "leave_type": leave_type,
        "from_date": start.isoformat(), "to_date": end.isoformat(),
        "total_days": total_days, "reason": reason.strip(),
        "status": LEAVE_PENDING, "review_remarks": None,
        "reviewed_by": None, "reviewed_on": None,
        "applied_on": _now(),
    }
    if document_source:
        stored = _store_document(document_source, leave["student_id"],
                                 start.isoformat(), end.isoformat())
        if stored:
            updates["document_path"] = str(stored)

    db.update("leave_applications", updates, "leave_id = ?", (leave_id,))
    log_audit(user, ACTION_LEAVE_APPLY, "Leave", "leave_applications", leave_id,
              old_value={"status": leave["status"]}, new_value=updates,
              reason="Resubmitted after correction")
    return True, "Leave application resubmitted."


def _store_document(source: str | Path, student_id: int,
                    from_date: str, to_date: str) -> Path | None:
    """File a certificate under the naming convention."""
    source = Path(source)
    if not source.exists():
        return None

    student = get_db().fetch_one(
        "SELECT enrollment_no, full_name FROM students WHERE student_id = ?", (student_id,))
    if student is None:
        return None

    MEDICAL_CERT_DIR.mkdir(parents=True, exist_ok=True)
    filename = certificate_filename(student["full_name"], student["enrollment_no"],
                                    from_date, to_date, source.suffix.lower())
    target = MEDICAL_CERT_DIR / filename
    try:
        shutil.copy2(source, target)
        return target
    except OSError as exc:
        logger.error("Could not store leave document: %s", exc)
        return None


# ===========================================================================
# Review
# ===========================================================================
def review_leave(leave_id: int, decision: str, remarks: str = "",
                 user: dict | None = None) -> tuple[bool, str]:
    """Faculty decision: Approve, Reject, or Return for Correction.

    Approving pushes the leave into attendance for the covered dates.
    """
    db = get_db()
    leave = get_leave(leave_id)
    if leave is None:
        return False, "Leave application not found."
    if leave["status"] == LEAVE_APPROVED and decision == LEAVE_APPROVED:
        return False, "This application is already approved."

    if decision in (LEAVE_REJECTED, LEAVE_RETURNED) and not remarks.strip():
        return False, f"Please give a reason when choosing '{decision}'."

    db.update("leave_applications", {
        "status": decision,
        "reviewed_by": (user or {}).get("user_id"),
        "reviewed_on": _now(),
        "review_remarks": remarks.strip() or None,
    }, "leave_id = ?", (leave_id,))

    message = f"Leave application {decision.lower()}."

    if decision == LEAVE_APPROVED:
        status = STATUS_MEDICAL if leave["leave_type"] == MEDICAL_LEAVE else STATUS_LEAVE
        affected = apply_leave_status(leave["student_id"], leave["from_date"],
                                      leave["to_date"], status, leave_id, user)
        db.update("leave_applications", {"attendance_applied": 1},
                  "leave_id = ?", (leave_id,))
        message += f" {affected} attendance record(s) updated to '{status}'."

    log_audit(user, ACTION_LEAVE_REVIEW, "Leave", "leave_applications", leave_id,
              old_value={"status": leave["status"]},
              new_value={"status": decision}, reason=remarks.strip() or decision)
    logger.info("Leave #%s reviewed: %s", leave_id, decision)
    return True, message


def override_leave(leave_id: int, new_status: str, reason: str,
                   user: dict | None = None) -> tuple[bool, str]:
    """Administrator override of a faculty decision.  Reason is mandatory."""
    db = get_db()
    if not reason.strip():
        return False, "A reason is required to override a decision."

    leave = get_leave(leave_id)
    if leave is None:
        return False, "Leave application not found."

    db.update("leave_applications", {
        "status": new_status,
        "admin_override": 1,
        "override_by": (user or {}).get("user_id"),
        "override_reason": reason.strip(),
        "reviewed_on": _now(),
    }, "leave_id = ?", (leave_id,))

    message = f"Decision overridden to '{new_status}'."

    if new_status == LEAVE_APPROVED:
        status = STATUS_MEDICAL if leave["leave_type"] == MEDICAL_LEAVE else STATUS_LEAVE
        affected = apply_leave_status(leave["student_id"], leave["from_date"],
                                      leave["to_date"], status, leave_id, user)
        db.update("leave_applications", {"attendance_applied": 1},
                  "leave_id = ?", (leave_id,))
        message += f" {affected} attendance record(s) updated."
    elif leave["attendance_applied"]:
        # Withdrawing an approval must roll the attendance back to Absent.
        reverted = db.execute(
            """UPDATE attendance SET status = 'Absent', marked_method = 'Manual',
                      modified_by = ?, modified_at = ?, is_modified = 1,
                      modify_reason = ?
               WHERE student_id = ? AND class_date BETWEEN ? AND ?
                 AND status IN ('Leave','Medical Leave')
                 AND att_session_id IN (SELECT att_session_id FROM attendance_sessions
                                        WHERE is_locked = 0)""",
            ((user or {}).get("user_id"), _now(),
             f"Leave #{leave_id} approval withdrawn", leave["student_id"],
             leave["from_date"], leave["to_date"]))
        db.update("leave_applications", {"attendance_applied": 0},
                  "leave_id = ?", (leave_id,))
        message += f" {reverted} attendance record(s) reverted to Absent."

    log_audit(user, ACTION_LEAVE_OVERRIDE, "Leave", "leave_applications", leave_id,
              old_value={"status": leave["status"]},
              new_value={"status": new_status}, reason=reason.strip())
    logger.warning("Leave #%s overridden to %s by %s",
                   leave_id, new_status, (user or {}).get("username"))
    return True, message


def delete_leave(leave_id: int, reason: str = "",
                 user: dict | None = None) -> tuple[bool, str]:
    """Administrator delete.  Any attendance effect is reverted first."""
    db = get_db()
    leave = get_leave(leave_id)
    if leave is None:
        return False, "Leave application not found."

    if leave["attendance_applied"]:
        db.execute(
            """UPDATE attendance SET status = 'Absent', marked_method = 'Manual',
                      modified_by = ?, modified_at = ?, is_modified = 1,
                      modify_reason = ?
               WHERE student_id = ? AND class_date BETWEEN ? AND ?
                 AND status IN ('Leave','Medical Leave')
                 AND att_session_id IN (SELECT att_session_id FROM attendance_sessions
                                        WHERE is_locked = 0)""",
            ((user or {}).get("user_id"), _now(),
             f"Leave #{leave_id} deleted", leave["student_id"],
             leave["from_date"], leave["to_date"]))

    if leave["document_path"]:
        Path(leave["document_path"]).unlink(missing_ok=True)

    db.delete("leave_applications", "leave_id = ?", (leave_id,))

    log_audit(user, ACTION_DELETE, "Leave", "leave_applications", leave_id,
              old_value={"student": leave["student_name"], "type": leave["leave_type"],
                         "status": leave["status"]},
              reason=reason or "Leave application deleted")
    return True, "Leave application deleted."


def get_leave_history(student_id: int) -> list:
    return get_db().fetch_all(
        f"{_BASE_SELECT} WHERE l.student_id = ? ORDER BY l.leave_id DESC", (student_id,))


# ===========================================================================
# Routing -- who is this application waiting on?
# ===========================================================================
def get_pending_with(leave) -> dict:
    """Resolve who is expected to act on an application next.

    A student should never have to guess whose desk their application is on,
    so this always returns a name: the class teacher for their class, falling
    back through subject teacher and HOD (see
    :func:`models.academic.get_class_teacher`).
    """
    status = leave["status"] if "status" in leave.keys() else leave.get("status")

    if status == LEAVE_APPROVED:
        return {"name": "-", "role": "", "note": "Approved - no action pending"}
    if status == LEAVE_REJECTED:
        return {"name": "-", "role": "", "note": "Rejected - no action pending"}
    if status == LEAVE_RETURNED:
        return {"name": leave["student_name"] if "student_name" in leave.keys() else "You",
                "role": "Student",
                "note": "Returned for correction - waiting on the student to resubmit"}

    student = get_db().fetch_one(
        "SELECT branch_id, semester_id, section_id FROM students WHERE student_id = ?",
        (leave["student_id"],))
    if student is None:
        return {"name": "Unassigned", "role": "", "note": "Student record not found"}

    from models.academic import get_class_teacher
    teacher = get_class_teacher(student["branch_id"], student["semester_id"],
                                student["section_id"])

    if teacher is None:
        return {"name": "Administrator", "role": "Admin",
                "note": "No class teacher assigned - an administrator will review"}

    return {
        "name": teacher["full_name"],
        "role": teacher["source"],
        "faculty_id": teacher["faculty_id"],
        "mobile": teacher["mobile"],
        "email": teacher["email"],
        "note": f"Awaiting review by {teacher['full_name']} ({teacher['source']})",
    }


def annotate_pending_with(rows: list) -> list:
    """Attach ``pending_with`` to each row for display in the grid."""
    annotated = []
    for row in rows:
        entry = dict(row)
        try:
            entry["pending_with"] = get_pending_with(row)["name"]
        except Exception:                       # noqa: BLE001 - display only
            entry["pending_with"] = "-"
        annotated.append(entry)
    return annotated


# ===========================================================================
# Student cancellation
# ===========================================================================
def cancel_leave(leave_id: int, student_id: int,
                 user: dict | None = None) -> tuple[bool, str]:
    """Let a student withdraw their own application.

    Permitted only while nobody has acted on it, or after it was returned for
    correction.  An approved application has already changed attendance, so it
    must go back through a faculty or administrator rather than simply
    vanishing.
    """
    db = get_db()
    leave = get_leave(leave_id)

    if leave is None:
        return False, "Leave application not found."
    if leave["student_id"] != student_id:
        return False, "You can only cancel your own leave application."

    if leave["status"] == LEAVE_APPROVED:
        return False, ("This application has already been approved and your "
                       "attendance updated.\n\n"
                       "Ask your class teacher or the administrator to reverse it.")
    if leave["status"] == LEAVE_REJECTED:
        return False, ("This application was already rejected, so there is "
                       "nothing to cancel.")

    db.delete("leave_applications", "leave_id = ?", (leave_id,))

    log_audit(user, ACTION_DELETE, "Leave", "leave_applications", leave_id,
              old_value={"type": leave["leave_type"], "from": leave["from_date"],
                         "to": leave["to_date"], "status": leave["status"]},
              reason="Cancelled by the student before review")

    return True, (f"Your {leave['leave_type']} application for "
                  f"{leave['from_date']} to {leave['to_date']} has been cancelled.")


def get_medical_leaves(from_date=None, to_date=None, branch_id=None,
                       with_certificate: bool | None = None) -> list:
    """Source for the Medical Leave Report."""
    clauses, params = ["l.leave_type = 'Medical Leave'"], []
    if from_date:
        clauses.append("l.to_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("l.from_date <= ?")
        params.append(to_date)
    if branch_id:
        clauses.append("s.branch_id = ?")
        params.append(branch_id)
    if with_certificate is True:
        clauses.append("l.document_path IS NOT NULL")
    elif with_certificate is False:
        clauses.append("l.document_path IS NULL")

    return get_db().fetch_all(
        f"{_BASE_SELECT} WHERE {' AND '.join(clauses)} ORDER BY l.from_date DESC", params)
