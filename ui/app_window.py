"""
Main application shell.

Owns the three fixed regions -- top bar, sidebar navigation rail, content area
-- and swaps views in and out of the content area.

Navigation is **permission-driven**: :data:`NAV_ITEMS` declares the permission
each destination needs and the sidebar simply omits anything the signed-in role
cannot use.  Adding a screen therefore never means editing three role branches.

Views are constructed lazily on first visit and cached, so switching back to a
screen is instant while start-up stays fast.
"""

from __future__ import annotations

import importlib
import tkinter as tk
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from config.settings import (APP_NAME, APP_VERSION, ROLE_ADMIN, ROLE_FACULTY,
                             ROLE_STUDENT, config)
from config.theme import FONTS, SEMANTIC, apply_theme, color, palette
from core.audit import log_activity
from core.auth import Permission, logout, session
from core.backup import auto_backup_if_due
from core.logger import get_logger
from ui.widgets.components import StatusBar
from ui.widgets.dialogs import ask_confirm, show_error

logger = get_logger("ui.app")

SIDEBAR_WIDTH = 232
SIDEBAR_COLLAPSED = 64


class NavItem:
    """One sidebar destination."""

    def __init__(self, key: str, label: str, icon: str, module: str, klass: str,
                 permission: str | tuple | None = None, roles: tuple | None = None,
                 group: str = "Main"):
        self.key = key
        self.label = label
        self.icon = icon
        self.module = module
        self.klass = klass
        # A tuple means "any one of these is enough" -- Leave Management, for
        # instance, is reachable by students who apply and faculty who review.
        self.permissions = ((permission,) if isinstance(permission, str)
                            else tuple(permission or ()))
        self.roles = roles
        self.group = group

    def visible_for(self, role: str) -> bool:
        if self.roles and role not in self.roles:
            return False
        if self.permissions and not any(session.can(p) for p in self.permissions):
            return False
        return True


# ---------------------------------------------------------------------------
# Navigation map.  Order here is the order in the sidebar.
# ---------------------------------------------------------------------------
NAV_ITEMS = [
    NavItem("dashboard", "Dashboard", "▤", "ui.views.dashboard", "DashboardView",
            Permission.VIEW_DASHBOARD, group="Main"),

    NavItem("attendance_face", "Take Attendance", "◉", "ui.views.attendance_face",
            "FaceAttendanceView", Permission.TAKE_ATTENDANCE, group="Attendance"),
    NavItem("attendance_manual", "Manual Attendance", "✓", "ui.views.attendance_manual",
            "ManualAttendanceView", Permission.EDIT_ATTENDANCE, group="Attendance"),
    # Students raise self-attendance here; faculty verify it here.  Both halves
    # live in one view because they are two sides of the same workflow.
    NavItem("self_attendance", "Mark My Attendance", "✓", "ui.views.self_attendance",
            "SelfAttendanceView", Permission.MARK_SELF_ATTENDANCE,
            roles=(ROLE_STUDENT,), group="Attendance"),
    NavItem("verify_attendance", "Verify Attendance", "⚖", "ui.views.self_attendance",
            "SelfAttendanceView", Permission.VERIFY_SELF_ATTENDANCE,
            roles=(ROLE_ADMIN, ROLE_FACULTY), group="Attendance"),
    NavItem("attendance_records", "Attendance Records", "≡", "ui.views.attendance_records",
            "AttendanceRecordsView", Permission.VIEW_REPORTS, group="Attendance"),

    NavItem("students", "Students", "☺", "ui.views.students", "StudentsView",
            Permission.MANAGE_STUDENTS, group="People"),
    NavItem("faculty", "Faculty", "★", "ui.views.faculty", "FacultyView",
            Permission.MANAGE_FACULTY, group="People"),

    NavItem("academic", "Academic Setup", "⌂", "ui.views.academic", "AcademicView",
            Permission.MANAGE_ACADEMIC, group="Academics"),
    NavItem("subjects", "Subjects", "▣", "ui.views.subjects", "SubjectsView",
            Permission.MANAGE_SUBJECTS, group="Academics"),
    NavItem("timetable", "Timetable", "◷", "ui.views.timetable", "TimetableView",
            Permission.VIEW_DASHBOARD, group="Academics"),

    # Students apply, faculty review, admins override -- all the same screen.
    NavItem("leave", "Leave Management", "⎙", "ui.views.leave", "LeaveView",
            (Permission.APPLY_LEAVE, Permission.REVIEW_LEAVE,
             Permission.OVERRIDE_LEAVE), group="Workflow"),

    NavItem("reports", "Reports", "▦", "ui.views.reports", "ReportsView",
            Permission.VIEW_REPORTS, group="Insights"),
    NavItem("analytics", "Analytics", "◱", "ui.views.analytics", "AnalyticsView",
            Permission.VIEW_ANALYTICS, group="Insights"),

    NavItem("audit", "Audit Trail", "⚿", "ui.views.audit", "AuditView",
            Permission.VIEW_AUDIT, group="System"),
    NavItem("settings", "Settings", "⚙", "ui.views.settings", "SettingsView",
            Permission.VIEW_DASHBOARD, group="System"),
    NavItem("about", "About", "ⓘ", "ui.views.about", "AboutView",
            Permission.VIEW_DASHBOARD, group="System"),
]

GROUP_ORDER = ["Main", "Attendance", "People", "Academics", "Workflow",
               "Insights", "System"]


class AppWindow(ctk.CTkFrame):
    """The signed-in application surface, hosted inside the root window."""

    def __init__(self, master, on_logout):
        super().__init__(master, fg_color=color("bg"), corner_radius=0)

        self.on_logout = on_logout
        self._views: dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._active_key: str | None = None
        self._sidebar_collapsed = False
        self._clock_job = None

        self.pack(fill="both", expand=True)

        self._build_topbar()
        self._build_body()
        self._build_statusbar()

        self._populate_nav()
        self._start_clock()

        # A daily automatic backup, taken quietly a moment after start-up.
        self.after(2500, self._maybe_auto_backup)

        self.navigate("dashboard")

    # ==================================================================
    # Top bar
    # ==================================================================
    def _build_topbar(self) -> None:
        bar = ctk.CTkFrame(self, height=62, corner_radius=0,
                           fg_color=color("surface"))
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", fill="y", padx=(14, 0))

        ctk.CTkButton(left, text="☰", width=38, height=38, corner_radius=7,
                      font=(FONTS["heading"][0], 16), fg_color="transparent",
                      text_color=color("text"), hover_color=color("surface_alt"),
                      command=self.toggle_sidebar).pack(side="left", pady=12)

        # ---- college identity -------------------------------------------
        brand = ctk.CTkFrame(left, fg_color="transparent")
        brand.pack(side="left", padx=(10, 0))

        logo_path = Path(config.get("logo_path", ""))
        if logo_path.exists():
            try:
                from PIL import Image
                image = ctk.CTkImage(Image.open(logo_path), size=(34, 34))
                ctk.CTkLabel(brand, image=image, text="").pack(side="left", padx=(0, 10))
            except Exception:                   # noqa: BLE001
                pass

        titles = ctk.CTkFrame(brand, fg_color="transparent")
        titles.pack(side="left")

        college_name = config.get("college_name", "College")
        ctk.CTkLabel(titles, text=college_name[:46],
                     font=(FONTS["subhead"][0], 14, "bold"),
                     text_color=color("text"), anchor="w").pack(fill="x")
        ctk.CTkLabel(titles, text=f"{APP_NAME}  |  Session {config.get('current_academic_session','')}",
                     font=FONTS["small"], text_color=color("text_muted"),
                     anchor="w").pack(fill="x")

        # ---- right-hand controls ----------------------------------------
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=(0, 16))

        self.clock_label = ctk.CTkLabel(right, text="", font=FONTS["small"],
                                        text_color=color("text_muted"))
        self.clock_label.pack(side="left", padx=(0, 16))

        self.theme_button = ctk.CTkButton(
            right, text="◐", width=38, height=38, corner_radius=7,
            font=(FONTS["heading"][0], 15), fg_color="transparent",
            text_color=color("text"), hover_color=color("surface_alt"),
            command=self.toggle_theme)
        self.theme_button.pack(side="left", padx=(0, 8))

        # ---- user chip ----------------------------------------------------
        chip = ctk.CTkFrame(right, corner_radius=8, fg_color=color("surface_alt"))
        chip.pack(side="left")

        initials = "".join(w[0] for w in session.full_name.split()[:2]).upper() or "U"
        ctk.CTkLabel(chip, text=initials, font=FONTS["small_bold"],
                     text_color="#FFFFFF", fg_color=color("primary"),
                     corner_radius=16, width=32, height=32).pack(
                         side="left", padx=(6, 9), pady=6)

        info = ctk.CTkFrame(chip, fg_color="transparent")
        info.pack(side="left", padx=(0, 12))
        ctk.CTkLabel(info, text=session.full_name[:26], font=FONTS["small_bold"],
                     text_color=color("text"), anchor="w").pack(fill="x")
        ctk.CTkLabel(info, text=session.role, font=(FONTS["small"][0], 10),
                     text_color=color("text_muted"), anchor="w").pack(fill="x")

        ctk.CTkButton(right, text="Sign Out", width=90, height=34, corner_radius=7,
                      font=FONTS["small_bold"], fg_color="transparent",
                      border_width=1, border_color=color("border"),
                      text_color=color("text"), hover_color=color("surface_alt"),
                      command=self.sign_out).pack(side="left", padx=(10, 0))

        ctk.CTkFrame(self, height=1, fg_color=color("border"),
                     corner_radius=0).pack(fill="x")

    # ==================================================================
    # Body: sidebar + content
    # ==================================================================
    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)

        self.sidebar = ctk.CTkFrame(body, width=SIDEBAR_WIDTH, corner_radius=0,
                                    fg_color=color("sidebar"))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.nav_container = ctk.CTkScrollableFrame(
            self.sidebar, fg_color="transparent", scrollbar_button_color=color("sidebar"),
            scrollbar_button_hover_color=color("sidebar_hover"))
        self.nav_container.pack(fill="both", expand=True, padx=8, pady=(12, 4))

        # Version pinned to the bottom of the rail.
        self.sidebar_footer = ctk.CTkFrame(self.sidebar, fg_color="transparent", height=34)
        self.sidebar_footer.pack(side="bottom", fill="x", pady=(0, 10))
        self.version_label = ctk.CTkLabel(
            self.sidebar_footer, text=f"v{APP_VERSION}",
            font=(FONTS["small"][0], 9), text_color="#7A93B5")
        self.version_label.pack()

        self.content = ctk.CTkFrame(body, fg_color=color("bg"), corner_radius=0)
        self.content.pack(side="left", fill="both", expand=True)

    def _build_statusbar(self) -> None:
        ctk.CTkFrame(self, height=1, fg_color=color("border"),
                     corner_radius=0).pack(fill="x")
        self.status_bar = StatusBar(self)
        self.status_bar.pack(fill="x", side="bottom")
        self.status_bar.set_info(
            f"{session.role}: {session.username}  |  "
            f"{config.get('college_code','')}")

    # ==================================================================
    # Navigation
    # ==================================================================
    def _populate_nav(self) -> None:
        for widget in self.nav_container.winfo_children():
            widget.destroy()
        self._nav_buttons.clear()

        visible = [item for item in NAV_ITEMS if item.visible_for(session.role)]

        for group in GROUP_ORDER:
            items = [item for item in visible if item.group == group]
            if not items:
                continue

            if group != "Main" and not self._sidebar_collapsed:
                ctk.CTkLabel(self.nav_container, text=group.upper(),
                             font=(FONTS["small"][0], 9, "bold"),
                             text_color="#5B7CA6", anchor="w").pack(
                                 fill="x", padx=12, pady=(14, 5))
            elif group != "Main":
                ctk.CTkFrame(self.nav_container, height=1,
                             fg_color="#1B3F6B").pack(fill="x", padx=10, pady=8)

            for item in items:
                self._add_nav_button(item)

    def _add_nav_button(self, item: NavItem) -> None:
        text = item.icon if self._sidebar_collapsed else f"  {item.icon}   {item.label}"
        button = ctk.CTkButton(
            self.nav_container, text=text, anchor="center" if self._sidebar_collapsed else "w",
            height=40, corner_radius=7, font=FONTS["body"],
            fg_color="transparent", text_color="#C6D6EA",
            hover_color="#1B3F6B",
            command=lambda k=item.key: self.navigate(k))
        button.pack(fill="x", pady=1)
        self._nav_buttons[item.key] = button

    def navigate(self, key: str) -> None:
        """Show a view, creating it on first use."""
        item = next((i for i in NAV_ITEMS if i.key == key), None)
        if item is None:
            logger.warning("Unknown navigation key: %s", key)
            return

        if not item.visible_for(session.role):
            show_error(self, "Access Denied",
                       f"Your role ({session.role}) cannot open {item.label}.")
            return

        if self._active_key == key and key in self._views:
            view = self._views[key]
            if hasattr(view, "refresh"):
                view.refresh()
            return

        # Hide the current view without destroying it.
        if self._active_key and self._active_key in self._views:
            self._views[self._active_key].pack_forget()

        view = self._views.get(key)
        if view is None:
            view = self._create_view(item)
            if view is None:
                return
            self._views[key] = view

        view.pack(fill="both", expand=True)
        self._active_key = key
        self._highlight_nav(key)

        if hasattr(view, "refresh"):
            try:
                view.refresh()
            except Exception as exc:            # noqa: BLE001
                logger.error("Refresh failed for %s: %s", key, exc, exc_info=True)

        self.status_bar.set_message(f"{item.label}", "info", timeout=3000)
        log_activity(session.user, f"Opened {item.label}")

    def _create_view(self, item: NavItem):
        """Import and instantiate a view module on demand."""
        try:
            module = importlib.import_module(item.module)
            view_class = getattr(module, item.klass)
            return view_class(self.content, self)
        except Exception as exc:                # noqa: BLE001
            logger.error("Could not load view %s: %s", item.module, exc, exc_info=True)
            return self._error_view(item, exc)

    def _error_view(self, item: NavItem, exc: Exception) -> ctk.CTkFrame:
        """Placeholder so one broken screen cannot take down the application."""
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        centre = ctk.CTkFrame(frame, fg_color="transparent")
        centre.place(relx=0.5, rely=0.42, anchor="center")

        ctk.CTkLabel(centre, text="!", font=(FONTS["title"][0], 40, "bold"),
                     text_color=SEMANTIC["danger"]).pack()
        ctk.CTkLabel(centre, text=f"{item.label} could not be loaded",
                     font=FONTS["heading"], text_color=color("text")).pack(pady=(10, 4))
        ctk.CTkLabel(centre, text=f"{type(exc).__name__}: {exc}", font=FONTS["small"],
                     text_color=color("text_muted"), wraplength=520,
                     justify="center").pack()
        ctk.CTkLabel(centre, text="Details have been written to logs/error.log",
                     font=FONTS["small"], text_color=color("text_muted")).pack(pady=(8, 0))
        return frame

    def _highlight_nav(self, key: str) -> None:
        for name, button in self._nav_buttons.items():
            active = name == key
            button.configure(
                fg_color="#1D4ED8" if active else "transparent",
                text_color="#FFFFFF" if active else "#C6D6EA",
                font=FONTS["body_bold"] if active else FONTS["body"])

    def toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        self.sidebar.configure(
            width=SIDEBAR_COLLAPSED if self._sidebar_collapsed else SIDEBAR_WIDTH)
        self.version_label.configure(
            text="" if self._sidebar_collapsed else f"v{APP_VERSION}")
        self._populate_nav()
        if self._active_key:
            self._highlight_nav(self._active_key)

    # ==================================================================
    # Theme
    # ==================================================================
    def toggle_theme(self) -> None:
        new_mode = "Dark" if ctk.get_appearance_mode() == "Light" else "Light"
        apply_theme(new_mode, config.get("color_theme", "blue"))
        config.set("theme_mode", new_mode)

        # ttk widgets do not follow CustomTkinter's appearance mode.
        for view in self._views.values():
            self._refresh_theme_recursive(view)

        self.status_bar.set_message(f"{new_mode} theme applied.", "success")

    def _refresh_theme_recursive(self, widget) -> None:
        if hasattr(widget, "refresh_theme"):
            try:
                widget.refresh_theme()
            except Exception:                   # noqa: BLE001
                pass
        for child in widget.winfo_children():
            self._refresh_theme_recursive(child)

    # ==================================================================
    # Housekeeping
    # ==================================================================
    def _start_clock(self) -> None:
        def tick() -> None:
            try:
                now = datetime.now()
                self.clock_label.configure(
                    text=now.strftime("%A, %d %b %Y   |   %I:%M:%S %p"))
                self._clock_job = self.after(1000, tick)
            except tk.TclError:
                pass
        tick()

    def _maybe_auto_backup(self) -> None:
        try:
            if auto_backup_if_due(session.user):
                self.status_bar.set_message("Automatic database backup completed.",
                                            "success")
        except Exception as exc:                # noqa: BLE001
            logger.error("Automatic backup failed: %s", exc)

    def set_status(self, message: str, kind: str = "info") -> None:
        """Convenience for views: push a message into the status bar."""
        self.status_bar.set_message(message, kind)

    def refresh_active(self) -> None:
        """Re-run refresh on the visible view -- used after cross-screen changes."""
        if self._active_key and self._active_key in self._views:
            view = self._views[self._active_key]
            if hasattr(view, "refresh"):
                view.refresh()

    def invalidate(self, *keys: str) -> None:
        """Drop cached views so they rebuild with fresh structure next visit.

        Called after an academic-structure change, since several screens build
        their dropdowns once at construction time.
        """
        for key in keys or list(self._views):
            if key == self._active_key:
                continue
            view = self._views.pop(key, None)
            if view is not None:
                view.destroy()

    # ==================================================================
    # Session end
    # ==================================================================
    def sign_out(self) -> None:
        if not ask_confirm(self, "Sign Out",
                           f"Sign out of {session.full_name}'s session?",
                           confirm_text="Sign Out"):
            return

        if self._clock_job:
            self.after_cancel(self._clock_job)

        logout()
        for view in self._views.values():
            view.destroy()
        self._views.clear()
        self.destroy()
        self.on_logout()
