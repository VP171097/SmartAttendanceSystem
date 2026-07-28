"""
Audit trail viewer.

Read-only by design.  An audit log that can be edited from inside the
application is not an audit log, so this screen offers filtering, inspection
and export -- but no way to alter a row.  Purging old entries is available to
administrators and is itself audited.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import customtkinter as ctk

from config.settings import EXPORT_DIR
from config.theme import FONTS, SEMANTIC, color
from core.audit import ALL_ACTIONS, get_activity_logs, get_audit_logs, purge_old_logs
from core.auth import session
from core.database import get_db
from core.logger import get_logger
from ui.widgets.components import FilterBar, PageHeader, StatCard
from ui.widgets.dialogs import (DetailDialog, ask_confirm, ask_reason, show_error,
                                show_info, show_success)
from ui.widgets.table import DataTable, column, dash_format

logger = get_logger("ui.audit")

MODULES = ["Authentication", "Students", "Faculty", "Academic", "Subjects",
           "Timetable", "Attendance", "Leave", "Reports", "Backup",
           "User Management", "Settings"]


class AuditView(ctk.CTkFrame):
    """Audit and activity log browser."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._rows: list = []

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Audit Trail",
            subtitle=("Every attendance edit, leave decision, login and administrative "
                      "action, with the user, timestamp and reason"),
            icon="⚿")
        header.pack(fill="x", padx=18, pady=(14, 10))
        header.add_button("Export CSV", self.export_csv, width=130,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"))
        header.add_button("Purge Old Entries", self.purge_logs, width=170,
                          fg_color=SEMANTIC["danger"], hover_color="#B91C1C")

        # ---- tiles ---------------------------------------------------------
        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=(0, 9))
        for index in range(5):
            tiles.grid_columnconfigure(index, weight=1, uniform="tiles")

        self.tiles = {}
        for index, (key, label, icon, accent) in enumerate([
                ("total", "Total Entries", "≡", SEMANTIC["info"]),
                ("today", "Today", "◷", SEMANTIC["success"]),
                ("edits", "Attendance Edits", "✎", SEMANTIC["warning"]),
                ("logins", "Sign-ins", "⚿", SEMANTIC["purple"]),
                ("failed", "Failed Sign-ins", "!", SEMANTIC["danger"])]):
            tile = StatCard(tiles, label, "0", icon, accent)
            tile.grid(row=0, column=index, sticky="ew", padx=4)
            self.tiles[key] = tile

        # ---- filters -----------------------------------------------------------
        self.filters = FilterBar(self, on_change=self.refresh,
                                 search_placeholder="User, reason, entity...")
        self.filters.pack(fill="x", padx=18, pady=(0, 9))

        users = get_db().fetch_all(
            "SELECT user_id, username, role FROM users ORDER BY role, username")
        self.filters.add_filter(
            "user_id", "User",
            {f"{u['username']} ({u['role']})": u["user_id"] for u in users}, width=180)
        self.filters.add_filter("action", "Action",
                                {a: a for a in ALL_ACTIONS}, width=190)
        self.filters.add_filter("module", "Module",
                                {m: m for m in MODULES}, width=155)
        self.filters.add_search(230)
        self.filters.add_button("Reset", self.filters.reset, width=75,
                                fg_color="transparent", border_width=1,
                                border_color=color("border"), text_color=color("text"),
                                hover_color=color("surface_alt"))

        # ---- date range ---------------------------------------------------------
        date_bar = ctk.CTkFrame(self, height=0, corner_radius=9, fg_color=color("surface"),
                                border_width=1, border_color=color("border"))
        date_bar.pack(fill="x", padx=18, pady=(0, 9))

        inner = ctk.CTkFrame(date_bar, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=9)

        for key, label, default in (
                ("from_date", "From", (date.today() - timedelta(days=30)).isoformat()),
                ("to_date", "To", date.today().isoformat())):
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
        ctk.CTkButton(apply_holder, text="Apply", command=self.refresh, width=100,
                      height=31, corner_radius=6, font=FONTS["small_bold"]).pack()

        ctk.CTkLabel(inner,
                     text="Audit entries cannot be edited from within the application.",
                     font=FONTS["small"], text_color=color("text_muted")).pack(
                         side="right")

        # ---- tabs ----------------------------------------------------------------
        self.tabs = ctk.CTkTabview(
            self, corner_radius=10,
            segmented_button_selected_color=color("primary"),
            segmented_button_selected_hover_color=color("primary_hover"))
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.tabs.add("Audit Log")
        self.tabs.add("Activity Log")

        self.audit_table = DataTable(
            self.tabs.tab("Audit Log"),
            columns=[
                column("timestamp", "Timestamp", 150),
                column("username", "User", 130),
                column("role", "Role", 85, "center"),
                column("action", "Action", 165),
                column("module", "Module", 125, format=dash_format),
                column("entity_type", "Entity", 115, format=dash_format),
                column("entity_id", "ID", 55, "center", format=dash_format),
                column("reason", "Reason", 300, stretch=True, format=dash_format),
            ],
            on_double_click=self._show_detail, height=17)
        self.audit_table.pack(fill="both", expand=True)

        self.activity_table = DataTable(
            self.tabs.tab("Activity Log"),
            columns=[
                column("timestamp", "Timestamp", 160),
                column("username", "User", 150),
                column("activity", "Activity", 260),
                column("details", "Details", 400, stretch=True, format=dash_format),
            ],
            height=17)
        self.activity_table.pack(fill="both", expand=True)

        self.tabs.set("Audit Log")

    # ==================================================================
    def refresh(self) -> None:
        values = self.filters.values()

        try:
            self._rows = get_audit_logs(
                user_id=values.get("user_id"), action=values.get("action"),
                module=values.get("module"), from_date=self.from_date_var.get().strip(),
                to_date=self.to_date_var.get().strip(),
                search=values.get("search", ""), limit=2000)

            self.audit_table.set_data(self._rows)
            self.activity_table.set_data(get_activity_logs(1000))
        except Exception as exc:                # noqa: BLE001
            logger.error("Audit query failed: %s", exc, exc_info=True)
            show_error(self, "Query Failed", str(exc))
            return

        db = get_db()
        counts = {
            "total":  db.count("audit_log"),
            "today":  db.count("audit_log", "date(timestamp) = date('now','localtime')"),
            "edits":  db.count("audit_log",
                               "action IN ('ATTENDANCE_EDIT','ATTENDANCE_UNLOCK')"),
            "logins": db.count("audit_log", "action = 'LOGIN'"),
            "failed": db.count("audit_log", "action = 'LOGIN_FAILED'"),
        }
        for key, value in counts.items():
            self.tiles[key].update_value(f"{value:,}")

    def _show_detail(self, row: dict) -> None:
        sections = {
            "Entry": [
                ("Audit ID", row.get("audit_id")),
                ("Timestamp", row.get("timestamp")),
                ("User", row.get("username")),
                ("Role", row.get("role")),
                ("Action", row.get("action")),
                ("Module", row.get("module") or "-"),
                ("Machine", row.get("ip_address") or "-"),
            ],
            "Target": [
                ("Entity Type", row.get("entity_type") or "-"),
                ("Entity ID", row.get("entity_id") or "-"),
            ],
            "Change": [
                ("Previous Value", row.get("old_value") or "-"),
                ("New Value", row.get("new_value") or "-"),
                ("Reason Given", row.get("reason") or "-"),
            ],
        }
        DetailDialog(self, "Audit Entry", sections, width=640, height=560).show()

    # ==================================================================
    def export_csv(self) -> None:
        if not self._rows:
            show_info(self, "Nothing to Export",
                      "No audit entries match the current filters.")
            return

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPORT_DIR / f"AuditTrail_{datetime.now():%Y%m%d_%H%M%S}.csv"

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Audit ID", "Timestamp", "User", "Role", "Action",
                                 "Module", "Entity Type", "Entity ID",
                                 "Old Value", "New Value", "Reason", "Machine"])
                for row in self._rows:
                    writer.writerow([
                        row["audit_id"], row["timestamp"], row["username"], row["role"],
                        row["action"], row["module"], row["entity_type"],
                        row["entity_id"], row["old_value"], row["new_value"],
                        row["reason"], row["ip_address"]])
        except OSError as exc:
            show_error(self, "Export Failed", str(exc))
            return

        from core.audit import ACTION_EXPORT, log_audit
        log_audit(session.user, ACTION_EXPORT, "Audit", "audit_log", None,
                  new_value={"rows": len(self._rows), "file": path.name})

        show_success(self, "Audit Trail Exported",
                     f"{len(self._rows):,} entry(s) exported.\n\nSaved to:\n{path}")

    def purge_logs(self) -> None:
        reason = ask_reason(
            self, "Purge Old Audit Entries",
            "Permanently delete audit and activity entries older than 365 days.\n\n"
            "This is irreversible. Export the trail to CSV first if you need to "
            "keep a copy for records.\n\n"
            "The purge itself is recorded in the audit trail.",
            min_length=10, confirm_text="Purge Entries", danger=True)

        if not reason:
            return

        try:
            removed = purge_old_logs(365)
        except Exception as exc:                # noqa: BLE001
            show_error(self, "Purge Failed", str(exc))
            return

        from core.audit import ACTION_DELETE, log_audit
        log_audit(session.user, ACTION_DELETE, "Audit", "audit_log", None,
                  new_value={"removed": removed}, reason=reason)

        show_success(self, "Purge Complete",
                     f"{removed:,} old entry(s) removed.")
        self.refresh()
