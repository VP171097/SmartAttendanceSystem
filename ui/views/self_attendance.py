"""
Self-marked attendance.

Role-aware, because the same feature has two halves:

*   **Student**  -- see today's classes and tap "Mark Me Present".  This raises
    a request; it is *not* attendance until the faculty approves it, and the
    screen says so plainly so nobody assumes they are marked.
*   **Faculty / Admin** -- a verification queue.  Approve (optionally as Late),
    reject with a reason, or handle a whole batch at once.

Faculty also get a one-click roster here, so marking a student present takes a
single tap without going through the full Manual Attendance flow.
"""

from __future__ import annotations

from datetime import date, datetime

import customtkinter as ctk

from config.settings import config
from config.theme import FONTS, SEMANTIC, color
from core.auth import Permission, session
from core.logger import get_logger
from models import academic, self_attendance as self_model
from models import subject as subject_model, timetable as timetable_model
from ui.widgets.components import (EmptyState, FilterBar, PageHeader, SectionCard,
                                   StatCard)
from ui.widgets.dialogs import (DetailDialog, ask_confirm, ask_reason, show_error,
                                show_info, show_success, show_warning)
from ui.widgets.table import DataTable, column, dash_format, date_format

logger = get_logger("ui.self_attendance")

STATE_COLORS = {
    "Present": SEMANTIC["success"], "Late": SEMANTIC["warning"],
    "In Progress": SEMANTIC["info"], "Upcoming": SEMANTIC["neutral"],
    "Ended": SEMANTIC["neutral"], "Scheduled": SEMANTIC["neutral"],
    "Request Pending": SEMANTIC["warning"],
    "Request Approved": SEMANTIC["success"],
    "Request Rejected": SEMANTIC["danger"],
}


class SelfAttendanceView(ctk.CTkFrame):
    """Student self-marking and faculty verification."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._is_student = session.is_student
        self._selected_request: dict | None = None

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        if self._is_student:
            self._build_student()
        else:
            self._build_faculty()

    # ==================================================================
    # Student
    # ==================================================================
    def _build_student(self) -> None:
        header = PageHeader(
            self, title="Mark My Attendance",
            subtitle="Mark yourself present for today's classes - your teacher verifies it",
            icon="✓")
        header.pack(fill="x", padx=18, pady=(14, 10))
        header.add_button("Refresh", self.refresh, width=110,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))

        # ---- how it works banner ------------------------------------------
        banner = ctk.CTkFrame(self, height=0, corner_radius=9, fg_color=color("surface"),
                              border_width=1, border_color=SEMANTIC["info"])
        banner.pack(fill="x", padx=18, pady=(0, 9))

        inner = ctk.CTkFrame(banner, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=11)

        ctk.CTkLabel(inner, text="i", font=(FONTS["title"][0], 15, "bold"),
                     text_color="#FFFFFF", fg_color=SEMANTIC["info"],
                     corner_radius=14, width=28, height=28).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(
            inner,
            text=("Marking yourself present creates a request for your teacher to "
                  "verify. It is NOT counted as attendance until they approve it. "
                  "Your teacher can also mark you present directly, and face "
                  "recognition in class marks you automatically."),
            font=FONTS["small"], text_color=color("text_muted"),
            anchor="w", justify="left", wraplength=980).pack(side="left", fill="x",
                                                             expand=True)

        # ---- tiles ---------------------------------------------------------
        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(4):
            tiles.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tiles = {}
        for index, (key, label, icon, accent) in enumerate([
                ("classes", "Classes Today", "◷", SEMANTIC["info"]),
                ("Pending", "Awaiting Approval", "⏳", SEMANTIC["warning"]),
                ("Approved", "Approved", "✓", SEMANTIC["success"]),
                ("Rejected", "Rejected", "✗", SEMANTIC["danger"])]):
            tile = StatCard(tiles, label, "0", icon, accent)
            tile.grid(row=0, column=index, sticky="ew", padx=4)
            self.tiles[key] = tile

        # ---- today's classes -------------------------------------------------
        self.classes_card = SectionCard(
            self, "Today's Classes",
            f"{datetime.now():%A, %d %B %Y}")
        self.classes_card.pack(fill="both", expand=True, padx=18, pady=(0, 9))

        self.classes_body = ctk.CTkScrollableFrame(self.classes_card.body,
                                                   fg_color="transparent")
        self.classes_body.pack(fill="both", expand=True)

        # ---- my request history -----------------------------------------------
        history_card = SectionCard(self, "My Requests",
                                   "Every self-marking request you have raised")
        history_card.pack(fill="x", padx=18, pady=(0, 14))

        self.history_table = DataTable(
            history_card.body,
            columns=[
                column("class_date", "Date", 100, format=date_format),
                column("slot_start", "Time", 70, "center", format=dash_format),
                column("subject_code", "Code", 80),
                column("subject_name", "Subject", 190, stretch=True),
                column("faculty_name", "Faculty", 165, format=dash_format),
                column("status", "Status", 100, "center"),
                column("applied_status", "Marked As", 95, "center", format=dash_format),
                column("review_remarks", "Teacher's Remark", 230, format=dash_format),
            ],
            on_select=lambda row: setattr(self, "_selected_request", row),
            on_double_click=self._show_request_detail,
            height=7)
        self.history_table.pack(fill="both", expand=True)

        withdraw = ctk.CTkFrame(history_card.body, fg_color="transparent")
        withdraw.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(withdraw, text="Withdraw Selected Request",
                      command=self.withdraw_request, width=210, height=32,
                      corner_radius=7, font=FONTS["small_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt")).pack(side="left")
        ctk.CTkLabel(withdraw,
                     text="Only requests still awaiting approval can be withdrawn.",
                     font=FONTS["small"],
                     text_color=color("text_muted")).pack(side="left", padx=12)

    def _render_student_classes(self) -> None:
        for widget in self.classes_body.winfo_children():
            widget.destroy()

        if not session.linked_id:
            EmptyState(self.classes_body, "!", "No student record linked",
                       "This login is not attached to a student record. "
                       "Ask the administrator to link it."
                       ).pack(fill="both", expand=True, pady=30)
            return

        classes = self_model.get_today_classes_for_student(session.linked_id)
        self.tiles["classes"].update_value(str(len(classes)))

        if not classes:
            EmptyState(
                self.classes_body, "◷", "No classes scheduled today",
                "Your timetable has no classes for today. Enjoy the break!\n\n"
                "If you believe this is wrong, ask your class teacher to check "
                "the timetable."
            ).pack(fill="both", expand=True, pady=30)
            return

        for entry in classes:
            self._render_class_row(entry)

    def _render_class_row(self, entry: dict) -> None:
        accent = STATE_COLORS.get(entry["state"], SEMANTIC["neutral"])

        row = ctk.CTkFrame(self.classes_body, height=0, corner_radius=8,
                           fg_color=color("surface_alt"))
        row.pack(fill="x", pady=4)

        ctk.CTkFrame(row, width=5, corner_radius=3, fg_color=accent).pack(
            side="left", fill="y", padx=(7, 12), pady=10)

        # ---- time --------------------------------------------------------
        time_box = ctk.CTkFrame(row, fg_color="transparent")
        time_box.pack(side="left", pady=11)
        ctk.CTkLabel(time_box, text=entry["start_time"], font=FONTS["body_bold"],
                     text_color=color("text")).pack()
        ctk.CTkLabel(time_box, text=entry["end_time"], font=(FONTS["small"][0], 10),
                     text_color=color("text_muted")).pack()

        # ---- subject ------------------------------------------------------
        details = ctk.CTkFrame(row, fg_color="transparent")
        details.pack(side="left", fill="x", expand=True, padx=16)

        ctk.CTkLabel(details,
                     text=f"{entry['subject_name']} ({entry['subject_code']})",
                     font=FONTS["body_bold"], text_color=color("text"),
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(details,
                     text=(f"{entry['faculty_name'] or 'Faculty not assigned'}"
                           f"   |   Room {entry['room_no'] or '-'}"),
                     font=FONTS["small"], text_color=color("text_muted"),
                     anchor="w").pack(fill="x")

        if entry.get("blocked_reason"):
            ctk.CTkLabel(details, text=entry["blocked_reason"],
                         font=(FONTS["small"][0], 10),
                         text_color=(SEMANTIC["danger"]
                                     if entry["request_status"] == "Rejected"
                                     else color("text_muted")),
                         anchor="w", wraplength=520,
                         justify="left").pack(fill="x", pady=(2, 0))

        if entry.get("review_remarks"):
            ctk.CTkLabel(details, text=f"Teacher: {entry['review_remarks']}",
                         font=(FONTS["small"][0], 10),
                         text_color=color("text_muted"), anchor="w",
                         wraplength=520, justify="left").pack(fill="x")

        # ---- state badge ---------------------------------------------------
        ctk.CTkLabel(row, text=f"  {entry['state']}  ", font=FONTS["small_bold"],
                     text_color="#FFFFFF", fg_color=accent, corner_radius=9,
                     height=24).pack(side="right", padx=(8, 14))

        # ---- action -------------------------------------------------------
        if entry["can_mark"]:
            ctk.CTkButton(
                row, text="✓  Mark Me Present", width=175, height=36, corner_radius=7,
                font=FONTS["body_bold"], fg_color=SEMANTIC["success"],
                hover_color="#15803D",
                command=lambda e=entry: self.mark_present(e)).pack(side="right", padx=4)
        else:
            ctk.CTkButton(
                row, text="Not Available", width=175, height=36, corner_radius=7,
                font=FONTS["small_bold"], state="disabled",
                fg_color="transparent", border_width=1,
                border_color=color("border"),
                text_color=color("text_muted")).pack(side="right", padx=4)

    def mark_present(self, entry: dict) -> None:
        if not config.get("allow_self_attendance", True):
            show_warning(self, "Self-Marking Disabled",
                         "The administrator has turned off student self-marking.\n\n"
                         "Your teacher will mark attendance for you.")
            return

        confirmed = ask_confirm(
            self, "Mark Yourself Present",
            f"Submit an attendance request for:\n\n"
            f"  {entry['subject_name']} ({entry['subject_code']})\n"
            f"  {entry['start_time']} - {entry['end_time']}\n"
            f"  {entry['faculty_name'] or 'Faculty not assigned'}\n\n"
            "This is sent to your teacher for verification. It will NOT count as "
            "attendance until they approve it.\n\n"
            "Submitting a false attendance request is a disciplinary matter, and "
            "every request is permanently recorded with your name.",
            confirm_text="Submit Request")

        if not confirmed:
            return

        ok, message, _ = self_model.request_self_attendance(
            student_id=session.linked_id,
            subject_id=entry["subject_id"],
            class_date=datetime.now().strftime("%Y-%m-%d"),
            timetable_id=entry.get("timetable_id"),
            slot_start=entry.get("start_time"),
            slot_end=entry.get("end_time"),
            user=session.user)

        if ok:
            show_success(self, "Request Submitted", message)
            self.refresh()
        else:
            show_error(self, "Could Not Submit", message)

    def withdraw_request(self) -> None:
        if not self._selected_request:
            show_info(self, "No Selection",
                      "Select one of your requests from the list first.")
            return

        request = self._selected_request
        if request.get("status") != "Pending":
            show_info(self, "Cannot Withdraw",
                      f"This request has already been {request['status'].lower()} "
                      "by your teacher.")
            return

        if not ask_confirm(self, "Withdraw Request",
                           f"Withdraw your attendance request for "
                           f"{request['subject_name']} on {request['class_date']}?",
                           confirm_text="Withdraw"):
            return

        ok, message = self_model.withdraw_request(
            request["request_id"], session.linked_id, session.user)

        if ok:
            show_success(self, "Request Withdrawn", message)
            self.refresh()
        else:
            show_error(self, "Could Not Withdraw", message)

    # ==================================================================
    # Faculty / Admin
    # ==================================================================
    def _build_faculty(self) -> None:
        header = PageHeader(
            self, title="Attendance Verification",
            subtitle="Approve or reject attendance that students marked themselves",
            icon="⚖")
        header.pack(fill="x", padx=18, pady=(14, 10))
        header.add_button("Refresh", self.refresh, width=110,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))

        banner = ctk.CTkFrame(self, height=0, corner_radius=9, fg_color=color("surface"),
                              border_width=1, border_color=SEMANTIC["warning"])
        banner.pack(fill="x", padx=18, pady=(0, 9))
        inner = ctk.CTkFrame(banner, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=11)

        ctk.CTkLabel(inner, text="!", font=(FONTS["title"][0], 15, "bold"),
                     text_color="#FFFFFF", fg_color=SEMANTIC["warning"],
                     corner_radius=14, width=28, height=28).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            inner,
            text=("These students marked themselves present. Nothing is counted "
                  "until you approve it. Approving writes the attendance record and "
                  "logs your decision; rejecting leaves attendance untouched. "
                  "If the class had no attendance session, approving opens one."),
            font=FONTS["small"], text_color=color("text_muted"), anchor="w",
            justify="left", wraplength=980).pack(side="left", fill="x", expand=True)

        # ---- tiles -----------------------------------------------------------
        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(4):
            tiles.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tiles = {}
        for index, (key, label, icon, accent) in enumerate([
                ("Pending", "Awaiting Your Review", "⏳", SEMANTIC["warning"]),
                ("Approved", "Approved", "✓", SEMANTIC["success"]),
                ("Rejected", "Rejected", "✗", SEMANTIC["danger"]),
                ("total", "Total Requests", "≡", SEMANTIC["info"])]):
            tile = StatCard(tiles, label, "0", icon, accent)
            tile.grid(row=0, column=index, sticky="ew", padx=4)
            self.tiles[key] = tile

        # ---- filters ------------------------------------------------------------
        self.filters = FilterBar(self, on_change=self.refresh,
                                 search_placeholder="Student name, enrollment, subject...")
        self.filters.pack(fill="x", padx=18, pady=(0, 9))

        self.filters.add_filter("status", "Status",
                                {s: s for s in ("Pending", "Approved", "Rejected")},
                                width=140)
        self.filters.add_filter(
            "branch_id", "Branch",
            {b["branch_name"]: b["branch_id"] for b in academic.get_branches()},
            width=180)
        self.filters.add_filter(
            "semester_id", "Semester",
            {s["semester_name"]: s["semester_id"] for s in academic.get_semesters()},
            width=130)
        self.filters.add_search(230)
        self.filters.add_button("Reset", self.filters.reset, width=75,
                                fg_color="transparent", border_width=1,
                                border_color=color("border"), text_color=color("text"),
                                hover_color=color("surface_alt"))

        # ---- action bar ------------------------------------------------------
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(0, 8))

        ctk.CTkButton(actions, text="✓  Approve Selected", command=self.approve_selected,
                      width=175, height=36, corner_radius=7, font=FONTS["body_bold"],
                      fg_color=SEMANTIC["success"],
                      hover_color="#15803D").pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Approve as Late",
                      command=lambda: self.approve_selected(mark_late=True),
                      width=155, height=36, corner_radius=7, font=FONTS["small_bold"],
                      fg_color=SEMANTIC["warning"],
                      hover_color="#D97706").pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="✗  Reject Selected", command=self.reject_selected,
                      width=165, height=36, corner_radius=7, font=FONTS["body_bold"],
                      fg_color=SEMANTIC["danger"],
                      hover_color="#B91C1C").pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="View Details", command=self._show_selected_detail,
                      width=130, height=36, corner_radius=7, font=FONTS["small_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt")).pack(side="left", padx=(0, 8))

        ctk.CTkFrame(actions, width=1, fg_color=color("border")).pack(
            side="left", fill="y", padx=10)

        ctk.CTkButton(actions, text="Approve All Pending",
                      command=lambda: self.bulk_review(True),
                      width=170, height=36, corner_radius=7, font=FONTS["small_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=SEMANTIC["success"],
                      text_color=SEMANTIC["success"],
                      hover_color=color("surface_alt")).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Reject All Pending",
                      command=lambda: self.bulk_review(False),
                      width=165, height=36, corner_radius=7, font=FONTS["small_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=SEMANTIC["danger"], text_color=SEMANTIC["danger"],
                      hover_color=color("surface_alt")).pack(side="left")

        # ---- queue -------------------------------------------------------------
        self.table = DataTable(
            self,
            columns=[
                column("requested_at", "Requested", 140),
                column("enrollment_no", "Enrollment No", 115),
                column("student_name", "Student", 170, stretch=True),
                column("roll_no", "Roll", 50, "center"),
                column("branch_code", "Branch", 70, "center"),
                column("semester_name", "Semester", 100),
                column("section_name", "Sec", 45, "center", format=dash_format),
                column("subject_code", "Code", 75),
                column("subject_name", "Subject", 165),
                column("class_date", "Class Date", 100, format=date_format),
                column("slot_start", "Time", 65, "center", format=dash_format),
                column("status", "Status", 90, "center"),
                column("applied_status", "Marked", 80, "center", format=dash_format),
            ],
            on_select=lambda row: setattr(self, "_selected_request", row),
            on_double_click=self._show_request_detail,
            height=15)
        self.table.pack(fill="both", expand=True, padx=18, pady=(0, 14))

    # ==================================================================
    def approve_selected(self, mark_late: bool = False) -> None:
        if not self._require_pending():
            return

        request = self._selected_request
        status_word = "Late" if mark_late else "Present"

        if not ask_confirm(
                self, f"Approve as {status_word}",
                f"Mark {request['student_name']} ({request['enrollment_no']}) as "
                f"{status_word}?\n\n"
                f"  Subject : {request['subject_name']} ({request['subject_code']})\n"
                f"  Date    : {request['class_date']}\n"
                f"  Time    : {request['slot_start'] or '-'}\n\n"
                "This writes the attendance record and logs your approval in the "
                "audit trail.",
                confirm_text=f"Approve as {status_word}"):
            return

        ok, message = self_model.approve_request(
            request["request_id"],
            remarks=f"Verified by {session.full_name}",
            mark_late=mark_late, user=session.user)

        if ok:
            show_success(self, "Attendance Approved", message)
            self.refresh()
            self.app.invalidate("dashboard", "attendance_manual", "attendance_records")
        else:
            show_error(self, "Could Not Approve", message)

    def reject_selected(self) -> None:
        if not self._require_pending():
            return

        request = self._selected_request
        remarks = ask_reason(
            self, "Reject Attendance Request",
            f"Reject the request from {request['student_name']} "
            f"({request['enrollment_no']})?\n\n"
            f"  Subject : {request['subject_name']}\n"
            f"  Date    : {request['class_date']}\n\n"
            "Their attendance is left unchanged. The student will see your "
            "reason, so be specific - for example 'You were not in class'.",
            min_length=10, confirm_text="Reject Request", danger=True)

        if not remarks:
            return

        ok, message = self_model.reject_request(
            request["request_id"], remarks, session.user)

        if ok:
            show_success(self, "Request Rejected", message)
            self.refresh()
        else:
            show_error(self, "Could Not Reject", message)

    def bulk_review(self, approve: bool) -> None:
        pending = [r for r in self.table.all_rows() if r.get("status") == "Pending"]

        if not pending:
            show_info(self, "Nothing Pending",
                      "There are no pending requests in the current view.")
            return

        verb = "Approve" if approve else "Reject"
        names = "\n".join(f"  - {r['student_name']} ({r['subject_code']}, "
                          f"{r['class_date']})" for r in pending[:10])
        if len(pending) > 10:
            names += f"\n  ... and {len(pending) - 10} more"

        if approve:
            if not ask_confirm(
                    self, "Approve All Pending",
                    f"Approve {len(pending)} pending request(s)?\n\n{names}\n\n"
                    "Each will be marked Present and written to the audit trail.",
                    confirm_text=f"Approve {len(pending)}"):
                return
            remarks = f"Bulk approved by {session.full_name}"
        else:
            remarks = ask_reason(
                self, "Reject All Pending",
                f"Reject {len(pending)} pending request(s)?\n\n{names}\n\n"
                "Please give a reason - every student will see it.",
                min_length=10, confirm_text=f"Reject {len(pending)}", danger=True)
            if not remarks:
                return

        ok, message, count = self_model.bulk_review(
            [r["request_id"] for r in pending], approve, remarks, session.user)

        if ok:
            show_success(self, f"{verb}ed", message)
            self.refresh()
            self.app.invalidate("dashboard", "attendance_manual", "attendance_records")
        else:
            show_error(self, f"Could Not {verb}", message)

    def _require_pending(self) -> bool:
        if not self._selected_request:
            show_info(self, "No Selection",
                      "Select a request from the queue first.")
            return False
        if self._selected_request.get("status") != "Pending":
            show_info(self, "Already Reviewed",
                      f"This request has already been "
                      f"{self._selected_request['status'].lower()}.")
            return False
        return True

    def _show_selected_detail(self) -> None:
        if not self._selected_request:
            show_info(self, "No Selection", "Select a request first.")
            return
        self._show_request_detail(self._selected_request)

    def _show_request_detail(self, row: dict) -> None:
        request = self_model.get_request(row["request_id"])
        if request is None:
            show_error(self, "Not Found", "This request no longer exists.")
            self.refresh()
            return

        request = dict(request)

        sections = {
            "Student": [
                ("Name", request["student_name"]),
                ("Enrollment Number", request["enrollment_no"]),
                ("Roll Number", request["roll_no"]),
                ("Branch", request["branch_name"]),
                ("Semester", request["semester_name"]),
                ("Section", request["section_name"] or "-"),
            ],
            "Class": [
                ("Subject", f"{request['subject_name']} ({request['subject_code']})"),
                ("Faculty", request["faculty_name"] or "-"),
                ("Class Date", request["class_date"]),
                ("Slot", f"{request['slot_start'] or '-'} to "
                         f"{request['slot_end'] or '-'}"),
                ("Attendance Session",
                 request["att_session_id"] or "Not opened at request time"),
            ],
            "Request": [
                ("Submitted At", request["requested_at"]),
                ("Student's Remark", request["student_remark"] or "-"),
                ("Status", request["status"]),
                ("Reviewed By", request["reviewer_name"] or "-"),
                ("Reviewed On", request["reviewed_on"] or "-"),
                ("Teacher's Remark", request["review_remarks"] or "-"),
                ("Attendance Applied", request["applied_status"] or "Not applied"),
            ],
        }

        DetailDialog(self, "Self-Attendance Request", sections,
                     image_path=request.get("photo_path"),
                     width=640, height=620).show()

    # ==================================================================
    def refresh(self) -> None:
        try:
            if self._is_student:
                self._refresh_student()
            else:
                self._refresh_faculty()
        except Exception as exc:                # noqa: BLE001
            logger.error("Self-attendance refresh failed: %s", exc, exc_info=True)
            show_error(self, "Could Not Load", str(exc))

    def _refresh_student(self) -> None:
        self._render_student_classes()

        history = self_model.get_student_history(session.linked_id)
        self.history_table.set_data(history)

        stats = self_model.get_stats(student_id=session.linked_id)
        for key in ("Pending", "Approved", "Rejected"):
            self.tiles[key].update_value(str(stats.get(key, 0)))

    def _refresh_faculty(self) -> None:
        values = self.filters.values()
        faculty_id = session.linked_id if session.is_faculty else None

        rows = self_model.get_requests(
            status=values.get("status"),
            faculty_id=faculty_id,
            branch_id=values.get("branch_id"),
            semester_id=values.get("semester_id"),
            search=values.get("search", ""))

        self.table.set_data(rows)

        stats = self_model.get_stats(faculty_id=faculty_id)
        for key in ("Pending", "Approved", "Rejected", "total"):
            self.tiles[key].update_value(str(stats.get(key, 0)))
