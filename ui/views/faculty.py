"""
Faculty management.

CRUD plus subject assignment, workload summary and Excel import/export.
A faculty member's assigned subjects are shown live from the ``subjects`` table
rather than a duplicated list, so an assignment made here is instantly visible
on the Subjects screen and in the attendance subject picker.
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
from PIL import Image

from config.settings import FACULTY_STATUSES, GENDERS
from config.theme import FONTS, SEMANTIC, color
from core.auth import create_user, session
from core.logger import get_logger
from core.validators import validate_email, validate_mobile, validate_name
from models import academic, faculty as faculty_model, subject as subject_model
from services import import_export
from ui.widgets.components import FilterBar, PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import (DetailDialog, FormDialog, FormField, ask_confirm,
                                ask_reason, pick_file, show_error, show_info,
                                show_success, show_warning)
from ui.widgets.table import DataTable, column, dash_format

logger = get_logger("ui.faculty")


class FacultyView(ctk.CTkFrame):
    """Faculty records screen."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected: dict | None = None
        self._page = 1

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(self, title="Faculty Management",
                            subtitle="Teaching staff records, subject assignments and workload",
                            icon="★")
        header.pack(fill="x", padx=18, pady=(14, 10))

        header.add_button("+  Add Faculty", self.add_faculty, width=145)
        header.add_button("Import", self.import_faculty, width=100,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))
        header.add_button("Export", self.export_faculty, width=100,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))

        # ---- tiles ---------------------------------------------------------
        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(4):
            tiles.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tile_total = StatCard(tiles, "Total Faculty", "0", "★", SEMANTIC["info"])
        self.tile_total.grid(row=0, column=0, sticky="ew", padx=4)
        self.tile_active = StatCard(tiles, "Active", "0", "✓", SEMANTIC["success"])
        self.tile_active.grid(row=0, column=1, sticky="ew", padx=4)
        self.tile_subjects = StatCard(tiles, "Subjects Assigned", "0", "▣",
                                      SEMANTIC["purple"])
        self.tile_subjects.grid(row=0, column=2, sticky="ew", padx=4)
        self.tile_unassigned = StatCard(tiles, "Unassigned Subjects", "0", "!",
                                        SEMANTIC["warning"])
        self.tile_unassigned.grid(row=0, column=3, sticky="ew", padx=4)

        # ---- filters --------------------------------------------------------
        self.filters = FilterBar(self, on_change=self._filters_changed,
                                 search_placeholder="Code, name, qualification, mobile...")
        self.filters.pack(fill="x", padx=18, pady=(0, 9))
        self.filters.add_filter(
            "branch_id", "Department",
            {b["branch_name"]: b["branch_id"] for b in academic.get_branches()}, width=200)
        self.filters.add_filter("status", "Status",
                                {s: s for s in FACULTY_STATUSES}, width=120)
        self.filters.add_search(260)
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
                column("faculty_code", "Code", 80),
                column("full_name", "Faculty Name", 190, stretch=True),
                column("designation", "Designation", 145, format=dash_format),
                column("qualification", "Qualification", 190, format=dash_format),
                column("branch_name", "Department", 175, format=dash_format),
                column("experience_years", "Exp (yrs)", 80, "center"),
                column("mobile", "Mobile", 105, format=dash_format),
                column("subject_count", "Subjects", 75, "center"),
                column("status", "Status", 80, "center"),
            ],
            on_select=self._row_selected,
            on_double_click=lambda _row: self.view_faculty(),
            server_side=True, height=17)
        self.table.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.table.set_page_change_handler(self._page_changed)

        self._build_detail_panel(body)

    def _build_detail_panel(self, parent) -> None:
        panel = ctk.CTkFrame(parent, fg_color="transparent")
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        self.detail_card = SectionCard(panel, "Faculty Details",
                                       "Select a faculty member")
        self.detail_card.grid(row=0, column=0, sticky="nsew")

        self.detail_body = ctk.CTkScrollableFrame(self.detail_card.body,
                                                  fg_color="transparent")
        self.detail_body.pack(fill="both", expand=True)

        ctk.CTkLabel(self.detail_body,
                     text="No faculty selected.\n\nClick a row to see their\n"
                          "profile, subjects and workload.",
                     font=FONTS["body"], text_color=color("text_muted"),
                     justify="center").pack(pady=40)

        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for index in range(2):
            actions.grid_columnconfigure(index, weight=1, uniform="actions")

        self._action_buttons = {}
        specs = [
            ("view", "View Full Record", self.view_faculty, None),
            ("edit", "Edit", self.edit_faculty, None),
            ("subjects", "Assign Subjects", self.assign_subjects, SEMANTIC["purple"]),
            ("login", "Create Login", self.create_login, SEMANTIC["teal"]),
            ("delete", "Delete Faculty", self.delete_faculty, SEMANTIC["danger"]),
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
    def _filters_changed(self) -> None:
        self._page = 1
        self.refresh()

    def _page_changed(self, page: int) -> None:
        self._page = page
        self.refresh(keep_page=True)

    def refresh(self, keep_page: bool = False) -> None:
        if not keep_page:
            self._page = 1

        values = self.filters.values()
        rows, total = faculty_model.search_faculty(
            branch_id=values.get("branch_id"), status=values.get("status"),
            search=values.get("search", ""), page=self._page,
            page_size=self.table.page_size)

        self.table.set_data(rows, total_rows=total, page=self._page)

        from core.database import get_db
        db = get_db()
        self.tile_total.update_value(str(db.count("faculty")))
        self.tile_active.update_value(str(db.count("faculty", "status = 'Active'")))
        self.tile_subjects.update_value(
            str(db.count("subjects", "faculty_id IS NOT NULL AND is_active = 1")))
        self.tile_unassigned.update_value(
            str(db.count("subjects", "faculty_id IS NULL AND is_active = 1")))

    # ==================================================================
    def _row_selected(self, row: dict) -> None:
        self._selected = row
        for button in self._action_buttons.values():
            button.configure(state="normal")
        self._render_detail(row)

    def _render_detail(self, row: dict) -> None:
        for widget in self.detail_body.winfo_children():
            widget.destroy()

        photo_path = row.get("photo_path")
        if photo_path and Path(photo_path).exists():
            try:
                image = ctk.CTkImage(Image.open(photo_path), size=(92, 92))
                ctk.CTkLabel(self.detail_body, image=image, text="").pack(pady=(0, 10))
            except Exception:                   # noqa: BLE001
                self._initials(row)
        else:
            self._initials(row)

        ctk.CTkLabel(self.detail_body, text=row["full_name"], font=FONTS["subhead"],
                     text_color=color("text"), wraplength=220).pack()
        ctk.CTkLabel(self.detail_body,
                     text=f"{row['faculty_code']}  |  {row.get('designation') or 'Faculty'}",
                     font=FONTS["small"], text_color=color("text_muted")).pack(pady=(0, 12))

        for label, value in (
                ("Qualification", row.get("qualification")),
                ("Department", row.get("branch_name")),
                ("Experience", f"{row.get('experience_years') or 0:g} years"),
                ("Mobile", row.get("mobile")),
                ("Email", row.get("email")),
                ("Joined", row.get("joining_date")),
                ("Status", row.get("status"))):
            entry = ctk.CTkFrame(self.detail_body, fg_color="transparent")
            entry.pack(fill="x", pady=1)
            ctk.CTkLabel(entry, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), width=95,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=str(value or "-"), font=FONTS["small"],
                         text_color=color("text"), anchor="w", wraplength=165,
                         justify="left").pack(side="left", fill="x", expand=True)

        # ---- workload -------------------------------------------------------
        workload = faculty_model.get_workload(row["faculty_id"])
        card = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                            fg_color=color("surface_alt"))
        card.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(card, text="WORKLOAD", font=(FONTS["small"][0], 9, "bold"),
                     text_color=color("text_muted")).pack(pady=(9, 4))
        for label, value in (("Subjects", workload["subjects"]),
                             ("Weekly periods", workload["weekly_classes"]),
                             ("Students taught", workload["students"]),
                             ("Classes conducted", workload["sessions_taken"])):
            entry = ctk.CTkFrame(card, fg_color="transparent")
            entry.pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(entry, text=label, font=FONTS["small"],
                         text_color=color("text_muted"), anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=str(value), font=FONTS["small_bold"],
                         text_color=color("text")).pack(side="right")
        ctk.CTkFrame(card, height=8, fg_color="transparent").pack()

        # ---- assigned subjects ------------------------------------------------
        subjects = faculty_model.get_assigned_subjects(row["faculty_id"])
        subject_card = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                                    fg_color=color("surface_alt"))
        subject_card.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(subject_card, text=f"ASSIGNED SUBJECTS ({len(subjects)})",
                     font=(FONTS["small"][0], 9, "bold"),
                     text_color=color("text_muted")).pack(pady=(9, 4))

        if not subjects:
            ctk.CTkLabel(subject_card, text="No subjects assigned", font=FONTS["small"],
                         text_color=color("text_muted")).pack(pady=(0, 10))
        for subject in subjects[:10]:
            entry = ctk.CTkFrame(subject_card, fg_color="transparent")
            entry.pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(entry, text=f"{subject['subject_code']}", font=FONTS["small_bold"],
                         text_color=color("primary"), width=58,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=str(subject["subject_name"])[:26],
                         font=(FONTS["small"][0], 10), text_color=color("text"),
                         anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(entry, text=f"S{subject['semester_number']}",
                         font=(FONTS["small"][0], 10),
                         text_color=color("text_muted")).pack(side="right")
        ctk.CTkFrame(subject_card, height=8, fg_color="transparent").pack()

    def _initials(self, row: dict) -> None:
        initials = "".join(w[0] for w in str(row.get("full_name", "F")).split()
                           if w[0].isalpha())[:2].upper()
        ctk.CTkLabel(self.detail_body, text=initials or "F",
                     font=(FONTS["title"][0], 28, "bold"), text_color="#FFFFFF",
                     fg_color=SEMANTIC["purple"], corner_radius=46,
                     width=92, height=92).pack(pady=(0, 10))

    # ==================================================================
    def _form_fields(self) -> list[FormField]:
        branches = academic.get_branches()
        return [
            FormField("faculty_code", "Faculty Code", required=True,
                      placeholder=faculty_model.next_faculty_code(),
                      hint="Unique identifier, e.g. FAC013"),
            FormField("full_name", "Full Name", required=True,
                      validator=lambda v: validate_name(v, "Full Name"),
                      placeholder="Dr. Anil Mehta"),
            FormField("designation", "Designation",
                      placeholder="Lecturer / Head of Department"),
            FormField("qualification", "Qualification",
                      placeholder="M.Tech (Computer Science)"),
            FormField("branch_id", "Department", "select",
                      options={b["branch_name"]: b["branch_id"] for b in branches}),
            FormField("experience_years", "Experience (years)", "number", default=0),
            FormField("gender", "Gender", "select", options={g: g for g in GENDERS}),
            FormField("dob", "Date of Birth", "date"),
            FormField("mobile", "Mobile Number", validator=validate_mobile,
                      placeholder="9876543210"),
            FormField("email", "Email", validator=validate_email,
                      placeholder="faculty@gpcollege.ac.in"),
            FormField("joining_date", "Joining Date", "date"),
            FormField("status", "Status", "select",
                      options={s: s for s in FACULTY_STATUSES}, default="Active"),
            FormField("address", "Address", "textarea", span=2),
            FormField("photo_source", "Photograph", "image", span=2,
                      hint="Stored as FacultyID_FacultyName.jpg"),
        ]

    def add_faculty(self) -> None:
        values = FormDialog(self, "Add Faculty", self._form_fields(),
                            submit_text="Add Faculty", height=620,
                            description="Fields marked * are required").show()
        if not values:
            return

        photo = values.pop("photo_source", None)
        ok, message, faculty_id = faculty_model.add_faculty(
            values, photo_source=photo or None, user=session.user)

        if not ok:
            show_error(self, "Could Not Add Faculty", message)
            return

        show_success(self, "Faculty Added", message)
        self.refresh()

        if ask_confirm(self, "Create Login Account?",
                       f"Create a login account for {values['full_name']}?\n\n"
                       "You will be able to choose their username and initial "
                       "password on the next screen.",
                       confirm_text="Create Login", cancel_text="Skip"):
            self._create_login_for(faculty_id, values["faculty_code"],
                                   values["full_name"], values.get("email", ""))

    def edit_faculty(self) -> None:
        if not self._selected:
            return

        record = faculty_model.get_faculty(self._selected["faculty_id"])
        if record is None:
            show_error(self, "Not Found", "This faculty record no longer exists.")
            self.refresh()
            return

        values = dict(record)
        values["photo_source"] = ""

        result = FormDialog(self, f"Edit Faculty - {record['full_name']}",
                            self._form_fields(), values=values,
                            submit_text="Save Changes", height=620,
                            description=f"Code {record['faculty_code']}").show()
        if not result:
            return

        photo = result.pop("photo_source", None)
        ok, message = faculty_model.update_faculty(
            record["faculty_id"], result, photo_source=photo or None, user=session.user)

        if ok:
            show_success(self, "Faculty Updated", message)
            self.refresh(keep_page=True)
        else:
            show_error(self, "Could Not Update", message)

    def view_faculty(self) -> None:
        if not self._selected:
            return

        record = dict(faculty_model.get_faculty(self._selected["faculty_id"]))
        workload = faculty_model.get_workload(record["faculty_id"])
        subjects = faculty_model.get_assigned_subjects(record["faculty_id"])

        sections = {
            "Personal Information": [
                ("Full Name", record["full_name"]),
                ("Gender", record["gender"]),
                ("Date of Birth", record["dob"]),
                ("Mobile", record["mobile"]),
                ("Email", record["email"]),
                ("Address", record["address"]),
            ],
            "Professional Information": [
                ("Faculty Code", record["faculty_code"]),
                ("Designation", record["designation"]),
                ("Qualification", record["qualification"]),
                ("Department", record["branch_name"]),
                ("Experience", f"{record['experience_years'] or 0:g} years"),
                ("Joining Date", record["joining_date"]),
                ("Status", record["status"]),
            ],
            "Teaching Workload": [
                ("Subjects Assigned", workload["subjects"]),
                ("Weekly Periods", workload["weekly_classes"]),
                ("Students Taught", workload["students"]),
                ("Classes Conducted", workload["sessions_taken"]),
            ],
            f"Assigned Subjects ({len(subjects)})": [
                (f"{s['subject_code']}",
                 f"{s['subject_name']} - {s['branch_code']} {s['semester_name']}")
                for s in subjects
            ] or [("-", "No subjects assigned")],
        }

        DetailDialog(self, f"Faculty Record - {record['full_name']}", sections,
                     image_path=record["photo_path"]).show()

    def delete_faculty(self) -> None:
        if not self._selected:
            return

        record = self._selected
        subject_count = record.get("subject_count", 0)

        reason = ask_reason(
            self, "Delete Faculty",
            f"Permanently delete {record['full_name']} ({record['faculty_code']})?\n\n"
            f"{subject_count} subject(s) will become unassigned and their login "
            "account will be removed.\n\n"
            "Attendance sessions they conducted are preserved.\n\n"
            "Please state why.",
            min_length=10, confirm_text="Delete Permanently", danger=True)
        if not reason:
            return

        ok, message = faculty_model.delete_faculty(record["faculty_id"], session.user)
        if ok:
            show_success(self, "Faculty Deleted", message)
            self._selected = None
            for button in self._action_buttons.values():
                button.configure(state="disabled")
            self.refresh()
        else:
            show_error(self, "Could Not Delete", message)

    # ==================================================================
    def assign_subjects(self) -> None:
        """Assign one of the unassigned subjects to this faculty member."""
        if not self._selected:
            return

        faculty_id = self._selected["faculty_id"]
        assigned = faculty_model.get_assigned_subjects(faculty_id)
        available = subject_model.search_subjects(unassigned_only=True)

        if not available:
            show_info(self, "No Unassigned Subjects",
                      f"{self._selected['full_name']} currently teaches "
                      f"{len(assigned)} subject(s).\n\n"
                      "Every active subject already has a faculty member. "
                      "Reassign one from the Subjects screen if needed.")
            return

        options = {f"{s['subject_code']} - {s['subject_name']} "
                   f"({s['branch_code']} {s['semester_name']})": s["subject_id"]
                   for s in available}

        values = FormDialog(
            self, f"Assign Subject - {self._selected['full_name']}",
            [FormField("subject_id", "Unassigned Subject", "select", required=True,
                       options=options, span=2,
                       hint=f"{len(available)} subject(s) currently have no faculty")],
            submit_text="Assign", width=620, height=280,
            description=f"Currently teaching {len(assigned)} subject(s)").show()

        if not values:
            return

        ok, message = faculty_model.assign_subject(
            faculty_id, values["subject_id"], session.user)

        if ok:
            show_success(self, "Subject Assigned", message)
            self.refresh(keep_page=True)
            self._render_detail(dict(faculty_model.get_faculty(faculty_id)))
            self.app.invalidate("subjects", "timetable")
        else:
            show_error(self, "Could Not Assign", message)

    def create_login(self) -> None:
        if not self._selected:
            return
        record = self._selected
        self._create_login_for(record["faculty_id"], record["faculty_code"],
                               record["full_name"], record.get("email", ""))

    def _create_login_for(self, faculty_id: int, code: str, name: str,
                          email: str) -> None:
        """Create a login, letting the administrator choose the credentials."""
        from core.database import get_db
        from core.validators import validate_password, validate_username

        existing = get_db().fetch_one(
            "SELECT username FROM users WHERE role = 'Faculty' AND linked_id = ?",
            (faculty_id,))

        if existing:
            show_info(self, "Login Already Exists",
                      f"{name} already has a login account.\n\n"
                      f"Username: {existing['username']}\n\n"
                      "Use Settings -> User Accounts to reset the password.")
            return

        def _check(values: dict) -> tuple[bool, str]:
            ok, message = validate_username(values.get("username", ""))
            if not ok:
                return False, message
            if get_db().exists("users", "username = ?", (values["username"],)):
                return False, f"Username '{values['username']}' is already taken."
            ok, message = validate_password(values.get("password", ""))
            if not ok:
                return False, message
            return True, ""

        values = FormDialog(
            self, f"Create Login - {name}",
            [
                FormField("username", "Username", required=True,
                          default=code.lower(),
                          hint="Suggested from the faculty code. Change it to "
                               "whatever the college prefers - it must be unique."),
                FormField("password", "Initial Password", "password", required=True,
                          default="faculty123",
                          hint="At least 8 characters with a letter and a digit"),
                FormField("must_change", "Force Password Change", "checkbox",
                          default=True, span=2,
                          placeholder="Require a new password at first sign-in"),
            ],
            submit_text="Create Login", width=680, height=430,
            on_validate=_check,
            description=f"Faculty code {code}").show()

        if not values:
            return

        try:
            create_user(username=values["username"], password=values["password"],
                        role="Faculty", full_name=name, email=email,
                        linked_id=faculty_id,
                        must_change=bool(values.get("must_change")))
        except Exception as exc:                # noqa: BLE001
            show_error(self, "Could Not Create Login", str(exc))
            return

        show_success(self, "Login Created",
                     f"A login account has been created for {name}.\n\n"
                     f"Username : {values['username']}\n"
                     f"Password : {values['password']}\n\n"
                     + ("They will be asked to change this password at first "
                        "sign-in.\n\n" if values.get("must_change") else "")
                     + "Share these credentials through a secure channel.")

    # ==================================================================
    def import_faculty(self) -> None:
        choice = ask_confirm(
            self, "Import Faculty",
            "Import faculty records from an Excel or CSV file.\n\n"
            "Choose 'Select File' to import an existing file, or 'Get Template' "
            "to create a blank one with the correct columns.",
            confirm_text="Select File", cancel_text="Get Template")

        if not choice:
            ok, message, path = import_export.create_faculty_template()
            if ok:
                show_success(self, "Template Created",
                             f"{message}\n\nFill it in, then use Import again.")
            else:
                show_error(self, "Could Not Create Template", message)
            return

        path = pick_file(self, "Select the faculty import file",
                         [("Excel or CSV", "*.xlsx *.xls *.csv"), ("All files", "*.*")])
        if not path:
            return

        result = import_export.import_faculty(path, user=session.user)
        self.refresh()

        if result.imported and not result.errors:
            show_success(self, "Import Complete",
                         f"{result.imported} faculty record(s) imported.")
        elif result.imported:
            show_warning(self, "Import Completed With Problems", result.summary())
        else:
            show_error(self, "Nothing Imported", result.summary())

    def export_faculty(self) -> None:
        values = self.filters.values()
        rows, total = faculty_model.search_faculty(
            branch_id=values.get("branch_id"), status=values.get("status"),
            search=values.get("search", ""), page=1, page_size=100000)

        if not rows:
            show_info(self, "Nothing to Export", "No faculty match the current filters.")
            return

        as_excel = ask_confirm(self, "Export Faculty",
                               f"Export {total} faculty record(s).\n\nChoose a format.",
                               confirm_text="Excel (.xlsx)", cancel_text="CSV (.csv)")

        ok, message, path = import_export.export_faculty(
            rows, "Excel" if as_excel else "CSV", session.user)

        if ok:
            show_success(self, "Export Complete", f"{message}\n\nSaved to:\n{path}")
        else:
            show_error(self, "Export Failed", message)
