"""
Subject management.

Enforces the specification's hierarchy at the point of entry:

    Course -> Branch -> Semester -> Subject -> Faculty

The branch and semester dropdowns re-filter themselves when the course changes,
so an invalid combination cannot be selected in the first place; the model then
rejects any that slip through.
"""

from __future__ import annotations

import customtkinter as ctk

from config.theme import FONTS, SEMANTIC, color, percentage_color
from core.auth import session
from core.logger import get_logger
from core.validators import validate_code
from models import academic, faculty as faculty_model, subject as subject_model
from ui.widgets.components import FilterBar, PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import (FormDialog, FormField, ask_confirm, show_error,
                                show_info, show_success)
from ui.widgets.table import DataTable, column, dash_format

logger = get_logger("ui.subjects")

SUBJECT_TYPES = ["Theory", "Practical", "Project", "Tutorial"]


class SubjectsView(ctk.CTkFrame):
    """Subject catalogue screen."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected: dict | None = None

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Subject Management",
            subtitle="Course -> Branch -> Semester -> Subject -> Faculty",
            icon="▣")
        header.pack(fill="x", padx=18, pady=(14, 10))
        header.add_button("+  Add Subject", self.add_subject, width=150)

        # ---- tiles ---------------------------------------------------------
        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(4):
            tiles.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tile_total = StatCard(tiles, "Total Subjects", "0", "▣", SEMANTIC["info"])
        self.tile_total.grid(row=0, column=0, sticky="ew", padx=4)
        self.tile_theory = StatCard(tiles, "Theory", "0", "≡", SEMANTIC["purple"])
        self.tile_theory.grid(row=0, column=1, sticky="ew", padx=4)
        self.tile_practical = StatCard(tiles, "Practical", "0", "⚙", SEMANTIC["teal"])
        self.tile_practical.grid(row=0, column=2, sticky="ew", padx=4)
        self.tile_unassigned = StatCard(tiles, "Unassigned", "0", "!", SEMANTIC["warning"])
        self.tile_unassigned.grid(row=0, column=3, sticky="ew", padx=4)

        # ---- filters -------------------------------------------------------
        self.filters = FilterBar(self, on_change=self.refresh,
                                 search_placeholder="Subject code, name or faculty...")
        self.filters.pack(fill="x", padx=18, pady=(0, 9))
        self.filters.add_filter(
            "branch_id", "Branch",
            {b["branch_name"]: b["branch_id"] for b in academic.get_branches()}, width=190)
        self.filters.add_filter(
            "semester_id", "Semester",
            {s["semester_name"]: s["semester_id"] for s in academic.get_semesters()},
            width=130)
        self.filters.add_filter(
            "faculty_id", "Faculty",
            {f["full_name"]: f["faculty_id"] for f in faculty_model.get_all_faculty()},
            width=190)
        self.filters.add_search(230)
        self.filters.add_button("Reset", self.filters.reset, width=80,
                                fg_color="transparent", border_width=1,
                                border_color=color("border"), text_color=color("text"),
                                hover_color=color("surface_alt"))

        # ---- table + detail --------------------------------------------------
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=7, uniform="body")
        body.grid_columnconfigure(1, weight=3, uniform="body")
        body.grid_rowconfigure(0, weight=1)

        self.table = DataTable(
            body,
            columns=[
                column("subject_code", "Code", 85),
                column("subject_name", "Subject Name", 220, stretch=True),
                column("branch_code", "Branch", 75, "center"),
                column("semester_name", "Semester", 105),
                column("subject_type", "Type", 90, "center"),
                column("credits", "Credits", 70, "center"),
                column("faculty_name", "Faculty", 175,
                       format=lambda v, _r: v or "-- Unassigned --"),
                column("sessions_conducted", "Classes", 80, "center"),
            ],
            on_select=self._row_selected,
            on_double_click=lambda _row: self.edit_subject(), height=17)
        self.table.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self._build_detail_panel(body)

    def _build_detail_panel(self, parent) -> None:
        panel = ctk.CTkFrame(parent, fg_color="transparent")
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        self.detail_card = SectionCard(panel, "Subject Details", "Select a subject")
        self.detail_card.grid(row=0, column=0, sticky="nsew")

        self.detail_body = ctk.CTkScrollableFrame(self.detail_card.body,
                                                  fg_color="transparent")
        self.detail_body.pack(fill="both", expand=True)

        ctk.CTkLabel(self.detail_body,
                     text="No subject selected.\n\nClick a row to see its\n"
                          "details and attendance statistics.",
                     font=FONTS["body"], text_color=color("text_muted"),
                     justify="center").pack(pady=40)

        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for index in range(2):
            actions.grid_columnconfigure(index, weight=1, uniform="actions")

        self._action_buttons = {}
        specs = [
            ("edit", "Edit Subject", self.edit_subject, None),
            ("assign", "Change Faculty", self.change_faculty, SEMANTIC["purple"]),
            ("toggle", "Deactivate", self.toggle_active, None),
            ("delete", "Delete Subject", self.delete_subject, SEMANTIC["danger"]),
        ]
        for index, (key, label, command, accent) in enumerate(specs):
            button = ctk.CTkButton(
                actions, text=label, command=command, height=34, corner_radius=7,
                font=FONTS["small_bold"], state="disabled",
                fg_color=accent or "transparent",
                border_width=0 if accent else 1, border_color=color("border"),
                text_color="#FFFFFF" if accent else color("text"),
                hover_color=accent or color("surface_alt"))
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=3, pady=3)
            self._action_buttons[key] = button

    # ==================================================================
    def refresh(self) -> None:
        values = self.filters.values()
        rows = subject_model.search_subjects(
            branch_id=values.get("branch_id"),
            semester_id=values.get("semester_id"),
            faculty_id=values.get("faculty_id"),
            search=values.get("search", ""),
            active_only=False)

        self.table.set_data(rows)

        from core.database import get_db
        db = get_db()
        self.tile_total.update_value(str(db.count("subjects", "is_active = 1")))
        self.tile_theory.update_value(
            str(db.count("subjects", "subject_type = 'Theory' AND is_active = 1")))
        self.tile_practical.update_value(
            str(db.count("subjects", "subject_type = 'Practical' AND is_active = 1")))
        self.tile_unassigned.update_value(
            str(db.count("subjects", "faculty_id IS NULL AND is_active = 1")))

    def _row_selected(self, row: dict) -> None:
        self._selected = row
        for button in self._action_buttons.values():
            button.configure(state="normal")

        self._action_buttons["toggle"].configure(
            text="Deactivate" if row.get("is_active") else "Activate")
        self._render_detail(row)

    def _render_detail(self, row: dict) -> None:
        for widget in self.detail_body.winfo_children():
            widget.destroy()

        ctk.CTkLabel(self.detail_body, text=row["subject_name"], font=FONTS["subhead"],
                     text_color=color("text"), wraplength=220).pack()
        ctk.CTkLabel(self.detail_body, text=row["subject_code"], font=FONTS["small"],
                     text_color=color("primary")).pack(pady=(0, 12))

        for label, value in (
                ("Course", row.get("course_name")),
                ("Branch", row.get("branch_name")),
                ("Semester", row.get("semester_name")),
                ("Type", row.get("subject_type")),
                ("Credits", row.get("credits")),
                ("Faculty", row.get("faculty_name") or "Unassigned"),
                ("Status", "Active" if row.get("is_active") else "Inactive")):
            entry = ctk.CTkFrame(self.detail_body, fg_color="transparent")
            entry.pack(fill="x", pady=1)
            ctk.CTkLabel(entry, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), width=85,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=str(value or "-"), font=FONTS["small"],
                         text_color=color("text"), anchor="w", wraplength=175,
                         justify="left").pack(side="left", fill="x", expand=True)

        # ---- attendance statistics -------------------------------------------
        stats = subject_model.get_subject_stats(row["subject_id"])
        card = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                            fg_color=color("surface_alt"))
        card.pack(fill="x", pady=(14, 0))

        ctk.CTkLabel(card, text="ATTENDANCE", font=(FONTS["small"][0], 9, "bold"),
                     text_color=color("text_muted")).pack(pady=(9, 3))
        ctk.CTkLabel(card, text=f"{stats['percentage']:.1f}%",
                     font=(FONTS["title"][0], 24, "bold"),
                     text_color=percentage_color(stats["percentage"])).pack()

        for label, value in (("Classes held", stats["sessions"]),
                             ("Records", stats["records"]),
                             ("Attended", stats["attended"]),
                             ("Absent", stats["absent"])):
            entry = ctk.CTkFrame(card, fg_color="transparent")
            entry.pack(fill="x", padx=14, pady=1)
            ctk.CTkLabel(entry, text=label, font=FONTS["small"],
                         text_color=color("text_muted"), anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=str(value), font=FONTS["small_bold"],
                         text_color=color("text")).pack(side="right")
        ctk.CTkFrame(card, height=10, fg_color="transparent").pack()

    # ==================================================================
    def _form_fields(self) -> list[FormField]:
        courses = academic.get_courses()
        return [
            FormField("subject_code", "Subject Code", required=True,
                      validator=lambda v: validate_code(v, "Subject code"),
                      placeholder="CS407", hint="Unique across the college"),
            FormField("subject_name", "Subject Name", required=True,
                      placeholder="Python Programming"),
            FormField("course_id", "Course", "select", required=True,
                      options={c["course_name"]: c["course_id"] for c in courses}),
            FormField("branch_id", "Branch", "select", required=True,
                      options={b["branch_name"]: b["branch_id"]
                               for b in academic.get_branches()}),
            FormField("semester_id", "Semester", "select", required=True,
                      options={s["semester_name"]: s["semester_id"]
                               for s in academic.get_semesters()}),
            FormField("faculty_id", "Assigned Faculty", "select",
                      options={"-- Unassigned --": None,
                               **{f["full_name"]: f["faculty_id"]
                                  for f in faculty_model.get_all_faculty()}}),
            FormField("subject_type", "Subject Type", "select",
                      options={t: t for t in SUBJECT_TYPES}, default="Theory"),
            FormField("credits", "Credits", "number", default=4),
            FormField("total_classes", "Planned Classes", "number", default=60,
                      hint="Classes expected in the term"),
        ]

    def add_subject(self) -> None:
        values = FormDialog(
            self, "Add Subject", self._form_fields(),
            submit_text="Add Subject", width=740, height=520,
            description="A subject belongs to one course, branch and semester").show()

        if not values:
            return

        ok, message, _ = subject_model.add_subject(values, session.user)
        if ok:
            show_success(self, "Subject Added", message)
            self.refresh()
            self.app.invalidate("timetable", "attendance_face", "attendance_manual")
        else:
            show_error(self, "Could Not Add Subject", message)

    def edit_subject(self) -> None:
        if not self._selected:
            return

        record = subject_model.get_subject(self._selected["subject_id"])
        if record is None:
            show_error(self, "Not Found", "This subject no longer exists.")
            self.refresh()
            return

        values = FormDialog(
            self, f"Edit Subject - {record['subject_name']}", self._form_fields(),
            values=dict(record), submit_text="Save Changes", width=740, height=520,
            description=f"{record['sessions_conducted']} class(es) already conducted").show()

        if not values:
            return

        ok, message = subject_model.update_subject(
            record["subject_id"], values, session.user)

        if ok:
            show_success(self, "Subject Updated", message)
            self.refresh()
            self.app.invalidate("timetable", "attendance_face", "attendance_manual")
        else:
            show_error(self, "Could Not Update", message)

    def change_faculty(self) -> None:
        if not self._selected:
            return

        record = self._selected
        faculty_options = {"-- Unassigned --": None,
                           **{f["full_name"]: f["faculty_id"]
                              for f in faculty_model.get_all_faculty()}}

        values = FormDialog(
            self, f"Assign Faculty - {record['subject_name']}",
            [FormField("faculty_id", "Faculty", "select", options=faculty_options,
                       default=record.get("faculty_id"), span=2,
                       hint="The assigned faculty may take attendance for this subject")],
            submit_text="Assign", width=600, height=280,
            description=f"Currently: {record.get('faculty_name') or 'Unassigned'}").show()

        if not values:
            return

        ok, message = subject_model.update_subject(
            record["subject_id"], {"faculty_id": values["faculty_id"]}, session.user)

        if ok:
            show_success(self, "Faculty Assigned", message)
            self.refresh()
            self.app.invalidate("faculty", "timetable")
        else:
            show_error(self, "Could Not Assign", message)

    def toggle_active(self) -> None:
        if not self._selected:
            return

        record = self._selected
        activating = not record.get("is_active")

        if not activating and not ask_confirm(
                self, "Deactivate Subject",
                f"Deactivate '{record['subject_name']}'?\n\n"
                "It disappears from attendance and timetable pickers, but all "
                "existing attendance history is preserved.",
                confirm_text="Deactivate"):
            return

        ok, message = subject_model.set_active(
            record["subject_id"], activating, session.user)

        if ok:
            show_success(self, "Subject Updated", message)
            self.refresh()
        else:
            show_error(self, "Could Not Update", message)

    def delete_subject(self) -> None:
        if not self._selected:
            return

        record = self._selected
        if record.get("sessions_conducted"):
            show_info(
                self, "Cannot Delete",
                f"'{record['subject_name']}' has {record['sessions_conducted']} "
                "attendance session(s) recorded against it.\n\n"
                "Deleting it would destroy that history. Use 'Deactivate' instead "
                "to hide it from new attendance while keeping the records.")
            return

        if not ask_confirm(
                self, "Delete Subject",
                f"Permanently delete '{record['subject_name']}' "
                f"({record['subject_code']})?\n\n"
                "Any timetable slots for it are removed as well.",
                confirm_text="Delete", danger=True):
            return

        ok, message = subject_model.delete_subject(record["subject_id"], session.user)
        if ok:
            show_success(self, "Subject Deleted", message)
            self._selected = None
            for button in self._action_buttons.values():
                button.configure(state="disabled")
            self.refresh()
            self.app.invalidate("timetable")
        else:
            show_error(self, "Cannot Delete", message)
