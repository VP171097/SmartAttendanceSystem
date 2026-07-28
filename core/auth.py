"""
Authentication, session state and role-based access control.

Passwords are hashed with **bcrypt** (cost 12) and never stored or logged in
plaintext.  Repeated failures lock an account for a cooling-off period, which
stops casual password guessing on a shared lab machine.

The :class:`Session` singleton holds the signed-in user for the lifetime of the
window; views ask it questions like ``session.is_admin`` or
``session.can(Permission.MANAGE_STUDENTS)`` rather than comparing role strings.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import bcrypt

from config.settings import ROLE_ADMIN, ROLE_FACULTY, ROLE_STUDENT, config
from core.audit import (ACTION_LOGIN, ACTION_LOGIN_FAILED, ACTION_LOGOUT,
                        ACTION_PASSWORD_CHANGE, log_activity, log_audit)
from core.database import get_db
from core.logger import get_logger
from core.validators import validate_password

logger = get_logger("core.auth")

BCRYPT_ROUNDS = 12
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    """Return a bcrypt hash for ``plain`` as a UTF-8 string."""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison of a candidate password against a stored hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash in the database -- treat as a failed login, not a crash.
        logger.warning("Password verification failed: stored hash is malformed")
        return False


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
class Permission:
    """Named capabilities checked by the UI before revealing a feature."""

    VIEW_DASHBOARD = "view_dashboard"
    MANAGE_STUDENTS = "manage_students"
    MANAGE_FACULTY = "manage_faculty"
    MANAGE_ACADEMIC = "manage_academic"      # courses, branches, semesters...
    MANAGE_SUBJECTS = "manage_subjects"
    MANAGE_TIMETABLE = "manage_timetable"
    TAKE_ATTENDANCE = "take_attendance"
    EDIT_ATTENDANCE = "edit_attendance"
    LOCK_ATTENDANCE = "lock_attendance"
    UNLOCK_ATTENDANCE = "unlock_attendance"
    APPLY_LEAVE = "apply_leave"
    REVIEW_LEAVE = "review_leave"
    OVERRIDE_LEAVE = "override_leave"
    VIEW_REPORTS = "view_reports"
    VIEW_ALL_REPORTS = "view_all_reports"
    VIEW_ANALYTICS = "view_analytics"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT = "view_audit"
    MANAGE_BACKUP = "manage_backup"
    MANAGE_SETTINGS = "manage_settings"
    PROMOTE_STUDENTS = "promote_students"
    GENERATE_ID_CARD = "generate_id_card"
    MANAGE_HOLIDAYS = "manage_holidays"
    IMPORT_EXPORT = "import_export"
    REGISTER_FACE = "register_face"
    MARK_SELF_ATTENDANCE = "mark_self_attendance"      # student raises a request
    VERIFY_SELF_ATTENDANCE = "verify_self_attendance"  # faculty approves it


# Faculty deliberately cannot unlock attendance or override a leave decision --
# those are administrative acts and stay with the Admin role.
ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_ADMIN: {
        value for name, value in vars(Permission).items()
        if not name.startswith("_") and isinstance(value, str)
    },
    ROLE_FACULTY: {
        Permission.VIEW_DASHBOARD, Permission.TAKE_ATTENDANCE,
        Permission.EDIT_ATTENDANCE, Permission.LOCK_ATTENDANCE,
        Permission.REVIEW_LEAVE, Permission.VIEW_REPORTS,
        Permission.VIEW_ANALYTICS, Permission.REGISTER_FACE,
        Permission.MANAGE_STUDENTS,     # limited to their own classes
        Permission.VERIFY_SELF_ATTENDANCE,
        Permission.GENERATE_ID_CARD,
        # Note: MANAGE_TIMETABLE is deliberately withheld. Faculty can view the
        # timetable (that nav item only needs VIEW_DASHBOARD) but building it
        # stays an administrative task.
    },
    ROLE_STUDENT: {
        Permission.VIEW_DASHBOARD, Permission.APPLY_LEAVE,
        Permission.VIEW_REPORTS, Permission.MARK_SELF_ATTENDANCE,
    },
}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class Session:
    """Holds the currently signed-in user (singleton)."""

    _instance: "Session | None" = None

    def __new__(cls) -> "Session":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._user = None
            cls._instance._login_time = None
            cls._instance._profile = None
        return cls._instance

    # -- state -------------------------------------------------------------
    @property
    def user(self) -> dict | None:
        return self._user

    @property
    def profile(self) -> dict | None:
        """The linked faculty/student record, loaded once at login."""
        return self._profile

    @property
    def is_authenticated(self) -> bool:
        return self._user is not None

    @property
    def role(self) -> str:
        return (self._user or {}).get("role", "")

    @property
    def user_id(self) -> int | None:
        return (self._user or {}).get("user_id")

    @property
    def username(self) -> str:
        return (self._user or {}).get("username", "")

    @property
    def full_name(self) -> str:
        return (self._user or {}).get("full_name", "")

    @property
    def linked_id(self) -> int | None:
        """faculty_id when Faculty, student_id when Student, else None."""
        return (self._user or {}).get("linked_id")

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_faculty(self) -> bool:
        return self.role == ROLE_FACULTY

    @property
    def is_student(self) -> bool:
        return self.role == ROLE_STUDENT

    @property
    def login_time(self) -> datetime | None:
        return self._login_time

    # -- lifecycle ---------------------------------------------------------
    def start(self, user_row) -> None:
        self._user = dict(user_row)
        self._login_time = datetime.now()
        self._profile = self._load_profile()

    def end(self) -> None:
        if self._user:
            log_audit(self._user, ACTION_LOGOUT, module="Authentication")
            log_activity(self._user, "Logged out")
        self._user = None
        self._login_time = None
        self._profile = None

    def _load_profile(self) -> dict | None:
        """Fetch the faculty/student record this login is attached to."""
        if not self._user or not self._user.get("linked_id"):
            return None
        db = get_db()
        try:
            if self.is_faculty:
                row = db.fetch_one(
                    "SELECT f.*, b.branch_name, b.branch_code FROM faculty f "
                    "LEFT JOIN branches b ON b.branch_id = f.branch_id "
                    "WHERE f.faculty_id = ?", (self._user["linked_id"],))
            elif self.is_student:
                row = db.fetch_one(
                    "SELECT * FROM v_student_full WHERE student_id = ?",
                    (self._user["linked_id"],))
            else:
                return None
            return dict(row) if row else None
        except Exception as exc:                  # noqa: BLE001
            logger.error("Could not load profile for %s: %s", self.username, exc)
            return None

    def refresh_profile(self) -> None:
        """Reload the profile after the user edits their own record."""
        self._profile = self._load_profile()

    # -- authorisation -----------------------------------------------------
    def can(self, permission: str) -> bool:
        """True when the signed-in role holds ``permission``."""
        return permission in ROLE_PERMISSIONS.get(self.role, set())

    def is_expired(self) -> bool:
        """True once the configured idle timeout has elapsed."""
        if not self._login_time:
            return True
        minutes = int(config.get("session_timeout_minutes", 120))
        if minutes <= 0:
            return False
        return datetime.now() - self._login_time > timedelta(minutes=minutes)

    def touch(self) -> None:
        """Reset the inactivity clock -- called on meaningful interaction."""
        self._login_time = datetime.now()


session = Session()


# ---------------------------------------------------------------------------
# Login / account management
# ---------------------------------------------------------------------------
class AuthError(Exception):
    """Raised when a login attempt cannot succeed."""


def authenticate(username: str, password: str, expected_role: str | None = None) -> dict:
    """Verify credentials and start a session.

    Raises:
        AuthError: with a message safe to show the user.
    """
    db = get_db()
    username = (username or "").strip()

    if not username or not password:
        raise AuthError("Please enter both username and password.")

    row = db.fetch_one("SELECT * FROM users WHERE username = ?", (username,))

    if row is None:
        # Same message for unknown user and wrong password -- do not confirm
        # which usernames exist.
        log_audit(None, ACTION_LOGIN_FAILED, module="Authentication",
                  new_value=username, reason="Unknown username")
        raise AuthError("Invalid username or password.")

    user = dict(row)

    if not user["is_active"]:
        raise AuthError("This account is deactivated. Contact the administrator.")

    # -- lockout check -----------------------------------------------------
    locked_until = user.get("locked_until")
    if locked_until:
        try:
            until = datetime.strptime(locked_until, "%Y-%m-%d %H:%M:%S")
            if datetime.now() < until:
                remaining = int((until - datetime.now()).total_seconds() / 60) + 1
                raise AuthError(
                    f"Account locked after repeated failed attempts. "
                    f"Try again in {remaining} minute(s)."
                )
        except ValueError:
            pass   # unparseable timestamp -> treat as not locked

    # -- password ----------------------------------------------------------
    if not verify_password(password, user["password_hash"]):
        attempts = int(user.get("failed_attempts", 0)) + 1
        updates = {"failed_attempts": attempts}
        if attempts >= MAX_FAILED_ATTEMPTS:
            lock_until = datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)
            updates["locked_until"] = lock_until.strftime("%Y-%m-%d %H:%M:%S")
        db.update("users", updates, "user_id = ?", (user["user_id"],))

        log_audit(None, ACTION_LOGIN_FAILED, module="Authentication",
                  entity_type="users", entity_id=user["user_id"],
                  new_value=username, reason=f"Wrong password (attempt {attempts})")

        if attempts >= MAX_FAILED_ATTEMPTS:
            raise AuthError(
                f"Too many failed attempts. Account locked for {LOCKOUT_MINUTES} minutes."
            )
        remaining = MAX_FAILED_ATTEMPTS - attempts
        raise AuthError(f"Invalid username or password. {remaining} attempt(s) remaining.")

    # -- role gate ---------------------------------------------------------
    if expected_role and user["role"] != expected_role:
        log_audit(None, ACTION_LOGIN_FAILED, module="Authentication",
                  entity_type="users", entity_id=user["user_id"],
                  reason=f"Role mismatch: selected {expected_role}, actual {user['role']}")
        raise AuthError(f"This account is not registered as {expected_role}.")

    # -- success -----------------------------------------------------------
    db.update("users", {
        "last_login": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "failed_attempts": 0,
        "locked_until": None,
    }, "user_id = ?", (user["user_id"],))

    session.start(user)
    log_audit(user, ACTION_LOGIN, module="Authentication",
              entity_type="users", entity_id=user["user_id"])
    log_activity(user, "Signed in", f"Role: {user['role']}")
    logger.info("Login successful: %s (%s)", username, user["role"])
    return user


def logout() -> None:
    session.end()


def change_password(user_id: int, current: str, new: str) -> tuple[bool, str]:
    """Change a password after verifying the current one."""
    db = get_db()
    row = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if row is None:
        return False, "User account not found."

    if not verify_password(current, row["password_hash"]):
        return False, "Current password is incorrect."

    ok, message = validate_password(new)
    if not ok:
        return False, message

    if verify_password(new, row["password_hash"]):
        return False, "The new password must be different from the current one."

    db.update("users", {
        "password_hash": hash_password(new),
        "must_change_pw": 0,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, "user_id = ?", (user_id,))

    log_audit(session.user, ACTION_PASSWORD_CHANGE, module="Authentication",
              entity_type="users", entity_id=user_id, reason="Self-service change")
    return True, "Password changed successfully."


def reset_password(user_id: int, new_password: str, reason: str = "") -> tuple[bool, str]:
    """Administrator reset -- forces a change at next login."""
    ok, message = validate_password(new_password)
    if not ok:
        return False, message

    get_db().update("users", {
        "password_hash": hash_password(new_password),
        "must_change_pw": 1,
        "failed_attempts": 0,
        "locked_until": None,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, "user_id = ?", (user_id,))

    log_audit(session.user, ACTION_PASSWORD_CHANGE, module="User Management",
              entity_type="users", entity_id=user_id,
              reason=reason or "Administrator reset")
    return True, "Password reset. The user must change it at next sign-in."


def create_user(username: str, password: str, role: str, full_name: str,
                email: str = "", linked_id: int | None = None,
                must_change: bool = True) -> int:
    """Create a login account.  Returns the new ``user_id``."""
    db = get_db()
    if db.exists("users", "username = ?", (username,)):
        raise AuthError(f"Username '{username}' is already taken.")

    user_id = db.insert("users", {
        "username": username.strip(),
        "password_hash": hash_password(password),
        "role": role,
        "full_name": full_name.strip(),
        "email": (email or "").strip() or None,
        "linked_id": linked_id,
        "must_change_pw": 1 if must_change else 0,
    })
    log_audit(session.user, "CREATE", module="User Management",
              entity_type="users", entity_id=user_id,
              new_value={"username": username, "role": role})
    return user_id


def set_user_active(user_id: int, active: bool, reason: str = "") -> None:
    get_db().update("users", {"is_active": 1 if active else 0},
                    "user_id = ?", (user_id,))
    log_audit(session.user, "UPDATE", module="User Management",
              entity_type="users", entity_id=user_id,
              new_value={"is_active": active}, reason=reason)


def unlock_user(user_id: int) -> None:
    """Clear a lockout early (administrator action)."""
    get_db().update("users", {"failed_attempts": 0, "locked_until": None},
                    "user_id = ?", (user_id,))
    log_audit(session.user, "UPDATE", module="User Management",
              entity_type="users", entity_id=user_id, reason="Account unlocked")


# ---------------------------------------------------------------------------
# Self-service password reset
# ---------------------------------------------------------------------------
# The application is offline, so there is no email to send a reset link to.
# Instead a user proves identity against details already on their record --
# their linked mobile number or date of birth -- which the administrator
# entered when the record was created.  Anything more elaborate would be
# security theatre on a single-college desktop install.
RESET_FIELDS = ("mobile", "dob")


def find_account(username: str) -> dict | None:
    """Look up an account for the reset flow.  Returns None if unknown."""
    row = get_db().fetch_one(
        "SELECT * FROM users WHERE username = ?", ((username or "").strip(),))
    return dict(row) if row else None


def get_reset_challenge(username: str) -> tuple[bool, str, dict | None]:
    """Describe what the user must supply to reset this account.

    Returns ``(ok, message, challenge)`` where ``challenge`` names the fields
    available for verification.
    """
    user = find_account(username)
    if user is None:
        # Do not confirm which usernames exist.
        return False, ("If that username exists, its reset details will be "
                       "requested. Please check the username and try again."), None

    if not user["is_active"]:
        return False, "This account is deactivated. Contact the administrator.", None

    db = get_db()
    record = None
    if user["role"] == ROLE_STUDENT and user["linked_id"]:
        record = db.fetch_one(
            "SELECT mobile, dob, full_name FROM students WHERE student_id = ?",
            (user["linked_id"],))
    elif user["role"] == ROLE_FACULTY and user["linked_id"]:
        record = db.fetch_one(
            "SELECT mobile, dob, full_name FROM faculty WHERE faculty_id = ?",
            (user["linked_id"],))

    if record is None or not (record["mobile"] or record["dob"]):
        return False, ("This account has no mobile number or date of birth on "
                       "record, so it cannot be reset here.\n\n"
                       "Please ask the administrator to reset it from "
                       "Settings -> User Accounts."), None

    return True, "", {
        "user_id": user["user_id"],
        "username": user["username"],
        "role": user["role"],
        "full_name": user["full_name"],
        "has_mobile": bool(record["mobile"]),
        "has_dob": bool(record["dob"]),
        "mobile": record["mobile"],
        "dob": record["dob"],
    }


def reset_password_self_service(username: str, mobile: str, dob: str,
                                new_password: str) -> tuple[bool, str]:
    """Reset a password after verifying the on-record identity details."""
    ok, message, challenge = get_reset_challenge(username)
    if not ok:
        return False, message

    from core.validators import normalise_mobile, parse_date

    verified = False

    if challenge["has_mobile"] and mobile:
        if normalise_mobile(mobile) == normalise_mobile(challenge["mobile"]):
            verified = True
        else:
            return False, "The mobile number does not match our records."

    if challenge["has_dob"] and dob:
        supplied, stored = parse_date(dob), parse_date(challenge["dob"])
        if supplied and stored and supplied == stored:
            verified = True
        else:
            return False, "The date of birth does not match our records."

    if not verified:
        return False, ("Please supply at least one detail that matches your "
                       "record, so we can confirm it is really you.")

    ok, message = validate_password(new_password)
    if not ok:
        return False, message

    get_db().update("users", {
        "password_hash": hash_password(new_password),
        "must_change_pw": 0,
        "failed_attempts": 0,
        "locked_until": None,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, "user_id = ?", (challenge["user_id"],))

    log_audit({"user_id": challenge["user_id"], "username": challenge["username"],
               "role": challenge["role"]},
              ACTION_PASSWORD_CHANGE, module="Authentication",
              entity_type="users", entity_id=challenge["user_id"],
              reason="Self-service reset verified against on-record details")

    logger.info("Self-service password reset for %s", challenge["username"])
    return True, ("Your password has been reset. "
                  "You can now sign in with your new password.")


def require(permission: str) -> None:
    """Raise :class:`PermissionError` unless the session holds ``permission``."""
    if not session.can(permission):
        raise PermissionError(
            f"Your role ({session.role or 'guest'}) is not allowed to perform this action."
        )
