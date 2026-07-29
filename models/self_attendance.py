"""
Self-marked attendance with faculty verification.

The rule that makes this safe: **a student's own click is never attendance.**
It creates a *request*.  Only when the subject's faculty approves does an
attendance row actually change, and that approval is audited with the
approver's name, the timestamp and any remark.

Workflow
--------
    Student sees today's scheduled classes
        -> taps "Mark Me Present"
        -> a Pending request is created
        -> faculty sees it in their verification queue
        -> Approve  -> attendance becomes Present (or Late) and is audited
        -> Reject   -> attendance is untouched; the student sees the reason

Approving a request for a class whose attendance session was never opened will
open one automatically, so a faculty member is never blocked by having taken
the class without the software.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config.settings import (MARK_METHOD_MANUAL, STATUS_ABSENT, STATUS_LATE,
                             STATUS_PRESENT, config)
from core.audit import ACTION_ATTENDANCE_EDIT, ACTION_ATTENDANCE_MARK, log_audit
from core.database import get_db
from core.logger import get_logger

logger = get_logger("models.self_attendance")

STATUS_PENDING = "Pending"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"

MARK_METHOD_SELF = "Self-Marked (Approved)"

_BASE_SELECT = """
    SELECT r.*, s.enrollment_no, s.roll_no, s.full_name AS student_name,
           s.photo_path, b.branch_name, b.branch_code,
           sem.semester_name, sem.semester_number, sec.section_name,
           sub.subject_code, sub.subject_name,
           f.full_name AS faculty_name, f.faculty_code,
           u.full_name AS reviewer_name
    FROM self_attendance_requests r
    JOIN students  s   ON s.student_id  = r.student_id
    JOIN subjects  sub ON sub.subject_id = r.subject_id
    JOIN branches  b   ON b.branch_id   = s.branch_id
    JOIN semesters sem ON sem.semester_id = s.semester_id
    LEFT JOIN sections sec ON sec.section_id = s.section_id
    LEFT JOIN faculty  f   ON f.faculty_id = r.faculty_id
    LEFT JOIN users    u   ON u.user_id = r.reviewed_by
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ===========================================================================
# Student side
# ===========================================================================
def get_today_classes_for_student(student_id: int, for_date: str | None = None) -> list:
    """Today's scheduled classes for a student, annotated with their state.

    Each entry says whether the student can mark themselves present, and if
    not, why -- so the UI never shows a button that will simply be refused.
    """
    db = get_db()
    target = for_date or _today()

    try:
        day_name = datetime.strptime(target, "%Y-%m-%d").strftime("%A")
    except ValueError:
        return []

    student = db.fetch_one(
        "SELECT branch_id, semester_id, section_id FROM students WHERE student_id = ?",
        (student_id,))
    if student is None:
        return []

    slots = db.fetch_all(
        """SELECT * FROM v_timetable_full
           WHERE day_of_week = ? AND branch_id = ? AND semester_id = ?
             AND (section_id = ? OR ? IS NULL) AND is_active = 1
           ORDER BY start_time""",
        (day_name, student["branch_id"], student["semester_id"],
         student["section_id"], student["section_id"]))

    from models.academic import is_holiday
    holiday, holiday_name = is_holiday(target)

    now = datetime.now().strftime("%H:%M")
    is_today = target == _today()

    results = []
    for slot in slots:
        entry = dict(slot)

        # Has the faculty opened a session for this class?
        att_session = db.fetch_one(
            """SELECT att_session_id, is_locked FROM attendance_sessions
               WHERE subject_id = ? AND class_date = ?
                 AND (timetable_id = ? OR start_time = ?)
               LIMIT 1""",
            (slot["subject_id"], target, slot["timetable_id"], slot["start_time"]))

        entry["att_session_id"] = att_session["att_session_id"] if att_session else None
        entry["session_open"] = att_session is not None
        entry["session_locked"] = bool(att_session["is_locked"]) if att_session else False

        # What is already recorded for this student?
        recorded = None
        if att_session:
            row = db.fetch_one(
                "SELECT status, marked_method FROM attendance "
                "WHERE att_session_id = ? AND student_id = ?",
                (att_session["att_session_id"], student_id))
            recorded = dict(row) if row else None
        entry["recorded_status"] = recorded["status"] if recorded else None
        entry["recorded_method"] = recorded["marked_method"] if recorded else None

        # Any request already raised?
        request = db.fetch_one(
            """SELECT request_id, status, review_remarks FROM self_attendance_requests
               WHERE student_id = ? AND subject_id = ? AND class_date = ?
                 AND slot_start = ?""",
            (student_id, slot["subject_id"], target, slot["start_time"]))
        entry["request_status"] = request["status"] if request else None
        entry["request_id"] = request["request_id"] if request else None
        entry["review_remarks"] = request["review_remarks"] if request else None

        # ---- can this student mark themselves? --------------------------
        can_mark, reason = True, ""

        if holiday:
            can_mark, reason = False, f"{target} is a holiday ({holiday_name})."
        elif not is_today:
            can_mark, reason = False, "You can only mark attendance on the day of the class."
        elif entry["session_locked"]:
            can_mark, reason = False, "Attendance for this class has been locked."
        elif entry["recorded_status"] in (STATUS_PRESENT, STATUS_LATE):
            can_mark, reason = False, f"Already recorded as {entry['recorded_status']}."
        elif entry["recorded_status"] in ("Leave", "Medical Leave"):
            can_mark, reason = False, f"You are on approved {entry['recorded_status']}."
        elif request and request["status"] == STATUS_PENDING:
            can_mark, reason = False, "Your request is awaiting faculty approval."
        elif request and request["status"] == STATUS_APPROVED:
            can_mark, reason = False, "Already approved by faculty."
        elif request and request["status"] == STATUS_REJECTED:
            can_mark, reason = False, "Your request was rejected by the faculty."
        elif now < slot["start_time"]:
            can_mark, reason = False, f"This class starts at {slot['start_time']}."
        else:
            # A window after the class ends keeps late submissions honest.
            grace = int(config.get("self_mark_grace_minutes", 30))
            try:
                end = datetime.strptime(f"{target} {slot['end_time']}", "%Y-%m-%d %H:%M")
                if datetime.now() > end + timedelta(minutes=grace):
                    can_mark = False
                    reason = (f"The window closed {grace} minutes after the class "
                              f"ended at {slot['end_time']}.")
            except ValueError:
                pass

        entry["can_mark"] = can_mark
        entry["blocked_reason"] = reason

        if not is_today:
            entry["state"] = "Scheduled"
        elif entry["recorded_status"] in (STATUS_PRESENT, STATUS_LATE):
            entry["state"] = entry["recorded_status"]
        elif request:
            entry["state"] = f"Request {request['status']}"
        elif now < slot["start_time"]:
            entry["state"] = "Upcoming"
        elif slot["start_time"] <= now <= slot["end_time"]:
            entry["state"] = "In Progress"
        else:
            entry["state"] = "Ended"

        results.append(entry)

    return results


def request_self_attendance(student_id: int, subject_id: int, class_date: str,
                            timetable_id: int | None = None,
                            slot_start: str | None = None,
                            slot_end: str | None = None,
                            remark: str = "",
                            user: dict | None = None) -> tuple[bool, str, int | None]:
    """Raise a pending self-attendance request.

    Deliberately does **not** touch the attendance table.
    """
    db = get_db()

    student = db.fetch_one(
        "SELECT full_name, enrollment_no FROM students WHERE student_id = ?",
        (student_id,))
    if student is None:
        return False, "Student record not found.", None

    subject = db.fetch_one(
        "SELECT subject_name, subject_code, faculty_id FROM subjects WHERE subject_id = ?",
        (subject_id,))
    if subject is None:
        return False, "Subject not found.", None

    if db.exists("self_attendance_requests",
                 "student_id = ? AND subject_id = ? AND class_date = ? "
                 "AND IFNULL(slot_start,'') = IFNULL(?,'')",
                 (student_id, subject_id, class_date, slot_start)):
        return False, ("You have already submitted a request for this class. "
                       "Please wait for your faculty to review it."), None

    att_session = db.fetch_one(
        """SELECT att_session_id, is_locked FROM attendance_sessions
           WHERE subject_id = ? AND class_date = ?
             AND (timetable_id = ? OR start_time = ?) LIMIT 1""",
        (subject_id, class_date, timetable_id, slot_start))

    if att_session and att_session["is_locked"]:
        return False, "Attendance for this class is locked and cannot be changed.", None

    from models.academic import get_current_session_id

    request_id = db.insert("self_attendance_requests", {
        "student_id": student_id,
        "subject_id": subject_id,
        "timetable_id": timetable_id,
        "att_session_id": att_session["att_session_id"] if att_session else None,
        "faculty_id": subject["faculty_id"],
        "class_date": class_date,
        "slot_start": slot_start,
        "slot_end": slot_end,
        "student_remark": remark.strip() or None,
        "status": STATUS_PENDING,
        "session_id": get_current_session_id(),
    })

    log_audit(user, ACTION_ATTENDANCE_MARK, "Self Attendance",
              "self_attendance_requests", request_id,
              new_value={"student": student["full_name"],
                         "subject": subject["subject_code"],
                         "date": class_date, "slot": slot_start},
              reason="Student self-marked; pending faculty verification")

    logger.info("Self-attendance request %s raised by student %s for subject %s",
                request_id, student_id, subject_id)

    return True, ("Your attendance has been submitted for faculty verification.\n\n"
                  "It is not counted until your teacher approves it."), request_id


def withdraw_request(request_id: int, student_id: int,
                     user: dict | None = None) -> tuple[bool, str]:
    """Let a student cancel their own request while it is still pending."""
    db = get_db()
    request = db.fetch_one(
        "SELECT * FROM self_attendance_requests WHERE request_id = ?", (request_id,))

    if request is None:
        return False, "Request not found."
    if request["student_id"] != student_id:
        return False, "You can only withdraw your own request."
    if request["status"] != STATUS_PENDING:
        return False, f"This request has already been {request['status'].lower()}."

    db.delete("self_attendance_requests", "request_id = ?", (request_id,))
    log_audit(user, "DELETE", "Self Attendance", "self_attendance_requests",
              request_id, reason="Withdrawn by the student")
    return True, "Your request has been withdrawn."


# ===========================================================================
# Faculty side
# ===========================================================================
def get_requests(status: str | None = None, faculty_id: int | None = None,
                 student_id: int | None = None, branch_id: int | None = None,
                 semester_id: int | None = None, from_date: str | None = None,
                 to_date: str | None = None, search: str = "",
                 limit: int = 500) -> list:
    """Query requests.  Faculty see only classes they are responsible for."""
    clauses, params = ["1=1"], []

    if status:
        clauses.append("r.status = ?")
        params.append(status)
    if student_id:
        clauses.append("r.student_id = ?")
        params.append(student_id)
    if branch_id:
        clauses.append("s.branch_id = ?")
        params.append(branch_id)
    if semester_id:
        clauses.append("s.semester_id = ?")
        params.append(semester_id)
    if from_date:
        clauses.append("r.class_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("r.class_date <= ?")
        params.append(to_date)

    if faculty_id:
        # Either the request is addressed to them, or they own the subject.
        clauses.append("(r.faculty_id = ? OR sub.faculty_id = ?)")
        params.extend([faculty_id, faculty_id])

    if search:
        clauses.append("(s.enrollment_no LIKE ? OR s.full_name LIKE ? "
                       "OR sub.subject_name LIKE ?)")
        params.extend([f"%{search}%"] * 3)

    return get_db().fetch_all(
        f"{_BASE_SELECT} WHERE {' AND '.join(clauses)} "
        f"ORDER BY CASE r.status WHEN 'Pending' THEN 0 ELSE 1 END, "
        f"r.class_date DESC, r.requested_at DESC LIMIT ?", params + [limit])


def get_request(request_id: int):
    return get_db().fetch_one(f"{_BASE_SELECT} WHERE r.request_id = ?", (request_id,))


def count_pending(faculty_id: int | None = None) -> int:
    """Badge count for the faculty verification queue."""
    if faculty_id:
        return int(get_db().fetch_value(
            """SELECT COUNT(*) FROM self_attendance_requests r
               JOIN subjects sub ON sub.subject_id = r.subject_id
               WHERE r.status = 'Pending'
                 AND (r.faculty_id = ? OR sub.faculty_id = ?)""",
            (faculty_id, faculty_id), 0))
    return get_db().count("self_attendance_requests", "status = 'Pending'")


def approve_request(request_id: int, remarks: str = "",
                    mark_late: bool = False,
                    user: dict | None = None) -> tuple[bool, str]:
    """Approve a request and write the attendance it represents.

    Opens the attendance session first if the faculty never started one.
    """
    db = get_db()
    request = get_request(request_id)

    if request is None:
        return False, "Request not found."
    if request["status"] != STATUS_PENDING:
        return False, f"This request has already been {request['status'].lower()}."

    # The verification queue already filters to a faculty's own subjects, but
    # that is a UI convenience, not the security boundary -- a request built
    # by hand against someone else's request_id must still be refused here.
    if (user or {}).get("role") == "Faculty" \
            and request["faculty_id"] != (user or {}).get("linked_id"):
        return False, ("You are not the faculty for this subject, so you "
                       "cannot verify this attendance request.")

    att_session_id = request["att_session_id"]

    # ---- make sure a session exists ------------------------------------
    if not att_session_id:
        existing = db.fetch_one(
            """SELECT att_session_id FROM attendance_sessions
               WHERE subject_id = ? AND class_date = ?
                 AND (timetable_id = ? OR start_time = ?) LIMIT 1""",
            (request["subject_id"], request["class_date"],
             request["timetable_id"], request["slot_start"]))

        if existing:
            att_session_id = existing["att_session_id"]
        else:
            from models.attendance import open_session
            student = db.fetch_one(
                "SELECT branch_id, semester_id, section_id FROM students "
                "WHERE student_id = ?", (request["student_id"],))

            ok, message, att_session_id = open_session(
                subject_id=request["subject_id"],
                branch_id=student["branch_id"],
                semester_id=student["semester_id"],
                section_id=student["section_id"],
                faculty_id=request["faculty_id"],
                class_date=request["class_date"],
                start_time=request["slot_start"],
                end_time=request["slot_end"],
                timetable_id=request["timetable_id"],
                mode="Manual", user=user, allow_holiday=True)

            if not ok:
                return False, f"Could not open the attendance session: {message}"

    # ---- guard against a locked session --------------------------------
    locked = db.fetch_value(
        "SELECT is_locked FROM attendance_sessions WHERE att_session_id = ?",
        (att_session_id,), 0)
    if locked:
        return False, ("Attendance for this class is locked. "
                       "An administrator must unlock it before approving.")

    # ---- write the attendance -------------------------------------------
    record = db.fetch_one(
        "SELECT * FROM attendance WHERE att_session_id = ? AND student_id = ?",
        (att_session_id, request["student_id"]))

    if record is None:
        return False, ("This student is not on the roster for that class session. "
                       "Check their branch, semester and section.")

    applied_status = STATUS_LATE if mark_late else STATUS_PRESENT

    db.update("attendance", {
        "status": applied_status,
        "marked_method": MARK_METHOD_SELF,
        "marked_time": datetime.now().strftime("%H:%M:%S"),
        "is_modified": 1 if record["status"] != STATUS_ABSENT else 0,
        "modified_by": (user or {}).get("user_id"),
        "modified_at": _now(),
        "modify_reason": (remarks.strip()
                          or "Self-marked attendance approved by faculty"),
    }, "attendance_id = ?", (record["attendance_id"],))

    from models.attendance import refresh_counts
    refresh_counts(att_session_id)

    db.update("self_attendance_requests", {
        "status": STATUS_APPROVED,
        "att_session_id": att_session_id,
        "reviewed_by": (user or {}).get("user_id"),
        "reviewed_on": _now(),
        "review_remarks": remarks.strip() or None,
        "applied_status": applied_status,
    }, "request_id = ?", (request_id,))

    log_audit(user, ACTION_ATTENDANCE_EDIT, "Self Attendance", "attendance",
              record["attendance_id"],
              old_value={"status": record["status"], "method": record["marked_method"]},
              new_value={"status": applied_status, "method": MARK_METHOD_SELF,
                         "student": request["student_name"]},
              reason=(f"Self-attendance request #{request_id} approved. "
                      f"{remarks.strip()}").strip())

    logger.info("Self-attendance request %s approved -> %s", request_id, applied_status)
    return True, (f"{request['student_name']} marked {applied_status} for "
                  f"{request['subject_code']} on {request['class_date']}.")


def reject_request(request_id: int, remarks: str,
                   user: dict | None = None) -> tuple[bool, str]:
    """Reject a request.  Attendance is left exactly as it was."""
    db = get_db()

    if not remarks.strip():
        return False, "Please give a reason so the student understands the decision."

    request = get_request(request_id)
    if request is None:
        return False, "Request not found."
    if request["status"] != STATUS_PENDING:
        return False, f"This request has already been {request['status'].lower()}."

    if (user or {}).get("role") == "Faculty" \
            and request["faculty_id"] != (user or {}).get("linked_id"):
        return False, ("You are not the faculty for this subject, so you "
                       "cannot verify this attendance request.")

    db.update("self_attendance_requests", {
        "status": STATUS_REJECTED,
        "reviewed_by": (user or {}).get("user_id"),
        "reviewed_on": _now(),
        "review_remarks": remarks.strip(),
    }, "request_id = ?", (request_id,))

    log_audit(user, ACTION_ATTENDANCE_EDIT, "Self Attendance",
              "self_attendance_requests", request_id,
              old_value={"status": STATUS_PENDING},
              new_value={"status": STATUS_REJECTED,
                         "student": request["student_name"]},
              reason=remarks)

    return True, (f"Request from {request['student_name']} rejected. "
                  "Their attendance is unchanged.")


def bulk_review(request_ids: list[int], approve: bool, remarks: str = "",
                user: dict | None = None) -> tuple[bool, str, int]:
    """Approve or reject several requests in one action."""
    if not request_ids:
        return False, "No requests selected.", 0

    processed, failures = 0, []
    for request_id in request_ids:
        if approve:
            ok, message = approve_request(request_id, remarks, user=user)
        else:
            ok, message = reject_request(request_id, remarks or "Bulk rejection", user)
        if ok:
            processed += 1
        else:
            failures.append(f"#{request_id}: {message}")

    verb = "approved" if approve else "rejected"
    summary = f"{processed} request(s) {verb}."
    if failures:
        summary += "\n\nCould not process:\n  - " + "\n  - ".join(failures[:8])

    return processed > 0, summary, processed


def get_student_history(student_id: int, limit: int = 200) -> list:
    return get_db().fetch_all(
        f"{_BASE_SELECT} WHERE r.student_id = ? "
        f"ORDER BY r.class_date DESC, r.requested_at DESC LIMIT ?",
        (student_id, limit))


def get_stats(faculty_id: int | None = None, student_id: int | None = None) -> dict:
    """Counts by status for the dashboard tiles."""
    clauses, params = ["1=1"], []
    if student_id:
        clauses.append("r.student_id = ?")
        params.append(student_id)
    if faculty_id:
        clauses.append("(r.faculty_id = ? OR sub.faculty_id = ?)")
        params.extend([faculty_id, faculty_id])

    rows = get_db().fetch_all(
        f"""SELECT r.status, COUNT(*) AS count
            FROM self_attendance_requests r
            JOIN subjects sub ON sub.subject_id = r.subject_id
            WHERE {' AND '.join(clauses)} GROUP BY r.status""", params)

    stats = {row["status"]: row["count"] for row in rows}
    stats["total"] = sum(stats.values())
    return stats
