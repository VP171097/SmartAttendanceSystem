"""
Timetable repository.

Beyond plain CRUD this module answers the question the attendance screen asks
every time it opens: *is a class scheduled right now, and is this faculty the
one who should be taking it?*

Clash detection runs on three axes -- the class cannot be in two places at
once, a faculty cannot teach two classes at once, and a room cannot host two
classes at once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from config.settings import DAYS_OF_WEEK, config
from core.audit import ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE, log_audit
from core.database import get_db
from core.logger import get_logger

logger = get_logger("models.timetable")

_EDITABLE = {
    "day_of_week", "start_time", "end_time", "branch_id", "semester_id",
    "section_id", "subject_id", "faculty_id", "room_no", "session_id", "is_active",
}


def _clean(data: dict) -> dict:
    return {k: v for k, v in data.items() if k in _EDITABLE}


def _overlaps(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    """Half-open interval overlap; a 10:00 end and a 10:00 start do not clash."""
    return start_a < end_b and start_b < end_a


# ===========================================================================
# Retrieval
# ===========================================================================
def get_slot(timetable_id: int):
    return get_db().fetch_one("SELECT * FROM v_timetable_full WHERE timetable_id = ?",
                              (timetable_id,))


def search_timetable(branch_id=None, semester_id=None, section_id=None,
                     faculty_id=None, day=None, session_id=None,
                     active_only: bool = True) -> list:
    clauses, params = ["1=1"], []

    if branch_id:
        clauses.append("branch_id = ?")
        params.append(branch_id)
    if semester_id:
        clauses.append("semester_id = ?")
        params.append(semester_id)
    if section_id:
        clauses.append("section_id = ?")
        params.append(section_id)
    if faculty_id:
        clauses.append("faculty_id = ?")
        params.append(faculty_id)
    if day:
        clauses.append("day_of_week = ?")
        params.append(day)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if active_only:
        clauses.append("is_active = 1")

    rows = get_db().fetch_all(
        f"SELECT * FROM v_timetable_full WHERE {' AND '.join(clauses)}", params)

    # Sort by weekday order, then clock time -- SQLite has no weekday collation.
    order = {day_name: index for index, day_name in enumerate(DAYS_OF_WEEK)}
    return sorted(rows, key=lambda r: (order.get(r["day_of_week"], 9), r["start_time"]))


def get_weekly_grid(branch_id: int, semester_id: int, section_id: int) -> dict:
    """``{day: [slots]}`` for rendering the weekly timetable board."""
    slots = search_timetable(branch_id=branch_id, semester_id=semester_id,
                             section_id=section_id)
    grid: dict[str, list] = {day: [] for day in DAYS_OF_WEEK}
    for slot in slots:
        grid.setdefault(slot["day_of_week"], []).append(slot)
    return grid


def get_today_classes(faculty_id: int | None = None, branch_id: int | None = None,
                      semester_id: int | None = None, section_id: int | None = None,
                      for_date: str | None = None) -> list:
    """Classes scheduled for a given date, annotated with attendance state."""
    target = for_date or datetime.now().strftime("%Y-%m-%d")
    day_name = datetime.strptime(target, "%Y-%m-%d").strftime("%A")

    slots = search_timetable(faculty_id=faculty_id, branch_id=branch_id,
                             semester_id=semester_id, section_id=section_id,
                             day=day_name)

    db = get_db()
    now = datetime.now().strftime("%H:%M")
    is_today = target == datetime.now().strftime("%Y-%m-%d")

    result = []
    for slot in slots:
        entry = dict(slot)
        taken = db.fetch_one(
            """SELECT att_session_id, present_count, total_students, is_locked
               FROM attendance_sessions
               WHERE timetable_id = ? AND class_date = ?""",
            (slot["timetable_id"], target))

        entry["attendance_taken"] = taken is not None
        entry["att_session_id"] = taken["att_session_id"] if taken else None
        entry["present_count"] = taken["present_count"] if taken else 0
        entry["total_students"] = taken["total_students"] if taken else 0
        entry["is_locked"] = bool(taken["is_locked"]) if taken else False

        if not is_today:
            entry["state"] = "Scheduled"
        elif taken:
            entry["state"] = "Completed"
        elif now < slot["start_time"]:
            entry["state"] = "Upcoming"
        elif slot["start_time"] <= now <= slot["end_time"]:
            entry["state"] = "In Progress"
        else:
            entry["state"] = "Missed"

        result.append(entry)
    return result


def get_current_slot(faculty_id: int | None = None, subject_id: int | None = None):
    """The slot running right now, if any."""
    now = datetime.now()
    day_name = now.strftime("%A")
    clock = now.strftime("%H:%M")

    clauses = ["day_of_week = ?", "is_active = 1", "start_time <= ?", "end_time >= ?"]
    params = [day_name, clock, clock]
    if faculty_id:
        clauses.append("faculty_id = ?")
        params.append(faculty_id)
    if subject_id:
        clauses.append("subject_id = ?")
        params.append(subject_id)

    return get_db().fetch_one(
        f"SELECT * FROM v_timetable_full WHERE {' AND '.join(clauses)} LIMIT 1", params)


def is_class_time(subject_id: int, faculty_id: int | None = None,
                  grace_minutes: int = 15) -> tuple[bool, str, dict | None]:
    """Gate for opening the attendance screen.

    Returns ``(allowed, message, slot)``.  When
    ``enforce_timetable_window`` is off in settings the gate always allows,
    which is what a classroom demo needs; colleges turn it on in production.
    """
    if not config.get("enforce_timetable_window", False):
        slot = get_current_slot(faculty_id=faculty_id, subject_id=subject_id)
        return True, "", dict(slot) if slot else None

    now = datetime.now()
    day_name = now.strftime("%A")

    clauses = ["day_of_week = ?", "subject_id = ?", "is_active = 1"]
    params = [day_name, subject_id]
    if faculty_id:
        clauses.append("faculty_id = ?")
        params.append(faculty_id)

    slots = get_db().fetch_all(
        f"SELECT * FROM v_timetable_full WHERE {' AND '.join(clauses)}", params)

    if not slots:
        return False, f"No class is scheduled for this subject on {day_name}.", None

    # A grace window either side keeps a class from being blocked by a clock
    # that is a few minutes out.
    for slot in slots:
        start = datetime.strptime(slot["start_time"], "%H:%M").replace(
            year=now.year, month=now.month, day=now.day) - timedelta(minutes=grace_minutes)
        end = datetime.strptime(slot["end_time"], "%H:%M").replace(
            year=now.year, month=now.month, day=now.day) + timedelta(minutes=grace_minutes)
        if start <= now <= end:
            return True, "", dict(slot)

    times = ", ".join(f"{s['start_time']}-{s['end_time']}" for s in slots)
    return False, (f"Attendance for this subject is only open during its scheduled "
                   f"slot today ({times})."), None


# ===========================================================================
# Clash detection
# ===========================================================================
def find_clashes(day: str, start_time: str, end_time: str, branch_id: int,
                 semester_id: int, section_id: int, faculty_id: int | None,
                 room_no: str | None, exclude_id: int | None = None) -> list[str]:
    """Return human-readable descriptions of every clash found."""
    clauses = ["day_of_week = ?", "is_active = 1"]
    params = [day]
    if exclude_id:
        clauses.append("timetable_id <> ?")
        params.append(exclude_id)

    existing = get_db().fetch_all(
        f"SELECT * FROM v_timetable_full WHERE {' AND '.join(clauses)}", params)

    problems = []
    for slot in existing:
        if not _overlaps(start_time, end_time, slot["start_time"], slot["end_time"]):
            continue
        window = f"{slot['start_time']}-{slot['end_time']}"

        if (slot["branch_id"] == branch_id and slot["semester_id"] == semester_id
                and slot["section_id"] == section_id):
            problems.append(
                f"This class already has '{slot['subject_name']}' at {window}.")

        if faculty_id and slot["faculty_id"] == faculty_id:
            problems.append(
                f"{slot['faculty_name']} is teaching '{slot['subject_name']}' "
                f"to {slot['branch_code']} {slot['semester_name']}-{slot['section_name']} "
                f"at {window}.")

        if room_no and slot["room_no"] and \
                str(slot["room_no"]).strip().lower() == str(room_no).strip().lower():
            problems.append(f"Room {room_no} is occupied at {window}.")

    return problems


# ===========================================================================
# Create / update / delete
# ===========================================================================
def add_slot(data: dict, user: dict | None = None,
             allow_clash: bool = False) -> tuple[bool, str, int | None]:
    db = get_db()
    payload = _clean(data)

    required = ("day_of_week", "start_time", "end_time", "branch_id",
                "semester_id", "section_id", "subject_id")
    for field in required:
        if not payload.get(field):
            return False, f"'{field.replace('_', ' ').title()}' is required.", None

    if payload["end_time"] <= payload["start_time"]:
        return False, "End time must be after start time.", None

    # Default the faculty to whoever owns the subject.
    if not payload.get("faculty_id"):
        payload["faculty_id"] = db.fetch_value(
            "SELECT faculty_id FROM subjects WHERE subject_id = ?", (payload["subject_id"],))

    if not allow_clash:
        clashes = find_clashes(
            payload["day_of_week"], payload["start_time"], payload["end_time"],
            payload["branch_id"], payload["semester_id"], payload["section_id"],
            payload.get("faculty_id"), payload.get("room_no"))
        if clashes:
            return False, "Timetable clash:\n  - " + "\n  - ".join(clashes), None

    timetable_id = db.insert("timetable", payload)
    log_audit(user, ACTION_CREATE, "Timetable", "timetable", timetable_id,
              new_value={k: payload.get(k) for k in
                         ("day_of_week", "start_time", "end_time", "subject_id")})
    return True, "Timetable slot added.", timetable_id


def update_slot(timetable_id: int, data: dict, user: dict | None = None,
                allow_clash: bool = False) -> tuple[bool, str]:
    db = get_db()
    old = get_slot(timetable_id)
    if old is None:
        return False, "Timetable slot not found."

    payload = _clean(data)
    merged = {**{k: old[k] for k in _EDITABLE if k in old.keys()}, **payload}

    if merged["end_time"] <= merged["start_time"]:
        return False, "End time must be after start time."

    if not allow_clash:
        clashes = find_clashes(
            merged["day_of_week"], merged["start_time"], merged["end_time"],
            merged["branch_id"], merged["semester_id"], merged["section_id"],
            merged.get("faculty_id"), merged.get("room_no"), exclude_id=timetable_id)
        if clashes:
            return False, "Timetable clash:\n  - " + "\n  - ".join(clashes)

    db.update("timetable", payload, "timetable_id = ?", (timetable_id,))
    log_audit(user, ACTION_UPDATE, "Timetable", "timetable", timetable_id,
              old_value={k: old[k] for k in payload if k in old.keys()},
              new_value=payload)
    return True, "Timetable slot updated."


def delete_slot(timetable_id: int, user: dict | None = None) -> tuple[bool, str]:
    db = get_db()
    slot = get_slot(timetable_id)
    if slot is None:
        return False, "Timetable slot not found."

    # Attendance rows keep their own copy of the class context, so unlinking
    # is safe and preserves history.
    db.execute("UPDATE attendance_sessions SET timetable_id = NULL WHERE timetable_id = ?",
               (timetable_id,))
    db.delete("timetable", "timetable_id = ?", (timetable_id,))

    log_audit(user, ACTION_DELETE, "Timetable", "timetable", timetable_id,
              old_value={"day": slot["day_of_week"], "subject": slot["subject_name"]})
    return True, "Timetable slot deleted."


def copy_day(source_day: str, target_day: str, branch_id: int, semester_id: int,
             section_id: int, user: dict | None = None) -> tuple[bool, str, int]:
    """Duplicate one day's schedule onto another -- saves a lot of typing."""
    slots = search_timetable(branch_id=branch_id, semester_id=semester_id,
                             section_id=section_id, day=source_day)
    if not slots:
        return False, f"No slots found on {source_day} for this class.", 0

    copied, skipped = 0, 0
    for slot in slots:
        ok, _, _ = add_slot({
            "day_of_week": target_day,
            "start_time": slot["start_time"], "end_time": slot["end_time"],
            "branch_id": branch_id, "semester_id": semester_id, "section_id": section_id,
            "subject_id": slot["subject_id"], "faculty_id": slot["faculty_id"],
            "room_no": slot["room_no"], "session_id": slot["session_id"],
        }, user=user)
        copied += 1 if ok else 0
        skipped += 0 if ok else 1

    message = f"Copied {copied} slot(s) from {source_day} to {target_day}."
    if skipped:
        message += f" {skipped} skipped due to clashes."
    return True, message, copied
