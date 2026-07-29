"""
Dashboard.

One view class that renders a different layout per role, because all three
dashboards share the same scaffolding (header, tile row, chart panels) and only
differ in which numbers they show:

*   **Admin**   -- institution-wide totals, today's attendance, defaulters.
*   **Faculty** -- today's classes with their attendance state, pending leave.
*   **Student** -- own percentage, per-subject breakdown, leave status, trend.
"""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from config.settings import config
from config.theme import FONTS, SEMANTIC, color, percentage_color
from core.auth import session
from core.database import get_db
from core.logger import get_logger
from models import academic, attendance as attendance_model, faculty as faculty_model
from models import leave as leave_model, student as student_model, timetable as timetable_model
from services import analytics_service
from ui.widgets.components import (EmptyState, InfoRow, PageHeader, ProgressStat,
                                   SectionCard, StatCard, scrollable_page)

logger = get_logger("ui.dashboard")


class DashboardView(ctk.CTkFrame):
    """Role-aware landing screen."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._canvases: list[FigureCanvasTkAgg] = []

        self.page = scrollable_page(self)
        self._build_header()

        self.body = ctk.CTkFrame(self.page, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.refresh()

    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        hour = datetime.now().hour
        greeting = ("Good morning" if hour < 12
                    else "Good afternoon" if hour < 17 else "Good evening")

        from core.validators import first_name
        display_name = first_name(session.full_name) if session.full_name else "there"

        header = PageHeader(
            self.page,
            title=f"{greeting}, {display_name}",
            subtitle=(f"{session.role} dashboard  |  "
                      f"{datetime.now():%A, %d %B %Y}  |  "
                      f"Session {config.get('current_academic_session','')}"),
            icon="▤")
        header.pack(fill="x", padx=18, pady=(14, 10))
        header.add_button("Refresh", self.refresh, width=110,
                          fg_color="transparent", hover_color=color("surface_alt"))

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the body for the current role."""
        for canvas in self._canvases:
            canvas.get_tk_widget().destroy()
        self._canvases.clear()

        for widget in self.body.winfo_children():
            widget.destroy()

        try:
            if session.is_admin:
                self._build_admin()
            elif session.is_faculty:
                self._build_faculty()
            else:
                self._build_student()
        except Exception as exc:                # noqa: BLE001
            logger.error("Dashboard build failed: %s", exc, exc_info=True)
            EmptyState(self.body, "!", "Dashboard could not be loaded",
                       f"{type(exc).__name__}: {exc}").pack(fill="both", expand=True)

    def _tile_row(self, parent, tiles: list[dict], columns: int = 5) -> None:
        """Lay out StatCards in a responsive grid."""
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="x", pady=(0, 18))
        for index in range(columns):
            grid.grid_columnconfigure(index, weight=1, uniform="tiles")

        for index, tile in enumerate(tiles):
            card = StatCard(grid, **tile)
            card.grid(row=index // columns, column=index % columns,
                      sticky="ew", padx=5, pady=5)

    def _chart(self, parent, builder, **kwargs) -> None:
        """Embed a Matplotlib figure, matching the current appearance mode."""
        try:
            figure = builder(mode=ctk.get_appearance_mode(), **kwargs)
            canvas = FigureCanvasTkAgg(figure, master=parent)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="both", expand=True)
            self._canvases.append(canvas)
        except Exception as exc:                # noqa: BLE001
            logger.error("Chart failed: %s", exc, exc_info=True)
            ctk.CTkLabel(parent, text=f"Chart unavailable: {exc}",
                         font=FONTS["small"], text_color=color("text_muted")).pack(pady=20)

    # ==================================================================
    # Administrator
    # ==================================================================
    def _build_admin(self) -> None:
        db = get_db()
        today = attendance_model.get_today_stats()
        threshold = float(config.get("attendance_threshold", 75))

        total_students = student_model.count_students()
        total_faculty = faculty_model.count_faculty()
        total_branches = len(academic.get_branches())
        defaulters = attendance_model.get_defaulters(threshold)
        pending_leaves = leave_model.count_pending()

        self._tile_row(self.body, [
            dict(title="Total Students", value=f"{total_students:,}", icon="☺",
                 accent=SEMANTIC["info"], subtitle=f"across {total_branches} branches"),
            dict(title="Total Faculty", value=str(total_faculty), icon="★",
                 accent=SEMANTIC["purple"], subtitle="active teaching staff"),
            dict(title="Classes Today", value=str(today["classes_held"]), icon="◷",
                 accent=SEMANTIC["teal"], subtitle="attendance sessions held"),
            dict(title="Attendance Today", value=f"{today['percentage']:.1f}%", icon="◉",
                 accent=percentage_color(today["percentage"], threshold),
                 subtitle=f"{today['present'] + today['late']} of {today['total']} records"),
            dict(title="Defaulters", value=str(len(defaulters)), icon="!",
                 accent=SEMANTIC["danger"] if defaulters else SEMANTIC["success"],
                 subtitle=f"below {threshold:.0f}% threshold",
                 on_click=lambda: self.app.navigate("reports")),
        ], columns=5)

        self._tile_row(self.body, [
            dict(title="Present", value=str(today["present"]), icon="✓",
                 accent=SEMANTIC["success"], subtitle="marked today"),
            dict(title="Absent", value=str(today["absent"]), icon="✗",
                 accent=SEMANTIC["danger"], subtitle="marked today"),
            dict(title="Late", value=str(today["late"]), icon="◷",
                 accent=SEMANTIC["warning"], subtitle="marked today"),
            dict(title="On Leave", value=str(today["leave"] + today["medical"]), icon="⎙",
                 accent=SEMANTIC["info"],
                 subtitle=f"{today['medical']} medical"),
            dict(title="Pending Leaves", value=str(pending_leaves), icon="⏳",
                 accent=SEMANTIC["warning"] if pending_leaves else SEMANTIC["neutral"],
                 subtitle="awaiting review",
                 on_click=lambda: self.app.navigate("leave")),
        ], columns=5)

        # ---- charts ------------------------------------------------------
        charts = ctk.CTkFrame(self.body, fg_color="transparent")
        charts.pack(fill="both", expand=True)
        charts.grid_columnconfigure(0, weight=3, uniform="charts")
        charts.grid_columnconfigure(1, weight=2, uniform="charts")

        trend = SectionCard(charts, "Attendance Trend",
                            "Institution-wide percentage over the last 30 days")
        trend.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 14))
        self._chart(trend.body, analytics_service.daily_trend_chart,
                    days=30, width=6.6, height=3.2)

        donut = SectionCard(charts, "Status Breakdown", "All recorded attendance")
        donut.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 14))
        self._chart(donut.body, analytics_service.status_donut_chart,
                    width=4.6, height=3.2)

        branches = SectionCard(charts, "Branch Comparison",
                               "Average attendance by branch")
        branches.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self._chart(branches.body, analytics_service.branch_comparison_chart,
                    width=6.6, height=3.2)

        # ---- defaulter shortlist ----------------------------------------
        watchlist = SectionCard(charts, "Defaulter Watchlist",
                                f"Lowest attendance, below {threshold:.0f}%",
                                action_text="Full Report",
                                action_command=lambda: self.app.navigate("reports"))
        watchlist.grid(row=1, column=1, sticky="nsew", padx=(8, 0))

        if not defaulters:
            ctk.CTkLabel(watchlist.body, text="No defaulters. Every student is eligible.",
                         font=FONTS["body"], text_color=SEMANTIC["success"]).pack(pady=28)
        else:
            for row in defaulters[:8]:
                entry = ctk.CTkFrame(watchlist.body, fg_color="transparent")
                entry.pack(fill="x", pady=3)

                ctk.CTkLabel(entry, text=str(row["full_name"])[:22], font=FONTS["body"],
                             text_color=color("text"), anchor="w", width=150).pack(side="left")
                ctk.CTkLabel(entry, text=f"{row['branch_code']} {row['semester_name']}",
                             font=FONTS["small"], text_color=color("text_muted"),
                             anchor="w", width=110).pack(side="left")

                percentage = float(row["percentage"] or 0)
                ctk.CTkLabel(entry, text=f"{percentage:.1f}%", font=FONTS["body_bold"],
                             text_color=percentage_color(percentage, threshold),
                             anchor="e").pack(side="right")

            if len(defaulters) > 8:
                ctk.CTkLabel(watchlist.body,
                             text=f"+ {len(defaulters) - 8} more",
                             font=FONTS["small"], text_color=color("text_muted")
                             ).pack(anchor="w", pady=(8, 0))

        # ---- recent activity --------------------------------------------
        recent = SectionCard(self.body, "Recent Activity",
                             "Latest actions recorded in the audit trail",
                             action_text="View Audit",
                             action_command=lambda: self.app.navigate("audit"))
        recent.pack(fill="x", pady=(14, 0))

        rows = db.fetch_all(
            "SELECT username, role, action, module, timestamp FROM audit_log "
            "ORDER BY audit_id DESC LIMIT 8")
        if not rows:
            ctk.CTkLabel(recent.body, text="No activity recorded yet.",
                         font=FONTS["body"], text_color=color("text_muted")).pack(pady=14)
        for row in rows:
            entry = ctk.CTkFrame(recent.body, fg_color="transparent")
            entry.pack(fill="x", pady=2)
            ctk.CTkLabel(entry, text=str(row["timestamp"])[:19], font=FONTS["small"],
                         text_color=color("text_muted"), width=150, anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=f"{row['username']} ({row['role']})",
                         font=FONTS["small_bold"], text_color=color("text"),
                         width=190, anchor="w").pack(side="left")
            ctk.CTkLabel(entry, text=f"{row['action']}  -  {row['module'] or '-'}",
                         font=FONTS["small"], text_color=color("text_muted"),
                         anchor="w").pack(side="left")

    # ==================================================================
    # Faculty
    # ==================================================================
    def _build_faculty(self) -> None:
        faculty_id = session.linked_id
        if not faculty_id:
            EmptyState(self.body, "!", "No faculty record linked",
                       "This login is not attached to a faculty record. "
                       "Ask the administrator to link it.").pack(fill="both", expand=True)
            return

        classes = timetable_model.get_today_classes(faculty_id=faculty_id)
        completed = [c for c in classes if c["attendance_taken"]]
        pending = [c for c in classes if not c["attendance_taken"]
                   and c["state"] in ("In Progress", "Missed")]
        upcoming = [c for c in classes if c["state"] == "Upcoming"]

        subjects = faculty_model.get_assigned_subjects(faculty_id)
        workload = faculty_model.get_workload(faculty_id)
        pending_leaves = leave_model.count_pending(faculty_id)

        from models import self_attendance as self_model
        pending_verifications = self_model.count_pending(faculty_id)

        self._tile_row(self.body, [
            dict(title="Today's Classes", value=str(len(classes)), icon="◷",
                 accent=SEMANTIC["info"], subtitle=f"{len(upcoming)} still upcoming"),
            dict(title="Completed", value=str(len(completed)), icon="✓",
                 accent=SEMANTIC["success"], subtitle="attendance recorded"),
            dict(title="Pending", value=str(len(pending)), icon="!",
                 accent=SEMANTIC["warning"] if pending else SEMANTIC["neutral"],
                 subtitle="attendance not yet taken"),
            dict(title="To Verify", value=str(pending_verifications), icon="⚖",
                 accent=SEMANTIC["danger"] if pending_verifications else SEMANTIC["neutral"],
                 subtitle="students marked themselves",
                 on_click=lambda: self.app.navigate("verify_attendance")),
            dict(title="Leave Requests", value=str(pending_leaves), icon="⎙",
                 accent=SEMANTIC["warning"] if pending_leaves else SEMANTIC["neutral"],
                 subtitle="awaiting your review",
                 on_click=lambda: self.app.navigate("leave")),
        ], columns=5)

        self._tile_row(self.body, [
            dict(title="My Subjects", value=str(workload["subjects"]), icon="▣",
                 accent=SEMANTIC["purple"],
                 subtitle=f"{workload['weekly_classes']} weekly periods"),
            dict(title="Students Taught", value=str(workload["students"]), icon="☺",
                 accent=SEMANTIC["info"], subtitle="across your classes"),
            dict(title="Classes Conducted", value=str(workload["sessions_taken"]),
                 icon="≡", accent=SEMANTIC["teal"], subtitle="all time"),
            dict(title="Take Attendance", value="Go", icon="◉",
                 accent=SEMANTIC["success"], subtitle="open the camera",
                 on_click=lambda: self.app.navigate("attendance_face")),
            dict(title="Mark Manually", value="Go", icon="✓",
                 accent=SEMANTIC["warning"], subtitle="one-click Present",
                 on_click=lambda: self.app.navigate("attendance_manual")),
        ], columns=5)

        # ---- today's schedule -------------------------------------------
        schedule = SectionCard(self.body, "Today's Schedule",
                               f"{datetime.now():%A, %d %B %Y}",
                               action_text="Take Attendance",
                               action_command=lambda: self.app.navigate("attendance_face"))
        schedule.pack(fill="x", pady=(0, 14))

        if not classes:
            ctk.CTkLabel(schedule.body,
                         text="No classes are scheduled for you today.",
                         font=FONTS["body"], text_color=color("text_muted")).pack(pady=22)
        else:
            state_colours = {
                "Completed": SEMANTIC["success"], "In Progress": SEMANTIC["warning"],
                "Upcoming": SEMANTIC["info"], "Missed": SEMANTIC["danger"],
                "Scheduled": SEMANTIC["neutral"],
            }
            for entry in classes:
                row = ctk.CTkFrame(schedule.body, height=0, corner_radius=7,
                                   fg_color=color("surface_alt"))
                row.pack(fill="x", pady=3)

                accent = state_colours.get(entry["state"], SEMANTIC["neutral"])
                ctk.CTkFrame(row, width=4, corner_radius=2, fg_color=accent).pack(
                    side="left", fill="y", padx=(6, 10), pady=8)

                ctk.CTkLabel(row, text=f"{entry['start_time']} - {entry['end_time']}",
                             font=FONTS["small_bold"], text_color=color("text"),
                             width=110, anchor="w").pack(side="left", pady=10)

                details = ctk.CTkFrame(row, fg_color="transparent")
                details.pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(details,
                             text=f"{entry['subject_name']} ({entry['subject_code']})",
                             font=FONTS["body_bold"], text_color=color("text"),
                             anchor="w").pack(fill="x")
                ctk.CTkLabel(details,
                             text=(f"{entry['branch_code']} - {entry['semester_name']} "
                                   f"Sec {entry['section_name']}   |   "
                                   f"Room {entry['room_no'] or '-'}"),
                             font=FONTS["small"], text_color=color("text_muted"),
                             anchor="w").pack(fill="x")

                if entry["attendance_taken"]:
                    ctk.CTkLabel(row,
                                 text=f"{entry['present_count']}/{entry['total_students']} present"
                                      + ("  (locked)" if entry["is_locked"] else ""),
                                 font=FONTS["small"], text_color=color("text_muted")
                                 ).pack(side="right", padx=12)

                ctk.CTkLabel(row, text=f"  {entry['state']}  ", font=FONTS["small_bold"],
                             text_color="#FFFFFF", fg_color=accent, corner_radius=9,
                             height=22).pack(side="right", padx=12, pady=10)

        # ---- subjects + chart -------------------------------------------
        lower = ctk.CTkFrame(self.body, fg_color="transparent")
        lower.pack(fill="both", expand=True)
        lower.grid_columnconfigure(0, weight=1, uniform="lower")
        lower.grid_columnconfigure(1, weight=1, uniform="lower")

        subject_card = SectionCard(lower, "My Subjects",
                                   f"{len(subjects)} subject(s) assigned to you")
        subject_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        if not subjects:
            ctk.CTkLabel(subject_card.body, text="No subjects assigned yet.",
                         font=FONTS["body"], text_color=color("text_muted")).pack(pady=22)
        for subject in subjects[:9]:
            from models.subject import get_subject_stats
            stats = get_subject_stats(subject["subject_id"])
            row = ctk.CTkFrame(subject_card.body, fg_color="transparent")
            row.pack(fill="x", pady=3)

            details = ctk.CTkFrame(row, fg_color="transparent")
            details.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(details, text=str(subject["subject_name"])[:34],
                         font=FONTS["body"], text_color=color("text"),
                         anchor="w").pack(fill="x")
            ctk.CTkLabel(details,
                         text=(f"{subject['subject_code']}  |  {subject['branch_code']} "
                               f"{subject['semester_name']}  |  "
                               f"{stats['sessions']} classes held"),
                         font=FONTS["small"], text_color=color("text_muted"),
                         anchor="w").pack(fill="x")

            ctk.CTkLabel(row, text=f"{stats['percentage']:.0f}%", font=FONTS["body_bold"],
                         text_color=percentage_color(stats["percentage"])).pack(
                             side="right", padx=8)

        comparison = SectionCard(lower, "Subject Performance",
                                 "Average attendance across your subjects")
        comparison.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._chart(comparison.body, analytics_service.subject_comparison_chart,
                    width=5.4, height=3.4)

    # ==================================================================
    # Student
    # ==================================================================
    def _build_student(self) -> None:
        student_id = session.linked_id
        if not student_id:
            EmptyState(self.body, "!", "No student record linked",
                       "This login is not attached to a student record. "
                       "Ask the administrator to link it.").pack(fill="both", expand=True)
            return

        student = student_model.get_student(student_id)
        summary = attendance_model.get_student_summary(student_id)
        eligibility = attendance_model.check_eligibility(student_id)
        subjects = attendance_model.get_student_subject_summary(student_id)
        leave_stats = leave_model.get_leave_stats(student_id)
        threshold = float(config.get("attendance_threshold", 75))

        today = datetime.now().strftime("%Y-%m-%d")
        today_records = attendance_model.get_attendance_records(
            student_id=student_id, from_date=today, to_date=today)

        today_present = sum(1 for r in today_records
                            if r["status"] in ("Present", "Late"))

        self._tile_row(self.body, [
            dict(title="Overall Attendance", value=f"{summary['percentage']:.1f}%",
                 icon="◉", accent=percentage_color(summary["percentage"], threshold),
                 subtitle=f"{summary['attended']} of {summary['total']} classes"),
            dict(title="Today", value=f"{today_present}/{len(today_records)}"
                 if today_records else "-", icon="◷", accent=SEMANTIC["info"],
                 subtitle="classes attended today" if today_records else "no classes yet"),
            dict(title="Exam Eligibility", value=eligibility["verdict"], icon="✓",
                 accent=SEMANTIC["success"] if eligibility["eligible"] else SEMANTIC["danger"],
                 subtitle=(f"threshold {threshold:.0f}%" if eligibility["eligible"]
                           else f"attend {eligibility['shortfall_classes']} more classes")),
            dict(title="Absences", value=str(summary["absent"]), icon="✗",
                 accent=SEMANTIC["danger"] if summary["absent"] > 8 else SEMANTIC["neutral"],
                 subtitle=f"{summary['late']} late arrivals"),
            dict(title="Leave Applications", value=str(leave_stats.get("total", 0)),
                 icon="⎙", accent=SEMANTIC["purple"],
                 subtitle=f"{leave_stats.get('Pending', 0)} pending",
                 on_click=lambda: self.app.navigate("leave")),
        ], columns=5)

        # ---- quick actions ----------------------------------------------
        from models import self_attendance as self_model
        self_stats = self_model.get_stats(student_id=student_id)
        todays_classes = self_model.get_today_classes_for_student(student_id)
        markable = sum(1 for c in todays_classes if c["can_mark"])

        self._tile_row(self.body, [
            dict(title="Mark My Attendance",
                 value=str(markable) if markable else "-", icon="✓",
                 accent=SEMANTIC["success"] if markable else SEMANTIC["neutral"],
                 subtitle=("class(es) you can mark now" if markable
                           else "nothing to mark right now"),
                 on_click=lambda: self.app.navigate("self_attendance")),
            dict(title="Awaiting Approval",
                 value=str(self_stats.get("Pending", 0)), icon="⏳",
                 accent=SEMANTIC["warning"] if self_stats.get("Pending") else SEMANTIC["neutral"],
                 subtitle="sent to your teacher",
                 on_click=lambda: self.app.navigate("self_attendance")),
            dict(title="Classes Today", value=str(len(todays_classes)), icon="◷",
                 accent=SEMANTIC["info"], subtitle="on your timetable",
                 on_click=lambda: self.app.navigate("timetable")),
            dict(title="Apply for Leave", value="Go", icon="⎙",
                 accent=SEMANTIC["purple"], subtitle="casual, medical and more",
                 on_click=lambda: self.app.navigate("leave")),
            dict(title="My Reports", value="Go", icon="▦",
                 accent=SEMANTIC["teal"], subtitle="attendance statements",
                 on_click=lambda: self.app.navigate("reports")),
        ], columns=5)

        # ---- profile + eligibility --------------------------------------
        top = ctk.CTkFrame(self.body, fg_color="transparent")
        top.pack(fill="x", pady=(0, 14))
        top.grid_columnconfigure(0, weight=1, uniform="top")
        top.grid_columnconfigure(1, weight=1, uniform="top")

        profile = SectionCard(top, "My Profile", "Enrolment details on record")
        profile.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        if student:
            for label, value in (
                    ("Enrollment No", student["enrollment_no"]),
                    ("Roll No", student["roll_no"]),
                    ("Branch", student["branch_name"]),
                    ("Semester", student["semester_name"]),
                    ("Section", student["section_name"] or "-"),
                    ("Batch", student["batch_name"] or "-"),
                    ("Academic Session", student["session_name"] or "-"),
                    ("Face Registered",
                     "Yes" if student["face_registered"] else "Not yet captured")):
                InfoRow(profile.body, label, value).pack(fill="x", pady=2)

        status = SectionCard(top, "Attendance Standing",
                             f"College requirement: {threshold:.0f}%")
        status.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        ProgressStat(status.body, "Overall Attendance", summary["percentage"],
                     detail=(f"{summary['attended']} attended  |  "
                             f"{summary['absent']} absent  |  "
                             f"{summary['late']} late  |  "
                             f"{summary['leave'] + summary['medical']} on leave"),
                     bar_color=percentage_color(summary["percentage"], threshold)
                     ).pack(fill="x", pady=(0, 14))

        verdict_colour = (SEMANTIC["success"] if eligibility["eligible"]
                          else SEMANTIC["danger"])
        banner = ctk.CTkFrame(status.body, height=0, corner_radius=8, fg_color=verdict_colour)
        banner.pack(fill="x")
        ctk.CTkLabel(banner,
                     text=("You are eligible to sit the examination."
                           if eligibility["eligible"]
                           else "You are below the attendance requirement."),
                     font=FONTS["body_bold"], text_color="#FFFFFF").pack(pady=(11, 2))
        ctk.CTkLabel(banner,
                     text=(f"Current: {summary['percentage']:.1f}%   |   "
                           f"Required: {threshold:.0f}%" +
                           ("" if eligibility["eligible"]
                            else f"   |   Attend {eligibility['shortfall_classes']} "
                                 "consecutive classes to recover")),
                     font=FONTS["small"], text_color="#FFFFFF").pack(pady=(0, 11))

        # ---- charts ------------------------------------------------------
        charts = ctk.CTkFrame(self.body, fg_color="transparent")
        charts.pack(fill="both", expand=True)
        charts.grid_columnconfigure(0, weight=1, uniform="charts")
        charts.grid_columnconfigure(1, weight=1, uniform="charts")

        trend = SectionCard(charts, "My Attendance Trend", "Last 30 days")
        trend.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._chart(trend.body, analytics_service.student_trend_chart,
                    student_id=student_id, days=30, width=5.6, height=3.2)

        by_subject = SectionCard(charts, "Subject-wise Attendance",
                                 f"{len(subjects)} subject(s)")
        by_subject.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._chart(by_subject.body, analytics_service.subject_wise_chart,
                    student_id=student_id, width=5.6, height=3.2)

        # ---- subject detail ---------------------------------------------
        detail = SectionCard(self.body, "Subject Breakdown",
                             "Attendance in each subject this semester")
        detail.pack(fill="x", pady=(14, 0))

        if not subjects:
            ctk.CTkLabel(detail.body, text="No attendance has been recorded yet.",
                         font=FONTS["body"], text_color=color("text_muted")).pack(pady=20)

        for subject in subjects:
            percentage = float(subject["percentage"] or 0)
            ProgressStat(
                detail.body,
                f"{subject['subject_name']} ({subject['subject_code']})",
                percentage,
                detail=(f"{subject['attended']} of {subject['total_classes']} classes  |  "
                        f"{subject['absent']} absent  |  {subject['late']} late"),
                bar_color=percentage_color(percentage, threshold)).pack(fill="x", pady=6)
