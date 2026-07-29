"""
Student management.

Add / edit / delete / search, Excel import and export, face-dataset capture,
ID-card generation and bulk semester promotion.

The grid uses **server-side paging** -- only one page is fetched at a time, so
the screen stays responsive with several thousand students.
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from config.settings import (GENDERS, STUDENT_STATUSES, config)
from config.theme import FONTS, SEMANTIC, color, percentage_color
from core.auth import session
from core.logger import get_logger
from core.validators import (validate_email, validate_enrollment, validate_mobile,
                             validate_name)
from models import academic, attendance as attendance_model, student as student_model
from services import face_service, idcard_service, import_export
from ui.widgets.components import FilterBar, PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import (DetailDialog, FormDialog, FormField, ProgressDialog,
                                ask_confirm, ask_reason, pick_file, show_error,
                                show_info, show_success, show_warning)
from ui.widgets.table import DataTable, column, date_format, dash_format, yes_no_format

logger = get_logger("ui.students")


class StudentsView(ctk.CTkFrame):
    """Student records screen."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected: dict | None = None
        self._page = 1

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(self, title="Student Management",
                            subtitle="Add, edit and search student records; capture face datasets",
                            icon="☺")
        header.pack(fill="x", padx=18, pady=(14, 10))

        header.add_button("+  Add Student", self.add_student, width=150)
        header.add_button("Import", self.import_students, width=100,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))
        header.add_button("Export", self.export_students, width=100,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))
        header.add_button("Promote", self.promote_students, width=110,
                          fg_color=SEMANTIC["purple"], hover_color="#6D28D9")

        # ---- summary tiles ------------------------------------------------
        self.tiles_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.tiles_frame.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(4):
            self.tiles_frame.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tile_total = StatCard(self.tiles_frame, "Total Students", "0", "☺",
                                   SEMANTIC["info"])
        self.tile_total.grid(row=0, column=0, sticky="ew", padx=4)
        self.tile_active = StatCard(self.tiles_frame, "Active", "0", "✓",
                                    SEMANTIC["success"])
        self.tile_active.grid(row=0, column=1, sticky="ew", padx=4)
        self.tile_faces = StatCard(self.tiles_frame, "Face Registered", "0", "◉",
                                   SEMANTIC["purple"])
        self.tile_faces.grid(row=0, column=2, sticky="ew", padx=4)
        self.tile_pending = StatCard(self.tiles_frame, "Face Pending", "0", "!",
                                     SEMANTIC["warning"])
        self.tile_pending.grid(row=0, column=3, sticky="ew", padx=4)

        # ---- filters -------------------------------------------------------
        self.filters = FilterBar(self, on_change=self._filters_changed,
                                 search_placeholder="Enrollment, roll no, name, mobile...")
        self.filters.pack(fill="x", padx=18, pady=(0, 9))

        self.filters.add_filter(
            "branch_id", "Branch",
            {b["branch_name"]: b["branch_id"] for b in academic.get_branches()}, width=180)
        self.filters.add_filter(
            "semester_id", "Semester",
            {s["semester_name"]: s["semester_id"] for s in academic.get_semesters()}, width=130)
        self.filters.add_filter(
            "section_id", "Section",
            {s["section_name"]: s["section_id"] for s in academic.get_sections()}, width=95)
        self.filters.add_filter(
            "batch_id", "Batch",
            {b["batch_name"]: b["batch_id"] for b in academic.get_batches()}, width=125)
        self.filters.add_filter("status", "Status",
                                {s: s for s in STUDENT_STATUSES}, width=110)
        self.filters.add_filter("face", "Face Data",
                                {"Registered": 1, "Not Registered": 0}, width=135)
        self.filters.add_search(230)
        self.filters.add_button("Reset", self.filters.reset, width=80,
                                fg_color="transparent", border_width=1,
                                border_color=color("border"), text_color=color("text"),
                                hover_color=color("surface_alt"))

        # ---- table + detail panel ------------------------------------------
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=7, uniform="body")
        body.grid_columnconfigure(1, weight=3, uniform="body")
        body.grid_rowconfigure(0, weight=1)

        self.table = DataTable(
            body,
            columns=[
                column("roll_no", "Roll", 55, "center"),
                column("enrollment_no", "Enrollment No", 115),
                column("full_name", "Student Name", 175, stretch=True),
                column("branch_code", "Branch", 70, "center"),
                column("semester_name", "Semester", 95),
                column("section_name", "Sec", 45, "center", format=dash_format),
                column("gender", "Gender", 70, format=dash_format),
                column("mobile", "Mobile", 100, format=dash_format),
                column("batch_name", "Batch", 95, format=dash_format),
                column("face_registered", "Face", 55, "center", format=yes_no_format),
                column("status", "Status", 75, "center"),
            ],
            on_select=self._row_selected,
            on_double_click=lambda row: self.view_student(),
            server_side=True, height=17)
        self.table.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.table.set_page_change_handler(self._page_changed)

        self._build_detail_panel(body)

    def _build_detail_panel(self, parent) -> None:
        panel = ctk.CTkFrame(parent, fg_color="transparent")
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        self.detail_card = SectionCard(panel, "Student Details",
                                       "Select a student from the list")
        self.detail_card.grid(row=0, column=0, sticky="nsew")

        self.detail_body = ctk.CTkScrollableFrame(self.detail_card.body,
                                                  fg_color="transparent")
        self.detail_body.pack(fill="both", expand=True)

        self.detail_placeholder = ctk.CTkLabel(
            self.detail_body, text="No student selected.\n\nClick a row to see details,\n"
                                   "capture a face dataset or generate an ID card.",
            font=FONTS["body"], text_color=color("text_muted"), justify="center")
        self.detail_placeholder.pack(pady=40)

        # ---- action buttons -------------------------------------------------
        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for index in range(2):
            actions.grid_columnconfigure(index, weight=1, uniform="actions")

        self._action_buttons: dict[str, ctk.CTkButton] = {}
        specs = [
            ("view", "View Full Record", self.view_student, None),
            ("edit", "Edit", self.edit_student, None),
            ("face", "Capture Face Dataset", self.capture_face, SEMANTIC["purple"]),
            ("clear_face", "Clear Face Data", self.clear_face, None),
            ("idcard", "Generate ID Card", self.generate_id_card, SEMANTIC["teal"]),
            ("delete", "Delete Student", self.delete_student, SEMANTIC["danger"]),
        ]
        for index, (key, label, command, accent) in enumerate(specs):
            button = ctk.CTkButton(
                actions, text=label, command=command, height=34, corner_radius=7,
                font=FONTS["small_bold"], state="disabled",
                fg_color=accent or "transparent",
                border_width=0 if accent else 1,
                border_color=color("border"),
                text_color="#FFFFFF" if accent else color("text"),
                hover_color=accent or color("surface_alt"))
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=3, pady=3)
            self._action_buttons[key] = button

    # ==================================================================
    # Data loading
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
        face = values.get("face")

        try:
            rows, total = student_model.search_students(
                branch_id=values.get("branch_id"),
                semester_id=values.get("semester_id"),
                section_id=values.get("section_id"),
                batch_id=values.get("batch_id"),
                status=values.get("status"),
                search=values.get("search", ""),
                face_registered=None if face is None else bool(face),
                page=self._page, page_size=self.table.page_size)
        except Exception as exc:                # noqa: BLE001
            logger.error("Student query failed: %s", exc, exc_info=True)
            show_error(self, "Query Failed", str(exc))
            return

        self.table.set_data(rows, total_rows=total, page=self._page)
        self._update_tiles()

    def _update_tiles(self) -> None:
        from core.database import get_db
        db = get_db()

        total = db.count("students")
        active = db.count("students", "status = 'Active'")
        registered = db.count("students", "face_registered = 1 AND status = 'Active'")

        self.tile_total.update_value(f"{total:,}")
        self.tile_active.update_value(f"{active:,}")
        self.tile_faces.update_value(f"{registered:,}",
                                     f"{(100*registered/active) if active else 0:.0f}% of active")
        self.tile_pending.update_value(f"{max(0, active - registered):,}")

    # ==================================================================
    # Selection & detail
    # ==================================================================
    def _row_selected(self, row: dict) -> None:
        self._selected = row
        for button in self._action_buttons.values():
            button.configure(state="normal")

        self._action_buttons["clear_face"].configure(
            state="normal" if row.get("face_registered") else "disabled")
        self._action_buttons["face"].configure(
            text="Update Face Dataset" if row.get("face_registered")
            else "Capture Face Dataset")

        self._render_detail(row)

    def _render_detail(self, row: dict) -> None:
        for widget in self.detail_body.winfo_children():
            widget.destroy()

        # ---- photograph ------------------------------------------------
        photo_path = row.get("photo_path")
        if photo_path and Path(photo_path).exists():
            try:
                image = ctk.CTkImage(Image.open(photo_path), size=(96, 96))
                ctk.CTkLabel(self.detail_body, image=image, text="").pack(pady=(0, 10))
            except Exception:                   # noqa: BLE001
                self._initials_avatar(row)
        else:
            self._initials_avatar(row)

        ctk.CTkLabel(self.detail_body, text=row["full_name"], font=FONTS["subhead"],
                     text_color=color("text")).pack()
        ctk.CTkLabel(self.detail_body, text=row["enrollment_no"], font=FONTS["small"],
                     text_color=color("text_muted")).pack(pady=(0, 12))

        # ---- key facts ---------------------------------------------------
        for label, value in (
                ("Roll No", row.get("roll_no")),
                ("Branch", row.get("branch_name")),
                ("Semester", row.get("semester_name")),
                ("Section", row.get("section_name") or "-"),
                ("Batch", row.get("batch_name") or "-"),
                ("Session", row.get("session_name") or "-"),
                ("Mobile", row.get("mobile") or "-"),
                ("Status", row.get("status"))):
            entry = ctk.CTkFrame(self.detail_body, fg_color="transparent")
            entry.pack(fill="x", pady=1)
            ctk.CTkLabel(entry, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), width=95,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=str(value or "-"), font=FONTS["small"],
                         text_color=color("text"), anchor="w",
                         wraplength=170, justify="left").pack(side="left", fill="x",
                                                              expand=True)

        # ---- face status ---------------------------------------------------
        registered = bool(row.get("face_registered"))
        badge = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                             fg_color=SEMANTIC["success"] if registered else SEMANTIC["warning"])
        badge.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(badge,
                     text=("Face dataset registered" if registered
                           else "Face dataset not captured"),
                     font=FONTS["small_bold"], text_color="#FFFFFF").pack(pady=(8, 1))
        ctk.CTkLabel(badge,
                     text=(f"{row.get('face_sample_count', 0)} samples stored"
                           if registered else "Recognition will not identify this student"),
                     font=(FONTS["small"][0], 10), text_color="#FFFFFF").pack(pady=(0, 8))

        # ---- attendance snapshot -------------------------------------------
        try:
            summary = attendance_model.get_student_summary(row["student_id"])
            if summary["total"]:
                from config.theme import percentage_color
                threshold = float(config.get("attendance_threshold", 75))
                stat = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                                    fg_color=color("surface_alt"))
                stat.pack(fill="x", pady=(10, 0))
                ctk.CTkLabel(stat, text="ATTENDANCE", font=(FONTS["small"][0], 9, "bold"),
                             text_color=color("text_muted")).pack(pady=(8, 2))
                ctk.CTkLabel(stat, text=f"{summary['percentage']:.1f}%",
                             font=(FONTS["title"][0], 22, "bold"),
                             text_color=percentage_color(summary["percentage"], threshold)
                             ).pack()
                ctk.CTkLabel(stat,
                             text=f"{summary['attended']} of {summary['total']} classes",
                             font=(FONTS["small"][0], 10),
                             text_color=color("text_muted")).pack(pady=(0, 8))
        except Exception:                       # noqa: BLE001
            pass

    def _initials_avatar(self, row: dict) -> None:
        initials = "".join(w[0] for w in str(row.get("full_name", "S")).split()[:2]).upper()
        ctk.CTkLabel(self.detail_body, text=initials or "S",
                     font=(FONTS["title"][0], 30, "bold"), text_color="#FFFFFF",
                     fg_color=color("primary"), corner_radius=48,
                     width=96, height=96).pack(pady=(0, 10))

    # ==================================================================
    # Form
    # ==================================================================
    def _form_fields(self, for_edit: bool = False) -> list[FormField]:
        lookups = academic.get_academic_lookups()

        return [
            FormField("enrollment_no", "Enrollment Number", required=True,
                      validator=validate_enrollment, placeholder="DCS26001",
                      hint="Unique across the college"),
            FormField("roll_no", "Roll Number", required=True,
                      placeholder="1", hint="Unique within the class"),
            FormField("full_name", "Full Name", required=True,
                      validator=lambda v: validate_name(v, "Full Name"),
                      placeholder="Rahul Verma", span=2),
            FormField("father_name", "Father's Name", placeholder="Ramesh Verma"),
            FormField("mother_name", "Mother's Name", placeholder="Sunita Verma"),
            FormField("gender", "Gender", "select", options={g: g for g in GENDERS}),
            FormField("dob", "Date of Birth", "date"),
            FormField("mobile", "Mobile Number", validator=validate_mobile,
                      placeholder="9876543210"),
            FormField("email", "Email", validator=validate_email,
                      placeholder="student@gpcollege.ac.in"),
            FormField("address", "Address", "textarea", span=2),
            FormField("admission_date", "Admission Date", "date"),
            FormField("status", "Status", "select",
                      options={s: s for s in STUDENT_STATUSES}, default="Active"),
            FormField("course_id", "Course", "select", required=True,
                      options=lookups["courses"]),
            FormField("branch_id", "Branch", "select", required=True,
                      options=lookups["branches"]),
            FormField("semester_id", "Semester", "select", required=True,
                      options=lookups["semesters"]),
            FormField("section_id", "Section", "select", options=lookups["sections"]),
            FormField("batch_id", "Batch", "select", options=lookups["batches"]),
            FormField("session_id", "Academic Session", "select",
                      options=lookups["sessions"]),
            FormField("photo_source", "Photograph", "photo", span=2,
                      on_capture=lambda: SinglePhotoDialog(
                          self, "Capture Student Photo").show(),
                      hint="Stored as EnrollmentNo_StudentName.jpg. Capture with "
                           "the camera or upload an existing file."),
        ]

    def add_student(self) -> None:
        dialog = FormDialog(
            self, "Add Student", self._form_fields(),
            description="Fields marked * are required",
            submit_text="Add Student", height=680)
        values = dialog.show()
        if not values:
            return

        photo = values.pop("photo_source", None)
        ok, message, student_id = student_model.add_student(
            values, photo_source=photo or None, user=session.user)

        if ok:
            show_success(self, "Student Added", message)
            self.refresh()
            if ask_confirm(self, "Capture Face Dataset?",
                           f"{values['full_name']} has been added.\n\n"
                           "Capture their face dataset now so they can be "
                           "recognised in attendance?",
                           confirm_text="Capture Now", cancel_text="Later"):
                self._selected = dict(student_model.get_student(student_id))
                self.capture_face()
        else:
            show_error(self, "Could Not Add Student", message)

    def edit_student(self) -> None:
        if not self._selected:
            return

        record = student_model.get_student(self._selected["student_id"])
        if record is None:
            show_error(self, "Not Found", "This student no longer exists.")
            self.refresh()
            return

        values = dict(record)
        values["photo_source"] = ""

        dialog = FormDialog(
            self, f"Edit Student - {record['full_name']}", self._form_fields(True),
            values=values, submit_text="Save Changes", height=680,
            description=f"Enrollment {record['enrollment_no']}")
        result = dialog.show()
        if not result:
            return

        photo = result.pop("photo_source", None)
        ok, message = student_model.update_student(
            record["student_id"], result, photo_source=photo or None, user=session.user)

        if ok:
            show_success(self, "Student Updated", message)
            self.refresh(keep_page=True)
        else:
            show_error(self, "Could Not Update", message)

    def view_student(self) -> None:
        if not self._selected:
            return

        record = student_model.get_student(self._selected["student_id"])
        if record is None:
            return

        record = dict(record)
        summary = attendance_model.get_student_summary(record["student_id"])
        eligibility = attendance_model.check_eligibility(record["student_id"])

        sections = {
            "Personal Information": [
                ("Full Name", record["full_name"]),
                ("Father's Name", record["father_name"]),
                ("Mother's Name", record["mother_name"]),
                ("Gender", record["gender"]),
                ("Date of Birth", record["dob"]),
                ("Mobile", record["mobile"]),
                ("Email", record["email"]),
                ("Address", record["address"]),
            ],
            "Academic Information": [
                ("Enrollment Number", record["enrollment_no"]),
                ("Roll Number", record["roll_no"]),
                ("Course", record["course_name"]),
                ("Branch", record["branch_name"]),
                ("Semester", record["semester_name"]),
                ("Section", record["section_name"] or "-"),
                ("Batch", record["batch_name"] or "-"),
                ("Academic Session", record["session_name"] or "-"),
                ("Admission Date", record["admission_date"]),
                ("Status", record["status"]),
            ],
            "Attendance": [
                ("Total Classes", summary["total"]),
                ("Attended", summary["attended"]),
                ("Absent", summary["absent"]),
                ("Late", summary["late"]),
                ("Leave", summary["leave"] + summary["medical"]),
                ("Percentage", f"{summary['percentage']:.2f}%"),
                ("Exam Eligibility", eligibility["verdict"]),
            ],
            "Face Recognition": [
                ("Registered", "Yes" if record["face_registered"] else "No"),
                ("Samples Stored", record["face_sample_count"]),
                ("Dataset Folder", record["dataset_path"] or "-"),
                ("Photograph", record["photo_path"] or "-"),
            ],
        }

        DetailDialog(self, f"Student Record - {record['full_name']}", sections,
                     image_path=record["photo_path"],
                     actions=[("Generate ID Card", self.generate_id_card)]).show()

    def delete_student(self) -> None:
        if not self._selected:
            return

        record = self._selected
        from core.database import get_db
        attendance_count = get_db().count("attendance", "student_id = ?",
                                          (record["student_id"],))

        reason = ask_reason(
            self, "Delete Student",
            f"Permanently delete {record['full_name']} ({record['enrollment_no']})?\n\n"
            f"This also removes {attendance_count} attendance record(s), their login "
            "account, photograph and face dataset.\n\n"
            "This cannot be undone. Please state why.",
            min_length=10, confirm_text="Delete Permanently", danger=True)
        if not reason:
            return

        ok, message = student_model.delete_student(
            record["student_id"], remove_files=True, user=session.user)

        if ok:
            show_success(self, "Student Deleted", message)
            self._selected = None
            for button in self._action_buttons.values():
                button.configure(state="disabled")
            self.refresh()
        else:
            show_error(self, "Could Not Delete", message)

    # ==================================================================
    # Face dataset
    # ==================================================================
    def capture_face(self) -> None:
        if not self._selected:
            return

        if face_service.ACTIVE_BACKEND is None:
            show_error(self, "Face Recognition Unavailable",
                       "No face recognition backend is installed.\n\n"
                       "Install 'opencv-contrib-python' (recommended) or "
                       "'face_recognition', then restart the application.")
            return

        record = self._selected
        target = int(config.get("face_dataset_size", 60))

        choice = ask_confirm(
            self, "Capture Face Dataset",
            f"Capture a face dataset for {record['full_name']}?\n\n"
            f"The camera will open and take about {target} images.\n\n"
            "Ask the student to:\n"
            "  - sit facing the camera in good light\n"
            "  - slowly turn their head left and right\n"
            "  - keep a neutral expression, then smile\n\n"
            "Choose 'From Photos' to build the dataset from existing image files "
            "instead of using the camera.",
            confirm_text="Open Camera", cancel_text="From Photos")

        if choice:
            self._capture_from_camera(record, target)
        else:
            self._capture_from_files(record)

    def _capture_from_camera(self, record: dict, target: int) -> None:
        dialog = CaptureDialog(self, record, target)
        result = dialog.show()

        if not result or not result.get("success"):
            if result and result.get("message"):
                show_warning(self, "Capture Incomplete", result["message"])
            return

        self._train_and_save(record, result["dataset_dir"], result["captured"])

    def _capture_from_files(self, record: dict) -> None:
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            parent=self, title=f"Select photos of {record['full_name']}",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp")])
        if not paths:
            return

        ok, message, count = face_service.capture_from_images(
            record["student_id"], record["enrollment_no"], record["full_name"], list(paths))

        if not ok:
            show_error(self, "Could Not Build Dataset", message)
            return

        dataset_dir = student_model.face_dataset_path(
            record["enrollment_no"], record["full_name"])
        self._train_and_save(record, dataset_dir, count)

    def _train_and_save(self, record: dict, dataset_dir, captured: int) -> None:
        """Check for a duplicate face, then encode the dataset and retrain."""
        check = ProgressDialog(self, "Checking for Duplicates",
                               "Comparing against other registered students...")
        check.update_progress(1, 2, "Checking")

        def check_worker() -> None:
            try:
                matches = face_service.find_duplicate_matches(
                    record["student_id"], dataset_dir)
            except Exception as exc:            # noqa: BLE001
                logger.error("Duplicate check failed: %s", exc, exc_info=True)
                matches = []
            self.after(0, lambda: self._after_duplicate_check(
                check, matches, record, dataset_dir, captured))

        threading.Thread(target=check_worker, daemon=True,
                         name="duplicate-check").start()

    def _after_duplicate_check(self, check: ProgressDialog, matches: list[dict],
                               record: dict, dataset_dir, captured: int) -> None:
        check.update_progress(2, 2, "Done")
        check.finish()

        if matches:
            best = matches[0]
            names = "\n".join(f"  - {m['full_name']} ({m['enrollment_no']}) "
                              f"- {m['confidence']:.0f}% match" for m in matches[:5])
            proceed = ask_confirm(
                self, "Possible Duplicate Face",
                f"The captured face closely resembles {len(matches)} already-"
                f"registered student(s):\n\n{names}\n\n"
                "This can mean the same person was registered twice, or two "
                "students' photos got mixed up. Check the photos before "
                "continuing.\n\n"
                f"Register this dataset for {record['full_name']} anyway?",
                confirm_text="Register Anyway", cancel_text="Cancel", danger=True)
            if not proceed:
                show_info(self, "Registration Cancelled",
                          "The captured dataset was discarded. Nothing was saved.")
                return

        self._run_training(record, dataset_dir, captured)

    def _run_training(self, record: dict, dataset_dir, captured: int) -> None:
        """Encode the dataset and retrain the shared model."""
        progress = ProgressDialog(self, "Training Recognition Model",
                                  f"Encoding {captured} image(s) for {record['full_name']}...")
        progress.update_progress(1, 3, "Encoding face samples")

        def worker() -> None:
            try:
                ok, message, samples = face_service.train_student(
                    record["student_id"], dataset_dir)

                if ok:
                    student_model.mark_face_registered(
                        record["student_id"], dataset_dir, samples, session.user)
                    self.after(0, lambda: progress.update_progress(
                        2, 3, "Rebuilding the recognition model"))
                    train_ok, train_message, count = face_service.train_all()
                    self.after(0, lambda: self._training_done(
                        progress, True,
                        f"{message}\n\n{train_message}", record))
                else:
                    self.after(0, lambda: self._training_done(
                        progress, False, message, record))
            except Exception as exc:            # noqa: BLE001
                logger.error("Training failed: %s", exc, exc_info=True)
                self.after(0, lambda: self._training_done(
                    progress, False, f"Training failed: {exc}", record))

        threading.Thread(target=worker, daemon=True, name="face-training").start()

    def _training_done(self, progress: ProgressDialog, ok: bool,
                       message: str, record: dict) -> None:
        progress.update_progress(3, 3, "Done")
        progress.finish()

        if ok:
            show_success(self, "Face Dataset Registered",
                         f"{record['full_name']} can now be recognised in "
                         f"attendance sessions.\n\n{message}")
            self.refresh(keep_page=True)
        else:
            show_error(self, "Registration Failed", message)

    def clear_face(self) -> None:
        if not self._selected:
            return

        record = self._selected
        if not ask_confirm(
                self, "Clear Face Data",
                f"Remove the face dataset and encodings for {record['full_name']}?\n\n"
                "Their attendance history is not affected, but they will no longer "
                "be recognised until a new dataset is captured.",
                confirm_text="Clear Data", danger=True):
            return

        ok, message = student_model.clear_face_data(record["student_id"], session.user)
        if ok:
            face_service.train_all()          # rebuild without this identity
            show_success(self, "Face Data Cleared", message)
            self.refresh(keep_page=True)
        else:
            show_error(self, "Could Not Clear", message)

    # ==================================================================
    # ID cards
    # ==================================================================
    def generate_id_card(self) -> None:
        if not self._selected:
            return

        record = dict(student_model.get_student(self._selected["student_id"]))
        ok, message, path = idcard_service.generate_id_card(record)

        if not ok:
            show_error(self, "Could Not Generate ID Card", message)
            return

        if ask_confirm(self, "ID Card Generated",
                       f"{message}\n\nSaved to:\n{path}\n\nOpen it now?",
                       confirm_text="Open", cancel_text="Close"):
            try:
                import os
                os.startfile(str(path))       # Windows shell open
            except Exception as exc:            # noqa: BLE001
                show_info(self, "File Saved", f"Could not open automatically: {exc}")

    # ==================================================================
    # Import / export
    # ==================================================================
    def import_students(self) -> None:
        choice = ask_confirm(
            self, "Import Students",
            "Import student records from an Excel or CSV file.\n\n"
            "The file must use the standard column headings. If you do not have "
            "one, generate a blank template with dropdowns for branch, section "
            "and batch.\n\n"
            "Choose 'Select File' to import, or 'Get Template' to create one.",
            confirm_text="Select File", cancel_text="Get Template")

        if not choice:
            ok, message, path = import_export.create_student_template()
            if ok:
                show_success(self, "Template Created",
                             f"{message}\n\nFill it in, then use Import again.")
            else:
                show_error(self, "Could Not Create Template", message)
            return

        path = pick_file(self, "Select the student import file",
                         [("Excel or CSV", "*.xlsx *.xls *.csv"), ("All files", "*.*")])
        if not path:
            return

        progress = ProgressDialog(self, "Importing Students",
                                  f"Reading {Path(path).name}...")
        self.update_idletasks()

        try:
            result = import_export.import_students(path, user=session.user)
        except Exception as exc:                # noqa: BLE001
            progress.finish()
            logger.error("Import failed: %s", exc, exc_info=True)
            show_error(self, "Import Failed", str(exc))
            return

        progress.finish()
        self.refresh()

        if result.imported and not result.errors:
            show_success(self, "Import Complete",
                         f"{result.imported} student(s) imported successfully.")
        elif result.imported:
            show_warning(self, "Import Completed With Problems", result.summary())
        else:
            show_error(self, "Nothing Imported", result.summary())

    def export_students(self) -> None:
        values = self.filters.values()
        rows, total = student_model.search_students(
            branch_id=values.get("branch_id"), semester_id=values.get("semester_id"),
            section_id=values.get("section_id"), batch_id=values.get("batch_id"),
            status=values.get("status"), search=values.get("search", ""),
            page=1, page_size=100000)

        if not rows:
            show_info(self, "Nothing to Export",
                      "No students match the current filters.")
            return

        as_excel = ask_confirm(
            self, "Export Students",
            f"Export {total} student record(s) matching the current filters.\n\n"
            "Choose a format.",
            confirm_text="Excel (.xlsx)", cancel_text="CSV (.csv)")

        ok, message, path = import_export.export_students(
            rows, "Excel" if as_excel else "CSV", session.user)

        if ok:
            show_success(self, "Export Complete", f"{message}\n\nSaved to:\n{path}")
        else:
            show_error(self, "Export Failed", message)

    # ==================================================================
    # Promotion
    # ==================================================================
    def promote_students(self) -> None:
        semesters = academic.get_semesters()
        branches = academic.get_branches()
        sessions = academic.get_sessions()

        dialog = FormDialog(
            self, "Bulk Student Promotion",
            [
                FormField("branch_id", "Branch", "select", required=True,
                          options={b["branch_name"]: b["branch_id"] for b in branches}),
                FormField("section_id", "Section (optional)", "select",
                          options={"All Sections": None,
                                   **{s["section_name"]: s["section_id"]
                                      for s in academic.get_sections()}}),
                FormField("from_semester_id", "From Semester", "select", required=True,
                          options={s["semester_name"]: s["semester_id"] for s in semesters}),
                FormField("to_semester_id", "To Semester", "select", required=True,
                          options={s["semester_name"]: s["semester_id"] for s in semesters}),
                FormField("to_session_id", "New Academic Session", "select",
                          options={"Keep current": None,
                                   **{s["session_name"]: s["session_id"] for s in sessions}}),
            ],
            submit_text="Preview Promotion", width=680, height=440,
            description=("Moves a whole cohort into the next semester. "
                         "Attendance history is preserved."))

        values = dialog.show()
        if not values:
            return

        if values["from_semester_id"] == values["to_semester_id"]:
            show_warning(self, "Invalid Selection",
                         "The source and target semester must be different.")
            return

        candidates = student_model.get_class_students(
            values["branch_id"], values["from_semester_id"], values.get("section_id"))

        if not candidates:
            show_info(self, "No Students",
                      "No active students match that branch, semester and section.")
            return

        from_name = next(s["semester_name"] for s in semesters
                         if s["semester_id"] == values["from_semester_id"])
        to_name = next(s["semester_name"] for s in semesters
                       if s["semester_id"] == values["to_semester_id"])

        # Individual selection matters: students who failed are held back, so
        # promoting the whole cohort blindly would be wrong.
        selected_ids = PromotionSelectionDialog(
            self, [dict(c) for c in candidates], from_name, to_name).show()

        if not selected_ids:
            return

        ok, message, count = student_model.promote_students(
            branch_id=values["branch_id"],
            from_semester_id=values["from_semester_id"],
            to_semester_id=values["to_semester_id"],
            section_id=values.get("section_id"),
            to_session_id=values.get("to_session_id"),
            student_ids=selected_ids,
            user=session.user)

        if ok:
            held_back = len(candidates) - count
            detail = message
            if held_back:
                detail += (f"\n\n{held_back} student(s) were not selected and "
                           f"remain in {from_name}.")
            show_success(self, "Promotion Complete", detail)
            self.refresh()
        else:
            show_error(self, "Promotion Failed", message)


# ===========================================================================
# Promotion selection
# ===========================================================================
class PromotionSelectionDialog(ctk.CTkToplevel):
    """Pick exactly which students move up.

    Everyone is ticked by default -- the common case is a whole cohort
    progressing -- but each student can be unticked, which is how a failed
    student is held back.  Attendance percentage is shown alongside each name
    so the decision can be made without leaving the dialog.
    """

    def __init__(self, parent, students: list[dict], from_name: str, to_name: str):
        super().__init__(parent)

        self.students = students
        self.result: list[int] | None = None
        self._vars: dict[int, tk.BooleanVar] = {}

        self.title("Select Students to Promote")
        self.configure(fg_color=color("bg"))
        self.resizable(True, True)
        self.minsize(620, 460)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        width, height = 760, 640
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + 40
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

        self._build(from_name, to_name)
        self.after(80, self._make_modal)

    def _make_modal(self) -> None:
        try:
            self.grab_set()
            self.lift()
        except tk.TclError:
            pass

    def _build(self, from_name: str, to_name: str) -> None:
        header = ctk.CTkFrame(self, fg_color=color("surface"), corner_radius=0,
                              height=62)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=20, pady=10)
        ctk.CTkLabel(titles, text=f"Promote {from_name} to {to_name}",
                     font=(FONTS["heading"][0], 15, "bold"),
                     text_color=color("text"), anchor="w").pack(fill="x")
        ctk.CTkLabel(titles,
                     text=("Untick any student who has failed or is otherwise "
                           "being held back."),
                     font=(FONTS["small"][0], 11),
                     text_color=color("text_muted"), anchor="w").pack(fill="x")

        # ---- footer first, so the buttons are always reachable -------------
        footer = ctk.CTkFrame(self, fg_color=color("surface"), corner_radius=0,
                              height=64)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)

        self.count_label = ctk.CTkLabel(footer, text="", font=FONTS["small_bold"],
                                        text_color=color("primary"), anchor="w")
        self.count_label.pack(side="left", padx=20)

        buttons = ctk.CTkFrame(footer, fg_color="transparent")
        buttons.pack(side="right", padx=20, pady=13)

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel, width=110,
                      height=37, corner_radius=7, fg_color="transparent",
                      border_width=1, border_color=color("border"),
                      text_color=color("text"), hover_color=color("surface_alt"),
                      font=FONTS["body_bold"]).pack(side="right", padx=(10, 0))

        self.promote_button = ctk.CTkButton(
            buttons, text="Promote Selected", command=self._confirm, width=170,
            height=37, corner_radius=7, font=FONTS["body_bold"],
            fg_color=SEMANTIC["purple"], hover_color="#6D28D9")
        self.promote_button.pack(side="right")

        # ---- bulk toggles ---------------------------------------------------
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(side="top", fill="x", padx=18, pady=(12, 6))

        for text, command in (("Select All", lambda: self._set_all(True)),
                              ("Clear All", lambda: self._set_all(False)),
                              ("Only Eligible", self._select_eligible)):
            ctk.CTkButton(toolbar, text=text, command=command, width=120, height=30,
                          corner_radius=6, font=FONTS["small_bold"],
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt")).pack(side="left",
                                                                 padx=(0, 7))

        threshold = float(config.get("attendance_threshold", 75))
        ctk.CTkLabel(toolbar,
                     text=f"'Only Eligible' ticks students at or above {threshold:.0f}% "
                          "attendance.",
                     font=FONTS["small"],
                     text_color=color("text_muted")).pack(side="left", padx=8)

        # ---- student list -----------------------------------------------------
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True, padx=18, pady=(0, 8))

        from models import attendance as attendance_model
        for student in self.students:
            try:
                summary = attendance_model.get_student_summary(student["student_id"])
                percentage = summary["percentage"]
                total = summary["total"]
            except Exception:                   # noqa: BLE001
                percentage, total = 0.0, 0
            student["_percentage"] = percentage
            student["_total"] = total

            row = ctk.CTkFrame(body, height=0, corner_radius=7,
                               fg_color=color("surface_alt"))
            row.pack(fill="x", pady=2)

            variable = tk.BooleanVar(value=True)
            self._vars[student["student_id"]] = variable
            variable.trace_add("write", lambda *_: self._update_count())

            ctk.CTkCheckBox(row, text="", variable=variable, width=26,
                            corner_radius=5).pack(side="left", padx=(11, 4), pady=8)

            ctk.CTkLabel(row, text=str(student["roll_no"]), font=FONTS["body_bold"],
                         text_color=color("text"), width=44,
                         anchor="w").pack(side="left")

            details = ctk.CTkFrame(row, fg_color="transparent")
            details.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(details, text=student["full_name"], font=FONTS["body"],
                         text_color=color("text"), anchor="w").pack(fill="x")
            ctk.CTkLabel(details, text=student["enrollment_no"],
                         font=(FONTS["small"][0], 10),
                         text_color=color("text_muted"), anchor="w").pack(fill="x")

            ctk.CTkLabel(row,
                         text=(f"{percentage:.1f}%  ({total} classes)"
                               if total else "no attendance yet"),
                         font=FONTS["small_bold"],
                         text_color=(percentage_color(percentage, threshold)
                                     if total else color("text_muted"))
                         ).pack(side="right", padx=14)

        self._update_count()

    def _set_all(self, value: bool) -> None:
        for variable in self._vars.values():
            variable.set(value)

    def _select_eligible(self) -> None:
        threshold = float(config.get("attendance_threshold", 75))
        for student in self.students:
            eligible = (student.get("_total", 0) == 0
                        or student.get("_percentage", 0) >= threshold)
            self._vars[student["student_id"]].set(eligible)

    def _update_count(self) -> None:
        selected = sum(1 for v in self._vars.values() if v.get())
        held = len(self._vars) - selected
        self.count_label.configure(
            text=f"{selected} selected for promotion"
                 + (f"   |   {held} held back" if held else ""))
        self.promote_button.configure(state="normal" if selected else "disabled")

    def _confirm(self) -> None:
        self.result = [sid for sid, var in self._vars.items() if var.get()]
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self):
        self.wait_window()
        return self.result


# ===========================================================================
# Face capture dialog
# ===========================================================================
class CaptureDialog(ctk.CTkToplevel):
    """Live camera window that builds a student's face dataset."""

    def __init__(self, parent, student: dict, target: int):
        super().__init__(parent)

        self.student = student
        self.target = target
        self.result = None
        self._preview_image = None
        self._latest = None

        self.title(f"Capture Face Dataset - {student['full_name']}")
        self.configure(fg_color=color("bg"))
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        width, height = 720, 660
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + 30
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

        self._build()
        self.after(80, self._make_modal)
        self.after(300, self._start)

    def _make_modal(self) -> None:
        try:
            self.grab_set()
            self.lift()
        except tk.TclError:
            pass

    def _build(self) -> None:
        ctk.CTkLabel(self, text=f"Capturing face dataset for {self.student['full_name']}",
                     font=FONTS["heading"], text_color=color("text")).pack(pady=(18, 2))
        ctk.CTkLabel(self, text=f"{self.student['enrollment_no']}  |  "
                                f"target {self.target} images",
                     font=FONTS["small"], text_color=color("text_muted")).pack(pady=(0, 12))

        self.preview = ctk.CTkLabel(self, text="Starting camera...", font=FONTS["body"],
                                    text_color=color("text_muted"), fg_color="#0B1220",
                                    corner_radius=8, width=640, height=420)
        self.preview.pack(padx=20)

        self.progress = ctk.CTkProgressBar(self, height=10, corner_radius=5)
        self.progress.pack(fill="x", padx=20, pady=(14, 6))
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(self, text="Preparing...", font=FONTS["body"],
                                         text_color=color("text"))
        self.status_label.pack()

        self.count_label = ctk.CTkLabel(self, text=f"0 / {self.target}",
                                        font=FONTS["subhead"], text_color=color("primary"))
        self.count_label.pack(pady=(2, 10))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(pady=(0, 16))

        self.stop_button = ctk.CTkButton(buttons, text="Stop & Save", command=self._stop,
                                         width=140, height=36, corner_radius=7,
                                         font=FONTS["body_bold"])
        self.stop_button.pack(side="left", padx=5)
        ctk.CTkButton(buttons, text="Cancel", command=self._cancel, width=120, height=36,
                      corner_radius=7, font=FONTS["body_bold"], fg_color="transparent",
                      border_width=1, border_color=color("border"),
                      text_color=color("text"),
                      hover_color=color("surface_alt")).pack(side="left", padx=5)

    def _start(self) -> None:
        self.capture = face_service.DatasetCapture(
            self.student["student_id"], self.student["enrollment_no"],
            self.student["full_name"], self.target)
        self.capture.start(self._on_progress)
        self._poll()

    def _on_progress(self, progress) -> None:
        """Called from the capture thread -- only store, never touch widgets."""
        self._latest = progress

    def _poll(self) -> None:
        progress = self._latest
        if progress is not None:
            if progress.frame is not None:
                try:
                    import cv2
                    rgb = cv2.cvtColor(progress.frame, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(rgb).resize((640, 420), Image.LANCZOS)
                    self._preview_image = ctk.CTkImage(image, size=(640, 420))
                    self.preview.configure(image=self._preview_image, text="")
                except Exception:               # noqa: BLE001
                    pass

            self.status_label.configure(text=progress.message)
            self.count_label.configure(text=f"{progress.captured} / {progress.target or self.target}")
            self.progress.set((progress.captured / self.target) if self.target else 0)

            if progress.finished:
                self._finish(progress)
                return

        if self.capture.is_running():
            self.after(35, self._poll)
        else:
            self.after(120, lambda: self._finish(self._latest))

    def _finish(self, progress) -> None:
        captured = progress.captured if progress else 0
        self.result = {
            "success": bool(progress and progress.success),
            "captured": captured,
            "dataset_dir": self.capture.dataset_dir,
            "message": progress.message if progress else "Capture ended unexpectedly.",
        }
        self._close()

    def _stop(self) -> None:
        self.capture.stop()
        self.status_label.configure(text="Finishing...")

    def _cancel(self) -> None:
        try:
            self.capture.stop()
        except Exception:                       # noqa: BLE001
            pass
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self):
        self.wait_window()
        return self.result


# ===========================================================================
# Single-photo capture -- profile photo, not the face-recognition dataset
# ===========================================================================
class SinglePhotoDialog(ctk.CTkToplevel):
    """Live camera preview with a single Capture button.

    Used for the profile photo (Add/Edit Student, and a student's own
    self-service update) -- distinct from :class:`CaptureDialog`, which builds
    the multi-image face-recognition dataset.
    """

    def __init__(self, parent, title: str):
        super().__init__(parent)

        self.result: str | None = None
        self._frame = None
        self._preview_image = None
        self._captured_path: Path | None = None
        self._running = True
        self._camera_index = int(config.get("camera_index", 0))

        self.title(title)
        self.configure(fg_color=color("bg"))
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        width, height = 560, 560
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + 40
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

        self._build(title)
        self.after(80, self._make_modal)
        self.after(200, self._start_camera)

    def _make_modal(self) -> None:
        try:
            self.grab_set()
            self.lift()
        except tk.TclError:
            pass

    def _build(self, title: str) -> None:
        ctk.CTkLabel(self, text=title, font=FONTS["heading"],
                     text_color=color("text")).pack(pady=(18, 4))

        self.preview = ctk.CTkLabel(self, text="Starting camera...", font=FONTS["body"],
                                    text_color=color("text_muted"), fg_color="#0B1220",
                                    corner_radius=8, width=480, height=360)
        self.preview.pack(padx=20)

        self.hint_label = ctk.CTkLabel(
            self, text="Look at the camera, then press Capture.",
            font=FONTS["small"], text_color=color("text_muted"))
        self.hint_label.pack(pady=(10, 0))

        self.buttons = ctk.CTkFrame(self, fg_color="transparent")
        self.buttons.pack(pady=16)

        # Two button sets that swap: "live" (Capture / Cancel) and
        # "reviewing" (Use This Photo / Retake / Cancel).
        self.live_buttons = ctk.CTkFrame(self.buttons, fg_color="transparent")
        self.review_buttons = ctk.CTkFrame(self.buttons, fg_color="transparent")

        self.capture_button = ctk.CTkButton(
            self.live_buttons, text="Capture", command=self._capture, width=140,
            height=38, corner_radius=7, font=FONTS["body_bold"])
        self.capture_button.pack(side="left", padx=5)
        ctk.CTkButton(self.live_buttons, text="Cancel", command=self._cancel,
                      width=100, height=38, corner_radius=7, font=FONTS["body_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt")).pack(side="left", padx=5)

        ctk.CTkButton(self.review_buttons, text="Use This Photo", command=self._confirm,
                      width=150, height=38, corner_radius=7, font=FONTS["body_bold"],
                      fg_color=SEMANTIC["success"], hover_color="#15803D"
                      ).pack(side="left", padx=5)
        ctk.CTkButton(self.review_buttons, text="Retake", command=self._retake,
                      width=110, height=38, corner_radius=7, font=FONTS["body_bold"],
                      fg_color="transparent", border_width=1, border_color=color("border"),
                      text_color=color("text"), hover_color=color("surface_alt")
                      ).pack(side="left", padx=5)
        ctk.CTkButton(self.review_buttons, text="Cancel", command=self._cancel,
                      width=100, height=38, corner_radius=7, font=FONTS["body_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt")).pack(side="left", padx=5)

        self.live_buttons.pack()

    def _start_camera(self) -> None:
        self._running = True
        self._captured_path = None
        self._thread = threading.Thread(target=self._camera_loop, daemon=True,
                                        name="photo-capture")
        self._thread.start()
        self._poll()

    def _camera_loop(self) -> None:
        import time
        import cv2
        camera = cv2.VideoCapture(self._camera_index, cv2.CAP_DSHOW)
        if not camera.isOpened():
            camera = cv2.VideoCapture(self._camera_index)
        if not camera.isOpened():
            self._frame = "error"
            return

        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while self._running:
            ok, frame = camera.read()
            if ok:
                self._frame = cv2.flip(frame, 1)
            time.sleep(0.03)
        camera.release()

    def _poll(self) -> None:
        if not self._running:
            return

        if isinstance(self._frame, str) and self._frame == "error":
            self.preview.configure(
                text=f"Camera {self._camera_index} could not be opened.\n\n"
                     "Check that no other application is using it.")
            self.capture_button.configure(state="disabled")
            return

        if self._frame is not None and not isinstance(self._frame, str):
            try:
                import cv2
                rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(rgb).resize((480, 360), Image.LANCZOS)
                self._preview_image = ctk.CTkImage(image, size=(480, 360))
                self.preview.configure(image=self._preview_image, text="")
            except Exception:                   # noqa: BLE001
                pass

        self.after(33, self._poll)

    def _capture(self) -> None:
        if self._frame is None or isinstance(self._frame, str):
            return

        import cv2
        from config.settings import TEMP_DIR
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        path = TEMP_DIR / f"profile_capture_{datetime.now():%Y%m%d_%H%M%S}.jpg"
        cv2.imwrite(str(path), self._frame)
        self._captured_path = path

        self._running = False   # freeze the preview on the captured frame
        self.hint_label.configure(text="Captured. Use this photo, or retake it.")
        self.live_buttons.pack_forget()
        self.review_buttons.pack()

    def _retake(self) -> None:
        self.review_buttons.pack_forget()
        self.live_buttons.pack()
        self.capture_button.configure(state="normal")
        self.hint_label.configure(text="Look at the camera, then press Capture.")
        self._start_camera()

    def _confirm(self) -> None:
        self.result = str(self._captured_path) if self._captured_path else None
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        self._running = False
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self):
        self.wait_window()
        return self.result


def choose_photo_source(parent, title: str, subject_name: str = "") -> str | None:
    """Ask Capture vs Upload, then return a file path (or None if cancelled).

    Shared by Add/Edit Student, the student self-service profile update, and
    anywhere a single photograph is needed rather than a face-recognition
    dataset.
    """
    use_camera = ask_confirm(
        parent, title,
        (f"Update the photo for {subject_name}?\n\n" if subject_name else "") +
        "Choose how to provide the photo.",
        confirm_text="Use Camera", cancel_text="Upload File")

    if use_camera:
        return SinglePhotoDialog(parent, title).show()

    return pick_file(parent, "Select a photo",
                     [("Images", "*.jpg *.jpeg *.png *.bmp"), ("All files", "*.*")])
