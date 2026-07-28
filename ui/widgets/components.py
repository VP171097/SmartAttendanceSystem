"""
Reusable CustomTkinter building blocks.

Keeping cards, badges, headers and toolbars here means every screen looks the
same without copying layout code, and a design tweak lands everywhere at once.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from config.theme import (FONTS, LEAVE_STATUS_COLORS, SEMANTIC, STATUS_COLORS,
                          color, palette, percentage_color)


# ===========================================================================
# Cards
# ===========================================================================
# CustomTkinter frames default to 200x200.  Every card class below therefore
# sets an explicit height, otherwise a row of "small" tiles renders 200 px tall
# and swallows the screen.
CARD_HEIGHT = 78


class StatCard(ctk.CTkFrame):
    """Dashboard metric tile: big number, caption, coloured accent stripe."""

    def __init__(self, master, title: str, value: str = "0", icon: str = "",
                 accent: str = SEMANTIC["info"], subtitle: str = "",
                 on_click: Callable | None = None, height: int = CARD_HEIGHT,
                 **kwargs):
        super().__init__(master, corner_radius=9, fg_color=color("surface"),
                         border_width=1, border_color=color("border"),
                         height=height, **kwargs)

        self.accent = accent
        self._on_click = on_click

        # Stop the tile growing to fit its children -- the fixed height is the
        # whole point, and a long subtitle must wrap rather than expand.
        self.pack_propagate(False)
        self.grid_propagate(False)

        # Accent stripe down the left edge.
        stripe = ctk.CTkFrame(self, width=4, corner_radius=2, fg_color=accent)
        stripe.pack(side="left", fill="y", padx=(5, 0), pady=6)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True, padx=(9, 10), pady=7)

        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x")

        ctk.CTkLabel(top, text=title.upper(), font=(FONTS["small"][0], 10, "bold"),
                     text_color=color("text_muted"), anchor="w").pack(side="left")
        if icon:
            ctk.CTkLabel(top, text=icon, font=(FONTS["body"][0], 14),
                         text_color=accent).pack(side="right")

        self.value_label = ctk.CTkLabel(body, text=str(value),
                                        font=(FONTS["title"][0], 21, "bold"),
                                        text_color=color("text"), anchor="w")
        self.value_label.pack(fill="x", pady=(1, 0))

        self.subtitle_label = ctk.CTkLabel(
            body, text=subtitle, font=(FONTS["small"][0], 10),
            text_color=color("text_muted"), anchor="w")
        if subtitle:
            self.subtitle_label.pack(fill="x")

        if on_click:
            self.configure(cursor="hand2")
            for widget in (self, body, top, self.value_label, self.subtitle_label):
                widget.bind("<Button-1>", lambda _e: on_click())

    def update_value(self, value, subtitle: str | None = None,
                     accent: str | None = None) -> None:
        self.value_label.configure(text=str(value))
        if subtitle is not None:
            self.subtitle_label.configure(text=subtitle)
            if not self.subtitle_label.winfo_ismapped():
                self.subtitle_label.pack(fill="x")
        if accent:
            self.value_label.configure(text_color=accent)


class SectionCard(ctk.CTkFrame):
    """Titled panel with an optional action button in its header.

    Content goes into :attr:`body`.
    """

    def __init__(self, master, title: str = "", subtitle: str = "",
                 action_text: str = "", action_command: Callable | None = None,
                 **kwargs):
        # height=0 lets the card size to its content instead of the 200 px
        # CustomTkinter default.
        super().__init__(master, corner_radius=9, fg_color=color("surface"),
                         border_width=1, border_color=color("border"),
                         height=0, **kwargs)

        if title:
            header = ctk.CTkFrame(self, fg_color="transparent")
            header.pack(fill="x", padx=13, pady=(10, 0))

            titles = ctk.CTkFrame(header, fg_color="transparent")
            titles.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(titles, text=title, font=(FONTS["subhead"][0], 14, "bold"),
                         text_color=color("text"), anchor="w").pack(fill="x")
            if subtitle:
                ctk.CTkLabel(titles, text=subtitle, font=(FONTS["small"][0], 10),
                             text_color=color("text_muted"), anchor="w",
                             justify="left").pack(fill="x")

            if action_text and action_command:
                ctk.CTkButton(header, text=action_text, command=action_command,
                              width=112, height=27, font=FONTS["small_bold"],
                              corner_radius=6).pack(side="right")

            ctk.CTkFrame(self, height=1, fg_color=color("border")).pack(
                fill="x", padx=13, pady=(8, 0))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=13, pady=11)


class Badge(ctk.CTkLabel):
    """Small pill used for statuses."""

    def __init__(self, master, text: str, bg: str = SEMANTIC["neutral"], **kwargs):
        super().__init__(master, text=f"  {text}  ", font=FONTS["small_bold"],
                         fg_color=bg, text_color="#FFFFFF", corner_radius=9,
                         height=22, **kwargs)


def status_badge(master, status: str, **kwargs) -> Badge:
    """Badge coloured by attendance status."""
    return Badge(master, status, STATUS_COLORS.get(status, SEMANTIC["neutral"]), **kwargs)


def leave_badge(master, status: str, **kwargs) -> Badge:
    return Badge(master, status, LEAVE_STATUS_COLORS.get(status, SEMANTIC["neutral"]), **kwargs)


def percentage_badge(master, percentage: float, threshold: float = 75.0, **kwargs) -> Badge:
    return Badge(master, f"{percentage:.1f}%",
                 percentage_color(percentage, threshold), **kwargs)


# ===========================================================================
# Page furniture
# ===========================================================================
class PageHeader(ctk.CTkFrame):
    """Compact title bar at the top of every view, with an action slot.

    Kept deliberately short: the header is orientation, not content, and every
    pixel it takes is a pixel the table below loses.  Title and subtitle sit on
    one line each at modest sizes, and the whole bar is height-capped.
    """

    HEIGHT = 46

    def __init__(self, master, title: str, subtitle: str = "", icon: str = "", **kwargs):
        super().__init__(master, fg_color="transparent", height=self.HEIGHT, **kwargs)
        self.pack_propagate(False)

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True)

        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.pack(fill="x", anchor="w")
        if icon:
            ctk.CTkLabel(title_row, text=icon, font=(FONTS["title"][0], 17),
                         text_color=color("primary")).pack(side="left", padx=(0, 7))
        ctk.CTkLabel(title_row, text=title, font=(FONTS["title"][0], 18, "bold"),
                     text_color=color("text"), anchor="w").pack(side="left")

        if subtitle:
            ctk.CTkLabel(left, text=subtitle, font=(FONTS["small"][0], 11),
                         text_color=color("text_muted"), anchor="w").pack(
                             fill="x", anchor="w")

        # Right-hand slot for page-level buttons.
        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.pack(side="right")

    def add_button(self, text: str, command: Callable, width: int = 140,
                   fg_color=None, hover_color=None, **kwargs) -> ctk.CTkButton:
        button = ctk.CTkButton(self.actions, text=text, command=command,
                               width=width, height=32, corner_radius=6,
                               font=FONTS["small_bold"],
                               fg_color=fg_color or color("primary"),
                               hover_color=hover_color or color("primary_hover"),
                               **kwargs)
        button.pack(side="left", padx=(7, 0))
        return button


class FilterBar(ctk.CTkFrame):
    """Row of dropdown filters + a search box.

    Filters are declared once and read back via :meth:`values`, so a view never
    keeps its own StringVars in sync by hand.
    """

    def __init__(self, master, on_change: Callable | None = None,
                 search_placeholder: str = "Search...", **kwargs):
        super().__init__(master, corner_radius=9, fg_color=color("surface"),
                         border_width=1, border_color=color("border"),
                         height=0, **kwargs)

        self._on_change = on_change
        self._filters: dict[str, dict] = {}

        self._inner = ctk.CTkFrame(self, fg_color="transparent")
        self._inner.pack(fill="x", padx=10, pady=7)

        self.search_var = tk.StringVar()
        self._search_entry: ctk.CTkEntry | None = None
        self._search_placeholder = search_placeholder
        self._search_job = None

    def add_filter(self, key: str, label: str, options: dict[str, int | None] | list,
                   width: int = 165, default: str = "All") -> None:
        """Add a labelled dropdown.

        ``options`` may be a ``{label: value}`` mapping or a plain list.
        """
        if isinstance(options, dict):
            mapping = {"All": None, **options}
        else:
            mapping = {"All": None, **{str(o): str(o) for o in options}}

        container = ctk.CTkFrame(self._inner, fg_color="transparent")
        container.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(container, text=label, font=FONTS["small_bold"],
                     text_color=color("text_muted"), anchor="w").pack(fill="x")

        variable = tk.StringVar(value=default)
        combo = ctk.CTkOptionMenu(
            container, variable=variable, values=list(mapping),
            width=width, height=31, font=FONTS["body"], corner_radius=6,
            command=lambda _v: self._changed())
        combo.pack()

        self._filters[key] = {"var": variable, "map": mapping, "widget": combo}

    def add_search(self, width: int = 250) -> None:
        container = ctk.CTkFrame(self._inner, fg_color="transparent")
        container.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(container, text="Search", font=FONTS["small_bold"],
                     text_color=color("text_muted"), anchor="w").pack(fill="x")

        self._search_entry = ctk.CTkEntry(
            container, textvariable=self.search_var, width=width, height=31,
            placeholder_text=self._search_placeholder, font=FONTS["body"],
            corner_radius=6)
        self._search_entry.pack()
        # Debounce so we do not re-query on every keystroke.
        self.search_var.trace_add("write", lambda *_: self._debounced_search())

    def add_button(self, text: str, command: Callable, width: int = 110,
                   **kwargs) -> ctk.CTkButton:
        container = ctk.CTkFrame(self._inner, fg_color="transparent")
        container.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(container, text=" ", font=FONTS["small_bold"]).pack(fill="x")
        button = ctk.CTkButton(container, text=text, command=command, width=width,
                               height=31, font=FONTS["small_bold"],
                               corner_radius=6, **kwargs)
        button.pack()
        return button

    def _debounced_search(self) -> None:
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(320, self._changed)

    def _changed(self) -> None:
        if self._on_change:
            self._on_change()

    def values(self) -> dict:
        """Current selections as ``{key: resolved_value}`` plus ``search``."""
        result = {key: spec["map"].get(spec["var"].get())
                  for key, spec in self._filters.items()}
        result["search"] = self.search_var.get().strip()
        return result

    def set_options(self, key: str, options: dict, keep_selection: bool = False) -> None:
        """Repopulate a dropdown -- used for dependent filters."""
        spec = self._filters.get(key)
        if not spec:
            return
        current = spec["var"].get()
        mapping = {"All": None, **options}
        spec["map"] = mapping
        spec["widget"].configure(values=list(mapping))
        spec["var"].set(current if keep_selection and current in mapping else "All")

    def reset(self) -> None:
        for spec in self._filters.values():
            spec["var"].set("All")
        self.search_var.set("")
        self._changed()


# Shown centred in the status bar and on the About page.
PROJECT_CREDIT = "A Project Developed by Ankita Pandey - CSE - IInd Year"


class StatusBar(ctk.CTkFrame):
    """Bottom bar: message left, project credit centred, context right."""

    def __init__(self, master, **kwargs):
        super().__init__(master, height=26, corner_radius=0,
                         fg_color=color("surface_alt"), **kwargs)
        self.pack_propagate(False)

        self.message_label = ctk.CTkLabel(self, text="Ready",
                                          font=(FONTS["small"][0], 11),
                                          text_color=color("text_muted"), anchor="w")
        self.message_label.pack(side="left", padx=14)

        self.info_label = ctk.CTkLabel(self, text="", font=(FONTS["small"][0], 11),
                                       text_color=color("text_muted"), anchor="e")
        self.info_label.pack(side="right", padx=14)

        # Placed after the side widgets so it centres in the bar itself rather
        # than in the space left over between them.
        self.credit_label = ctk.CTkLabel(self, text=PROJECT_CREDIT,
                                         font=(FONTS["small"][0], 11, "bold"),
                                         text_color=color("text_muted"))
        self.credit_label.place(relx=0.5, rely=0.5, anchor="center")

        self._reset_job = None

    def set_message(self, text: str, kind: str = "info", timeout: int = 6000) -> None:
        """Show a transient message.  ``kind`` tints the text."""
        colours = {
            "info": color("text_muted"), "success": (SEMANTIC["success"],) * 2,
            "warning": (SEMANTIC["warning"],) * 2, "error": (SEMANTIC["danger"],) * 2,
        }
        self.message_label.configure(text=text, text_color=colours.get(kind, color("text_muted")))

        if self._reset_job:
            self.after_cancel(self._reset_job)
        if timeout:
            self._reset_job = self.after(timeout, lambda: self.message_label.configure(
                text="Ready", text_color=color("text_muted")))

    def set_info(self, text: str) -> None:
        self.info_label.configure(text=text)


class LoadingOverlay(ctk.CTkFrame):
    """Translucent 'working...' panel placed over a view during long tasks."""

    def __init__(self, master, text: str = "Loading...", **kwargs):
        super().__init__(master, corner_radius=0, fg_color=color("bg"), **kwargs)

        centre = ctk.CTkFrame(self, fg_color="transparent")
        centre.place(relx=0.5, rely=0.5, anchor="center")

        self.label = ctk.CTkLabel(centre, text=text, font=FONTS["subhead"],
                                  text_color=color("text"))
        self.label.pack(pady=(0, 12))

        self.progress = ctk.CTkProgressBar(centre, width=250, height=6,
                                           corner_radius=3, mode="indeterminate")
        self.progress.pack()

    def show(self, text: str | None = None) -> None:
        if text:
            self.label.configure(text=text)
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        self.progress.start()

    def hide(self) -> None:
        self.progress.stop()
        self.place_forget()


class EmptyState(ctk.CTkFrame):
    """Friendly placeholder when a list has nothing to show."""

    def __init__(self, master, icon: str = "[ ]", title: str = "Nothing here yet",
                 message: str = "", action_text: str = "",
                 action_command: Callable | None = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        centre = ctk.CTkFrame(self, fg_color="transparent")
        centre.place(relx=0.5, rely=0.45, anchor="center")

        ctk.CTkLabel(centre, text=icon, font=(FONTS["title"][0], 42),
                     text_color=color("text_muted")).pack(pady=(0, 10))
        ctk.CTkLabel(centre, text=title, font=FONTS["subhead"],
                     text_color=color("text")).pack()
        if message:
            ctk.CTkLabel(centre, text=message, font=FONTS["body"],
                         text_color=color("text_muted"), wraplength=430,
                         justify="center").pack(pady=(6, 0))
        if action_text and action_command:
            ctk.CTkButton(centre, text=action_text, command=action_command,
                          width=190, height=36, corner_radius=7,
                          font=FONTS["body_bold"]).pack(pady=(18, 0))


class InfoRow(ctk.CTkFrame):
    """A ``Label: value`` line used throughout detail panels."""

    def __init__(self, master, label: str, value: str = "-",
                 value_color=None, label_width: int = 130, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        ctk.CTkLabel(self, text=label, font=FONTS["small_bold"],
                     text_color=color("text_muted"), width=label_width,
                     anchor="w").pack(side="left")
        self.value_label = ctk.CTkLabel(
            self, text=str(value) if value not in (None, "") else "-",
            font=FONTS["body"], text_color=value_color or color("text"),
            anchor="w", justify="left")
        self.value_label.pack(side="left", fill="x", expand=True)

    def set_value(self, value, value_color=None) -> None:
        self.value_label.configure(text=str(value) if value not in (None, "") else "-")
        if value_color:
            self.value_label.configure(text_color=value_color)


class ProgressStat(ctk.CTkFrame):
    """Labelled progress bar -- attendance percentage with a target marker."""

    def __init__(self, master, label: str, value: float = 0, maximum: float = 100,
                 detail: str = "", bar_color: str | None = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=label, font=FONTS["body_bold"],
                     text_color=color("text"), anchor="w").pack(side="left")
        self.value_label = ctk.CTkLabel(top, text=f"{value:.1f}%", font=FONTS["body_bold"],
                                        text_color=bar_color or color("primary"))
        self.value_label.pack(side="right")

        self.bar = ctk.CTkProgressBar(self, height=9, corner_radius=5,
                                      progress_color=bar_color or color("primary"))
        self.bar.pack(fill="x", pady=(5, 3))
        self.bar.set(min(1.0, max(0.0, value / maximum if maximum else 0)))

        self.detail_label = ctk.CTkLabel(self, text=detail, font=FONTS["small"],
                                         text_color=color("text_muted"), anchor="w")
        if detail:
            self.detail_label.pack(fill="x")

    def update_value(self, value: float, detail: str = "", bar_color: str | None = None,
                     maximum: float = 100) -> None:
        self.bar.set(min(1.0, max(0.0, value / maximum if maximum else 0)))
        self.value_label.configure(text=f"{value:.1f}%")
        if bar_color:
            self.bar.configure(progress_color=bar_color)
            self.value_label.configure(text_color=bar_color)
        if detail:
            self.detail_label.configure(text=detail)
            if not self.detail_label.winfo_ismapped():
                self.detail_label.pack(fill="x")


def separator(master, orientation: str = "horizontal", **kwargs) -> ctk.CTkFrame:
    if orientation == "horizontal":
        return ctk.CTkFrame(master, height=1, fg_color=color("border"), **kwargs)
    return ctk.CTkFrame(master, width=1, fg_color=color("border"), **kwargs)


def scrollable_page(master) -> ctk.CTkScrollableFrame:
    """Standard scrollable body used by dashboards and long forms."""
    frame = ctk.CTkScrollableFrame(master, fg_color="transparent")
    frame.pack(fill="both", expand=True)
    return frame
