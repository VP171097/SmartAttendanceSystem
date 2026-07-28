"""
Report generation.

Eleven report types from the specification, each exportable to PDF, Excel or
CSV with the college branding, filters, timestamp and generating user in the
header.

The screen previews the data before exporting, so a user never generates a
50-page PDF only to discover the filters were wrong.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import customtkinter as ctk

from config.settings import ATTENDANCE_STATUSES, LEAVE_TYPES, REPORT_PDF_DIR, config
from config.theme import FONTS, SEMANTIC, color
from core.auth import session
from core.logger import get_logger
from models import academic, attendance as attendance_model, faculty as faculty_model
from models import leave as leave_model, subject as subject_model
from services import report_service
from ui.widgets.components import EmptyState, PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import (ask_confirm, show_error, show_info, show_success,
                                show_warning)
from ui.widgets.table import DataTable, column

logger = get_logger("ui.reports")


# ---------------------------------------------------------------------------
# Report catalogue.  Each entry declares which filters it needs and which
# builder shapes its rows -- so adding a report is a data change, not new UI.
# ---------------------------------------------------------------------------
REPORTS = {
    "Daily Attendance": {
        "builder": "detail", "period": "day",
        "description": "Every attendance record for a single date.",
    },
    "Weekly Attendance": {
        "builder": "detail", "period": "week",
        "description": "All records across a seven-day window.",
    },
    "Monthly Attendance": {
        "builder": "detail", "period": "month",
        "description": "All records for a calendar month.",
    },
    "Semester Attendance": {
        "builder": "summary", "period": "range",
        "description": "Per-student consolidated percentages for a semester.",
    },
    "Student Attendance": {
        "builder": "summary", "period": "range",
        "description": "Per-student totals with exam eligibility.",
    },
    "Subject Attendance": {
        "builder": "detail", "period": "range", "needs_subject": True,
        "description": "Every record for one subject.",
    },
    "Branch Attendance": {
        "builder": "summary", "period": "range",
        "description": "Per-student summary grouped by branch.",
    },
    "Faculty Attendance": {
        "builder": "faculty", "period": "range",
        "description": "Classes conducted and attendance achieved, by faculty.",
    },
    "Defaulter List": {
        "builder": "defaulter", "period": "none",
        "description": "Students below the attendance threshold for exam eligibility.",
    },
    "Leave Report": {
        "builder": "leave", "period": "range",
        "description": "All leave applications with their status.",
    },
    "Medical Leave Report": {
        "builder": "leave", "period": "range",
        "description": "Medical leave only, showing which have certificates.",
    },
}


class ReportsView(ctk.CTkFrame):
    """Report builder and exporter."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._rows: list = []
        self._columns: list[str] = []
        self._table_rows: list[list] = []
        self._summary: list = []

        self._build()
        self._report_changed()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Reports",
            subtitle="Generate branded attendance reports in PDF, Excel or CSV",
            icon="▦")
        header.pack(fill="x", padx=18, pady=(14, 10))
        header.add_button("Open Reports Folder", self._open_folder, width=185,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=0, minsize=310)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_control_panel(body)
        self._build_preview(body)

    def _build_control_panel(self, parent) -> None:
        panel = ctk.CTkScrollableFrame(parent, fg_color="transparent", width=300)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        # ---- report type ---------------------------------------------------
        type_card = SectionCard(panel, "Report Type", "What do you want to produce?")
        type_card.pack(fill="x", pady=(0, 12))

        self._type_card = type_card
        self.report_var = ctk.StringVar(value="Daily Attendance")
        ctk.CTkOptionMenu(type_card.body, variable=self.report_var,
                          values=list(REPORTS), width=260, height=36, corner_radius=7,
                          font=FONTS["body"],
                          command=lambda _v: self._report_changed()).pack(fill="x")

        self.description_label = ctk.CTkLabel(
            type_card.body, text="", font=FONTS["small"],
            text_color=color("text_muted"), wraplength=250, justify="left", anchor="w")
        self.description_label.pack(fill="x", pady=(8, 0))

        # ---- period ---------------------------------------------------------
        self.period_card = SectionCard(panel, "Period", "Date range for the report")
        self.period_card.pack(fill="x", pady=(0, 12))

        preset_row = ctk.CTkFrame(self.period_card.body, fg_color="transparent")
        preset_row.pack(fill="x", pady=(0, 10))
        for label, days in (("Today", 0), ("7 days", 7), ("30 days", 30), ("90 days", 90)):
            ctk.CTkButton(preset_row, text=label, width=58, height=27, corner_radius=6,
                          font=(FONTS["small"][0], 10, "bold"), fg_color="transparent",
                          border_width=1, border_color=color("border"),
                          text_color=color("text"), hover_color=color("surface_alt"),
                          command=lambda d=days: self._set_period(d)).pack(
                              side="left", padx=2)

        for key, label, default in (("from_date", "From", date.today().isoformat()),
                                    ("to_date", "To", date.today().isoformat())):
            ctk.CTkLabel(self.period_card.body, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), anchor="w").pack(
                             fill="x", pady=(4, 2))
            variable = ctk.StringVar(value=default)
            setattr(self, f"{key}_var", variable)
            ctk.CTkEntry(self.period_card.body, textvariable=variable, height=32,
                         corner_radius=7, font=FONTS["body"],
                         placeholder_text="YYYY-MM-DD").pack(fill="x")

        # ---- filters ---------------------------------------------------------
        filter_card = SectionCard(panel, "Filters", "Narrow the report down")
        filter_card.pack(fill="x", pady=(0, 12))

        self._filters: dict[str, dict] = {}

        def add_filter(key: str, label: str, mapping: dict) -> ctk.CTkOptionMenu:
            ctk.CTkLabel(filter_card.body, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), anchor="w").pack(
                             fill="x", pady=(6, 2))
            options = {"All": None, **mapping}
            variable = ctk.StringVar(value="All")
            widget = ctk.CTkOptionMenu(filter_card.body, variable=variable,
                                       values=list(options), height=32,
                                       corner_radius=7, font=FONTS["body"])
            widget.pack(fill="x")
            self._filters[key] = {"var": variable, "map": options, "widget": widget}
            return widget

        add_filter("branch_id", "Branch",
                   {b["branch_name"]: b["branch_id"] for b in academic.get_branches()})
        add_filter("semester_id", "Semester",
                   {s["semester_name"]: s["semester_id"] for s in academic.get_semesters()})
        add_filter("section_id", "Section",
                   {s["section_name"]: s["section_id"] for s in academic.get_sections()})
        self._subject_widget = add_filter(
            "subject_id", "Subject",
            {f"{s['subject_code']} - {s['subject_name']}": s["subject_id"]
             for s in subject_model.search_subjects()})
        add_filter("faculty_id", "Faculty",
                   {f["full_name"]: f["faculty_id"] for f in faculty_model.get_all_faculty()})
        add_filter("status", "Attendance Status", {s: s for s in ATTENDANCE_STATUSES})
        add_filter("session_id", "Academic Session",
                   {s["session_name"]: s["session_id"] for s in academic.get_sessions()})

        # ---- actions -----------------------------------------------------------
        action_card = SectionCard(panel, "Generate", "Preview first, then export")
        action_card.pack(fill="x")

        ctk.CTkButton(action_card.body, text="Preview Report", command=self.generate_preview,
                      height=38, corner_radius=7, font=FONTS["body_bold"]).pack(
                          fill="x", pady=(0, 10))

        for text, file_format, accent in (
                ("Export as PDF", "PDF", SEMANTIC["danger"]),
                ("Export as Excel", "Excel", SEMANTIC["success"]),
                ("Export as CSV", "CSV", SEMANTIC["info"])):
            ctk.CTkButton(action_card.body, text=text,
                          command=lambda f=file_format: self.export(f),
                          height=34, corner_radius=7, font=FONTS["small_bold"],
                          fg_color=accent, hover_color=accent).pack(fill="x", pady=3)

    def _build_preview(self, parent) -> None:
        panel = ctk.CTkFrame(parent, fg_color="transparent")
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_rowconfigure(2, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(panel, text="Report Preview",
                                        font=FONTS["heading"], text_color=color("text"),
                                        anchor="w")
        self.title_label.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        self.subtitle_label = ctk.CTkLabel(
            panel, text="Choose a report type and press 'Preview Report'.",
            font=FONTS["small"], text_color=color("text_muted"), anchor="w")
        self.subtitle_label.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.preview_container = ctk.CTkFrame(panel, fg_color="transparent")
        self.preview_container.grid(row=2, column=0, sticky="nsew")

        self.empty_state = EmptyState(
            self.preview_container, "▦", "No report generated yet",
            "Select a report type and period on the left, then press "
            "'Preview Report' to see the data before exporting.")
        self.empty_state.pack(fill="both", expand=True)

        self.summary_frame = ctk.CTkFrame(panel, fg_color="transparent")
        self.table: DataTable | None = None

    # ==================================================================
    def _set_period(self, days: int) -> None:
        today = date.today()
        self.to_date_var.set(today.isoformat())
        self.from_date_var.set((today - timedelta(days=days)).isoformat())

    def _report_changed(self) -> None:
        spec = REPORTS[self.report_var.get()]
        self.description_label.configure(text=spec["description"])

        period = spec["period"]
        # The Defaulter List has no period; hide the whole card for it.
        if period == "none":
            self.period_card.pack_forget()
        else:
            self.period_card.pack(fill="x", pady=(0, 12), after=self._type_card)

        # Sensible default window per report type.
        today = date.today()
        if period == "day":
            self.from_date_var.set(today.isoformat())
            self.to_date_var.set(today.isoformat())
        elif period == "week":
            self.from_date_var.set((today - timedelta(days=6)).isoformat())
            self.to_date_var.set(today.isoformat())
        elif period == "month":
            self.from_date_var.set(today.replace(day=1).isoformat())
            self.to_date_var.set(today.isoformat())
        elif period == "range":
            self.from_date_var.set((today - timedelta(days=90)).isoformat())
            self.to_date_var.set(today.isoformat())

    def _filter_value(self, key: str):
        spec = self._filters.get(key)
        return spec["map"].get(spec["var"].get()) if spec else None

    def _filter_label(self, key: str) -> str:
        spec = self._filters.get(key)
        return spec["var"].get() if spec else "All"

    def _collect_filters(self) -> dict:
        """Human-readable filter description embedded in the report header."""
        spec = REPORTS[self.report_var.get()]
        described = {}

        if spec["period"] != "none":
            described["period"] = f"{self.from_date_var.get()} to {self.to_date_var.get()}"

        for key, label in (("branch_id", "branch"), ("semester_id", "semester"),
                           ("section_id", "section"), ("subject_id", "subject"),
                           ("faculty_id", "faculty"), ("status", "status"),
                           ("session_id", "session")):
            value = self._filter_label(key)
            if value and value != "All":
                described[label] = value

        return described

    # ==================================================================
    def generate_preview(self) -> None:
        report_type = self.report_var.get()
        spec = REPORTS[report_type]

        from_date = self.from_date_var.get().strip() if spec["period"] != "none" else None
        to_date = self.to_date_var.get().strip() if spec["period"] != "none" else None

        if from_date:
            for value, label in ((from_date, "From"), (to_date, "To")):
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    show_warning(self, "Invalid Date",
                                 f"{label} date must be in YYYY-MM-DD format.")
                    return
            if to_date < from_date:
                show_warning(self, "Invalid Range",
                             "The 'To' date cannot be before the 'From' date.")
                return

        if spec.get("needs_subject") and not self._filter_value("subject_id"):
            show_warning(self, "Subject Required",
                         "Choose a subject in the filters for this report.")
            return

        try:
            data = self._fetch_data(report_type, spec, from_date, to_date)
        except Exception as exc:                # noqa: BLE001
            logger.error("Report query failed: %s", exc, exc_info=True)
            show_error(self, "Query Failed", str(exc))
            return

        if not data:
            show_info(self, "No Data",
                      "No records match the selected report type, period and filters.\n\n"
                      "Try widening the date range or clearing some filters.")
            return

        builders = {
            "detail": report_service.build_attendance_detail,
            "summary": report_service.build_student_summary,
            "defaulter": report_service.build_defaulter_list,
            "leave": report_service.build_leave_report,
            "faculty": report_service.build_faculty_report,
        }

        try:
            self._columns, self._table_rows, self._summary = builders[spec["builder"]](data)
        except Exception as exc:                # noqa: BLE001
            logger.error("Report builder failed: %s", exc, exc_info=True)
            show_error(self, "Could Not Build Report", str(exc))
            return

        self._rows = data
        self._render_preview(report_type)

    def _fetch_data(self, report_type: str, spec: dict,
                    from_date: str | None, to_date: str | None) -> list:
        """Route each report type to the right model query."""
        branch_id = self._filter_value("branch_id")
        semester_id = self._filter_value("semester_id")
        section_id = self._filter_value("section_id")
        subject_id = self._filter_value("subject_id")
        faculty_id = self._filter_value("faculty_id")
        status = self._filter_value("status")
        session_id = self._filter_value("session_id")

        if report_type == "Defaulter List":
            return attendance_model.get_defaulters(
                threshold=float(config.get("attendance_threshold", 75)),
                branch_id=branch_id, semester_id=semester_id,
                section_id=section_id, subject_id=subject_id)

        if report_type == "Faculty Attendance":
            return report_service.get_faculty_report_data(from_date, to_date)

        if report_type == "Leave Report":
            return leave_model.search_leaves(
                branch_id=branch_id, semester_id=semester_id, section_id=section_id,
                from_date=from_date, to_date=to_date, limit=5000)

        if report_type == "Medical Leave Report":
            return leave_model.get_medical_leaves(
                from_date=from_date, to_date=to_date, branch_id=branch_id)

        if spec["builder"] == "summary":
            return report_service.get_class_summary_data(
                branch_id=branch_id, semester_id=semester_id, section_id=section_id,
                subject_id=subject_id, from_date=from_date, to_date=to_date)

        return attendance_model.get_attendance_records(
            from_date=from_date, to_date=to_date, branch_id=branch_id,
            semester_id=semester_id, section_id=section_id, subject_id=subject_id,
            faculty_id=faculty_id, status=status, session_id=session_id, limit=8000)

    def _render_preview(self, report_type: str) -> None:
        self.empty_state.pack_forget()
        for widget in self.preview_container.winfo_children():
            widget.destroy()

        filters = self._collect_filters()
        self.title_label.configure(text=report_type)
        self.subtitle_label.configure(
            text=(f"{len(self._table_rows):,} row(s)  |  "
                  + "  |  ".join(f"{k.title()}: {v}" for k, v in filters.items())))

        # ---- summary tiles ---------------------------------------------
        if self._summary:
            tiles = ctk.CTkFrame(self.preview_container, fg_color="transparent")
            tiles.pack(fill="x", pady=(0, 10))
            for index in range(len(self._summary)):
                tiles.grid_columnconfigure(index, weight=1, uniform="summary")

            palette = [SEMANTIC["info"], SEMANTIC["success"], SEMANTIC["danger"],
                       SEMANTIC["warning"], SEMANTIC["purple"], SEMANTIC["teal"]]
            for index, (label, value) in enumerate(self._summary):
                StatCard(tiles, label, value, "", palette[index % len(palette)]
                         ).grid(row=0, column=index, sticky="ew", padx=3)

        # ---- data table -------------------------------------------------
        widths = {"Student Name": 175, "Subject": 165, "Faculty Name": 165,
                  "Reason": 220, "Qualification": 175}
        table_columns = [
            column(str(index), name,
                   widths.get(name, max(75, min(150, len(name) * 11))),
                   stretch=(name in ("Student Name", "Subject", "Faculty Name", "Reason")))
            for index, name in enumerate(self._columns)
        ]

        table = DataTable(self.preview_container, columns=table_columns, height=16)
        table.pack(fill="both", expand=True)
        table.set_data([{str(i): v for i, v in enumerate(row)}
                        for row in self._table_rows])
        self.table = table

    # ==================================================================
    def export(self, file_format: str) -> None:
        if not self._rows:
            show_info(self, "Nothing to Export",
                      "Generate a preview first, then export it.")
            return

        report_type = self.report_var.get()
        spec = REPORTS[report_type]
        filters = self._collect_filters()

        ok, message, path = report_service.generate(
            report_type, self._rows, file_format, filters, session.user,
            builder=spec["builder"])

        if not ok:
            show_error(self, "Export Failed", message)
            return

        self.app.set_status(message, "success")

        if ask_confirm(self, "Report Generated",
                       f"{message}\n\nSaved to:\n{path}\n\nOpen it now?",
                       confirm_text="Open", cancel_text="Close"):
            try:
                os.startfile(str(path))
            except Exception as exc:            # noqa: BLE001
                show_info(self, "File Saved", f"Could not open automatically: {exc}")

    def _open_folder(self) -> None:
        try:
            os.startfile(str(REPORT_PDF_DIR.parent))
        except Exception as exc:                # noqa: BLE001
            show_info(self, "Reports Folder",
                      f"Reports are saved under:\n\n{REPORT_PDF_DIR.parent}\n\n"
                      f"(Could not open automatically: {exc})")

    def refresh(self) -> None:
        """Rebuild the subject filter -- subjects change on other screens."""
        spec = self._filters.get("subject_id")
        if not spec:
            return
        options = {"All": None,
                   **{f"{s['subject_code']} - {s['subject_name']}": s["subject_id"]
                      for s in subject_model.search_subjects()}}
        current = spec["var"].get()
        spec["map"] = options
        spec["widget"].configure(values=list(options))
        if current not in options:
            spec["var"].set("All")
