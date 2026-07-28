"""
Timetable management.

Two ways to look at the same data:

*   **Weekly grid** -- the board a class or a faculty member reads.
*   **Slot list** -- the table an administrator edits.

Adding or moving a slot runs three-way clash detection (class, faculty, room)
before anything is written, and reports every conflict it found rather than
just the first.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from config.settings import DAYS_OF_WEEK
from config.theme import CHART_COLORS, FONTS, SEMANTIC, color
from core.auth import Permission, session
from core.logger import get_logger
from core.validators import validate_time
from models import academic, faculty as faculty_model, subject as subject_model
from models import timetable as timetable_model
from ui.widgets.components import EmptyState, PageHeader, StatCard
from ui.widgets.dialogs import (FormDialog, FormField, ask_confirm, show_error,
                                show_info, show_success, show_warning)
from ui.widgets.table import DataTable, column, dash_format

logger = get_logger("ui.timetable")

PERIOD_PRESETS = [
    ("09:00", "10:00"), ("10:00", "11:00"), ("11:15", "12:15"),
    ("12:15", "13:15"), ("14:00", "15:00"), ("15:00", "16:00"),
    ("16:00", "17:00"),
]


class TimetableView(ctk.CTkFrame):
    """Weekly schedule editor and viewer."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._selected: dict | None = None
        self._can_edit = session.can(Permission.MANAGE_TIMETABLE)

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Timetable",
            subtitle="Weekly class schedule with clash detection across class, faculty and room",
            icon="◷")
        header.pack(fill="x", padx=18, pady=(14, 10))

        if self._can_edit:
            header.add_button("+  Add Slot", self.add_slot, width=130)
            header.add_button("Copy Day", self.copy_day, width=115,
                              fg_color="transparent", border_width=1,
                              border_color=color("border"), text_color=color("text"),
                              hover_color=color("surface_alt"))

        # ---- filters ---------------------------------------------------------
        filter_card = ctk.CTkFrame(self, height=0, corner_radius=10, fg_color=color("surface"),
                                   border_width=1, border_color=color("border"))
        filter_card.pack(fill="x", padx=18, pady=(0, 9))

        inner = ctk.CTkFrame(filter_card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        self._selectors: dict[str, dict] = {}

        def add_selector(key: str, label: str, mapping: dict, width: int,
                         include_all: bool = False) -> None:
            holder = ctk.CTkFrame(inner, fg_color="transparent")
            holder.pack(side="left", padx=(0, 12))
            ctk.CTkLabel(holder, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), anchor="w").pack(fill="x")

            options = {"All": None, **mapping} if include_all else dict(mapping)
            variable = tk.StringVar(value=list(options)[0] if options else "-")
            widget = ctk.CTkOptionMenu(holder, variable=variable,
                                       values=list(options) or ["-"], width=width,
                                       height=34, corner_radius=7, font=FONTS["body"],
                                       command=lambda _v: self.refresh())
            widget.pack()
            self._selectors[key] = {"var": variable, "widget": widget, "map": options}

        add_selector("branch_id", "Branch",
                     {b["branch_name"]: b["branch_id"] for b in academic.get_branches()},
                     190)
        add_selector("semester_id", "Semester",
                     {s["semester_name"]: s["semester_id"]
                      for s in academic.get_semesters()}, 135)
        add_selector("section_id", "Section",
                     {s["section_name"]: s["section_id"]
                      for s in academic.get_sections()}, 95)
        add_selector("faculty_id", "Faculty",
                     {f["full_name"]: f["faculty_id"]
                      for f in faculty_model.get_all_faculty()}, 190, include_all=True)

        # ---- tiles ------------------------------------------------------------
        tiles = ctk.CTkFrame(inner, fg_color="transparent")
        tiles.pack(side="right")
        ctk.CTkLabel(tiles, text=" ", font=FONTS["small_bold"]).pack(fill="x")
        self.summary_label = ctk.CTkLabel(tiles, text="", font=FONTS["small"],
                                          text_color=color("text_muted"))
        self.summary_label.pack()

        # ---- tabs ---------------------------------------------------------------
        self.tabs = ctk.CTkTabview(self, corner_radius=10,
                                   segmented_button_selected_color=color("primary"),
                                   segmented_button_selected_hover_color=color("primary_hover"))
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.tabs.add("Weekly Grid")
        self.tabs.add("Slot List")

        self.grid_frame = ctk.CTkScrollableFrame(self.tabs.tab("Weekly Grid"),
                                                 fg_color="transparent")
        self.grid_frame.pack(fill="both", expand=True)

        self._build_slot_list()
        self.tabs.set("Weekly Grid")

    def _build_slot_list(self) -> None:
        tab = self.tabs.tab("Slot List")

        if self._can_edit:
            bar = ctk.CTkFrame(tab, fg_color="transparent")
            bar.pack(fill="x", pady=(6, 10))
            ctk.CTkButton(bar, text="Edit Selected", command=self.edit_slot, width=140,
                          height=34, corner_radius=7, font=FONTS["small_bold"],
                          fg_color=SEMANTIC["teal"]).pack(side="left", padx=(0, 8))
            ctk.CTkButton(bar, text="Delete Selected", command=self.delete_slot, width=150,
                          height=34, corner_radius=7, font=FONTS["small_bold"],
                          fg_color=SEMANTIC["danger"]).pack(side="left")

        self.table = DataTable(
            tab,
            columns=[
                column("day_of_week", "Day", 105),
                column("start_time", "Start", 70, "center"),
                column("end_time", "End", 70, "center"),
                column("subject_code", "Code", 80),
                column("subject_name", "Subject", 200, stretch=True),
                column("faculty_name", "Faculty", 175, format=dash_format),
                column("branch_code", "Branch", 75, "center"),
                column("semester_name", "Semester", 105),
                column("section_name", "Sec", 50, "center"),
                column("room_no", "Room", 80, format=dash_format),
            ],
            on_select=lambda row: setattr(self, "_selected", row),
            on_double_click=lambda _row: self.edit_slot() if self._can_edit else None,
            height=15)
        self.table.pack(fill="both", expand=True)

    # ==================================================================
    def _selected_value(self, key: str):
        spec = self._selectors[key]
        return spec["map"].get(spec["var"].get())

    def refresh(self) -> None:
        branch_id = self._selected_value("branch_id")
        semester_id = self._selected_value("semester_id")
        section_id = self._selected_value("section_id")
        faculty_id = self._selected_value("faculty_id")

        slots = timetable_model.search_timetable(
            branch_id=branch_id, semester_id=semester_id,
            section_id=section_id, faculty_id=faculty_id)

        self.table.set_data(slots)
        self._render_grid(slots)

        total_hours = 0.0
        for slot in slots:
            try:
                start_h, start_m = map(int, str(slot["start_time"]).split(":"))
                end_h, end_m = map(int, str(slot["end_time"]).split(":"))
                total_hours += ((end_h * 60 + end_m) - (start_h * 60 + start_m)) / 60
            except (ValueError, AttributeError):
                pass

        self.summary_label.configure(
            text=f"{len(slots)} slot(s)  |  {total_hours:.1f} hours per week")

    def _render_grid(self, slots: list) -> None:
        for widget in self.grid_frame.winfo_children():
            widget.destroy()

        if not slots:
            EmptyState(
                self.grid_frame, "◷", "No timetable for this selection",
                "Choose a different class, or add slots to build the weekly schedule."
                + ("" if self._can_edit else " Ask an administrator to set it up."),
                action_text="+  Add Slot" if self._can_edit else "",
                action_command=self.add_slot if self._can_edit else None
            ).pack(fill="both", expand=True, pady=60)
            return

        # Distinct start times become the grid's rows.
        times = sorted({(s["start_time"], s["end_time"]) for s in slots})
        days = [d for d in DAYS_OF_WEEK if any(s["day_of_week"] == d for s in slots)]

        grid = ctk.CTkFrame(self.grid_frame, fg_color="transparent")
        grid.pack(fill="both", expand=True, pady=8)

        grid.grid_columnconfigure(0, weight=0, minsize=110)
        for index in range(len(days)):
            grid.grid_columnconfigure(index + 1, weight=1, uniform="days")

        # ---- header row --------------------------------------------------
        ctk.CTkLabel(grid, text="TIME", font=(FONTS["small"][0], 10, "bold"),
                     text_color="#FFFFFF", fg_color=color("header"),
                     corner_radius=6, height=38).grid(
                         row=0, column=0, sticky="nsew", padx=2, pady=2)

        for index, day in enumerate(days):
            ctk.CTkLabel(grid, text=day.upper(), font=(FONTS["small"][0], 10, "bold"),
                         text_color="#FFFFFF", fg_color=color("header"),
                         corner_radius=6, height=38).grid(
                             row=0, column=index + 1, sticky="nsew", padx=2, pady=2)

        # Stable colour per subject makes the board readable at a glance.
        subject_colours: dict[int, str] = {}
        for slot in slots:
            if slot["subject_id"] not in subject_colours:
                subject_colours[slot["subject_id"]] = CHART_COLORS[
                    len(subject_colours) % len(CHART_COLORS)]

        # ---- body ---------------------------------------------------------
        for row_index, (start, end) in enumerate(times, start=1):
            ctk.CTkLabel(grid, text=f"{start}\n{end}", font=(FONTS["small"][0], 10, "bold"),
                         text_color=color("text"), fg_color=color("surface_alt"),
                         corner_radius=6, height=74).grid(
                             row=row_index, column=0, sticky="nsew", padx=2, pady=2)

            for col_index, day in enumerate(days):
                match = next((s for s in slots
                              if s["day_of_week"] == day
                              and s["start_time"] == start and s["end_time"] == end), None)

                if match is None:
                    ctk.CTkFrame(grid, corner_radius=6, fg_color=color("surface"),
                                 border_width=1, border_color=color("border"),
                                 height=74).grid(
                                     row=row_index, column=col_index + 1,
                                     sticky="nsew", padx=2, pady=2)
                    continue

                accent = subject_colours[match["subject_id"]]
                cell = ctk.CTkFrame(grid, corner_radius=6, fg_color=color("surface"),
                                    border_width=1, border_color=color("border"),
                                    height=74)
                cell.grid(row=row_index, column=col_index + 1, sticky="nsew",
                          padx=2, pady=2)
                cell.grid_propagate(False)

                ctk.CTkFrame(cell, height=4, corner_radius=2, fg_color=accent).pack(
                    fill="x", padx=6, pady=(6, 4))

                ctk.CTkLabel(cell, text=str(match["subject_code"]),
                             font=(FONTS["small"][0], 11, "bold"),
                             text_color=accent).pack()
                ctk.CTkLabel(cell, text=str(match["subject_name"])[:22],
                             font=(FONTS["small"][0], 9), text_color=color("text"),
                             wraplength=140).pack()
                ctk.CTkLabel(cell,
                             text=(f"{str(match['faculty_name'] or '-')[:18]}\n"
                                   f"Room {match['room_no'] or '-'}"),
                             font=(FONTS["small"][0], 8),
                             text_color=color("text_muted")).pack()

                if self._can_edit:
                    for widget in (cell,) + tuple(cell.winfo_children()):
                        widget.bind("<Double-Button-1>",
                                    lambda _e, s=match: self._edit_specific(s))
                        widget.configure(cursor="hand2")

    # ==================================================================
    # Editing
    # ==================================================================
    def _form_fields(self, defaults: dict | None = None) -> list[FormField]:
        defaults = defaults or {}
        branch_id = defaults.get("branch_id") or self._selected_value("branch_id")
        semester_id = defaults.get("semester_id") or self._selected_value("semester_id")

        subjects = (subject_model.get_class_subjects(branch_id, semester_id)
                    if branch_id and semester_id else subject_model.search_subjects())

        return [
            FormField("day_of_week", "Day", "select", required=True,
                      options={d: d for d in DAYS_OF_WEEK}),
            FormField("start_time", "Start Time", required=True,
                      validator=lambda v: validate_time(v, "Start time"),
                      placeholder="09:00", hint="24-hour HH:MM"),
            FormField("end_time", "End Time", required=True,
                      validator=lambda v: validate_time(v, "End time"),
                      placeholder="10:00"),
            FormField("room_no", "Room", placeholder="CR-101"),
            FormField("branch_id", "Branch", "select", required=True,
                      options={b["branch_name"]: b["branch_id"]
                               for b in academic.get_branches()}),
            FormField("semester_id", "Semester", "select", required=True,
                      options={s["semester_name"]: s["semester_id"]
                               for s in academic.get_semesters()}),
            FormField("section_id", "Section", "select", required=True,
                      options={s["section_name"]: s["section_id"]
                               for s in academic.get_sections()}),
            FormField("subject_id", "Subject", "select", required=True,
                      options={f"{s['subject_code']} - {s['subject_name']}": s["subject_id"]
                               for s in subjects},
                      hint="Only subjects for the selected class are listed"),
            FormField("faculty_id", "Faculty", "select", span=2,
                      options={"Use subject's assigned faculty": None,
                               **{f["full_name"]: f["faculty_id"]
                                  for f in faculty_model.get_all_faculty()}}),
        ]

    def add_slot(self) -> None:
        branch_id = self._selected_value("branch_id")
        semester_id = self._selected_value("semester_id")
        section_id = self._selected_value("section_id")

        if not (branch_id and semester_id):
            show_warning(self, "Select a Class",
                         "Choose a branch and semester in the filters first, so the "
                         "subject list can be narrowed to that class.")
            return

        subjects = subject_model.get_class_subjects(branch_id, semester_id)
        if not subjects:
            show_warning(self, "No Subjects",
                         "This class has no subjects yet.\n\n"
                         "Add subjects from the Subjects screen before building "
                         "the timetable.")
            return

        defaults = {
            "branch_id": branch_id, "semester_id": semester_id,
            "section_id": section_id, "day_of_week": DAYS_OF_WEEK[0],
            "start_time": PERIOD_PRESETS[0][0], "end_time": PERIOD_PRESETS[0][1],
        }

        values = FormDialog(self, "Add Timetable Slot", self._form_fields(defaults),
                            values=defaults, submit_text="Add Slot",
                            width=760, height=520,
                            description="Clashes are checked before saving").show()
        if not values:
            return

        self._save_slot(values, timetable_id=None)

    def _edit_specific(self, slot: dict) -> None:
        self._selected = slot
        self.edit_slot()

    def edit_slot(self) -> None:
        if not self._selected:
            show_info(self, "No Selection",
                      "Select a slot from the Slot List, or double-click a cell "
                      "in the Weekly Grid.")
            return

        record = timetable_model.get_slot(self._selected["timetable_id"])
        if record is None:
            show_error(self, "Not Found", "This timetable slot no longer exists.")
            self.refresh()
            return

        values = FormDialog(
            self, "Edit Timetable Slot", self._form_fields(dict(record)),
            values=dict(record), submit_text="Save Changes", width=760, height=520,
            description=f"{record['day_of_week']} "
                        f"{record['start_time']}-{record['end_time']}").show()

        if not values:
            return

        self._save_slot(values, timetable_id=record["timetable_id"])

    def _save_slot(self, values: dict, timetable_id: int | None) -> None:
        """Save, offering an override when a clash is reported."""
        values["session_id"] = academic.get_current_session_id()

        if timetable_id:
            ok, message = timetable_model.update_slot(
                timetable_id, values, session.user, allow_clash=False)
        else:
            ok, message, _ = timetable_model.add_slot(
                values, session.user, allow_clash=False)

        if ok:
            show_success(self, "Timetable Updated", message)
            self.refresh()
            return

        if not message.startswith("Timetable clash"):
            show_error(self, "Could Not Save", message)
            return

        # A clash is sometimes intentional (a combined class, a shared lab).
        if not ask_confirm(
                self, "Timetable Clash Detected",
                f"{message}\n\n"
                "Saving anyway will create a genuine double-booking.\n\n"
                "Only continue if this is intentional, such as a combined class.",
                confirm_text="Save Anyway", danger=True):
            return

        if timetable_id:
            ok, message = timetable_model.update_slot(
                timetable_id, values, session.user, allow_clash=True)
        else:
            ok, message, _ = timetable_model.add_slot(
                values, session.user, allow_clash=True)

        if ok:
            show_success(self, "Timetable Updated", f"{message} (clash overridden)")
            self.refresh()
        else:
            show_error(self, "Could Not Save", message)

    def delete_slot(self) -> None:
        if not self._selected:
            show_info(self, "No Selection", "Select a slot from the Slot List first.")
            return

        record = self._selected
        if not ask_confirm(
                self, "Delete Timetable Slot",
                f"Delete {record['day_of_week']} "
                f"{record['start_time']}-{record['end_time']}?\n\n"
                f"{record['subject_name']} ({record['subject_code']})\n\n"
                "Attendance already recorded against this slot is preserved.",
                confirm_text="Delete", danger=True):
            return

        ok, message = timetable_model.delete_slot(record["timetable_id"], session.user)
        if ok:
            show_success(self, "Slot Deleted", message)
            self._selected = None
            self.refresh()
        else:
            show_error(self, "Could Not Delete", message)

    def copy_day(self) -> None:
        """Duplicate a whole day's schedule onto another day."""
        branch_id = self._selected_value("branch_id")
        semester_id = self._selected_value("semester_id")
        section_id = self._selected_value("section_id")

        if not (branch_id and semester_id and section_id):
            show_warning(self, "Select a Class",
                         "Choose a branch, semester and section in the filters first.")
            return

        values = FormDialog(
            self, "Copy Day Schedule",
            [
                FormField("source_day", "Copy From", "select", required=True,
                          options={d: d for d in DAYS_OF_WEEK}, default="Monday"),
                FormField("target_day", "Copy To", "select", required=True,
                          options={d: d for d in DAYS_OF_WEEK}, default="Tuesday"),
            ],
            submit_text="Copy Schedule", width=600, height=310,
            description="Duplicates every slot from one day to another. "
                        "Clashing slots are skipped.").show()

        if not values:
            return

        if values["source_day"] == values["target_day"]:
            show_warning(self, "Same Day", "Choose two different days.")
            return

        ok, message, copied = timetable_model.copy_day(
            values["source_day"], values["target_day"],
            branch_id, semester_id, section_id, session.user)

        if ok:
            show_success(self, "Schedule Copied", message)
            self.refresh()
        else:
            show_error(self, "Could Not Copy", message)
