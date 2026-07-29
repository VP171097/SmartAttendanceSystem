"""
Analytics dashboard.

Renders the eight chart families from the specification -- daily, weekly,
monthly, semester / subject / faculty / branch comparison, and student trend --
as embedded Matplotlib figures.

Charts are drawn on demand and cached per configuration, because re-rendering
every figure on each filter change is the difference between an instant screen
and a two-second stall.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from config.settings import config
from config.theme import FONTS, SEMANTIC, color
from core.auth import session
from core.logger import get_logger
from core.scope import current_scope
from models import academic, attendance as attendance_model
from services import analytics_service
from ui.widgets.components import EmptyState, PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import show_error, show_info, show_success, pick_save_path

logger = get_logger("ui.analytics")


class AnalyticsView(ctk.CTkFrame):
    """Charts and comparisons."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._canvases: list[FigureCanvasTkAgg] = []
        self._figures: dict[str, object] = {}

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        self.scope = current_scope()

        header = PageHeader(
            self, title="Analytics",
            subtitle=("Your own attendance trends" if self.scope.is_student
                      else "Trends and comparisons for the classes you teach"
                      if self.scope.is_faculty
                      else "Trends and comparisons across branches, subjects, "
                           "faculty and time"),
            icon="◱")
        header.pack(fill="x", padx=18, pady=(14, 10))
        header.add_button("Refresh Charts", self.refresh, width=145)
        header.add_button("Save Chart", self.save_chart, width=125,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))

        # ---- controls -------------------------------------------------------
        controls = ctk.CTkFrame(self, height=0, corner_radius=10, fg_color=color("surface"),
                                border_width=1, border_color=color("border"))
        controls.pack(fill="x", padx=18, pady=(0, 9))

        inner = ctk.CTkFrame(controls, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)

        self._controls: dict[str, dict] = {}

        def add_control(key: str, label: str, mapping: dict, width: int,
                        default: str | None = None) -> None:
            holder = ctk.CTkFrame(inner, fg_color="transparent")
            holder.pack(side="left", padx=(0, 12))
            ctk.CTkLabel(holder, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), anchor="w").pack(fill="x")
            variable = ctk.StringVar(value=default or list(mapping)[0])
            ctk.CTkOptionMenu(holder, variable=variable, values=list(mapping),
                              width=width, height=34, corner_radius=7,
                              font=FONTS["body"],
                              command=lambda _v: self.refresh()).pack()
            self._controls[key] = {"var": variable, "map": mapping}

        add_control("branch_id", "Branch",
                    {"All Branches": None,
                     **{b["branch_name"]: b["branch_id"]
                        for b in self.scope.allowed_branches()}},
                    190)
        add_control("semester_id", "Semester",
                    {"All Semesters": None,
                     **{s["semester_name"]: s["semester_id"]
                        for s in academic.get_semesters()}}, 145)
        add_control("days", "Trend Window",
                    {"Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30,
                     "Last 60 days": 60, "Last 90 days": 90}, 150, "Last 30 days")
        add_control("year", "Year",
                    {str(y): y for y in range(datetime.now().year - 2,
                                              datetime.now().year + 2)},
                    100, str(datetime.now().year))

        # ---- headline tiles ---------------------------------------------------
        self.tiles_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.tiles_frame.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(5):
            self.tiles_frame.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tiles = {}
        for index, (key, label, icon, accent) in enumerate([
                ("records", "Total Records", "≡", SEMANTIC["info"]),
                ("average", "Average Attendance", "◉", SEMANTIC["success"]),
                ("best", "Best Branch", "★", SEMANTIC["teal"]),
                ("worst", "Needs Attention", "!", SEMANTIC["danger"]),
                ("defaulters", "Defaulters", "▼", SEMANTIC["warning"])]):
            tile = StatCard(self.tiles_frame, label, "-", icon, accent)
            tile.grid(row=0, column=index, sticky="ew", padx=4)
            self.tiles[key] = tile

        # ---- chart tabs ---------------------------------------------------------
        self.tabs = ctk.CTkTabview(
            self, corner_radius=10,
            segmented_button_selected_color=color("primary"),
            segmented_button_selected_hover_color=color("primary_hover"),
            command=self._tab_changed)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        for name in ("Trends", "Comparisons", "Distribution"):
            self.tabs.add(name)
        self.tabs.set("Trends")

    # ==================================================================
    def _control(self, key: str):
        spec = self._controls[key]
        return spec["map"].get(spec["var"].get())

    def _tab_changed(self) -> None:
        self._render_active_tab()

    def refresh(self) -> None:
        self._update_tiles()
        self._render_active_tab()

    def _update_tiles(self) -> None:
        branch_id = self._control("branch_id")
        days = int(self._control("days") or 30)

        to_date = date.today()
        from_date = to_date - timedelta(days=days)

        try:
            series = attendance_model.get_daily_series(
                from_date.isoformat(), to_date.isoformat(), branch_id=branch_id,
                semester_id=self._control("semester_id"))

            total = sum(int(r["total"] or 0) for r in series)
            attended = sum(int(r["attended"] or 0) for r in series)
            average = (100.0 * attended / total) if total else 0.0

            comparison = attendance_model.get_comparison(
                "branch", from_date.isoformat(), to_date.isoformat())
            defaulters = attendance_model.get_defaulters(
                branch_id=branch_id, semester_id=self._control("semester_id"))
        except Exception as exc:                # noqa: BLE001
            logger.error("Analytics tiles failed: %s", exc, exc_info=True)
            return

        from config.theme import percentage_color
        threshold = float(config.get("attendance_threshold", 75))

        self.tiles["records"].update_value(f"{total:,}", f"over the last {days} days")
        self.tiles["average"].update_value(
            f"{average:.1f}%", f"{attended:,} attended",
            accent=percentage_color(average, threshold))

        if comparison:
            best = comparison[0]
            worst = comparison[-1]
            self.tiles["best"].update_value(
                f"{float(best['percentage'] or 0):.1f}%", str(best["label"])[:24])
            self.tiles["worst"].update_value(
                f"{float(worst['percentage'] or 0):.1f}%", str(worst["label"])[:24])
        else:
            self.tiles["best"].update_value("-", "no data")
            self.tiles["worst"].update_value("-", "no data")

        self.tiles["defaulters"].update_value(
            str(len(defaulters)), f"below {threshold:.0f}%")

    # ==================================================================
    def _render_active_tab(self) -> None:
        tab_name = self.tabs.get()
        tab = self.tabs.tab(tab_name)

        for widget in tab.winfo_children():
            widget.destroy()
        self._canvases = [c for c in self._canvases
                          if c.get_tk_widget().winfo_exists()]

        container = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        container.pack(fill="both", expand=True)

        if tab_name == "Trends":
            self._render_trends(container)
        elif tab_name == "Comparisons":
            self._render_comparisons(container)
        else:
            self._render_distribution(container)

    def _add_chart(self, parent, title: str, subtitle: str, builder,
                   key: str, **kwargs) -> None:
        """Build one chart card, catching failures so one bad chart is isolated."""
        card = SectionCard(parent, title, subtitle)
        card.pack(fill="both", expand=True, pady=(0, 14))

        try:
            figure = builder(mode=ctk.get_appearance_mode(), **kwargs)
            self._figures[key] = figure

            canvas = FigureCanvasTkAgg(figure, master=card.body)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="both", expand=True)
            self._canvases.append(canvas)
        except Exception as exc:                # noqa: BLE001
            logger.error("Chart '%s' failed: %s", title, exc, exc_info=True)
            ctk.CTkLabel(card.body, text=f"This chart could not be rendered.\n\n{exc}",
                         font=FONTS["small"], text_color=SEMANTIC["danger"],
                         wraplength=600, justify="center").pack(pady=30)

    def _render_trends(self, parent) -> None:
        branch_id = self._control("branch_id")
        semester_id = self._control("semester_id")
        days = int(self._control("days") or 30)
        year = int(self._control("year") or datetime.now().year)

        self._add_chart(
            parent, "Daily Attendance Trend",
            f"Percentage per day over the last {days} days",
            analytics_service.daily_trend_chart, "daily",
            days=days, branch_id=branch_id, semester_id=semester_id,
            width=12.2, height=3.5)

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="both", expand=True)
        row.grid_columnconfigure(0, weight=1, uniform="row")
        row.grid_columnconfigure(1, weight=1, uniform="row")

        weekly = SectionCard(row, "Weekly Attendance", "Aggregated by ISO week")
        weekly.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._embed(weekly.body, analytics_service.weekly_chart, "weekly",
                    weeks=max(4, days // 7), branch_id=branch_id,
                    width=6.0, height=3.4)

        monthly = SectionCard(row, "Monthly Attendance", f"Calendar year {year}")
        monthly.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._embed(monthly.body, analytics_service.monthly_trend_chart, "monthly",
                    year=year, branch_id=branch_id, semester_id=semester_id,
                    width=6.0, height=3.4)

    def _render_comparisons(self, parent) -> None:
        days = int(self._control("days") or 30)
        to_date = date.today().isoformat()
        from_date = (date.today() - timedelta(days=days)).isoformat()

        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure(0, weight=1, uniform="grid")
        grid.grid_columnconfigure(1, weight=1, uniform="grid")

        # A student has no peers to compare against, and a lecturer has no
        # business seeing a colleague's numbers -- so the comparison set is
        # chosen to match what each role may legitimately look at.
        if self.scope.is_student:
            specs = [
                ("My Subject Comparison", "Your attendance in each subject",
                 analytics_service.subject_comparison_chart, "subject"),
            ]
        elif self.scope.is_faculty:
            specs = [
                ("My Subject Comparison",
                 "Attendance across the subjects you teach",
                 analytics_service.subject_comparison_chart, "subject"),
                ("My Semester Comparison",
                 "Attendance across the semesters you teach",
                 analytics_service.semester_comparison_chart, "semester"),
                ("My Branch Comparison",
                 "Attendance across the branches you teach in",
                 analytics_service.branch_comparison_chart, "branch"),
            ]
        else:
            specs = [
                ("Branch Comparison", "Average attendance by branch",
                 analytics_service.branch_comparison_chart, "branch"),
                ("Subject Comparison", "Average attendance by subject",
                 analytics_service.subject_comparison_chart, "subject"),
                ("Faculty Comparison", "Attendance achieved in each faculty's classes",
                 analytics_service.faculty_comparison_chart, "faculty"),
                ("Semester Comparison", "Average attendance by semester",
                 analytics_service.semester_comparison_chart, "semester"),
            ]

        for index, (title, subtitle, builder, key) in enumerate(specs):
            card = SectionCard(grid, title, subtitle)
            card.grid(row=index // 2, column=index % 2, sticky="nsew",
                      padx=(0, 7) if index % 2 == 0 else (7, 0), pady=(0, 14))
            self._embed(card.body, builder, key,
                        from_date=from_date, to_date=to_date, width=6.0, height=3.6)

    def _render_distribution(self, parent) -> None:
        branch_id = self._control("branch_id")
        semester_id = self._control("semester_id")
        days = int(self._control("days") or 30)
        to_date = date.today().isoformat()
        from_date = (date.today() - timedelta(days=days)).isoformat()

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="both", expand=True, pady=(0, 14))
        row.grid_columnconfigure(0, weight=1, uniform="row")
        row.grid_columnconfigure(1, weight=1, uniform="row")

        donut = SectionCard(row, "Status Distribution",
                            "Present / Absent / Late / Leave breakdown")
        donut.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._embed(donut.body, analytics_service.status_donut_chart, "status",
                    from_date=from_date, to_date=to_date, branch_id=branch_id,
                    width=6.0, height=3.8)

        bands = SectionCard(row, "Attendance Bands",
                            "How many students sit in each attendance range")
        bands.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._embed(bands.body, analytics_service.defaulter_distribution_chart, "bands",
                    branch_id=branch_id, semester_id=semester_id,
                    width=6.0, height=3.8)

        # ---- defaulter table ------------------------------------------------
        threshold = float(config.get("attendance_threshold", 75))
        defaulters = attendance_model.get_defaulters(
            threshold, branch_id=branch_id, semester_id=semester_id)

        card = SectionCard(parent, "Students Below Threshold",
                           f"{len(defaulters)} student(s) under {threshold:.0f}%",
                           action_text="Full Report",
                           action_command=lambda: self.app.navigate("reports"))
        card.pack(fill="both", expand=True)

        if not defaulters:
            ctk.CTkLabel(card.body,
                         text="No defaulters in this selection - every student is eligible.",
                         font=FONTS["body"], text_color=SEMANTIC["success"]).pack(pady=28)
            return

        from ui.widgets.table import DataTable, column, percent_format
        table = DataTable(
            card.body,
            columns=[
                column("roll_no", "Roll", 60, "center"),
                column("enrollment_no", "Enrollment No", 115),
                column("full_name", "Student Name", 190, stretch=True),
                column("branch_code", "Branch", 75, "center"),
                column("semester_name", "Semester", 105),
                column("section_name", "Sec", 50, "center"),
                column("total_classes", "Classes", 80, "center"),
                column("attended", "Attended", 85, "center"),
                column("percentage", "Attendance", 100, "center", format=percent_format),
                column("mobile", "Mobile", 105),
            ],
            height=10)
        table.pack(fill="both", expand=True)
        table.set_data(defaulters)

    def _embed(self, parent, builder, key: str, **kwargs) -> None:
        try:
            figure = builder(mode=ctk.get_appearance_mode(), **kwargs)
            self._figures[key] = figure
            canvas = FigureCanvasTkAgg(figure, master=parent)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="both", expand=True)
            self._canvases.append(canvas)
        except Exception as exc:                # noqa: BLE001
            logger.error("Chart '%s' failed: %s", key, exc, exc_info=True)
            ctk.CTkLabel(parent, text=f"Chart unavailable.\n\n{exc}",
                         font=FONTS["small"], text_color=SEMANTIC["danger"],
                         wraplength=460, justify="center").pack(pady=26)

    # ==================================================================
    def save_chart(self) -> None:
        """Export the most recently rendered chart as a PNG."""
        if not self._figures:
            show_info(self, "No Chart", "Render a chart first.")
            return

        key = list(self._figures)[-1]
        path = pick_save_path(
            self, "Save chart as image",
            f"chart_{key}_{datetime.now():%Y%m%d_%H%M%S}.png",
            [("PNG image", "*.png")])
        if not path:
            return

        if analytics_service.save_chart(self._figures[key], path):
            show_success(self, "Chart Saved", f"Saved to:\n{path}")
        else:
            show_error(self, "Could Not Save", "The chart image could not be written.")

    def refresh_theme(self) -> None:
        """Re-render every chart after a Light/Dark switch."""
        self._render_active_tab()
