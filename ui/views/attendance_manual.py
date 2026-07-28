"""
Manual attendance and attendance correction.

Faculty pick a class, subject and date, then set each student's status by hand.
The same screen doubles as the **correction** tool: reopening a session that
face recognition already filled shows the existing marks, and changing one of
them demands a written reason that is stored in the audit trail.

Locking is enforced here as well as in the model, so a locked session is
visibly read-only rather than silently rejecting edits.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime

import customtkinter as ctk

from config.settings import ATTENDANCE_STATUSES, config
from config.theme import FONTS, SEMANTIC, STATUS_COLORS, color
from core.auth import Permission, session
from core.logger import get_logger
from models import academic, attendance as attendance_model, subject as subject_model
from models import faculty as faculty_model, student as student_model, timetable as timetable_model
from services.report_service import generate_attendance_sheet
from ui.widgets.components import EmptyState, PageHeader, StatCard
from ui.widgets.dialogs import (ask_confirm, ask_reason, show_error, show_info,
                                show_success, show_warning)

logger = get_logger("ui.attendance_manual")


class StudentRow(ctk.CTkFrame):
    """One student line with a segmented status selector."""

    def __init__(self, master, record: dict, on_change, read_only: bool = False):
        super().__init__(master, corner_radius=7, fg_color=color("surface_alt"))

        self.record = record
        self.on_change = on_change
        self.read_only = read_only
        self.status_var = tk.StringVar(value=record["status"])

        self._accent = ctk.CTkFrame(self, width=4, corner_radius=2,
                                    fg_color=STATUS_COLORS.get(record["status"],
                                                               SEMANTIC["neutral"]))
        self._accent.pack(side="left", fill="y", padx=(6, 10), pady=7)

        ctk.CTkLabel(self, text=str(record["roll_no"]), font=FONTS["body_bold"],
                     text_color=color("text"), width=44, anchor="w").pack(
                         side="left", pady=9)

        details = ctk.CTkFrame(self, fg_color="transparent")
        details.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(details, text=record["full_name"], font=FONTS["body"],
                     text_color=color("text"), anchor="w").pack(fill="x")

        meta = f"{record['enrollment_no']}"
        if record.get("marked_method") and record["marked_method"] != "Auto (Absent)":
            meta += f"   |   {record['marked_method']}"
        if record.get("confidence"):
            meta += f" ({record['confidence']:.0f}%)"
        if record.get("marked_time"):
            meta += f"   |   {record['marked_time']}"
        if record.get("is_modified"):
            meta += "   |   EDITED"

        self.meta_label = ctk.CTkLabel(details, text=meta, font=(FONTS["small"][0], 10),
                                       text_color=color("text_muted"), anchor="w")
        self.meta_label.pack(fill="x")

        # ---- status buttons ----------------------------------------------
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(side="right", padx=10, pady=7)

        self._buttons: dict[str, ctk.CTkButton] = {}
        short = {"Present": "P", "Absent": "A", "Late": "L",
                 "Leave": "LV", "Medical Leave": "ML"}

        for status in ATTENDANCE_STATUSES:
            button = ctk.CTkButton(
                buttons, text=short[status], width=42, height=28, corner_radius=6,
                font=FONTS["small_bold"],
                command=lambda s=status: self._set_status(s),
                state="disabled" if read_only else "normal")
            button.pack(side="left", padx=2)
            self._buttons[status] = button

        self._paint()

    def _paint(self) -> None:
        current = self.status_var.get()
        for status, button in self._buttons.items():
            active = status == current
            button.configure(
                fg_color=STATUS_COLORS[status] if active else "transparent",
                text_color="#FFFFFF" if active else color("text_muted"),
                border_width=0 if active else 1,
                border_color=color("border"),
                hover_color=STATUS_COLORS[status] if active else color("border"))
        self._accent.configure(fg_color=STATUS_COLORS.get(current, SEMANTIC["neutral"]))

    def _set_status(self, status: str) -> None:
        if self.read_only or status == self.status_var.get():
            return
        previous = self.status_var.get()
        if self.on_change(self.record, status, previous):
            self.status_var.set(status)
            self._paint()

    def set_status_silently(self, status: str) -> None:
        """Update after a bulk action, without re-triggering the callback."""
        self.status_var.set(status)
        self._paint()

    def matches(self, term: str) -> bool:
        if not term:
            return True
        term = term.lower()
        return (term in str(self.record["full_name"]).lower()
                or term in str(self.record["roll_no"]).lower()
                or term in str(self.record["enrollment_no"]).lower())


class ManualAttendanceView(ctk.CTkFrame):
    """Mark or correct attendance without the camera."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app

        self.att_session_id: int | None = None
        self.session_info: dict | None = None
        self.rows: list[StudentRow] = []
        self.read_only = False

        self._build()
        self._load_filters()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Manual Attendance",
            subtitle="Mark attendance by hand, or correct records set by face recognition",
            icon="✓")
        header.pack(fill="x", padx=18, pady=(14, 10))
        self.sheet_button = header.add_button("Attendance Sheet", self._export_sheet,
                                              width=160, fg_color="transparent",
                                              hover_color=color("surface_alt"))
        self.sheet_button.configure(state="disabled")

        # ---- selectors ----------------------------------------------------
        selector_card = ctk.CTkFrame(self, height=0, corner_radius=10, fg_color=color("surface"),
                                     border_width=1, border_color=color("border"))
        selector_card.pack(fill="x", padx=18, pady=(0, 10))

        inner = ctk.CTkFrame(selector_card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)

        self._selectors: dict[str, dict] = {}

        def add_selector(key: str, label: str, width: int, command=None) -> None:
            holder = ctk.CTkFrame(inner, fg_color="transparent")
            holder.pack(side="left", padx=(0, 12))
            ctk.CTkLabel(holder, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), anchor="w").pack(fill="x")
            variable = tk.StringVar(value="-")
            widget = ctk.CTkOptionMenu(holder, variable=variable, values=["-"],
                                       width=width, height=34, corner_radius=7,
                                       font=FONTS["body"], command=command)
            widget.pack()
            self._selectors[key] = {"var": variable, "widget": widget, "map": {}}

        add_selector("branch", "Branch", 180, lambda _v: self._branch_changed())
        add_selector("semester", "Semester", 130, lambda _v: self._class_changed())
        add_selector("section", "Section", 90, lambda _v: self._class_changed())
        add_selector("subject", "Subject", 240)

        date_holder = ctk.CTkFrame(inner, fg_color="transparent")
        date_holder.pack(side="left", padx=(0, 12))
        ctk.CTkLabel(date_holder, text="Date", font=FONTS["small_bold"],
                     text_color=color("text_muted"), anchor="w").pack(fill="x")
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        ctk.CTkEntry(date_holder, textvariable=self.date_var, width=120, height=34,
                     corner_radius=7, font=FONTS["body"]).pack()

        load_holder = ctk.CTkFrame(inner, fg_color="transparent")
        load_holder.pack(side="left")
        ctk.CTkLabel(load_holder, text=" ", font=FONTS["small_bold"]).pack(fill="x")
        ctk.CTkButton(load_holder, text="Load Class", command=self.load_class,
                      width=130, height=34, corner_radius=7,
                      font=FONTS["body_bold"]).pack()

        # ---- toolbar ------------------------------------------------------
        self.toolbar = ctk.CTkFrame(self, height=0, corner_radius=10, fg_color=color("surface"),
                                    border_width=1, border_color=color("border"))

        toolbar_inner = ctk.CTkFrame(self.toolbar, fg_color="transparent")
        toolbar_inner.pack(fill="x", padx=16, pady=11)

        self.info_label = ctk.CTkLabel(toolbar_inner, text="", font=FONTS["small"],
                                       text_color=color("text_muted"), anchor="w",
                                       justify="left")
        self.info_label.pack(side="left", fill="x", expand=True)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter_rows())
        ctk.CTkEntry(toolbar_inner, textvariable=self.search_var, width=200, height=32,
                     corner_radius=7, font=FONTS["body"],
                     placeholder_text="Search roll no or name").pack(side="left", padx=(0, 10))

        self.filter_var = tk.StringVar(value="All")
        ctk.CTkOptionMenu(toolbar_inner, variable=self.filter_var,
                          values=["All"] + ATTENDANCE_STATUSES, width=130, height=32,
                          corner_radius=7, font=FONTS["body"],
                          command=lambda _v: self._filter_rows()).pack(side="left", padx=(0, 10))

        self.all_present_btn = ctk.CTkButton(
            toolbar_inner, text="All Present", command=lambda: self._mark_all("Present"),
            width=110, height=32, corner_radius=7, font=FONTS["small_bold"],
            fg_color=SEMANTIC["success"], hover_color="#15803D")
        self.all_present_btn.pack(side="left", padx=(0, 6))

        self.all_absent_btn = ctk.CTkButton(
            toolbar_inner, text="All Absent", command=lambda: self._mark_all("Absent"),
            width=110, height=32, corner_radius=7, font=FONTS["small_bold"],
            fg_color=SEMANTIC["danger"], hover_color="#B91C1C")
        self.all_absent_btn.pack(side="left", padx=(0, 6))

        self.lock_btn = ctk.CTkButton(
            toolbar_inner, text="Lock", command=self._toggle_lock, width=100, height=32,
            corner_radius=7, font=FONTS["small_bold"], fg_color="transparent",
            border_width=1, border_color=color("border"), text_color=color("text"),
            hover_color=color("surface_alt"))
        self.lock_btn.pack(side="left")

        # ---- counters -----------------------------------------------------
        self.counters = ctk.CTkFrame(self, fg_color="transparent")
        for index in range(6):
            self.counters.grid_columnconfigure(index, weight=1, uniform="counters")

        self.tiles = {}
        for index, (label, icon, accent) in enumerate([
                ("Total", "☺", SEMANTIC["info"]),
                ("Present", "✓", SEMANTIC["success"]),
                ("Absent", "✗", SEMANTIC["danger"]),
                ("Late", "◷", SEMANTIC["warning"]),
                ("Leave", "⎙", STATUS_COLORS["Leave"]),
                ("Medical", "✚", STATUS_COLORS["Medical Leave"])]):
            tile = StatCard(self.counters, label, "0", icon, accent)
            tile.grid(row=0, column=index, sticky="ew", padx=3)
            self.tiles[label] = tile

        # ---- roster list ---------------------------------------------------
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")

        self.empty_state = EmptyState(
            self, "▤", "No class loaded",
            "Choose a branch, semester, section, subject and date, "
            "then press 'Load Class' to begin marking attendance.")
        self.empty_state.pack(fill="both", expand=True, padx=22, pady=22)

    # ==================================================================
    # Filters
    # ==================================================================
    def _set_options(self, key: str, mapping: dict) -> None:
        spec = self._selectors[key]
        spec["map"] = mapping
        names = list(mapping) or ["-"]
        spec["widget"].configure(values=names)
        spec["var"].set(names[0])

    def _selected(self, key: str):
        spec = self._selectors[key]
        return spec["map"].get(spec["var"].get())

    def _load_filters(self) -> None:
        branches = academic.get_branches()
        if session.is_faculty and session.linked_id:
            my_subjects = faculty_model.get_assigned_subjects(session.linked_id)
            allowed = {s["branch_id"] for s in my_subjects}
            if allowed:
                branches = [b for b in branches if b["branch_id"] in allowed]

        self._set_options("branch", {b["branch_name"]: b["branch_id"] for b in branches})
        self._set_options("section",
                          {s["section_name"]: s["section_id"] for s in academic.get_sections()})
        self._branch_changed()

    def _branch_changed(self) -> None:
        self._set_options("semester",
                          {s["semester_name"]: s["semester_id"]
                           for s in academic.get_semesters()})
        self._class_changed()

    def _class_changed(self) -> None:
        branch_id, semester_id = self._selected("branch"), self._selected("semester")
        if not (branch_id and semester_id):
            self._set_options("subject", {})
            return

        subjects = subject_model.get_class_subjects(branch_id, semester_id)
        if session.is_faculty and session.linked_id:
            subjects = [s for s in subjects if s["faculty_id"] == session.linked_id]

        self._set_options("subject",
                          {f"{s['subject_name']} ({s['subject_code']})": s["subject_id"]
                           for s in subjects})

    # ==================================================================
    # Loading a class
    # ==================================================================
    def load_class(self) -> None:
        subject_id = self._selected("subject")
        branch_id = self._selected("branch")
        semester_id = self._selected("semester")
        section_id = self._selected("section")
        class_date = self.date_var.get().strip()

        if not subject_id:
            show_warning(self, "No Subject", "Please select a subject.")
            return

        try:
            datetime.strptime(class_date, "%Y-%m-%d")
        except ValueError:
            show_warning(self, "Invalid Date", "Enter the date as YYYY-MM-DD.")
            return

        # An existing session for this class/date/subject is reopened for editing.
        existing = attendance_model.search_sessions(
            subject_id=subject_id, section_id=section_id,
            from_date=class_date, to_date=class_date)

        if existing:
            self.att_session_id = existing[0]["att_session_id"]
        else:
            holiday, holiday_name = academic.is_holiday(class_date)
            if holiday:
                proceed = ask_confirm(
                    self, "Holiday",
                    f"{class_date} is a holiday ({holiday_name}).\n\n"
                    "Attendance is normally disabled on holidays.\n\n"
                    "Open a session anyway? This will be recorded in the audit trail.",
                    confirm_text="Open Anyway")
                if not proceed:
                    return

            subject = subject_model.get_subject(subject_id)
            faculty_id = session.linked_id if session.is_faculty else subject["faculty_id"]

            ok, message, att_session_id = attendance_model.open_session(
                subject_id=subject_id, branch_id=branch_id, semester_id=semester_id,
                section_id=section_id, faculty_id=faculty_id, class_date=class_date,
                mode="Manual", user=session.user, allow_holiday=True)

            if not ok:
                show_error(self, "Could Not Open Class", message)
                return
            self.att_session_id = att_session_id
            self.app.set_status(message, "success")

        self._render_roster()

    def _render_roster(self) -> None:
        self.session_info = dict(attendance_model.get_session(self.att_session_id))
        records = [dict(r) for r in attendance_model.get_session_records(self.att_session_id)]

        self.read_only = bool(self.session_info["is_locked"])

        # Swap the empty state for the working UI.
        self.empty_state.pack_forget()
        self.toolbar.pack(fill="x", padx=18, pady=(0, 9))
        self.counters.pack(fill="x", padx=18, pady=(0, 9))
        self.list_frame.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        for widget in self.list_frame.winfo_children():
            widget.destroy()
        self.rows.clear()

        for record in records:
            row = StudentRow(self.list_frame, record, self._status_changed,
                             read_only=self.read_only)
            row.pack(fill="x", pady=2)
            self.rows.append(row)

        info = (f"{self.session_info['subject_name']} ({self.session_info['subject_code']})   |   "
                f"{self.session_info['branch_code']} {self.session_info['semester_name']}"
                f" Sec {self.session_info['section_name'] or '-'}   |   "
                f"{self.session_info['class_date']}   |   "
                f"Faculty: {self.session_info['faculty_name'] or '-'}")
        if self.read_only:
            info += "\nLOCKED - this session is read-only. An administrator must unlock it to edit."

        self.info_label.configure(text=info)

        can_lock = session.can(Permission.LOCK_ATTENDANCE)
        can_unlock = session.can(Permission.UNLOCK_ATTENDANCE)

        self.lock_btn.configure(
            text="Unlock" if self.read_only else "Lock",
            state="normal" if (can_unlock if self.read_only else can_lock) else "disabled")
        for button in (self.all_present_btn, self.all_absent_btn):
            button.configure(state="disabled" if self.read_only else "normal")

        self.sheet_button.configure(state="normal")
        self._update_counters()
        self._filter_rows()

    # ==================================================================
    # Marking
    # ==================================================================
    def _status_changed(self, record: dict, new_status: str, old_status: str) -> bool:
        """Return True when the change was accepted and should be painted."""
        if self.read_only:
            show_warning(self, "Session Locked",
                         "This attendance session is locked and cannot be edited.")
            return False

        reason = ""
        # Overriding a face-recognition result requires justification.
        if record.get("marked_method") == "Face Recognition":
            reason = ask_reason(
                self, "Reason for Correction",
                f"{record['full_name']} was marked '{old_status}' by face recognition "
                f"({record.get('confidence') or 0:.0f}% confidence).\n\n"
                f"You are changing this to '{new_status}'. "
                "Please state why - this is recorded in the audit trail.",
                min_length=10, confirm_text="Apply Change")
            if not reason:
                return False

        ok, message = attendance_model.mark_manual(
            self.att_session_id, record["student_id"], new_status,
            reason=reason, user=session.user)

        if not ok:
            show_error(self, "Could Not Update", message)
            return False

        record["status"] = new_status
        record["marked_method"] = "Manual"
        if reason:
            record["is_modified"] = 1

        self._update_counters()
        self.app.set_status(message, "success")
        return True

    def _mark_all(self, status: str) -> None:
        if self.read_only:
            return

        already_marked = sum(1 for r in self.rows
                             if r.record.get("marked_method") not in (None, "Auto (Absent)"))

        only_unmarked = True
        if already_marked:
            overwrite = ask_confirm(
                self, f"Mark All {status}",
                f"{already_marked} student(s) already have a recorded status "
                "(from face recognition, leave, or a manual edit).\n\n"
                f"Choose 'Only Unmarked' to set the remaining "
                f"{len(self.rows) - already_marked} student(s) to {status}, "
                "or 'Overwrite All' to replace every record.",
                confirm_text="Overwrite All", cancel_text="Only Unmarked")
            only_unmarked = not overwrite

            if not only_unmarked:
                reason = ask_reason(
                    self, "Reason for Bulk Override",
                    f"You are overwriting {len(self.rows)} existing record(s) "
                    f"with '{status}'. Please state why.",
                    min_length=10, confirm_text="Apply", danger=True)
                if not reason:
                    return
            else:
                reason = f"Bulk mark unmarked students as {status}"
        else:
            reason = f"Bulk mark all as {status}"

        ok, message, affected = attendance_model.mark_all(
            self.att_session_id, status, only_unmarked=only_unmarked,
            reason=reason, user=session.user)

        if not ok:
            show_error(self, "Could Not Update", message)
            return

        # Re-read so every row reflects what the database actually holds.
        self._render_roster()
        self.app.set_status(message, "success")

    def _toggle_lock(self) -> None:
        if self.read_only:
            reason = ask_reason(
                self, "Unlock Attendance",
                "Unlocking allows this attendance session to be edited again.\n\n"
                "Please state why it is being unlocked - this is permanently "
                "recorded in the audit trail.",
                min_length=10, confirm_text="Unlock", danger=True)
            if not reason:
                return
            ok, message = attendance_model.unlock_session(
                self.att_session_id, reason, session.user)
        else:
            if not ask_confirm(
                    self, "Lock Attendance",
                    "Lock this attendance session?\n\n"
                    "No further edits will be possible. Only an administrator "
                    "can unlock it.", confirm_text="Lock"):
                return
            ok, message = attendance_model.lock_session(self.att_session_id, session.user)

        if ok:
            show_success(self, "Done", message)
            self._render_roster()
        else:
            show_error(self, "Failed", message)

    # ==================================================================
    # Display helpers
    # ==================================================================
    def _update_counters(self) -> None:
        counts = {status: 0 for status in ATTENDANCE_STATUSES}
        for row in self.rows:
            counts[row.status_var.get()] = counts.get(row.status_var.get(), 0) + 1

        self.tiles["Total"].update_value(str(len(self.rows)))
        self.tiles["Present"].update_value(str(counts["Present"]))
        self.tiles["Absent"].update_value(str(counts["Absent"]))
        self.tiles["Late"].update_value(str(counts["Late"]))
        self.tiles["Leave"].update_value(str(counts["Leave"]))
        self.tiles["Medical"].update_value(str(counts["Medical Leave"]))

    def _filter_rows(self) -> None:
        term = self.search_var.get().strip()
        status_filter = self.filter_var.get()

        for row in self.rows:
            visible = row.matches(term)
            if visible and status_filter != "All":
                visible = row.status_var.get() == status_filter

            if visible:
                row.pack(fill="x", pady=2)
            else:
                row.pack_forget()

    def _export_sheet(self) -> None:
        if not self.att_session_id:
            show_info(self, "No Class Loaded", "Load a class first.")
            return

        records = attendance_model.get_session_records(self.att_session_id)
        ok, message, path = generate_attendance_sheet(
            self.att_session_id, [dict(r) for r in records],
            self.session_info or {}, session.user)

        if ok:
            show_success(self, "Attendance Sheet Ready", f"{message}\n\nSaved to:\n{path}")
        else:
            show_error(self, "Export Failed", message)

    # ==================================================================
    def refresh(self) -> None:
        if self.att_session_id:
            self._render_roster()
        else:
            self._load_filters()
