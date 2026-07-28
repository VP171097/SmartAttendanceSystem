"""
Paginated data table.

CustomTkinter has no grid widget, so this wraps ``ttk.Treeview`` -- which gives
native scrolling and column sorting for thousands of rows -- and restyles it to
match the CustomTkinter theme.  Around it sit a pagination footer, a row-count
label and optional row actions.

Two modes:

*   **Server-side paging** (``total_rows`` supplied) -- the view queries one
    page at a time.  Used for students and faculty, where the table can hold
    thousands of records.
*   **Client-side paging** (rows supplied in full) -- the table slices the list
    itself.  Used for report previews and shorter lists.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

import customtkinter as ctk

from config.settings import config
from config.theme import FONTS, STATUS_COLORS, color, palette


class DataTable(ctk.CTkFrame):
    """Themed, paginated table."""

    def __init__(self, master, columns: list[dict], on_select: Callable | None = None,
                 on_double_click: Callable | None = None, page_size: int | None = None,
                 server_side: bool = False, show_pagination: bool = True,
                 height: int = 15, striped: bool = True, **kwargs):
        """
        Args:
            columns: ``[{"key", "label", "width", "anchor", "stretch"}, ...]``
            on_select: called with the selected row dict.
            server_side: True when the parent supplies one page at a time.
        """
        super().__init__(master, corner_radius=9, fg_color=color("surface"),
                         border_width=1, border_color=color("border"), **kwargs)

        self.columns = columns
        self.on_select = on_select
        self.on_double_click = on_double_click
        self.page_size = page_size or int(config.get("rows_per_page", 25))
        self.server_side = server_side
        self.striped = striped

        self._all_rows: list[dict] = []
        self._page_rows: list[dict] = []
        self._total_rows = 0
        self._page = 1
        self._sort_key: str | None = None
        self._sort_reverse = False
        self._on_page_change: Callable | None = None
        self._on_sort: Callable | None = None

        self._build_tree(height)
        if show_pagination:
            self._build_footer()
        self._apply_style()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_tree(self, height: int) -> None:
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        keys = [c["key"] for c in self.columns]
        self.tree = ttk.Treeview(container, columns=keys, show="headings",
                                 height=height, selectmode="browse",
                                 style="Attendance.Treeview")

        for spec in self.columns:
            self.tree.heading(
                spec["key"], text=spec["label"], anchor=spec.get("heading_anchor", "w"),
                command=lambda k=spec["key"]: self._sort_by(k))
            self.tree.column(spec["key"], width=spec.get("width", 120),
                             minwidth=spec.get("minwidth", 50),
                             anchor=spec.get("anchor", "w"),
                             stretch=spec.get("stretch", False))

        y_scroll = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        if self.on_double_click:
            self.tree.bind("<Double-1>", self._double_clicked)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color="transparent", height=42)
        footer.pack(fill="x", padx=12, pady=(4, 8))

        self.count_label = ctk.CTkLabel(footer, text="No records",
                                        font=FONTS["small"],
                                        text_color=color("text_muted"))
        self.count_label.pack(side="left")

        controls = ctk.CTkFrame(footer, fg_color="transparent")
        controls.pack(side="right")

        button_style = dict(width=32, height=27, corner_radius=6,
                            font=FONTS["small_bold"],
                            fg_color=color("surface_alt"),
                            text_color=color("text"),
                            hover_color=color("border"))

        self.first_btn = ctk.CTkButton(controls, text="|<",
                                       command=lambda: self.go_to_page(1), **button_style)
        self.first_btn.pack(side="left", padx=2)
        self.prev_btn = ctk.CTkButton(controls, text="<",
                                      command=lambda: self.go_to_page(self._page - 1),
                                      **button_style)
        self.prev_btn.pack(side="left", padx=2)

        self.page_label = ctk.CTkLabel(controls, text="1 / 1", font=FONTS["small_bold"],
                                       text_color=color("text"), width=76)
        self.page_label.pack(side="left", padx=6)

        self.next_btn = ctk.CTkButton(controls, text=">",
                                      command=lambda: self.go_to_page(self._page + 1),
                                      **button_style)
        self.next_btn.pack(side="left", padx=2)
        self.last_btn = ctk.CTkButton(controls, text=">|",
                                      command=lambda: self.go_to_page(self.page_count),
                                      **button_style)
        self.last_btn.pack(side="left", padx=2)

        ctk.CTkLabel(controls, text="Rows", font=FONTS["small"],
                     text_color=color("text_muted")).pack(side="left", padx=(14, 4))

        self.page_size_var = tk.StringVar(value=str(self.page_size))
        ctk.CTkOptionMenu(controls, variable=self.page_size_var,
                          values=["10", "25", "50", "100", "200"], width=76, height=27,
                          font=FONTS["small"], corner_radius=6,
                          command=self._page_size_changed).pack(side="left")

    def _apply_style(self) -> None:
        """Restyle ttk.Treeview to match the CustomTkinter palette."""
        colours = palette()
        style = ttk.Style()
        try:
            style.theme_use("clam")     # the only built-in theme that fully recolours
        except tk.TclError:
            pass

        style.configure(
            "Attendance.Treeview",
            background=colours["surface"], foreground=colours["text"],
            fieldbackground=colours["surface"], borderwidth=0, relief="flat",
            rowheight=30, font=FONTS["body"])
        style.configure(
            "Attendance.Treeview.Heading",
            background=colours["header"], foreground="#FFFFFF",
            relief="flat", borderwidth=0, font=FONTS["small_bold"], padding=(8, 8))
        style.map("Attendance.Treeview.Heading",
                  background=[("active", colours["sidebar_active"])])
        style.map("Attendance.Treeview",
                  background=[("selected", colours["sidebar_active"])],
                  foreground=[("selected", "#FFFFFF")])

        self.tree.tag_configure("odd", background=colours["surface"])
        self.tree.tag_configure("even", background=colours["surface_alt"]
                                if self.striped else colours["surface"])
        for status, tint in STATUS_COLORS.items():
            self.tree.tag_configure(f"status_{status.replace(' ', '_')}", foreground=tint)

    def refresh_theme(self) -> None:
        """Re-apply styling after a Light/Dark switch."""
        self._apply_style()
        self.configure(fg_color=color("surface"), border_color=color("border"))

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def set_data(self, rows: list, total_rows: int | None = None,
                 page: int | None = None) -> None:
        """Load rows.

        In server-side mode ``rows`` is one page and ``total_rows`` is the full
        count.  Otherwise ``rows`` is everything and paging happens locally.
        """
        normalised = [dict(r) for r in rows]

        if self.server_side:
            self._page_rows = normalised
            self._total_rows = total_rows if total_rows is not None else len(normalised)
            if page:
                self._page = page
        else:
            self._all_rows = normalised
            self._total_rows = len(normalised)
            self._page = page or 1
            self._apply_local_sort()

        self._render()

    def _apply_local_sort(self) -> None:
        if not self._sort_key or self.server_side:
            return

        def sort_value(row):
            value = row.get(self._sort_key)
            if value is None:
                return (2, "")
            # Numeric-aware sorting so "10" follows "9", not "1".
            try:
                return (0, float(value))
            except (TypeError, ValueError):
                return (1, str(value).lower())

        self._all_rows.sort(key=sort_value, reverse=self._sort_reverse)

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())

        if self.server_side:
            visible = self._page_rows
        else:
            start = (self._page - 1) * self.page_size
            visible = self._all_rows[start:start + self.page_size]
            self._page_rows = visible

        for index, row in enumerate(visible):
            values = []
            for spec in self.columns:
                value = row.get(spec["key"], "")
                formatter = spec.get("format")
                if formatter:
                    try:
                        value = formatter(value, row)
                    except Exception:            # noqa: BLE001 - never break the grid
                        value = row.get(spec["key"], "")
                values.append("" if value is None else str(value))

            tags = ["even" if index % 2 else "odd"]
            status = row.get("status")
            if status in STATUS_COLORS:
                tags.append(f"status_{str(status).replace(' ', '_')}")

            self.tree.insert("", "end", iid=str(index), values=values, tags=tuple(tags))

        self._update_footer()

    def _update_footer(self) -> None:
        if not hasattr(self, "count_label"):
            return

        total = self._total_rows
        if total == 0:
            self.count_label.configure(text="No records found")
            self.page_label.configure(text="0 / 0")
        else:
            start = (self._page - 1) * self.page_size + 1
            end = min(start + len(self._page_rows) - 1, total)
            self.count_label.configure(text=f"Showing {start:,}-{end:,} of {total:,} records")
            self.page_label.configure(text=f"{self._page} / {self.page_count}")

        at_start = self._page <= 1
        at_end = self._page >= self.page_count
        for button, disabled in ((self.first_btn, at_start), (self.prev_btn, at_start),
                                 (self.next_btn, at_end), (self.last_btn, at_end)):
            button.configure(state="disabled" if disabled else "normal")

    # ------------------------------------------------------------------
    # Paging & sorting
    # ------------------------------------------------------------------
    @property
    def page_count(self) -> int:
        return max(1, -(-self._total_rows // self.page_size))   # ceiling division

    @property
    def current_page(self) -> int:
        return self._page

    def go_to_page(self, page: int) -> None:
        page = max(1, min(int(page), self.page_count))
        if page == self._page and self._page_rows:
            return
        self._page = page
        if self.server_side and self._on_page_change:
            self._on_page_change(page)
        else:
            self._render()

    def set_page_change_handler(self, handler: Callable) -> None:
        self._on_page_change = handler

    def set_sort_handler(self, handler: Callable) -> None:
        self._on_sort = handler

    def _sort_by(self, key: str) -> None:
        self._sort_reverse = (key == self._sort_key) and not self._sort_reverse
        self._sort_key = key

        # Arrow indicator on the active column.
        for spec in self.columns:
            arrow = ""
            if spec["key"] == key:
                arrow = "  ^" if not self._sort_reverse else "  v"
            self.tree.heading(spec["key"], text=spec["label"] + arrow)

        if self.server_side:
            if self._on_sort:
                self._on_sort(key, self._sort_reverse)
        else:
            self._apply_local_sort()
            self._page = 1
            self._render()

    def _page_size_changed(self, value: str) -> None:
        self.page_size = int(value)
        self._page = 1
        if self.server_side and self._on_page_change:
            self._on_page_change(1)
        else:
            self._render()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def _selection_changed(self, _event=None) -> None:
        if self.on_select:
            row = self.selected_row()
            if row:
                self.on_select(row)

    def _double_clicked(self, _event=None) -> None:
        row = self.selected_row()
        if row and self.on_double_click:
            self.on_double_click(row)

    def selected_row(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self._page_rows[int(selection[0])]
        except (ValueError, IndexError):
            return None

    def select_first(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])

    def clear_selection(self) -> None:
        self.tree.selection_remove(*self.tree.selection())

    def visible_rows(self) -> list[dict]:
        return list(self._page_rows)

    def all_rows(self) -> list[dict]:
        return list(self._all_rows) if not self.server_side else list(self._page_rows)

    def clear(self) -> None:
        self._all_rows, self._page_rows, self._total_rows, self._page = [], [], 0, 1
        self._render()


# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------
def column(key: str, label: str, width: int = 120, anchor: str = "w",
           stretch: bool = False, format=None, **kwargs) -> dict:
    """Shorthand for a column spec."""
    return {"key": key, "label": label, "width": width, "anchor": anchor,
            "stretch": stretch, "format": format, **kwargs}


def percent_format(value, _row=None) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "-"


def yes_no_format(value, _row=None) -> str:
    return "Yes" if value in (1, True, "1", "Yes") else "No"


def dash_format(value, _row=None) -> str:
    return str(value) if value not in (None, "", "None") else "-"


def date_format(value, _row=None) -> str:
    """Render an ISO date/timestamp as ``DD-MM-YYYY``."""
    text = str(value or "")[:10]
    if len(text) == 10 and text[4] == "-":
        return f"{text[8:10]}-{text[5:7]}-{text[0:4]}"
    return text or "-"
