"""
Modal dialogs and form fields.

Tkinter's stock ``messagebox`` looks alien beside a CustomTkinter window, so
every prompt in this application uses the themed dialogs here.

:class:`FormDialog` is the workhorse -- screens declare fields as data and get
back a validated dict, which is why the Students, Faculty, Subject, Timetable
and Academic screens contain almost no widget-wiring code.
"""

from __future__ import annotations

import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from config.theme import FONTS, SEMANTIC, color

try:
    from tkcalendar import DateEntry
    _HAS_CALENDAR = True
except Exception:                              # noqa: BLE001
    DateEntry = None                            # type: ignore
    _HAS_CALENDAR = False


# ===========================================================================
# Base
# ===========================================================================
class BaseDialog(ctk.CTkToplevel):
    """Modal window that centres on its parent and returns a result."""

    def __init__(self, parent, title: str, width: int = 460, height: int = 260,
                 resizable: bool = False):
        super().__init__(parent)

        self.title(title)
        self.result = None
        self._parent = parent

        self.transient(parent)
        self.resizable(resizable, resizable)
        self.configure(fg_color=color("bg"))
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._centre(width, height)

        # Grabbing before the window is mapped raises TclError on Windows.
        self.after(60, self._make_modal)
        self.bind("<Escape>", lambda _e: self._cancel())

    def _centre(self, width: int, height: int) -> None:
        self.update_idletasks()
        try:
            x = self._parent.winfo_rootx() + (self._parent.winfo_width() - width) // 2
            y = self._parent.winfo_rooty() + (self._parent.winfo_height() - height) // 3
        except (tk.TclError, AttributeError):
            x = (self.winfo_screenwidth() - width) // 2
            y = (self.winfo_screenheight() - height) // 3
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    def _make_modal(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def show(self):
        """Block until the dialog closes, then return its result."""
        self.wait_window()
        return self.result


# ===========================================================================
# Message dialogs
# ===========================================================================
class MessageDialog(BaseDialog):
    """Themed replacement for ``messagebox.showinfo`` and friends."""

    ICONS = {"info": "i", "success": "OK", "warning": "!", "error": "X", "question": "?"}
    COLOURS = {
        "info": SEMANTIC["info"], "success": SEMANTIC["success"],
        "warning": SEMANTIC["warning"], "error": SEMANTIC["danger"],
        "question": SEMANTIC["info"],
    }

    def __init__(self, parent, title: str, message: str, kind: str = "info",
                 confirm: bool = False, confirm_text: str = "OK",
                 cancel_text: str = "Cancel", danger: bool = False):
        # Estimate the wrapped line count so short messages get a short window
        # and long ones get a scrollable body rather than an oversized dialog.
        wrap_width = 62
        lines = sum(max(1, -(-len(part) // wrap_width))
                    for part in message.split("\n")) or 1
        needs_scroll = lines > 12

        height = 168 + min(lines, 12) * 19 + (30 if confirm else 0)
        super().__init__(parent, title, width=500,
                         height=min(560, max(215, height)), resizable=True)
        self.minsize(400, 200)

        accent = SEMANTIC["danger"] if danger else self.COLOURS.get(kind, SEMANTIC["info"])

        # ---- footer FIRST -------------------------------------------------
        # Packing the buttons against the bottom before the body means the body
        # can never squeeze them off-screen, however long the message is.
        footer = ctk.CTkFrame(self, fg_color="transparent", height=62)
        footer.pack(side="bottom", fill="x", padx=22, pady=(6, 16))
        footer.pack_propagate(False)

        confirm_button = ctk.CTkButton(
            footer, text=confirm_text, command=self._confirm, width=120, height=38,
            corner_radius=7, font=FONTS["body_bold"],
            fg_color=SEMANTIC["danger"] if danger else color("primary"),
            hover_color="#B91C1C" if danger else color("primary_hover"))
        confirm_button.pack(side="right", pady=8)

        if confirm:
            ctk.CTkButton(footer, text=cancel_text, command=self._cancel,
                          width=120, height=38, corner_radius=7,
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("text"),
                          hover_color=color("surface_alt"),
                          font=FONTS["body_bold"]).pack(side="right", padx=(0, 9),
                                                        pady=8)

        # ---- heading ------------------------------------------------------
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(side="top", fill="x", padx=22, pady=(20, 0))

        ctk.CTkLabel(head, text=self.ICONS.get(kind, "i"),
                     font=(FONTS["title"][0], 17, "bold"), text_color="#FFFFFF",
                     fg_color=accent, corner_radius=17, width=34, height=34
                     ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(head, text=title, font=(FONTS["heading"][0], 15, "bold"),
                     text_color=color("text"), anchor="w",
                     wraplength=380, justify="left").pack(side="left", fill="x",
                                                          expand=True)

        # ---- message ------------------------------------------------------
        if needs_scroll:
            body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        else:
            body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True, padx=22, pady=(11, 0))

        ctk.CTkLabel(body, text=message, font=FONTS["body"],
                     text_color=color("text_muted"), wraplength=430,
                     justify="left", anchor="nw").pack(fill="both", expand=True)

        confirm_button.focus_set()
        self.bind("<Return>", lambda _e: self._confirm())

    def _confirm(self) -> None:
        self.result = True
        self.destroy()


def show_info(parent, title: str, message: str) -> None:
    MessageDialog(parent, title, message, "info").show()


def show_success(parent, title: str, message: str) -> None:
    MessageDialog(parent, title, message, "success").show()


def show_warning(parent, title: str, message: str) -> None:
    MessageDialog(parent, title, message, "warning").show()


def show_error(parent, title: str, message: str) -> None:
    MessageDialog(parent, title, message, "error").show()


def ask_confirm(parent, title: str, message: str, confirm_text: str = "Confirm",
                cancel_text: str = "Cancel", danger: bool = False) -> bool:
    """Yes/no prompt.  Returns True only on explicit confirmation."""
    return bool(MessageDialog(parent, title, message, "question", confirm=True,
                              confirm_text=confirm_text, cancel_text=cancel_text,
                              danger=danger).show())


# ===========================================================================
# Reason prompt -- the accountability gate
# ===========================================================================
class ReasonDialog(BaseDialog):
    """Collect a mandatory justification.

    Used wherever the specification demands one: editing face-marked
    attendance, unlocking a session, overriding a leave decision, deleting a
    record.  The OK button stays disabled until enough text is typed, so an
    empty reason can never reach the audit log.
    """

    def __init__(self, parent, title: str = "Reason Required", message: str = "",
                 min_length: int = 10, confirm_text: str = "Submit",
                 danger: bool = False):
        wrap_width = 66
        lines = sum(max(1, -(-len(part) // wrap_width))
                    for part in (message or "").split("\n"))
        height = min(600, max(330, 300 + min(lines, 12) * 18))

        super().__init__(parent, title, width=540, height=height, resizable=True)
        self.minsize(440, 320)

        self.min_length = min_length

        # ---- footer FIRST, pinned to the bottom ---------------------------
        footer = ctk.CTkFrame(self, fg_color="transparent", height=62)
        footer.pack(side="bottom", fill="x", padx=22, pady=(4, 15))
        footer.pack_propagate(False)

        self.submit_button = ctk.CTkButton(
            footer, text=confirm_text, command=self._confirm, width=125, height=38,
            corner_radius=7, font=FONTS["body_bold"], state="disabled",
            fg_color=SEMANTIC["danger"] if danger else color("primary"),
            hover_color="#B91C1C" if danger else color("primary_hover"))
        self.submit_button.pack(side="right", pady=8)

        ctk.CTkButton(footer, text="Cancel", command=self._cancel, width=115, height=38,
                      corner_radius=7, fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt"),
                      font=FONTS["body_bold"]).pack(side="right", padx=(0, 9), pady=8)

        # ---- reason input, also pinned so it never scrolls away ------------
        entry_area = ctk.CTkFrame(self, fg_color="transparent")
        entry_area.pack(side="bottom", fill="x", padx=22)

        ctk.CTkLabel(entry_area, text="Reason  *", font=FONTS["small_bold"],
                     text_color=color("text_muted"), anchor="w").pack(fill="x",
                                                                      pady=(8, 4))
        self.textbox = ctk.CTkTextbox(entry_area, height=80, font=FONTS["body"],
                                      corner_radius=7, border_width=1,
                                      border_color=color("border"))
        self.textbox.pack(fill="x")
        self.textbox.bind("<KeyRelease>", self._validate)

        self.hint = ctk.CTkLabel(
            entry_area,
            text=f"At least {min_length} characters. This is recorded in the audit trail.",
            font=FONTS["small"], text_color=color("text_muted"), anchor="w")
        self.hint.pack(fill="x", pady=(4, 0))

        # ---- explanation, scrollable if long -------------------------------
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(side="top", fill="x", padx=22, pady=(20, 0))
        ctk.CTkLabel(head, text=title, font=(FONTS["heading"][0], 15, "bold"),
                     text_color=color("text"), anchor="w",
                     wraplength=470, justify="left").pack(fill="x")

        if message:
            body = (ctk.CTkScrollableFrame(self, fg_color="transparent")
                    if lines > 9 else ctk.CTkFrame(self, fg_color="transparent"))
            body.pack(side="top", fill="both", expand=True, padx=22, pady=(6, 0))
            ctk.CTkLabel(body, text=message, font=FONTS["body"],
                         text_color=color("text_muted"), wraplength=470,
                         justify="left", anchor="nw").pack(fill="both", expand=True)

        self.after(180, self.textbox.focus_set)

    def _validate(self, _event=None) -> None:
        text = self.textbox.get("1.0", "end").strip()
        enough = len(text) >= self.min_length
        self.submit_button.configure(state="normal" if enough else "disabled")
        if enough:
            self.hint.configure(text="Ready to submit.", text_color=SEMANTIC["success"])
        else:
            remaining = self.min_length - len(text)
            self.hint.configure(
                text=f"{remaining} more character(s) required.",
                text_color=color("text_muted"))

    def _confirm(self) -> None:
        self.result = self.textbox.get("1.0", "end").strip()
        self.destroy()


def ask_reason(parent, title: str = "Reason Required", message: str = "",
               min_length: int = 10, confirm_text: str = "Submit",
               danger: bool = False) -> str | None:
    return ReasonDialog(parent, title, message, min_length, confirm_text, danger).show()


# ===========================================================================
# Form fields
# ===========================================================================
class FormField:
    """Declarative description of one input in a :class:`FormDialog`."""

    def __init__(self, key: str, label: str, kind: str = "text",
                 required: bool = False, options: dict | list | None = None,
                 default=None, placeholder: str = "", width: int = 260,
                 validator: Callable | None = None, hint: str = "",
                 file_types: list | None = None, readonly: bool = False,
                 span: int = 1):
        """
        Args:
            kind: text | password | number | textarea | select | date |
                  checkbox | file | image | readonly
            validator: ``value -> (ok, message)``.
        """
        self.key = key
        self.label = label
        self.kind = kind
        self.required = required
        self.options = options or {}
        self.default = default
        self.placeholder = placeholder
        self.width = width
        self.validator = validator
        self.hint = hint
        self.file_types = file_types
        self.readonly = readonly
        self.span = span

        self.variable: tk.Variable | None = None
        self.widget = None
        self.error_label: ctk.CTkLabel | None = None


class FormDialog(BaseDialog):
    """Two-column modal form built from a list of :class:`FormField`."""

    def __init__(self, parent, title: str, fields: list[FormField],
                 values: dict | None = None, submit_text: str = "Save",
                 width: int = 780, height: int = 640, columns: int = 2,
                 on_validate: Callable | None = None, description: str = ""):
        super().__init__(parent, title, width=width, height=height, resizable=True)

        self.fields = {f.key: f for f in fields}
        self._field_list = fields
        self._on_validate = on_validate
        values = values or {}

        self.minsize(560, 400)

        # ---- header ------------------------------------------------------
        header = ctk.CTkFrame(self, fg_color=color("surface"), corner_radius=0, height=56)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        heading = ctk.CTkFrame(header, fg_color="transparent")
        heading.pack(side="left", padx=20, pady=9)
        ctk.CTkLabel(heading, text=title, font=(FONTS["heading"][0], 15, "bold"),
                     text_color=color("text"), anchor="w").pack(fill="x")
        if description:
            ctk.CTkLabel(heading, text=description, font=(FONTS["small"][0], 10),
                         text_color=color("text_muted"), anchor="w").pack(fill="x")

        # ---- footer BEFORE the body so Save/Cancel are never pushed off ---
        footer = ctk.CTkFrame(self, fg_color=color("surface"), corner_radius=0, height=64)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)

        self.error_banner = ctk.CTkLabel(
            footer, text="", font=FONTS["small_bold"],
            text_color=SEMANTIC["danger"], anchor="w", wraplength=380,
            justify="left")
        self.error_banner.pack(side="left", padx=20)

        buttons = ctk.CTkFrame(footer, fg_color="transparent")
        buttons.pack(side="right", padx=20, pady=13)

        ctk.CTkButton(buttons, text="Cancel", command=self._cancel, width=110, height=37,
                      corner_radius=7, fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt"),
                      font=FONTS["body_bold"]).pack(side="right", padx=(10, 0))

        ctk.CTkButton(buttons, text=submit_text, command=self._submit, width=130,
                      height=37, corner_radius=7, font=FONTS["body_bold"],
                      fg_color=color("primary"),
                      hover_color=color("primary_hover")).pack(side="right")

        # ---- scrollable body --------------------------------------------
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True, padx=16, pady=(11, 0))
        for index in range(columns):
            body.grid_columnconfigure(index, weight=1, uniform="form")

        row = col = 0
        for field in fields:
            span = min(field.span, columns)
            if col + span > columns:
                row, col = row + 1, 0

            container = ctk.CTkFrame(body, fg_color="transparent")
            container.grid(row=row, column=col, columnspan=span,
                           sticky="ew", padx=8, pady=(0, 12))

            self._build_field(container, field, values.get(field.key, field.default))

            col += span
            if col >= columns:
                row, col = row + 1, 0

    # ------------------------------------------------------------------
    def _build_field(self, parent, field: FormField, value) -> None:
        label_text = field.label + ("  *" if field.required else "")
        ctk.CTkLabel(parent, text=label_text, font=FONTS["small_bold"],
                     text_color=color("text_muted"), anchor="w").pack(fill="x", pady=(0, 3))

        kind = field.kind

        if kind in ("text", "password", "number", "readonly"):
            field.variable = tk.StringVar(value="" if value is None else str(value))
            widget = ctk.CTkEntry(
                parent, textvariable=field.variable, height=34, corner_radius=7,
                font=FONTS["body"], placeholder_text=field.placeholder,
                show="*" if kind == "password" else "")
            widget.pack(fill="x")
            if kind == "readonly" or field.readonly:
                widget.configure(state="disabled", fg_color=color("surface_alt"))
            field.widget = widget

        elif kind == "textarea":
            widget = ctk.CTkTextbox(parent, height=78, corner_radius=7,
                                    font=FONTS["body"], border_width=1,
                                    border_color=color("border"))
            widget.pack(fill="x")
            if value:
                widget.insert("1.0", str(value))
            field.widget = widget

        elif kind == "select":
            mapping = (field.options if isinstance(field.options, dict)
                       else {str(o): o for o in field.options})
            field.options = mapping
            names = list(mapping) or ["-"]

            initial = names[0]
            for name, mapped in mapping.items():
                if mapped == value or name == value:
                    initial = name
                    break

            field.variable = tk.StringVar(value=initial)
            widget = ctk.CTkOptionMenu(parent, variable=field.variable, values=names,
                                       height=34, corner_radius=7, font=FONTS["body"])
            widget.pack(fill="x")
            field.widget = widget

        elif kind == "date":
            field.variable = tk.StringVar(value=str(value) if value else "")
            if _HAS_CALENDAR:
                holder = tk.Frame(parent, bg=color("surface")[0]
                                  if ctk.get_appearance_mode() == "Light"
                                  else color("surface")[1], height=34)
                holder.pack(fill="x")
                try:
                    initial = (datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
                               if value else date.today())
                except ValueError:
                    initial = date.today()

                picker = DateEntry(holder, date_pattern="yyyy-mm-dd", width=18,
                                   background="#0F2A4A", foreground="white",
                                   borderwidth=0, font=("Segoe UI", 10),
                                   textvariable=field.variable)
                picker.set_date(initial)
                if not value:
                    field.variable.set("")     # do not pre-fill an optional date
                picker.pack(fill="x", ipady=4)
                field.widget = picker
            else:
                widget = ctk.CTkEntry(parent, textvariable=field.variable, height=34,
                                      corner_radius=7, font=FONTS["body"],
                                      placeholder_text="YYYY-MM-DD")
                widget.pack(fill="x")
                field.widget = widget

        elif kind == "checkbox":
            field.variable = tk.BooleanVar(value=bool(value))
            widget = ctk.CTkCheckBox(parent, text=field.placeholder or "Yes",
                                     variable=field.variable, font=FONTS["body"],
                                     corner_radius=5)
            widget.pack(anchor="w", pady=(4, 0))
            field.widget = widget

        elif kind in ("file", "image"):
            field.variable = tk.StringVar(value=str(value) if value else "")
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x")

            entry = ctk.CTkEntry(row, textvariable=field.variable, height=34,
                                 corner_radius=7, font=FONTS["small"],
                                 placeholder_text="No file selected")
            entry.pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="Browse", width=88, height=34, corner_radius=7,
                          font=FONTS["small_bold"],
                          command=lambda f=field: self._browse(f)).pack(side="left", padx=(8, 0))
            field.widget = entry

        if field.hint:
            ctk.CTkLabel(parent, text=field.hint, font=FONTS["small"],
                         text_color=color("text_muted"), anchor="w",
                         wraplength=field.width * 2).pack(fill="x", pady=(2, 0))

        field.error_label = ctk.CTkLabel(parent, text="", font=FONTS["small"],
                                         text_color=SEMANTIC["danger"], anchor="w",
                                         wraplength=field.width * 2)

    def _browse(self, field: FormField) -> None:
        if field.kind == "image":
            types = [("Images", "*.jpg *.jpeg *.png *.bmp"), ("All files", "*.*")]
        else:
            types = field.file_types or [
                ("Documents", "*.pdf *.jpg *.jpeg *.png"), ("All files", "*.*")]

        path = filedialog.askopenfilename(parent=self, title=f"Select {field.label}",
                                          filetypes=types)
        if path:
            field.variable.set(path)

    # ------------------------------------------------------------------
    def get_values(self) -> dict:
        """Read every field back into a plain dict."""
        values = {}
        for key, field in self.fields.items():
            if field.kind == "textarea":
                values[key] = field.widget.get("1.0", "end").strip()
            elif field.kind == "select":
                values[key] = field.options.get(field.variable.get())
            elif field.kind == "checkbox":
                values[key] = bool(field.variable.get())
            elif field.kind == "number":
                raw = field.variable.get().strip()
                try:
                    values[key] = float(raw) if "." in raw else int(raw)
                except (TypeError, ValueError):
                    values[key] = None if not raw else raw
            else:
                values[key] = field.variable.get().strip() if field.variable else None
        return values

    def _show_field_error(self, field: FormField, message: str) -> None:
        if field.error_label:
            field.error_label.configure(text=message)
            field.error_label.pack(fill="x", pady=(2, 0))

    def _clear_errors(self) -> None:
        self.error_banner.configure(text="")
        for field in self._field_list:
            if field.error_label:
                field.error_label.configure(text="")
                field.error_label.pack_forget()

    def _submit(self) -> None:
        self._clear_errors()
        values = self.get_values()
        first_error = None

        for field in self._field_list:
            value = values.get(field.key)

            if field.required and (value is None or str(value).strip() == ""):
                self._show_field_error(field, f"{field.label} is required.")
                first_error = first_error or f"{field.label} is required."
                continue

            if field.validator and value not in (None, ""):
                ok, message = field.validator(value)
                if not ok:
                    self._show_field_error(field, message)
                    first_error = first_error or message

        if first_error:
            self.error_banner.configure(text=first_error)
            return

        if self._on_validate:
            ok, message = self._on_validate(values)
            if not ok:
                self.error_banner.configure(text=message)
                return

        self.result = values
        self.destroy()


# ===========================================================================
# Progress dialog
# ===========================================================================
class ProgressDialog(BaseDialog):
    """Determinate progress window for imports, bulk ID cards and training."""

    def __init__(self, parent, title: str = "Please wait",
                 message: str = "Working...", cancellable: bool = False):
        super().__init__(parent, title, width=440, height=210)
        self.cancelled = False

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=26, pady=26)

        ctk.CTkLabel(body, text=title, font=FONTS["subhead"],
                     text_color=color("text"), anchor="w").pack(fill="x")

        self.message_label = ctk.CTkLabel(body, text=message, font=FONTS["body"],
                                          text_color=color("text_muted"), anchor="w",
                                          wraplength=380)
        self.message_label.pack(fill="x", pady=(6, 14))

        self.progress = ctk.CTkProgressBar(body, height=10, corner_radius=5)
        self.progress.pack(fill="x")
        self.progress.set(0)

        self.detail_label = ctk.CTkLabel(body, text="", font=FONTS["small"],
                                         text_color=color("text_muted"), anchor="w")
        self.detail_label.pack(fill="x", pady=(7, 0))

        if cancellable:
            ctk.CTkButton(body, text="Cancel", command=self._request_cancel, width=100,
                          height=32, corner_radius=7, fg_color="transparent",
                          border_width=1, border_color=color("border"),
                          text_color=color("text"), font=FONTS["small_bold"]
                          ).pack(anchor="e", pady=(12, 0))
        else:
            self.protocol("WM_DELETE_WINDOW", lambda: None)   # cannot be dismissed

    def _request_cancel(self) -> None:
        self.cancelled = True
        self.message_label.configure(text="Cancelling...")

    def update_progress(self, current: int, total: int, detail: str = "") -> None:
        fraction = (current / total) if total else 0
        self.progress.set(min(1.0, fraction))
        self.detail_label.configure(text=detail or f"{current} of {total}")
        try:
            self.update_idletasks()
        except tk.TclError:
            pass

    def finish(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# ===========================================================================
# Detail viewer
# ===========================================================================
class DetailDialog(BaseDialog):
    """Read-only record viewer: sections of label/value pairs."""

    def __init__(self, parent, title: str, sections: dict[str, list[tuple[str, str]]],
                 image_path: str | None = None, width: int = 620, height: int = 620,
                 actions: list[tuple[str, Callable]] | None = None):
        super().__init__(parent, title, width=width, height=height, resizable=True)
        self.minsize(460, 380)

        header = ctk.CTkFrame(self, fg_color=color("surface"), corner_radius=0, height=50)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text=title, font=(FONTS["heading"][0], 15, "bold"),
                     text_color=color("text")).pack(side="left", padx=20, pady=12)

        # Footer before body so Close stays reachable no matter how long the
        # record is.
        footer = ctk.CTkFrame(self, fg_color=color("surface"), corner_radius=0, height=58)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)

        ctk.CTkButton(footer, text="Close", command=self._cancel, width=110, height=35,
                      corner_radius=7, font=FONTS["body_bold"]).pack(
                          side="right", padx=20, pady=11)

        for text, command in reversed(actions or []):
            ctk.CTkButton(footer, text=text, command=command, width=145, height=35,
                          corner_radius=7, font=FONTS["small_bold"],
                          fg_color="transparent", border_width=1,
                          border_color=color("primary"),
                          text_color=color("primary"),
                          hover_color=color("surface_alt")).pack(
                              side="right", padx=(0, 9), pady=11)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True, padx=18, pady=(11, 0))

        if image_path and Path(image_path).exists():
            try:
                from PIL import Image
                photo = ctk.CTkImage(Image.open(image_path), size=(120, 120))
                ctk.CTkLabel(body, image=photo, text="").pack(pady=(0, 14))
            except Exception:                   # noqa: BLE001
                pass

        for section_title, rows in sections.items():
            # height=0 so the card hugs its rows instead of the 200 px default.
            card = ctk.CTkFrame(body, corner_radius=9, fg_color=color("surface"),
                                border_width=1, border_color=color("border"),
                                height=0)
            card.pack(fill="x", pady=(0, 10))

            ctk.CTkLabel(card, text=section_title.upper(),
                         font=(FONTS["small"][0], 10, "bold"),
                         text_color=color("primary"), anchor="w").pack(
                             fill="x", padx=14, pady=(10, 6))

            for label, value in rows:
                row = ctk.CTkFrame(card, fg_color="transparent", height=0)
                row.pack(fill="x", padx=14, pady=1)
                ctk.CTkLabel(row, text=label, font=FONTS["small_bold"],
                             text_color=color("text_muted"), width=160,
                             anchor="nw", justify="left").pack(side="left", anchor="n")
                ctk.CTkLabel(row, text=str(value) if value not in (None, "") else "-",
                             font=FONTS["body"], text_color=color("text"),
                             anchor="w", justify="left", wraplength=330).pack(
                                 side="left", fill="x", expand=True)

            ctk.CTkFrame(card, height=8, fg_color="transparent").pack()


# ===========================================================================
# File pickers
# ===========================================================================
def pick_file(parent, title: str = "Select file",
              file_types: list | None = None) -> str | None:
    path = filedialog.askopenfilename(
        parent=parent, title=title,
        filetypes=file_types or [("All files", "*.*")])
    return path or None


def pick_save_path(parent, title: str = "Save as", default_name: str = "",
                   file_types: list | None = None) -> str | None:
    path = filedialog.asksaveasfilename(
        parent=parent, title=title, initialfile=default_name,
        filetypes=file_types or [("All files", "*.*")])
    return path or None


def pick_directory(parent, title: str = "Select folder") -> str | None:
    path = filedialog.askdirectory(parent=parent, title=title)
    return path or None
