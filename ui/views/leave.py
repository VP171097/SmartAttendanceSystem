"""
Leave management.

Role-aware, because the same records mean different things to each user:

*   **Student**  -- apply, track status, resubmit a returned application.
*   **Faculty**  -- approve, reject or return for correction, limited to
    students in the classes they actually teach.
*   **Admin**    -- everything, plus override a faculty decision and delete.

The medical-certificate rule from the specification is enforced in the model
(``requires_certificate``) and surfaced in the form so the student is told
before submitting rather than after.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import customtkinter as ctk

from config.settings import LEAVE_STATUSES, LEAVE_TYPES, config
from config.theme import FONTS, LEAVE_STATUS_COLORS, SEMANTIC, color
from core.auth import Permission, session
from core.logger import get_logger
from models import academic, leave as leave_model
from ui.widgets.components import EmptyState, PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import (DetailDialog, FormDialog, FormField, ask_confirm,
                                ask_reason, show_error, show_info, show_success,
                                show_warning)
from ui.widgets.table import DataTable, column, dash_format, date_format

logger = get_logger("ui.leave")


class LeaveView(ctk.CTkFrame):
    """Leave application and review screen."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected: dict | None = None

        self._can_review = session.can(Permission.REVIEW_LEAVE)
        self._can_override = session.can(Permission.OVERRIDE_LEAVE)
        self._is_student = session.is_student

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Leave Management",
            subtitle=("Apply for leave and track your applications" if self._is_student
                      else "Review, approve and manage student leave applications"),
            icon="⎙")
        header.pack(fill="x", padx=18, pady=(14, 10))

        if self._is_student:
            header.add_button("+  Apply for Leave", self.apply_leave, width=170)

        # ---- tiles ---------------------------------------------------------
        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(5):
            tiles.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tiles = {}
        for index, (key, label, icon, accent) in enumerate([
                ("total", "Total Applications", "≡", SEMANTIC["info"]),
                ("Pending", "Pending", "⏳", SEMANTIC["warning"]),
                ("Approved", "Approved", "✓", SEMANTIC["success"]),
                ("Rejected", "Rejected", "✗", SEMANTIC["danger"]),
                ("Returned for Correction", "Returned", "↺", SEMANTIC["info"])]):
            tile = StatCard(tiles, label, "0", icon, accent)
            tile.grid(row=0, column=index, sticky="ew", padx=4)
            self.tiles[key] = tile

        # ---- filters ---------------------------------------------------------
        from ui.widgets.components import FilterBar
        self.filters = FilterBar(self, on_change=self.refresh,
                                 search_placeholder="Enrollment, student name, reason...")
        self.filters.pack(fill="x", padx=18, pady=(0, 9))

        self.filters.add_filter("status", "Status",
                                {s: s for s in LEAVE_STATUSES}, width=180)
        self.filters.add_filter("leave_type", "Leave Type",
                                {t: t for t in LEAVE_TYPES}, width=160)

        if not self._is_student:
            self.filters.add_filter(
                "branch_id", "Branch",
                {b["branch_name"]: b["branch_id"] for b in academic.get_branches()},
                width=180)
            self.filters.add_filter(
                "semester_id", "Semester",
                {s["semester_name"]: s["semester_id"] for s in academic.get_semesters()},
                width=130)
            self.filters.add_search(210)

        self.filters.add_button("Reset", self.filters.reset, width=80,
                                fg_color="transparent", border_width=1,
                                border_color=color("border"), text_color=color("text"),
                                hover_color=color("surface_alt"))

        # ---- table + detail ----------------------------------------------------
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=7, uniform="body")
        body.grid_columnconfigure(1, weight=3, uniform="body")
        body.grid_rowconfigure(0, weight=1)

        columns = [
            column("applied_on", "Applied", 100, format=date_format),
            column("leave_type", "Leave Type", 130),
            column("from_date", "From", 100, format=date_format),
            column("to_date", "To", 100, format=date_format),
            column("total_days", "Days", 55, "center"),
            column("status", "Status", 155, "center"),
            column("pending_with", "Pending With", 160, format=dash_format),
            column("document_path", "Certificate", 90, "center",
                   format=lambda v, _r: "Attached" if v else "-"),
        ]
        if not self._is_student:
            columns[1:1] = [
                column("enrollment_no", "Enrollment No", 115),
                column("student_name", "Student", 165, stretch=True),
                column("branch_code", "Branch", 70, "center"),
                column("semester_name", "Sem", 95),
            ]

        self.table = DataTable(
            body, columns=columns, on_select=self._row_selected,
            on_double_click=lambda _row: self.view_leave(), height=17)
        self.table.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self._build_detail_panel(body)

    def _build_detail_panel(self, parent) -> None:
        panel = ctk.CTkFrame(parent, fg_color="transparent")
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_rowconfigure(0, weight=1)
        panel.grid_columnconfigure(0, weight=1)

        self.detail_card = SectionCard(panel, "Application Details",
                                       "Select an application")
        self.detail_card.grid(row=0, column=0, sticky="nsew")

        self.detail_body = ctk.CTkScrollableFrame(self.detail_card.body,
                                                  fg_color="transparent")
        self.detail_body.pack(fill="both", expand=True)

        ctk.CTkLabel(self.detail_body,
                     text="No application selected.\n\nClick a row to read the\n"
                          "reason and take action.",
                     font=FONTS["body"], text_color=color("text_muted"),
                     justify="center").pack(pady=40)

        # ---- action buttons -----------------------------------------------
        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for index in range(2):
            actions.grid_columnconfigure(index, weight=1, uniform="actions")

        self._action_buttons: dict[str, ctk.CTkButton] = {}
        specs: list[tuple] = [("view", "View Full Details", self.view_leave, None)]

        if self._can_review:
            specs += [
                ("approve", "Approve", self.approve_leave, SEMANTIC["success"]),
                ("reject", "Reject", self.reject_leave, SEMANTIC["danger"]),
                ("return", "Return for Correction", self.return_leave, SEMANTIC["warning"]),
            ]
        if self._is_student:
            specs += [
                ("resubmit", "Edit & Resubmit", self.resubmit_leave, SEMANTIC["teal"]),
                ("cancel", "Cancel Application", self.cancel_leave, SEMANTIC["danger"]),
            ]
        if self._can_override:
            specs += [("override", "Override Decision", self.override_leave,
                       SEMANTIC["purple"])]
            specs += [("delete", "Delete Application", self.delete_leave,
                       SEMANTIC["danger"])]

        specs += [("document", "Open Certificate", self.open_document, None)]

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

        try:
            rows = leave_model.search_leaves(
                student_id=session.linked_id if self._is_student else None,
                status=values.get("status"),
                leave_type=values.get("leave_type"),
                branch_id=values.get("branch_id"),
                semester_id=values.get("semester_id"),
                search=values.get("search", ""),
                # Faculty see only their own students' applications.
                faculty_id=(session.linked_id if session.is_faculty else None))
        except Exception as exc:                # noqa: BLE001
            logger.error("Leave query failed: %s", exc, exc_info=True)
            show_error(self, "Query Failed", str(exc))
            return

        # Resolve who each application is waiting on before it hits the grid.
        self.table.set_data(leave_model.annotate_pending_with(rows))

        stats = leave_model.get_leave_stats(
            session.linked_id if self._is_student else None)
        self.tiles["total"].update_value(str(stats.get("total", 0)))
        for status in ("Pending", "Approved", "Rejected", "Returned for Correction"):
            self.tiles[status].update_value(str(stats.get(status, 0)))

    def _row_selected(self, row: dict) -> None:
        self._selected = row
        status = row.get("status")

        for key, button in self._action_buttons.items():
            button.configure(state="normal")

            # Only pending or returned applications can be acted on.
            if key in ("approve", "reject", "return"):
                button.configure(state="normal" if status == "Pending" else "disabled")
            elif key == "resubmit":
                button.configure(
                    state="normal" if status == "Returned for Correction" else "disabled")
            elif key == "cancel":
                # Only while nobody has acted on it; an approved leave has
                # already moved attendance and needs a faculty to reverse it.
                button.configure(
                    state="normal"
                    if status in ("Pending", "Returned for Correction") else "disabled")
            elif key == "document":
                button.configure(
                    state="normal" if row.get("document_path") else "disabled")

        self._render_detail(row)

    def _render_detail(self, row: dict) -> None:
        for widget in self.detail_body.winfo_children():
            widget.destroy()

        status = row.get("status", "")
        accent = LEAVE_STATUS_COLORS.get(status, SEMANTIC["neutral"])

        banner = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7, fg_color=accent)
        banner.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(banner, text=status.upper(), font=FONTS["subhead"],
                     text_color="#FFFFFF").pack(pady=(9, 1))
        if row.get("admin_override"):
            ctk.CTkLabel(banner, text="Administrator override applied",
                         font=(FONTS["small"][0], 10),
                         text_color="#FFFFFF").pack(pady=(0, 8))
        else:
            ctk.CTkFrame(banner, height=6, fg_color="transparent").pack()

        if not self._is_student:
            ctk.CTkLabel(self.detail_body, text=row.get("student_name", ""),
                         font=FONTS["subhead"], text_color=color("text"),
                         wraplength=220).pack()
            ctk.CTkLabel(self.detail_body,
                         text=(f"{row.get('enrollment_no','')}  |  "
                               f"{row.get('branch_code','')} "
                               f"{row.get('semester_name','')}"),
                         font=FONTS["small"],
                         text_color=color("text_muted")).pack(pady=(0, 10))

        # ---- who is it waiting on? ---------------------------------------
        try:
            routing = leave_model.get_pending_with(row)
        except Exception:                       # noqa: BLE001
            routing = {"name": "-", "role": "", "note": ""}

        if row.get("status") in ("Pending", "Returned for Correction"):
            waiting = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                                   fg_color=color("surface_alt"))
            waiting.pack(fill="x", pady=(0, 10))
            ctk.CTkLabel(waiting, text="PENDING WITH",
                         font=(FONTS["small"][0], 9, "bold"),
                         text_color=color("text_muted")).pack(pady=(9, 1))
            ctk.CTkLabel(waiting, text=routing["name"], font=FONTS["subhead"],
                         text_color=color("primary"), wraplength=210).pack()
            if routing.get("role"):
                ctk.CTkLabel(waiting, text=routing["role"],
                             font=(FONTS["small"][0], 10),
                             text_color=color("text_muted")).pack()
            if routing.get("mobile"):
                ctk.CTkLabel(waiting, text=routing["mobile"],
                             font=(FONTS["small"][0], 10),
                             text_color=color("text_muted")).pack()
            ctk.CTkFrame(waiting, height=9, fg_color="transparent").pack()

        for label, value in (
                ("Leave Type", row.get("leave_type")),
                ("From", row.get("from_date")),
                ("To", row.get("to_date")),
                ("Total Days", row.get("total_days")),
                ("Applied On", str(row.get("applied_on", ""))[:16]),
                ("Certificate", "Attached" if row.get("document_path") else "Not attached"),
                ("Reviewed By", row.get("reviewer_name") or "-"),
                ("Reviewed On", str(row.get("reviewed_on") or "-")[:16])):
            entry = ctk.CTkFrame(self.detail_body, fg_color="transparent")
            entry.pack(fill="x", pady=1)
            ctk.CTkLabel(entry, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), width=95,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=str(value or "-"), font=FONTS["small"],
                         text_color=color("text"), anchor="w", wraplength=165,
                         justify="left").pack(side="left", fill="x", expand=True)

        # ---- reason -----------------------------------------------------
        ctk.CTkLabel(self.detail_body, text="REASON", font=(FONTS["small"][0], 9, "bold"),
                     text_color=color("text_muted"), anchor="w").pack(
                         fill="x", pady=(14, 3))
        reason_box = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                                  fg_color=color("surface_alt"))
        reason_box.pack(fill="x")
        ctk.CTkLabel(reason_box, text=row.get("reason") or "-", font=FONTS["small"],
                     text_color=color("text"), wraplength=215, justify="left",
                     anchor="w").pack(fill="x", padx=11, pady=9)

        # ---- review remarks ------------------------------------------------
        if row.get("review_remarks"):
            ctk.CTkLabel(self.detail_body, text="REVIEW REMARKS",
                         font=(FONTS["small"][0], 9, "bold"),
                         text_color=color("text_muted"), anchor="w").pack(
                             fill="x", pady=(12, 3))
            remarks_box = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                                       fg_color=color("surface_alt"))
            remarks_box.pack(fill="x")
            ctk.CTkLabel(remarks_box, text=row["review_remarks"], font=FONTS["small"],
                         text_color=color("text"), wraplength=215, justify="left",
                         anchor="w").pack(fill="x", padx=11, pady=9)

        if row.get("override_reason"):
            ctk.CTkLabel(self.detail_body, text="OVERRIDE REASON",
                         font=(FONTS["small"][0], 9, "bold"),
                         text_color=SEMANTIC["purple"], anchor="w").pack(
                             fill="x", pady=(12, 3))
            override_box = ctk.CTkFrame(self.detail_body, height=0, corner_radius=7,
                                        fg_color=color("surface_alt"))
            override_box.pack(fill="x")
            ctk.CTkLabel(override_box, text=row["override_reason"], font=FONTS["small"],
                         text_color=color("text"), wraplength=215, justify="left",
                         anchor="w").pack(fill="x", padx=11, pady=9)

    # ==================================================================
    # Student actions
    # ==================================================================
    def _leave_form_fields(self) -> list[FormField]:
        limit = int(config.get("medical_cert_mandatory_days", 3))
        return [
            FormField("leave_type", "Leave Type", "select", required=True,
                      options={t: t for t in LEAVE_TYPES}, span=2),
            FormField("from_date", "From Date", "date", required=True,
                      default=date.today().isoformat()),
            FormField("to_date", "To Date", "date", required=True,
                      default=date.today().isoformat()),
            FormField("reason", "Reason", "textarea", required=True, span=2,
                      hint="At least 10 characters. Be specific - this is read by "
                           "your faculty."),
            FormField("document_source", "Supporting Document", "file", span=2,
                      file_types=[("PDF or image", "*.pdf *.jpg *.jpeg *.png")],
                      hint=(f"Medical leave longer than {limit} days REQUIRES a "
                            f"medical certificate. PDF, JPG, JPEG or PNG.")),
        ]

    def apply_leave(self) -> None:
        if not session.linked_id:
            show_error(self, "No Student Record",
                       "This login is not linked to a student record.")
            return

        def validate(values: dict) -> tuple[bool, str]:
            """Check the certificate rule before the dialog closes."""
            from core.validators import parse_date
            start, end = parse_date(values.get("from_date")), parse_date(values.get("to_date"))
            if start is None or end is None:
                return False, "Please enter valid From and To dates."
            if end < start:
                return False, "The 'To' date cannot be before the 'From' date."

            total_days = (end - start).days + 1
            if leave_model.requires_certificate(values.get("leave_type"), total_days):
                if not values.get("document_source"):
                    limit = int(config.get("medical_cert_mandatory_days", 3))
                    return False, (f"Medical leave of {total_days} days exceeds "
                                   f"{limit} days, so a medical certificate is "
                                   "mandatory. Please attach one.")
            if len((values.get("reason") or "").strip()) < 10:
                return False, "Please give a reason of at least 10 characters."
            return True, ""

        values = FormDialog(
            self, "Apply for Leave", self._leave_form_fields(),
            submit_text="Submit Application", width=720, height=560,
            on_validate=validate,
            description="Your application goes to the faculty teaching your class").show()

        if not values:
            return

        ok, message, _ = leave_model.apply_leave(
            session.linked_id, values["leave_type"], values["from_date"],
            values["to_date"], values["reason"],
            values.get("document_source") or None, session.user)

        if ok:
            show_success(self, "Application Submitted", message)
            self.refresh()
        else:
            show_error(self, "Could Not Submit", message)

    def resubmit_leave(self) -> None:
        if not self._selected:
            return

        record = self._selected
        if record.get("status") != "Returned for Correction":
            show_info(self, "Not Returned",
                      "Only applications returned for correction can be resubmitted.")
            return

        values = FormDialog(
            self, "Edit & Resubmit Application", self._leave_form_fields(),
            values=dict(record), submit_text="Resubmit", width=720, height=580,
            description=f"Faculty remarks: {record.get('review_remarks') or '-'}").show()

        if not values:
            return

        ok, message = leave_model.update_leave(
            record["leave_id"], values["leave_type"], values["from_date"],
            values["to_date"], values["reason"],
            values.get("document_source") or None, session.user)

        if ok:
            show_success(self, "Application Resubmitted", message)
            self.refresh()
        else:
            show_error(self, "Could Not Resubmit", message)

    def cancel_leave(self) -> None:
        """Student withdraws their own application before it is decided."""
        if not self._selected:
            show_info(self, "No Selection", "Select one of your applications first.")
            return

        record = self._selected
        if record.get("status") not in ("Pending", "Returned for Correction"):
            show_info(self, "Cannot Cancel",
                      f"This application is already {record['status'].lower()}.\n\n"
                      "An approved leave has changed your attendance, so ask your "
                      "class teacher or the administrator to reverse it.")
            return

        if not ask_confirm(
                self, "Cancel Leave Application",
                f"Withdraw this application?\n\n"
                f"  {record['leave_type']}\n"
                f"  {record['from_date']} to {record['to_date']} "
                f"({record['total_days']} day(s))\n"
                f"  Currently pending with: {record.get('pending_with', '-')}\n\n"
                "It will be removed and your teacher will no longer see it. "
                "You can apply again at any time.",
                confirm_text="Cancel Application", cancel_text="Keep It",
                danger=True):
            return

        ok, message = leave_model.cancel_leave(
            record["leave_id"], session.linked_id, session.user)

        if ok:
            show_success(self, "Application Cancelled", message)
            self._selected = None
            for button in self._action_buttons.values():
                button.configure(state="disabled")
            self.refresh()
        else:
            show_error(self, "Could Not Cancel", message)

    # ==================================================================
    # Faculty actions
    # ==================================================================
    def approve_leave(self) -> None:
        self._review("Approved", "Approve Leave",
                     "Approving marks the student's attendance as Leave "
                     "(or Medical Leave) for every class in the date range.",
                     require_remarks=False)

    def reject_leave(self) -> None:
        self._review("Rejected", "Reject Leave",
                     "The student will see your reason. Attendance is not changed.",
                     require_remarks=True)

    def return_leave(self) -> None:
        self._review("Returned for Correction", "Return for Correction",
                     "The student can edit and resubmit. Tell them what to fix.",
                     require_remarks=True)

    def _review(self, decision: str, title: str, explanation: str,
                require_remarks: bool) -> None:
        if not self._selected:
            return

        record = self._selected
        if record.get("status") != "Pending":
            show_info(self, "Already Reviewed",
                      f"This application is already '{record['status']}'.")
            return

        # A medical certificate that policy demands must actually be there.
        if decision == "Approved" and leave_model.requires_certificate(
                record["leave_type"], int(record["total_days"] or 0)):
            if not record.get("document_path"):
                limit = int(config.get("medical_cert_mandatory_days", 3))
                if not ask_confirm(
                        self, "Missing Medical Certificate",
                        f"This is {record['total_days']}-day medical leave, which "
                        f"requires a certificate (limit {limit} days), but none is "
                        "attached.\n\n"
                        "Approving without one goes against college policy.\n\n"
                        "Return it for correction instead?",
                        confirm_text="Approve Anyway",
                        cancel_text="Cancel", danger=True):
                    return

        summary = (f"{record.get('student_name', 'This student')} "
                   f"({record.get('enrollment_no','')})\n"
                   f"{record['leave_type']}  |  {record['from_date']} to "
                   f"{record['to_date']}  |  {record['total_days']} day(s)\n\n"
                   f"Reason: {record.get('reason','')}\n\n{explanation}")

        if require_remarks:
            remarks = ask_reason(
                self, title, summary, min_length=10,
                confirm_text=decision.split()[0],
                danger=(decision == "Rejected"))
            if not remarks:
                return
        else:
            if not ask_confirm(self, title, summary, confirm_text="Approve"):
                return
            remarks = "Approved."

        ok, message = leave_model.review_leave(
            record["leave_id"], decision, remarks, session.user)

        if ok:
            show_success(self, "Decision Recorded", message)
            self.refresh()
            self.app.invalidate("dashboard", "attendance_manual")
        else:
            show_error(self, "Could Not Record Decision", message)

    # ==================================================================
    # Admin actions
    # ==================================================================
    def override_leave(self) -> None:
        if not self._selected:
            return

        record = self._selected
        values = FormDialog(
            self, "Override Leave Decision",
            [
                FormField("new_status", "New Status", "select", required=True,
                          options={s: s for s in LEAVE_STATUSES},
                          default=record.get("status"), span=2),
                FormField("reason", "Reason for Override", "textarea", required=True,
                          span=2,
                          hint="Mandatory. Recorded permanently in the audit trail "
                               "with your name and the timestamp."),
            ],
            submit_text="Apply Override", width=680, height=420,
            description=(f"{record.get('student_name','')} - currently "
                         f"'{record.get('status')}' "
                         f"(reviewed by {record.get('reviewer_name') or 'nobody'})")).show()

        if not values:
            return

        if len((values.get("reason") or "").strip()) < 10:
            show_warning(self, "Reason Too Short",
                         "Please give a reason of at least 10 characters.")
            return

        ok, message = leave_model.override_leave(
            record["leave_id"], values["new_status"], values["reason"], session.user)

        if ok:
            show_success(self, "Override Applied", message)
            self.refresh()
            self.app.invalidate("dashboard", "attendance_manual")
        else:
            show_error(self, "Could Not Override", message)

    def delete_leave(self) -> None:
        if not self._selected:
            return

        record = self._selected
        reason = ask_reason(
            self, "Delete Leave Application",
            f"Permanently delete this application?\n\n"
            f"{record.get('student_name','')} ({record.get('enrollment_no','')})\n"
            f"{record['leave_type']}  |  {record['from_date']} to {record['to_date']}\n"
            f"Status: {record['status']}\n\n"
            "If it was approved, the attendance it set is left as-is. "
            "This cannot be undone.",
            min_length=10, confirm_text="Delete Permanently", danger=True)

        if not reason:
            return

        ok, message = leave_model.delete_leave(record["leave_id"], reason, session.user)
        if ok:
            show_success(self, "Application Deleted", message)
            self._selected = None
            for button in self._action_buttons.values():
                button.configure(state="disabled")
            self.refresh()
        else:
            show_error(self, "Could Not Delete", message)

    # ==================================================================
    def view_leave(self) -> None:
        if not self._selected:
            return

        record = dict(leave_model.get_leave(self._selected["leave_id"]))
        history = leave_model.get_leave_history(record["student_id"])
        try:
            routing = leave_model.get_pending_with(record)
        except Exception:                       # noqa: BLE001
            routing = {"name": "-", "role": "", "note": ""}

        sections = {
            "Student": [
                ("Name", record.get("student_name")),
                ("Enrollment Number", record.get("enrollment_no")),
                ("Roll Number", record.get("roll_no")),
                ("Branch", record.get("branch_name")),
                ("Semester", record.get("semester_name")),
                ("Section", record.get("section_name") or "-"),
                ("Mobile", record.get("mobile")),
            ],
            "Application": [
                ("Leave Type", record["leave_type"]),
                ("From Date", record["from_date"]),
                ("To Date", record["to_date"]),
                ("Total Days", record["total_days"]),
                ("Applied On", str(record["applied_on"])[:19]),
                ("Reason", record["reason"]),
                ("Supporting Document",
                 Path(record["document_path"]).name if record["document_path"]
                 else "Not attached"),
            ],
            "Review": [
                ("Status", record["status"]),
                ("Pending With", routing["name"]),
                ("Their Role", routing.get("role") or "-"),
                ("Routing Note", routing.get("note") or "-"),
                ("Reviewed By", record.get("reviewer_name") or "-"),
                ("Reviewer Role", record.get("reviewer_role") or "-"),
                ("Reviewed On", str(record.get("reviewed_on") or "-")[:19]),
                ("Remarks", record.get("review_remarks") or "-"),
                ("Admin Override", "Yes" if record.get("admin_override") else "No"),
                ("Override By", record.get("override_by_name") or "-"),
                ("Override Reason", record.get("override_reason") or "-"),
                ("Attendance Updated",
                 "Yes" if record.get("attendance_applied") else "No"),
            ],
            f"Leave History ({len(history)} application(s))": [
                (f"{h['from_date']} to {h['to_date']}",
                 f"{h['leave_type']} - {h['status']} ({h['total_days']} day(s))")
                for h in history[:12]
            ] or [("-", "No other applications")],
        }

        actions = []
        if record.get("document_path"):
            actions.append(("Open Certificate", self.open_document))

        DetailDialog(self, "Leave Application", sections, actions=actions,
                     width=660, height=680).show()

    def open_document(self) -> None:
        if not self._selected or not self._selected.get("document_path"):
            return

        path = Path(self._selected["document_path"])
        if not path.exists():
            show_warning(
                self, "File Not Found",
                f"The stored document could not be found:\n\n{path}\n\n"
                "It may have been moved or deleted from the storage folder. "
                "In the seeded demo data, certificate paths are recorded but the "
                "files themselves are not generated.")
            return

        try:
            os.startfile(str(path))
        except Exception as exc:                # noqa: BLE001
            show_error(self, "Could Not Open", f"{exc}\n\nThe file is at:\n{path}")
