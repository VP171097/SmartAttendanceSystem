"""
Attendance records browser.

Two views of the history:

*   **Sessions** -- every class that was conducted, with its counts and lock
    state.  This is where an administrator unlocks a session for correction.
*   **Records**  -- individual student rows, filterable on every axis the
    specification lists (session, course, branch, semester, section, batch,
    subject, faculty, date, status).
"""

from __future__ import annotations

import customtkinter as ctk

from config.settings import ATTENDANCE_STATUSES
from config.theme import FONTS, SEMANTIC, color
from core.auth import Permission, session
from core.logger import get_logger
from models import academic, attendance as attendance_model, faculty as faculty_model
from models import subject as subject_model
from services.report_service import generate_attendance_sheet
from ui.widgets.components import FilterBar, PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import (DetailDialog, ask_confirm, ask_reason, show_error,
                                show_info, show_success)
from ui.widgets.table import DataTable, column, dash_format, date_format, percent_format

logger = get_logger("ui.attendance_records")


class AttendanceRecordsView(ctk.CTkFrame):
    """Historical attendance browser."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected_session: dict | None = None

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Attendance Records",
            subtitle="Browse conducted classes and individual attendance records",
            icon="≡")
        header.pack(fill="x", padx=18, pady=(14, 10))

        # ---- filters ---------------------------------------------------------
        self.filters = FilterBar(self, on_change=self.refresh,
                                 search_placeholder="Student name or enrollment...")
        self.filters.pack(fill="x", padx=18, pady=(0, 9))

        self.filters.add_filter(
            "branch_id", "Branch",
            {b["branch_name"]: b["branch_id"] for b in academic.get_branches()}, width=175)
        self.filters.add_filter(
            "semester_id", "Semester",
            {s["semester_name"]: s["semester_id"] for s in academic.get_semesters()},
            width=125)
        self.filters.add_filter(
            "section_id", "Section",
            {s["section_name"]: s["section_id"] for s in academic.get_sections()}, width=90)
        self.filters.add_filter(
            "subject_id", "Subject",
            {f"{s['subject_code']}": s["subject_id"]
             for s in subject_model.search_subjects()}, width=110)
        self.filters.add_filter(
            "faculty_id", "Faculty",
            {f["full_name"]: f["faculty_id"] for f in faculty_model.get_all_faculty()},
            width=170)
        self.filters.add_filter("status", "Status",
                                {s: s for s in ATTENDANCE_STATUSES}, width=125)
        self.filters.add_search(190)
        self.filters.add_button("Reset", self.filters.reset, width=75,
                                fg_color="transparent", border_width=1,
                                border_color=color("border"), text_color=color("text"),
                                hover_color=color("surface_alt"))

        # ---- date range -------------------------------------------------------
        date_bar = ctk.CTkFrame(self, height=0, corner_radius=9, fg_color=color("surface"),
                                border_width=1, border_color=color("border"))
        date_bar.pack(fill="x", padx=18, pady=(0, 9))

        inner = ctk.CTkFrame(date_bar, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=9)

        from datetime import date, timedelta
        for key, label, default in (
                ("from_date", "From Date", (date.today() - timedelta(days=30)).isoformat()),
                ("to_date", "To Date", date.today().isoformat())):
            holder = ctk.CTkFrame(inner, fg_color="transparent")
            holder.pack(side="left", padx=(0, 12))
            ctk.CTkLabel(holder, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), anchor="w").pack(fill="x")
            variable = ctk.StringVar(value=default)
            setattr(self, f"{key}_var", variable)
            ctk.CTkEntry(holder, textvariable=variable, width=125, height=31,
                         corner_radius=6, font=FONTS["body"]).pack()

        apply_holder = ctk.CTkFrame(inner, fg_color="transparent")
        apply_holder.pack(side="left")
        ctk.CTkLabel(apply_holder, text=" ", font=FONTS["small_bold"]).pack(fill="x")
        ctk.CTkButton(apply_holder, text="Apply Range", command=self.refresh,
                      width=120, height=31, corner_radius=6,
                      font=FONTS["small_bold"]).pack()

        self.summary_label = ctk.CTkLabel(inner, text="", font=FONTS["small"],
                                          text_color=color("text_muted"))
        self.summary_label.pack(side="right")

        # ---- tabs ---------------------------------------------------------------
        self.tabs = ctk.CTkTabview(
            self, corner_radius=10,
            segmented_button_selected_color=color("primary"),
            segmented_button_selected_hover_color=color("primary_hover"))
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.tabs.add("Class Sessions")
        self.tabs.add("Student Records")

        self._build_sessions_tab()
        self._build_records_tab()
        self.tabs.set("Class Sessions")

    def _build_sessions_tab(self) -> None:
        tab = self.tabs.tab("Class Sessions")

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", pady=(6, 10))

        ctk.CTkButton(bar, text="View Session", command=self.view_session, width=140,
                      height=34, corner_radius=7, font=FONTS["small_bold"]).pack(
                          side="left", padx=(0, 8))
        ctk.CTkButton(bar, text="Attendance Sheet", command=self.export_sheet, width=160,
                      height=34, corner_radius=7, font=FONTS["small_bold"],
                      fg_color=SEMANTIC["teal"]).pack(side="left", padx=(0, 8))

        if session.can(Permission.UNLOCK_ATTENDANCE):
            ctk.CTkButton(bar, text="Unlock Session", command=self.unlock_session,
                          width=145, height=34, corner_radius=7,
                          font=FONTS["small_bold"],
                          fg_color=SEMANTIC["warning"]).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(bar,
                     text="Double-click a session to see its full student list.",
                     font=FONTS["small"], text_color=color("text_muted")).pack(
                         side="left", padx=10)

        self.sessions_table = DataTable(
            tab,
            columns=[
                column("class_date", "Date", 100, format=date_format),
                column("start_time", "Time", 70, "center"),
                column("subject_code", "Code", 75),
                column("subject_name", "Subject", 190, stretch=True),
                column("faculty_name", "Faculty", 160, format=dash_format),
                column("branch_code", "Branch", 70, "center"),
                column("semester_name", "Semester", 100),
                column("section_name", "Sec", 45, "center", format=dash_format),
                column("mode", "Mode", 125),
                column("present_count", "Present", 75, "center"),
                column("absent_count", "Absent", 70, "center"),
                column("total_students", "Total", 60, "center"),
                column("is_locked", "Locked", 70, "center",
                       format=lambda v, _r: "LOCKED" if v else ""),
            ],
            on_select=lambda row: setattr(self, "_selected_session", row),
            on_double_click=lambda _row: self.view_session(),
            height=16)
        self.sessions_table.pack(fill="both", expand=True)

    def _build_records_tab(self) -> None:
        tab = self.tabs.tab("Student Records")

        ctk.CTkLabel(
            tab,
            text=("Individual attendance records. Use the filters above to narrow by "
                  "branch, semester, section, subject, faculty, status or date. "
                  "Edits are made from the Manual Attendance screen."),
            font=FONTS["small"], text_color=color("text_muted"), anchor="w",
            justify="left", wraplength=1000).pack(fill="x", pady=(6, 10))

        self.records_table = DataTable(
            tab,
            columns=[
                column("class_date", "Date", 100, format=date_format),
                column("enrollment_no", "Enrollment No", 115),
                column("student_name", "Student Name", 175, stretch=True),
                column("roll_no", "Roll", 50, "center"),
                column("subject_code", "Code", 75),
                column("subject_name", "Subject", 165),
                column("branch_code", "Branch", 70, "center"),
                column("semester_name", "Semester", 100),
                column("status", "Status", 110, "center"),
                column("marked_method", "Method", 130),
                column("confidence", "Conf %", 70, "center",
                       format=lambda v, _r: f"{float(v):.0f}%" if v else "-"),
                column("marked_time", "Time", 80, format=dash_format),
                column("is_modified", "Edited", 65, "center",
                       format=lambda v, _r: "Yes" if v else ""),
            ],
            on_double_click=self._show_record_detail,
            height=16)
        self.records_table.pack(fill="both", expand=True)

    # ==================================================================
    def refresh(self) -> None:
        values = self.filters.values()
        from_date = self.from_date_var.get().strip()
        to_date = self.to_date_var.get().strip()

        try:
            sessions = attendance_model.search_sessions(
                subject_id=values.get("subject_id"),
                faculty_id=values.get("faculty_id"),
                branch_id=values.get("branch_id"),
                semester_id=values.get("semester_id"),
                section_id=values.get("section_id"),
                from_date=from_date or None, to_date=to_date or None, limit=1000)

            records = attendance_model.get_attendance_records(
                from_date=from_date or None, to_date=to_date or None,
                branch_id=values.get("branch_id"),
                semester_id=values.get("semester_id"),
                section_id=values.get("section_id"),
                subject_id=values.get("subject_id"),
                faculty_id=values.get("faculty_id"),
                status=values.get("status"), limit=5000)
        except Exception as exc:                # noqa: BLE001
            logger.error("Records query failed: %s", exc, exc_info=True)
            show_error(self, "Query Failed", str(exc))
            return

        # Free-text search runs client-side over the fetched page.
        term = (values.get("search") or "").lower()
        if term:
            records = [r for r in records
                       if term in str(r["student_name"]).lower()
                       or term in str(r["enrollment_no"]).lower()]

        self.sessions_table.set_data(sessions)
        self.records_table.set_data(records)

        attended = sum(1 for r in records if r["status"] in ("Present", "Late"))
        percentage = (100.0 * attended / len(records)) if records else 0.0
        locked = sum(1 for s in sessions if s["is_locked"])

        self.summary_label.configure(
            text=(f"{len(sessions):,} class session(s) ({locked} locked)   |   "
                  f"{len(records):,} record(s)   |   {percentage:.1f}% attendance"))

    # ==================================================================
    def view_session(self) -> None:
        if not self._selected_session:
            show_info(self, "No Selection", "Select a class session from the list first.")
            return

        att_session_id = self._selected_session["att_session_id"]
        info = dict(attendance_model.get_session(att_session_id))
        records = [dict(r) for r in attendance_model.get_session_records(att_session_id)]
        unknown = attendance_model.get_unknown_faces(att_session_id)

        counts: dict[str, int] = {}
        for record in records:
            counts[record["status"]] = counts.get(record["status"], 0) + 1

        sections = {
            "Class": [
                ("Subject", f"{info['subject_name']} ({info['subject_code']})"),
                ("Faculty", info["faculty_name"] or "-"),
                ("Branch", info["branch_name"]),
                ("Semester", info["semester_name"]),
                ("Section", info["section_name"] or "-"),
                ("Date", info["class_date"]),
                ("Time", f"{info['start_time'] or '-'} to {info['end_time'] or '-'}"),
                ("Mode", info["mode"]),
                ("Academic Session", info["session_name"] or "-"),
            ],
            "Summary": [
                ("Total Students", info["total_students"]),
                ("Present", counts.get("Present", 0)),
                ("Late", counts.get("Late", 0)),
                ("Absent", counts.get("Absent", 0)),
                ("Leave", counts.get("Leave", 0)),
                ("Medical Leave", counts.get("Medical Leave", 0)),
                ("Unknown faces detected", len(unknown)),
                ("Locked", "Yes" if info["is_locked"] else "No"),
            ],
            f"Students ({len(records)})": [
                (f"{r['roll_no']}. {r['full_name']}",
                 f"{r['status']}"
                 + (f"  ({r['marked_method']}"
                    + (f", {r['confidence']:.0f}%" if r["confidence"] else "")
                    + ")" if r["marked_method"] else "")
                 + ("  [EDITED]" if r["is_modified"] else ""))
                for r in records
            ],
        }

        DetailDialog(self, f"Class Session - {info['subject_code']} "
                           f"{info['class_date']}", sections,
                     width=700, height=700,
                     actions=[("Export Sheet", self.export_sheet)]).show()

    def export_sheet(self) -> None:
        if not self._selected_session:
            show_info(self, "No Selection", "Select a class session first.")
            return

        att_session_id = self._selected_session["att_session_id"]
        info = dict(attendance_model.get_session(att_session_id))
        records = [dict(r) for r in attendance_model.get_session_records(att_session_id)]

        ok, message, path = generate_attendance_sheet(
            att_session_id, records, info, session.user)

        if ok:
            show_success(self, "Attendance Sheet Ready", f"{message}\n\nSaved to:\n{path}")
        else:
            show_error(self, "Export Failed", message)

    def unlock_session(self) -> None:
        if not self._selected_session:
            show_info(self, "No Selection", "Select a class session first.")
            return

        record = self._selected_session
        if not record.get("is_locked"):
            show_info(self, "Not Locked", "This session is not locked.")
            return

        reason = ask_reason(
            self, "Unlock Attendance Session",
            f"Unlock attendance for {record['subject_name']} on "
            f"{record['class_date']}?\n\n"
            "Faculty will be able to edit these records again.\n\n"
            "Your name, the timestamp and this reason are recorded permanently "
            "in the audit trail.",
            min_length=10, confirm_text="Unlock", danger=True)

        if not reason:
            return

        ok, message = attendance_model.unlock_session(
            record["att_session_id"], reason, session.user)

        if ok:
            show_success(self, "Session Unlocked", message)
            self.refresh()
        else:
            show_error(self, "Could Not Unlock", message)

    def _show_record_detail(self, row: dict) -> None:
        """Full history of one attendance record, including its audit trail."""
        from core.audit import get_entity_history

        history = get_entity_history("attendance", row.get("attendance_id"))

        sections = {
            "Record": [
                ("Student", f"{row['student_name']} ({row['enrollment_no']})"),
                ("Roll Number", row["roll_no"]),
                ("Subject", f"{row['subject_name']} ({row['subject_code']})"),
                ("Class Date", row["class_date"]),
                ("Status", row["status"]),
                ("Marked By", row["marked_method"]),
                ("Confidence", f"{row['confidence']:.1f}%" if row["confidence"] else "-"),
                ("Marked At", row["marked_time"] or "-"),
                ("Snapshot", row.get("snapshot_path") or "-"),
            ],
            "Correction": [
                ("Modified", "Yes" if row["is_modified"] else "No"),
                ("Reason", row.get("modify_reason") or "-"),
            ],
            f"Audit Trail ({len(history)} entry)": [
                (str(h["timestamp"])[:19],
                 f"{h['username']} - {h['action']}"
                 + (f" ({h['reason']})" if h["reason"] else ""))
                for h in history
            ] or [("-", "No changes recorded")],
        }

        DetailDialog(self, "Attendance Record", sections, width=640, height=580).show()
