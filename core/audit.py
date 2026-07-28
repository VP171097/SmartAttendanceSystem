"""
Audit trail and activity logging.

Two separate streams, because they answer different questions:

*   **audit_log** -- "who changed what, when, and why".  Every attendance edit,
    leave decision, record deletion and administrative override lands here with
    the before/after values.  This is the tamper-evidence trail.
*   **activity_log** -- "what happened in the app".  Logins, report generation,
    navigation-level events.  Useful for support, not for accountability.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from core.database import get_db
from core.logger import get_logger

logger = get_logger("core.audit")

# ---------------------------------------------------------------------------
# Canonical action names.  Keeping them as constants means the audit viewer can
# offer a reliable filter dropdown instead of free-text search.
# ---------------------------------------------------------------------------
ACTION_LOGIN = "LOGIN"
ACTION_LOGIN_FAILED = "LOGIN_FAILED"
ACTION_LOGOUT = "LOGOUT"
ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"
ACTION_DELETE = "DELETE"
ACTION_ATTENDANCE_MARK = "ATTENDANCE_MARK"
ACTION_ATTENDANCE_EDIT = "ATTENDANCE_EDIT"
ACTION_ATTENDANCE_LOCK = "ATTENDANCE_LOCK"
ACTION_ATTENDANCE_UNLOCK = "ATTENDANCE_UNLOCK"
ACTION_LEAVE_APPLY = "LEAVE_APPLY"
ACTION_LEAVE_REVIEW = "LEAVE_REVIEW"
ACTION_LEAVE_OVERRIDE = "LEAVE_OVERRIDE"
ACTION_PROMOTION = "STUDENT_PROMOTION"
ACTION_BACKUP = "BACKUP"
ACTION_RESTORE = "RESTORE"
ACTION_IMPORT = "DATA_IMPORT"
ACTION_EXPORT = "DATA_EXPORT"
ACTION_REPORT = "REPORT_GENERATE"
ACTION_SETTINGS = "SETTINGS_CHANGE"
ACTION_FACE_REGISTER = "FACE_REGISTER"
ACTION_PASSWORD_CHANGE = "PASSWORD_CHANGE"

ALL_ACTIONS = [
    ACTION_LOGIN, ACTION_LOGIN_FAILED, ACTION_LOGOUT, ACTION_CREATE,
    ACTION_UPDATE, ACTION_DELETE, ACTION_ATTENDANCE_MARK, ACTION_ATTENDANCE_EDIT,
    ACTION_ATTENDANCE_LOCK, ACTION_ATTENDANCE_UNLOCK, ACTION_LEAVE_APPLY,
    ACTION_LEAVE_REVIEW, ACTION_LEAVE_OVERRIDE, ACTION_PROMOTION, ACTION_BACKUP,
    ACTION_RESTORE, ACTION_IMPORT, ACTION_EXPORT, ACTION_REPORT,
    ACTION_SETTINGS, ACTION_FACE_REGISTER, ACTION_PASSWORD_CHANGE,
]


def _serialise(value: Any) -> str | None:
    """Render an arbitrary value for storage in the audit columns."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _host_address() -> str:
    """Machine identity for the audit row -- helpful in a multi-PC lab."""
    try:
        return f"{socket.gethostname()} ({socket.gethostbyname(socket.gethostname())})"
    except OSError:
        return "unknown"


def log_audit(
    user: dict | None,
    action: str,
    module: str = "",
    entity_type: str = "",
    entity_id: int | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str = "",
) -> None:
    """Write one row to the audit trail.

    Audit failures are logged but never raised -- an unwritable audit row must
    not roll back the business operation the user just performed.
    """
    try:
        get_db().insert("audit_log", {
            "user_id":     (user or {}).get("user_id"),
            "username":    (user or {}).get("username", "system"),
            "role":        (user or {}).get("role", "System"),
            "action":      action,
            "module":      module,
            "entity_type": entity_type,
            "entity_id":   entity_id,
            "old_value":   _serialise(old_value),
            "new_value":   _serialise(new_value),
            "reason":      reason or None,
            "ip_address":  _host_address(),
        })
    except Exception as exc:                      # noqa: BLE001 - deliberate
        logger.error("Failed to write audit row (%s): %s", action, exc)


def log_activity(user: dict | None, activity: str, details: str = "") -> None:
    """Write a low-severity activity row."""
    try:
        get_db().insert("activity_log", {
            "user_id":  (user or {}).get("user_id"),
            "username": (user or {}).get("username", "system"),
            "activity": activity,
            "details":  details or None,
        })
    except Exception as exc:                      # noqa: BLE001
        logger.error("Failed to write activity row: %s", exc)


# ---------------------------------------------------------------------------
# Retrieval -- used by the Audit Trail view
# ---------------------------------------------------------------------------
def get_audit_logs(
    user_id: int | None = None,
    action: str | None = None,
    module: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list:
    """Query the audit trail with optional filters."""
    clauses, params = ["1=1"], []

    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if action:
        clauses.append("action = ?")
        params.append(action)
    if module:
        clauses.append("module = ?")
        params.append(module)
    if from_date:
        clauses.append("date(timestamp) >= date(?)")
        params.append(from_date)
    if to_date:
        clauses.append("date(timestamp) <= date(?)")
        params.append(to_date)
    if search:
        clauses.append("(username LIKE ? OR reason LIKE ? OR entity_type LIKE ? "
                       "OR new_value LIKE ?)")
        term = f"%{search}%"
        params.extend([term] * 4)

    sql = (f"SELECT * FROM audit_log WHERE {' AND '.join(clauses)} "
           f"ORDER BY audit_id DESC LIMIT ?")
    params.append(limit)
    return get_db().fetch_all(sql, params)


def get_activity_logs(limit: int = 300) -> list:
    return get_db().fetch_all(
        "SELECT * FROM activity_log ORDER BY activity_id DESC LIMIT ?", (limit,)
    )


def get_entity_history(entity_type: str, entity_id: int) -> list:
    """Full change history for a single record (e.g. one attendance row)."""
    return get_db().fetch_all(
        "SELECT * FROM audit_log WHERE entity_type = ? AND entity_id = ? "
        "ORDER BY audit_id DESC",
        (entity_type, entity_id),
    )


def purge_old_logs(days: int = 365) -> int:
    """Delete audit rows older than ``days``.  Returns rows removed."""
    removed = get_db().execute(
        "DELETE FROM audit_log WHERE date(timestamp) < date('now', ?)",
        (f"-{int(days)} days",),
    )
    get_db().execute(
        "DELETE FROM activity_log WHERE date(timestamp) < date('now', ?)",
        (f"-{int(days)} days",),
    )
    logger.info("Purged audit rows older than %s days (%s removed)", days, removed)
    return removed
