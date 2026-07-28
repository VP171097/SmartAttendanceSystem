"""
Academic structure management.

Tabbed screen covering courses, branches, semesters, sections, academic
sessions, batches and the holiday calendar.

This is what makes the system extensible without a code change: adding a new
course generates its semesters automatically, and every other screen builds its
dropdowns from these tables at runtime.
"""

from __future__ import annotations

from datetime import date, datetime

import customtkinter as ctk

from config.settings import config
from config.theme import FONTS, SEMANTIC, color
from core.auth import session
from core.logger import get_logger
from core.validators import (validate_batch_name, validate_code,
                             validate_session_name)
from models import academic
from ui.widgets.components import PageHeader, StatCard
from ui.widgets.dialogs import (FormDialog, FormField, ask_confirm, show_error,
                                show_info, show_success, show_warning)
from ui.widgets.table import DataTable, column, dash_format, date_format

logger = get_logger("ui.academic")

HOLIDAY_TYPES = ["Public", "Institutional", "Vacation", "Examination"]


class AcademicView(ctk.CTkFrame):
    """Tabbed academic structure editor."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._tables: dict[str, DataTable] = {}
        self._selected: dict[str, dict | None] = {}

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Academic Setup",
            subtitle=("Courses, branches, semesters, sections, sessions, batches "
                      "and holidays - the structure every other screen builds on"),
            icon="⌂")
        header.pack(fill="x", padx=18, pady=(14, 10))

        # ---- tiles ---------------------------------------------------------
        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(6):
            tiles.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tiles = {}
        for index, (key, label, icon, accent) in enumerate([
                ("courses", "Courses", "▤", SEMANTIC["info"]),
                ("branches", "Branches", "⌂", SEMANTIC["purple"]),
                ("semesters", "Semesters", "◷", SEMANTIC["teal"]),
                ("sections", "Sections", "▣", SEMANTIC["warning"]),
                ("sessions", "Sessions", "≡", SEMANTIC["success"]),
                ("holidays", "Holidays", "★", SEMANTIC["danger"])]):
            tile = StatCard(tiles, label, "0", icon, accent)
            tile.grid(row=0, column=index, sticky="ew", padx=3)
            self.tiles[key] = tile

        # ---- tabs ------------------------------------------------------------
        self.tabs = ctk.CTkTabview(self, corner_radius=10,
                                   segmented_button_selected_color=color("primary"),
                                   segmented_button_selected_hover_color=color("primary_hover"))
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        for name in ("Branches", "Courses", "Semesters", "Sections",
                     "Class Teachers", "Academic Sessions", "Batches", "Holidays"):
            self.tabs.add(name)

        self._build_branches()
        self._build_courses()
        self._build_semesters()
        self._build_sections()
        self._build_class_teachers()
        self._build_sessions()
        self._build_batches()
        self._build_holidays()

        self.tabs.set("Branches")

    def _tab_toolbar(self, tab_name: str, buttons: list[tuple]) -> ctk.CTkFrame:
        """Standard button strip at the top of each tab."""
        bar = ctk.CTkFrame(self.tabs.tab(tab_name), fg_color="transparent")
        bar.pack(fill="x", pady=(6, 10))

        for text, command, accent in buttons:
            ctk.CTkButton(
                bar, text=text, command=command, width=150, height=34, corner_radius=7,
                font=FONTS["small_bold"],
                fg_color=accent or color("primary"),
                hover_color=accent or color("primary_hover")).pack(side="left", padx=(0, 8))
        return bar

    # ==================================================================
    # Branches
    # ==================================================================
    def _build_branches(self) -> None:
        self._tab_toolbar("Branches", [
            ("+  Add Branch", self.add_branch, None),
            ("Edit Selected", self.edit_branch, SEMANTIC["teal"]),
            ("Delete Selected", self.delete_branch, SEMANTIC["danger"]),
        ])

        table = DataTable(
            self.tabs.tab("Branches"),
            columns=[
                column("branch_code", "Code", 80, "center"),
                column("branch_name", "Branch Name", 280, stretch=True),
                column("course_name", "Course", 130),
                column("hod_name", "Head of Department", 190, format=dash_format),
                column("intake_capacity", "Intake", 80, "center"),
                column("student_count", "Students", 90, "center"),
                column("faculty_count", "Faculty", 85, "center"),
            ],
            on_select=lambda row: self._selected.__setitem__("branch", row),
            on_double_click=lambda _row: self.edit_branch(), height=13)
        table.pack(fill="both", expand=True)
        self._tables["branches"] = table

    def add_branch(self) -> None:
        courses = academic.get_courses()
        if not courses:
            show_warning(self, "No Course", "Add a course before adding branches.")
            return

        values = FormDialog(
            self, "Add Branch",
            [
                FormField("course_id", "Course", "select", required=True,
                          options={c["course_name"]: c["course_id"] for c in courses}),
                FormField("branch_code", "Branch Code", required=True,
                          validator=lambda v: validate_code(v, "Branch code"),
                          placeholder="CSE", hint="Short code used in reports and IDs"),
                FormField("branch_name", "Branch Name", required=True, span=2,
                          placeholder="Computer Science & Engineering"),
                FormField("hod_name", "Head of Department", placeholder="Dr. Anil Mehta"),
                FormField("intake_capacity", "Intake Capacity", "number", default=60),
            ],
            submit_text="Add Branch", width=680, height=430).show()

        if not values:
            return

        ok, message, _ = academic.add_branch(
            values["course_id"], values["branch_name"], values["branch_code"],
            values.get("hod_name", ""), int(values.get("intake_capacity") or 60),
            session.user)

        if ok:
            show_success(self, "Branch Added", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Could Not Add Branch", message)

    def edit_branch(self) -> None:
        selected = self._selected.get("branch")
        if not selected:
            show_info(self, "No Selection", "Select a branch from the list first.")
            return

        values = FormDialog(
            self, f"Edit Branch - {selected['branch_name']}",
            [
                FormField("branch_code", "Branch Code", required=True,
                          validator=lambda v: validate_code(v, "Branch code")),
                FormField("branch_name", "Branch Name", required=True, span=2),
                FormField("hod_name", "Head of Department"),
                FormField("intake_capacity", "Intake Capacity", "number"),
                FormField("is_active", "Active", "checkbox", placeholder="Branch is active"),
            ],
            values=selected, submit_text="Save Changes", width=680, height=400).show()

        if not values:
            return

        values["is_active"] = 1 if values.get("is_active") else 0
        ok, message = academic.update_branch(selected["branch_id"], values, session.user)

        if ok:
            show_success(self, "Branch Updated", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Could Not Update", message)

    def delete_branch(self) -> None:
        selected = self._selected.get("branch")
        if not selected:
            show_info(self, "No Selection", "Select a branch from the list first.")
            return

        if not ask_confirm(
                self, "Delete Branch",
                f"Delete '{selected['branch_name']}' ({selected['branch_code']})?\n\n"
                f"It currently has {selected['student_count']} student(s) and "
                f"{selected['faculty_count']} faculty member(s).\n\n"
                "Deletion is refused if any students or subjects still reference it.",
                confirm_text="Delete", danger=True):
            return

        ok, message = academic.delete_branch(selected["branch_id"], session.user)
        if ok:
            show_success(self, "Branch Deleted", message)
            self._selected["branch"] = None
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Cannot Delete", message)

    # ==================================================================
    # Courses
    # ==================================================================
    def _build_courses(self) -> None:
        self._tab_toolbar("Courses", [
            ("+  Add Course", self.add_course, None),
            ("Delete Selected", self.delete_course, SEMANTIC["danger"]),
        ])

        table = DataTable(
            self.tabs.tab("Courses"),
            columns=[
                column("course_code", "Code", 90, "center"),
                column("course_name", "Course Name", 250, stretch=True),
                column("duration_years", "Duration (yrs)", 120, "center"),
                column("total_semesters", "Semesters", 100, "center"),
                column("description", "Description", 320, format=dash_format),
            ],
            on_select=lambda row: self._selected.__setitem__("course", row), height=13)
        table.pack(fill="both", expand=True)
        self._tables["courses"] = table

    def add_course(self) -> None:
        values = FormDialog(
            self, "Add Course",
            [
                FormField("course_name", "Course Name", required=True,
                          placeholder="Diploma", span=2),
                FormField("course_code", "Course Code", required=True,
                          validator=lambda v: validate_code(v, "Course code"),
                          placeholder="DIP"),
                FormField("duration_years", "Duration (years)", "number", default=3,
                          required=True),
                FormField("total_semesters", "Total Semesters", "number", default=6,
                          required=True,
                          hint="Semester records are created automatically"),
                FormField("sections", "Sections (optional)", span=2,
                          placeholder="A, B",
                          hint="Optional. Comma-separated, e.g. 'A, B'. Leave blank "
                               "if this college does not divide classes into "
                               "sections - everything works without them."),
                FormField("description", "Description", "textarea", span=2),
            ],
            submit_text="Add Course", width=700, height=520).show()

        if not values:
            return

        try:
            semesters = int(values.get("total_semesters") or 6)
            years = int(values.get("duration_years") or 3)
        except (TypeError, ValueError):
            show_error(self, "Invalid Input", "Duration and semesters must be numbers.")
            return

        if not 1 <= semesters <= 16:
            show_error(self, "Invalid Input", "Total semesters must be between 1 and 16.")
            return

        ok, message, _ = academic.add_course(
            values["course_name"], values["course_code"], years, semesters,
            values.get("description", ""), values.get("sections", ""), session.user)

        if ok:
            show_success(self, "Course Added", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Could Not Add Course", message)

    def delete_course(self) -> None:
        selected = self._selected.get("course")
        if not selected:
            show_info(self, "No Selection", "Select a course from the list first.")
            return

        if not ask_confirm(
                self, "Delete Course",
                f"Delete '{selected['course_name']}'?\n\n"
                "Its semesters are removed with it. Deletion is refused if any "
                "branches or students still reference it.",
                confirm_text="Delete", danger=True):
            return

        ok, message = academic.delete_course(selected["course_id"], session.user)
        if ok:
            show_success(self, "Course Deleted", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Cannot Delete", message)

    # ==================================================================
    # Semesters
    # ==================================================================
    def _build_semesters(self) -> None:
        self._tab_toolbar("Semesters", [
            ("+  Add Semester", self.add_semester, None),
            ("Delete Selected", self.delete_semester, SEMANTIC["danger"]),
        ])

        table = DataTable(
            self.tabs.tab("Semesters"),
            columns=[
                column("semester_number", "No.", 70, "center"),
                column("semester_name", "Semester Name", 220, stretch=True),
                column("course_name", "Course", 200),
                column("student_count", "Students", 110, "center"),
                column("subject_count", "Subjects", 110, "center"),
            ],
            on_select=lambda row: self._selected.__setitem__("semester", row), height=13)
        table.pack(fill="both", expand=True)
        self._tables["semesters"] = table

    def add_semester(self) -> None:
        courses = academic.get_courses()
        if not courses:
            show_warning(self, "No Course", "Add a course first.")
            return

        values = FormDialog(
            self, "Add Semester",
            [
                FormField("course_id", "Course", "select", required=True,
                          options={c["course_name"]: c["course_id"] for c in courses}),
                FormField("semester_number", "Semester Number", "number", required=True,
                          placeholder="7"),
                FormField("semester_name", "Semester Name", span=2,
                          placeholder="Semester 7",
                          hint="Leave blank to name it automatically"),
                FormField("sections", "Sections (optional)", span=2,
                          placeholder="A, B",
                          hint="Optional. Comma-separated. Leave blank if this "
                               "semester has no sections."),
            ],
            submit_text="Add Semester", width=660, height=430,
            description="Extend a course beyond its current semesters").show()

        if not values:
            return

        try:
            number = int(values["semester_number"])
        except (TypeError, ValueError):
            show_error(self, "Invalid Input", "Semester number must be a whole number.")
            return

        ok, message, _ = academic.add_semester(
            values["course_id"], number, values.get("semester_name", ""),
            values.get("sections", ""), session.user)

        if ok:
            show_success(self, "Semester Added", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Could Not Add Semester", message)

    def delete_semester(self) -> None:
        selected = self._selected.get("semester")
        if not selected:
            show_info(self, "No Selection", "Select a semester from the list first.")
            return

        if not ask_confirm(self, "Delete Semester",
                           f"Delete '{selected['semester_name']}'?\n\n"
                           "Deletion is refused if students or subjects reference it.",
                           confirm_text="Delete", danger=True):
            return

        ok, message = academic.delete_semester(selected["semester_id"], session.user)
        if ok:
            show_success(self, "Semester Deleted", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Cannot Delete", message)

    # ==================================================================
    # Sections
    # ==================================================================
    def _build_sections(self) -> None:
        self._tab_toolbar("Sections", [
            ("+  Add Section", self.add_section, None),
            ("Delete Selected", self.delete_section, SEMANTIC["danger"]),
        ])

        table = DataTable(
            self.tabs.tab("Sections"),
            columns=[
                column("section_name", "Section", 120, "center"),
                column("capacity", "Capacity", 130, "center"),
                column("student_count", "Students Assigned", 180, "center"),
                column("is_active", "Active", 100, "center",
                       format=lambda v, _r: "Yes" if v else "No"),
            ],
            on_select=lambda row: self._selected.__setitem__("section", row), height=13)
        table.pack(fill="both", expand=True)
        self._tables["sections"] = table

    def add_section(self) -> None:
        values = FormDialog(
            self, "Add Section",
            [
                FormField("section_name", "Section Name", required=True,
                          placeholder="D", hint="Any label - A, B, C, D, ... no limit"),
                FormField("capacity", "Capacity", "number", default=60),
            ],
            submit_text="Add Section", width=560, height=290).show()

        if not values:
            return

        ok, message, _ = academic.add_section(
            values["section_name"], int(values.get("capacity") or 60), session.user)

        if ok:
            show_success(self, "Section Added", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Could Not Add Section", message)

    def delete_section(self) -> None:
        selected = self._selected.get("section")
        if not selected:
            show_info(self, "No Selection", "Select a section from the list first.")
            return

        if not ask_confirm(self, "Delete Section",
                           f"Delete section '{selected['section_name']}'?",
                           confirm_text="Delete", danger=True):
            return

        ok, message = academic.delete_section(selected["section_id"], session.user)
        if ok:
            show_success(self, "Section Deleted", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Cannot Delete", message)

    # ==================================================================
    # Class teachers
    # ==================================================================
    def _build_class_teachers(self) -> None:
        self._tab_toolbar("Class Teachers", [
            ("+  Assign Teacher", self.assign_class_teacher, None),
            ("Remove Selected", self.remove_class_teacher, SEMANTIC["danger"]),
        ])

        ctk.CTkLabel(
            self.tabs.tab("Class Teachers"),
            text=("The class teacher is who a student's leave application goes to "
                  "first. If no class teacher is assigned, the system falls back to "
                  "the faculty teaching that class the most subjects, and then to "
                  "the head of department - so an application is never left "
                  "unrouted."),
            font=FONTS["small"], text_color=color("text_muted"), anchor="w",
            justify="left", wraplength=940).pack(fill="x", pady=(0, 8))

        table = DataTable(
            self.tabs.tab("Class Teachers"),
            columns=[
                column("branch_name", "Branch", 220, stretch=True),
                column("semester_name", "Semester", 120),
                column("section_name", "Section", 90, "center", format=dash_format),
                column("faculty_code", "Code", 85),
                column("faculty_name", "Class Teacher", 210),
                column("mobile", "Mobile", 120, format=dash_format),
                column("assigned_on", "Assigned On", 150, format=date_format),
            ],
            on_select=lambda row: self._selected.__setitem__("class_teacher", row),
            height=12)
        table.pack(fill="both", expand=True)
        self._tables["class_teachers"] = table

    def assign_class_teacher(self) -> None:
        from models import faculty as faculty_model

        faculty = faculty_model.get_all_faculty()
        if not faculty:
            show_warning(self, "No Faculty", "Add faculty members first.")
            return

        sections = academic.get_sections()
        values = FormDialog(
            self, "Assign Class Teacher",
            [
                FormField("branch_id", "Branch", "select", required=True,
                          options={b["branch_name"]: b["branch_id"]
                                   for b in academic.get_branches()}),
                FormField("semester_id", "Semester", "select", required=True,
                          options={s["semester_name"]: s["semester_id"]
                                   for s in academic.get_semesters()}),
                FormField("section_id", "Section", "select",
                          options={"All sections (no section)": None,
                                   **{s["section_name"]: s["section_id"]
                                      for s in sections}},
                          hint="Leave as 'All sections' when the class has no "
                               "sections defined"),
                FormField("faculty_id", "Class Teacher", "select", required=True,
                          options={f"{f['faculty_code']} - {f['full_name']}":
                                   f["faculty_id"] for f in faculty}),
            ],
            submit_text="Assign", width=680, height=400,
            description="Leave applications for this class will be routed here").show()

        if not values:
            return

        ok, message, _ = academic.assign_class_teacher(
            values["branch_id"], values["semester_id"], values.get("section_id"),
            values["faculty_id"], session.user)

        if ok:
            show_success(self, "Class Teacher Assigned", message)
            self.refresh()
            self.app.invalidate("leave")
        else:
            show_error(self, "Could Not Assign", message)

    def remove_class_teacher(self) -> None:
        selected = self._selected.get("class_teacher")
        if not selected:
            show_info(self, "No Selection", "Select an assignment from the list first.")
            return

        if not ask_confirm(
                self, "Remove Class Teacher",
                f"Remove {selected['faculty_name']} as class teacher for "
                f"{selected['branch_name']} {selected['semester_name']}"
                f"{(' Sec ' + selected['section_name']) if selected['section_name'] else ''}?\n\n"
                "Leave applications will fall back to the subject teacher or "
                "head of department.",
                confirm_text="Remove", danger=True):
            return

        ok, message = academic.remove_class_teacher(
            selected["class_teacher_id"], session.user)

        if ok:
            show_success(self, "Assignment Removed", message)
            self.refresh()
            self.app.invalidate("leave")
        else:
            show_error(self, "Could Not Remove", message)

    # ==================================================================
    # Academic sessions
    # ==================================================================
    def _build_sessions(self) -> None:
        self._tab_toolbar("Academic Sessions", [
            ("+  Add Session", self.add_session, None),
            ("Set as Current", self.set_current_session, SEMANTIC["success"]),
            ("Delete Selected", self.delete_session, SEMANTIC["danger"]),
        ])

        table = DataTable(
            self.tabs.tab("Academic Sessions"),
            columns=[
                column("session_name", "Session", 140, "center"),
                column("start_date", "Start Date", 140, format=date_format),
                column("end_date", "End Date", 140, format=date_format),
                column("is_current", "Current", 110, "center",
                       format=lambda v, _r: "CURRENT" if v else ""),
                column("is_active", "Active", 100, "center",
                       format=lambda v, _r: "Yes" if v else "No"),
            ],
            on_select=lambda row: self._selected.__setitem__("session", row), height=13)
        table.pack(fill="both", expand=True)
        self._tables["sessions"] = table

    def add_session(self) -> None:
        next_year = datetime.now().year + 1
        values = FormDialog(
            self, "Add Academic Session",
            [
                FormField("session_name", "Session Name", required=True,
                          validator=validate_session_name,
                          placeholder=f"{next_year}-{str(next_year + 1)[-2:]}",
                          hint="Format: 2027-28"),
                FormField("start_date", "Start Date", "date", required=True,
                          default=f"{next_year}-07-01"),
                FormField("end_date", "End Date", "date", required=True,
                          default=f"{next_year + 1}-06-30"),
                FormField("make_current", "Set as Current", "checkbox",
                          placeholder="Make this the active session"),
            ],
            submit_text="Add Session", width=620, height=390,
            description="Attendance is always linked to an academic session").show()

        if not values:
            return

        ok, message, _ = academic.add_session(
            values["session_name"], values["start_date"], values["end_date"],
            bool(values.get("make_current")), session.user)

        if ok:
            show_success(self, "Session Added", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Could Not Add Session", message)

    def set_current_session(self) -> None:
        selected = self._selected.get("session")
        if not selected:
            show_info(self, "No Selection", "Select an academic session first.")
            return

        if not ask_confirm(
                self, "Change Current Session",
                f"Make '{selected['session_name']}' the current academic session?\n\n"
                "New attendance sessions and student records will be linked to it. "
                "Existing records keep their original session.",
                confirm_text="Set as Current"):
            return

        ok, message = academic.set_current_session(selected["session_id"], session.user)
        if ok:
            show_success(self, "Session Changed", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Could Not Change", message)

    def delete_session(self) -> None:
        selected = self._selected.get("session")
        if not selected:
            show_info(self, "No Selection", "Select an academic session first.")
            return

        if not ask_confirm(self, "Delete Academic Session",
                           f"Delete '{selected['session_name']}'?\n\n"
                           "Deletion is refused if attendance or students reference it.",
                           confirm_text="Delete", danger=True):
            return

        ok, message = academic.delete_session(selected["session_id"], session.user)
        if ok:
            show_success(self, "Session Deleted", message)
            self.refresh()
        else:
            show_error(self, "Cannot Delete", message)

    # ==================================================================
    # Batches
    # ==================================================================
    def _build_batches(self) -> None:
        self._tab_toolbar("Batches", [
            ("+  Add Batch", self.add_batch, None),
            ("Delete Selected", self.delete_batch, SEMANTIC["danger"]),
        ])

        table = DataTable(
            self.tabs.tab("Batches"),
            columns=[
                column("batch_name", "Batch", 150, "center"),
                column("start_year", "Start Year", 130, "center"),
                column("end_year", "End Year", 130, "center"),
                column("student_count", "Students", 130, "center"),
                column("is_active", "Active", 100, "center",
                       format=lambda v, _r: "Yes" if v else "No"),
            ],
            on_select=lambda row: self._selected.__setitem__("batch", row), height=13)
        table.pack(fill="both", expand=True)
        self._tables["batches"] = table

    def add_batch(self) -> None:
        current_year = datetime.now().year
        values = FormDialog(
            self, "Add Batch",
            [
                FormField("batch_name", "Batch Name", required=True,
                          validator=validate_batch_name,
                          placeholder=f"{current_year}-{current_year + 3}",
                          hint="Format: 2027-2030", span=2),
            ],
            submit_text="Add Batch", width=560, height=280,
            description="A batch groups students by their admission year").show()

        if not values:
            return

        import re
        parts = re.split(r"[-–]", values["batch_name"])
        try:
            start_year, end_year = int(parts[0]), int(parts[1])
        except (IndexError, ValueError):
            show_error(self, "Invalid Batch", "Could not read the years from that name.")
            return

        ok, message, _ = academic.add_batch(
            values["batch_name"], start_year, end_year, session.user)

        if ok:
            show_success(self, "Batch Added", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Could Not Add Batch", message)

    def delete_batch(self) -> None:
        selected = self._selected.get("batch")
        if not selected:
            show_info(self, "No Selection", "Select a batch from the list first.")
            return

        if not ask_confirm(self, "Delete Batch",
                           f"Delete batch '{selected['batch_name']}'?",
                           confirm_text="Delete", danger=True):
            return

        ok, message = academic.delete_batch(selected["batch_id"], session.user)
        if ok:
            show_success(self, "Batch Deleted", message)
            self.refresh()
            self.app.invalidate()
        else:
            show_error(self, "Cannot Delete", message)

    # ==================================================================
    # Holidays
    # ==================================================================
    def _build_holidays(self) -> None:
        self._tab_toolbar("Holidays", [
            ("+  Add Holiday", self.add_holiday, None),
            ("Delete Selected", self.delete_holiday, SEMANTIC["danger"]),
        ])

        note = ctk.CTkLabel(
            self.tabs.tab("Holidays"),
            text=("Attendance is automatically disabled on these dates and on every "
                  "Sunday. A faculty member can still override this for a genuine "
                  "extra class, and the override is recorded in the audit trail."),
            font=FONTS["small"], text_color=color("text_muted"), anchor="w",
            justify="left", wraplength=900)
        note.pack(fill="x", pady=(0, 10))

        table = DataTable(
            self.tabs.tab("Holidays"),
            columns=[
                column("holiday_date", "Date", 130, format=date_format),
                column("holiday_name", "Holiday", 260, stretch=True),
                column("holiday_type", "Type", 140, "center"),
                column("session_name", "Academic Session", 160, format=dash_format),
                column("description", "Notes", 260, format=dash_format),
            ],
            on_select=lambda row: self._selected.__setitem__("holiday", row), height=12)
        table.pack(fill="both", expand=True)
        self._tables["holidays"] = table

    def add_holiday(self) -> None:
        sessions = academic.get_sessions()
        values = FormDialog(
            self, "Add Holiday",
            [
                FormField("holiday_date", "Date", "date", required=True,
                          default=date.today().isoformat()),
                FormField("holiday_type", "Type", "select",
                          options={t: t for t in HOLIDAY_TYPES}, default="Public"),
                FormField("holiday_name", "Holiday Name", required=True, span=2,
                          placeholder="Independence Day"),
                FormField("session_id", "Academic Session", "select",
                          options={"All sessions": None,
                                   **{s["session_name"]: s["session_id"] for s in sessions}}),
                FormField("description", "Notes", "textarea", span=2),
            ],
            submit_text="Add Holiday", width=680, height=430).show()

        if not values:
            return

        ok, message, _ = academic.add_holiday(
            values["holiday_date"], values["holiday_name"],
            values.get("holiday_type", "Public"), values.get("session_id"),
            values.get("description", ""), session.user)

        if ok:
            show_success(self, "Holiday Added", message)
            self.refresh()
        else:
            show_error(self, "Could Not Add Holiday", message)

    def delete_holiday(self) -> None:
        selected = self._selected.get("holiday")
        if not selected:
            show_info(self, "No Selection", "Select a holiday from the list first.")
            return

        if not ask_confirm(self, "Delete Holiday",
                           f"Remove '{selected['holiday_name']}' on "
                           f"{selected['holiday_date']}?\n\n"
                           "Attendance will become possible on that date again.",
                           confirm_text="Delete", danger=True):
            return

        ok, message = academic.delete_holiday(selected["holiday_id"], session.user)
        if ok:
            show_success(self, "Holiday Removed", message)
            self.refresh()
        else:
            show_error(self, "Could Not Remove", message)

    # ==================================================================
    def refresh(self) -> None:
        from core.database import get_db
        db = get_db()

        branches = academic.get_branches(active_only=False)
        courses = academic.get_courses(active_only=False)
        sections = academic.get_sections(active_only=False)
        sessions = academic.get_sessions(active_only=False)
        batches = academic.get_batches(active_only=False)
        holidays = academic.get_holidays()

        # Semesters need per-row counts the model does not provide.
        semesters = []
        for row in academic.get_semesters(active_only=False):
            entry = dict(row)
            course = academic.get_course(row["course_id"])
            entry["course_name"] = course["course_name"] if course else "-"
            entry["student_count"] = db.count("students", "semester_id = ?",
                                              (row["semester_id"],))
            entry["subject_count"] = db.count("subjects", "semester_id = ?",
                                              (row["semester_id"],))
            semesters.append(entry)

        self._tables["class_teachers"].set_data(academic.get_class_teachers())
        self._tables["branches"].set_data(branches)
        self._tables["courses"].set_data(courses)
        self._tables["semesters"].set_data(semesters)
        self._tables["sections"].set_data(sections)
        self._tables["sessions"].set_data(sessions)
        self._tables["batches"].set_data(batches)
        self._tables["holidays"].set_data(holidays)

        for key, rows in (("courses", courses), ("branches", branches),
                          ("semesters", semesters), ("sections", sections),
                          ("sessions", sessions), ("holidays", holidays)):
            self.tiles[key].update_value(str(len(rows)))

        current = academic.get_current_session()
        self.tiles["sessions"].update_value(
            str(len(sessions)),
            f"current: {current['session_name']}" if current else "none set")
