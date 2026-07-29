"""
Row-level data scoping.

Role-based *navigation* controls which screens a user can open.  This module
controls something different and more important: **which rows they may see
once they are there.**

The rules, in one place:

*   **Administrator** -- everything.
*   **Faculty** -- only classes they teach.  Their own subjects, the students
    enrolled in those classes, the attendance sessions they conducted, and
    their own analytics.  Never another lecturer's figures.
*   **Student** -- only their own records.  Their attendance, their sessions,
    their leave, their analytics.  Never a classmate's.

Every scoped query goes through :func:`apply_scope`, which returns SQL
fragments the caller splices into its WHERE clause.  Doing it here rather than
in each view means a screen cannot accidentally leak data by forgetting a
filter -- the restriction is applied in the data layer, below the UI.
"""

from __future__ import annotations

from core.auth import session
from core.database import get_db
from core.logger import get_logger

logger = get_logger("core.scope")


class Scope:
    """The set of rows the signed-in user is allowed to see."""

    def __init__(self) -> None:
        self.role = session.role
        self.is_admin = session.is_admin
        self.is_faculty = session.is_faculty
        self.is_student = session.is_student
        self.faculty_id = session.linked_id if self.is_faculty else None
        self.student_id = session.linked_id if self.is_student else None

        # Lazily computed, because most calls only need one of them.
        self._subject_ids: list[int] | None = None
        self._branch_ids: list[int] | None = None
        self._class_keys: list[tuple] | None = None

    # ------------------------------------------------------------------
    # Faculty reach
    # ------------------------------------------------------------------
    @property
    def subject_ids(self) -> list[int]:
        """Subjects a faculty teaches (empty list means 'no restriction')."""
        if self._subject_ids is None:
            if not self.is_faculty or not self.faculty_id:
                self._subject_ids = []
            else:
                rows = get_db().fetch_all(
                    "SELECT subject_id FROM subjects WHERE faculty_id = ?",
                    (self.faculty_id,))
                self._subject_ids = [r["subject_id"] for r in rows]
        return self._subject_ids

    @property
    def branch_ids(self) -> list[int]:
        """Branches a faculty is connected to, via their subjects or posting."""
        if self._branch_ids is None:
            if not self.is_faculty or not self.faculty_id:
                self._branch_ids = []
            else:
                rows = get_db().fetch_all(
                    """SELECT DISTINCT branch_id FROM (
                           SELECT branch_id FROM subjects WHERE faculty_id = ?
                           UNION
                           SELECT branch_id FROM faculty  WHERE faculty_id = ?
                           UNION
                           SELECT branch_id FROM class_teachers WHERE faculty_id = ?
                       ) WHERE branch_id IS NOT NULL""",
                    (self.faculty_id, self.faculty_id, self.faculty_id))
                self._branch_ids = [r["branch_id"] for r in rows]
        return self._branch_ids

    @property
    def class_keys(self) -> list[tuple]:
        """``(branch_id, semester_id)`` pairs a faculty is responsible for."""
        if self._class_keys is None:
            if not self.is_faculty or not self.faculty_id:
                self._class_keys = []
            else:
                rows = get_db().fetch_all(
                    """SELECT DISTINCT branch_id, semester_id FROM (
                           SELECT branch_id, semester_id FROM subjects
                           WHERE faculty_id = ?
                           UNION
                           SELECT branch_id, semester_id FROM class_teachers
                           WHERE faculty_id = ?
                       )""", (self.faculty_id, self.faculty_id))
                self._class_keys = [(r["branch_id"], r["semester_id"]) for r in rows]
        return self._class_keys

    # ------------------------------------------------------------------
    # SQL fragments
    # ------------------------------------------------------------------
    def attendance_clause(self, student_col: str = "student_id",
                          subject_col: str = "subject_id") -> tuple[str, list]:
        """Restrict an attendance query to what this user may see.

        Returns ``(sql_fragment, params)``; the fragment is ``"1=1"`` for an
        administrator so callers can always splice it in unconditionally.
        """
        if self.is_admin:
            return "1=1", []

        if self.is_student:
            if not self.student_id:
                return "1=0", []           # unlinked account sees nothing
            return f"{student_col} = ?", [self.student_id]

        if self.is_faculty:
            subject_ids = self.subject_ids
            if not subject_ids:
                return "1=0", []
            placeholders = ",".join("?" * len(subject_ids))
            return f"{subject_col} IN ({placeholders})", list(subject_ids)

        return "1=0", []

    def session_clause(self, subject_col: str = "subject_id",
                       faculty_col: str = "faculty_id",
                       session_id_col: str = "att_session_id") -> tuple[str, list]:
        """Restrict a class-session query."""
        if self.is_admin:
            return "1=1", []

        if self.is_student:
            if not self.student_id:
                return "1=0", []
            # A student may see a session only if they are on its roster.
            return (f"{session_id_col} IN (SELECT att_session_id FROM attendance "
                    f"WHERE student_id = ?)", [self.student_id])

        if self.is_faculty:
            subject_ids = self.subject_ids
            if not subject_ids:
                return f"{faculty_col} = ?", [self.faculty_id]
            placeholders = ",".join("?" * len(subject_ids))
            return (f"({subject_col} IN ({placeholders}) OR {faculty_col} = ?)",
                    list(subject_ids) + [self.faculty_id])

        return "1=0", []

    def student_clause(self, student_col: str = "student_id",
                       branch_col: str = "branch_id",
                       semester_col: str = "semester_id") -> tuple[str, list]:
        """Restrict a student-list query."""
        if self.is_admin:
            return "1=1", []

        if self.is_student:
            if not self.student_id:
                return "1=0", []
            return f"{student_col} = ?", [self.student_id]

        if self.is_faculty:
            keys = self.class_keys
            if not keys:
                return "1=0", []
            parts = " OR ".join(f"({branch_col} = ? AND {semester_col} = ?)"
                                for _ in keys)
            params: list = []
            for branch_id, semester_id in keys:
                params.extend([branch_id, semester_id])
            return f"({parts})", params

        return "1=0", []

    def subject_clause(self, subject_col: str = "subject_id") -> tuple[str, list]:
        """Restrict a subject query."""
        if self.is_admin:
            return "1=1", []

        if self.is_faculty:
            subject_ids = self.subject_ids
            if not subject_ids:
                return "1=0", []
            placeholders = ",".join("?" * len(subject_ids))
            return f"{subject_col} IN ({placeholders})", list(subject_ids)

        if self.is_student:
            if not self.student_id:
                return "1=0", []
            # Subjects taught to the student's own class.
            return (f"{subject_col} IN (SELECT sub.subject_id FROM subjects sub "
                    f"JOIN students st ON st.branch_id = sub.branch_id "
                    f"AND st.semester_id = sub.semester_id "
                    f"WHERE st.student_id = ?)", [self.student_id])

        return "1=0", []

    def faculty_clause(self, faculty_col: str = "faculty_id") -> tuple[str, list]:
        """Restrict a faculty-list query: a lecturer sees only themselves."""
        if self.is_admin:
            return "1=1", []
        if self.is_faculty and self.faculty_id:
            return f"{faculty_col} = ?", [self.faculty_id]
        return "1=0", []

    # ------------------------------------------------------------------
    # Convenience for the UI
    # ------------------------------------------------------------------
    def describe(self) -> str:
        """One line explaining what the user is looking at."""
        if self.is_admin:
            return "Showing all records across the institution."
        if self.is_student:
            return "Showing your own records only."
        if self.is_faculty:
            return (f"Showing only your {len(self.subject_ids)} assigned "
                    f"subject(s) and the classes you teach.")
        return "No records available for this account."

    def allowed_subjects(self) -> list:
        """Subject rows this user may select from, for filter dropdowns."""
        db = get_db()
        clause, params = self.subject_clause("s.subject_id")
        return db.fetch_all(
            f"""SELECT s.subject_id, s.subject_code, s.subject_name,
                       b.branch_code, sem.semester_name
                FROM subjects s
                JOIN branches b ON b.branch_id = s.branch_id
                JOIN semesters sem ON sem.semester_id = s.semester_id
                WHERE {clause} AND s.is_active = 1
                ORDER BY sem.semester_number, s.subject_code""", params)

    def allowed_branches(self) -> list:
        """Branch rows this user may select from."""
        db = get_db()

        if self.is_admin:
            return db.fetch_all(
                "SELECT branch_id, branch_name, branch_code FROM branches "
                "WHERE is_active = 1 ORDER BY branch_name")

        if self.is_student and self.student_id:
            return db.fetch_all(
                """SELECT b.branch_id, b.branch_name, b.branch_code
                   FROM branches b JOIN students s ON s.branch_id = b.branch_id
                   WHERE s.student_id = ?""", (self.student_id,))

        branch_ids = self.branch_ids
        if not branch_ids:
            return []
        placeholders = ",".join("?" * len(branch_ids))
        return db.fetch_all(
            f"SELECT branch_id, branch_name, branch_code FROM branches "
            f"WHERE branch_id IN ({placeholders}) ORDER BY branch_name",
            list(branch_ids))

    def allowed_faculty(self) -> list:
        """Faculty rows this user may select from."""
        db = get_db()
        if self.is_admin:
            return db.fetch_all(
                "SELECT faculty_id, full_name, faculty_code FROM faculty "
                "WHERE status = 'Active' ORDER BY full_name")
        if self.is_faculty and self.faculty_id:
            return db.fetch_all(
                "SELECT faculty_id, full_name, faculty_code FROM faculty "
                "WHERE faculty_id = ?", (self.faculty_id,))
        return []


def current_scope() -> Scope:
    """Build a scope for the signed-in user.

    Cheap to construct and never cached across logins, so a sign-out followed
    by a different sign-in can never reuse the previous user's reach.
    """
    return Scope()
