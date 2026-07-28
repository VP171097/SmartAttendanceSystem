"""
Login window.

Split panel: college branding on the left, credentials on the right.  The role
selector is a genuine security control, not decoration -- picking the wrong role
for an account is rejected by :func:`core.auth.authenticate`.

Sign-in runs on a worker thread so a bcrypt verification (deliberately slow, ~
250 ms at cost 12) never freezes the window.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

from config.settings import (APP_NAME, APP_VERSION, ROLE_ADMIN, ROLE_FACULTY,
                             ROLE_STUDENT, config)
from config.theme import FONTS, SEMANTIC, color
from core.auth import AuthError, authenticate
from core.logger import get_logger
from ui.widgets.dialogs import show_error, show_info

logger = get_logger("ui.login")

NAVY = "#0F2A4A"
BLUE = "#1D4ED8"
LIGHT_TEXT = "#BACDE6"

WIDTH, HEIGHT = 940, 580


class LoginWindow(ctk.CTkToplevel):
    """Collects credentials and hands a signed-in user back to the caller."""

    def __init__(self, master, on_success):
        super().__init__(master)

        self.on_success = on_success
        self._busy = False

        self.title(f"{APP_NAME} - Sign In")
        self.configure(fg_color=color("bg"))
        self.resizable(False, False)
        self._centre()
        self.protocol("WM_DELETE_WINDOW", self._quit)

        self._build()
        self.after(120, self._focus_first)

    def _centre(self) -> None:
        x = (self.winfo_screenwidth() - WIDTH) // 2
        y = (self.winfo_screenheight() - HEIGHT) // 2
        self.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    # ------------------------------------------------------------------
    def _build(self) -> None:
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        self._build_brand_panel(container)
        self._build_form_panel(container)

    def _build_brand_panel(self, parent) -> None:
        panel = ctk.CTkFrame(parent, width=430, corner_radius=0, fg_color=NAVY)
        panel.pack(side="left", fill="y")
        panel.pack_propagate(False)

        body = ctk.CTkFrame(panel, fg_color="transparent")
        body.place(relx=0.5, rely=0.5, anchor="center")

        logo_path = Path(config.get("logo_path", ""))
        if logo_path.exists():
            try:
                from PIL import Image
                image = ctk.CTkImage(Image.open(logo_path), size=(92, 92))
                ctk.CTkLabel(body, image=image, text="").pack(pady=(0, 18))
            except Exception:                   # noqa: BLE001
                self._logo_placeholder(body)
        else:
            self._logo_placeholder(body)

        college_name = config.get("college_name", "College")
        ctk.CTkLabel(body, text=college_name,
                     font=(FONTS["title"][0], 22 if len(college_name) < 36 else 18, "bold"),
                     text_color="#FFFFFF", wraplength=350, justify="center").pack()

        ctk.CTkLabel(body, text=config.get("college_address", ""),
                     font=(FONTS["small"][0], 11), text_color=LIGHT_TEXT,
                     wraplength=330, justify="center").pack(pady=(8, 0))

        ctk.CTkFrame(body, height=1, width=250, fg_color="#2A4A72").pack(pady=20)

        ctk.CTkLabel(body, text="Smart Attendance\nManagement System",
                     font=(FONTS["heading"][0], 17, "bold"), text_color="#FFFFFF",
                     justify="center").pack()
        ctk.CTkLabel(body, text="AI-Based Attendance Automation\nusing Face Recognition",
                     font=(FONTS["small"][0], 11), text_color=LIGHT_TEXT,
                     justify="center").pack(pady=(8, 0))

        footer = ctk.CTkFrame(panel, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=28, pady=18)

        from ui.widgets.components import PROJECT_CREDIT
        ctk.CTkLabel(footer, text=PROJECT_CREDIT, font=(FONTS["small"][0], 10, "bold"),
                     text_color="#BACDE6", wraplength=340,
                     justify="center").pack(pady=(0, 8))

        line = ctk.CTkFrame(footer, fg_color="transparent")
        line.pack(fill="x")
        ctk.CTkLabel(line, text=f"Code: {config.get('college_code','')}",
                     font=(FONTS["small"][0], 9), text_color="#7A93B5").pack(side="left")
        ctk.CTkLabel(line, text=f"v{APP_VERSION}", font=(FONTS["small"][0], 9),
                     text_color="#7A93B5").pack(side="right")

    def _logo_placeholder(self, parent) -> None:
        short = config.get("college_short_name", "C")[:3].upper()
        ctk.CTkLabel(parent, text=short, font=(FONTS["title"][0], 32, "bold"),
                     text_color=NAVY, fg_color="#FFFFFF", corner_radius=46,
                     width=92, height=92).pack(pady=(0, 18))

    # ------------------------------------------------------------------
    def _build_form_panel(self, parent) -> None:
        panel = ctk.CTkFrame(parent, corner_radius=0, fg_color=color("bg"))
        panel.pack(side="left", fill="both", expand=True)

        form = ctk.CTkFrame(panel, fg_color="transparent")
        form.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(form, text="Welcome back", font=(FONTS["title"][0], 25, "bold"),
                     text_color=color("text")).pack(anchor="w")
        ctk.CTkLabel(form, text="Sign in to continue to your dashboard",
                     font=FONTS["body"], text_color=color("text_muted")).pack(
                         anchor="w", pady=(3, 22))

        # ---- role --------------------------------------------------------
        ctk.CTkLabel(form, text="SIGN IN AS", font=FONTS["small_bold"],
                     text_color=color("text_muted")).pack(anchor="w", pady=(0, 6))

        self.role_var = tk.StringVar(value=ROLE_ADMIN)
        role_row = ctk.CTkFrame(form, fg_color="transparent")
        role_row.pack(fill="x", pady=(0, 16))

        self._role_buttons: dict[str, ctk.CTkButton] = {}
        for role in (ROLE_ADMIN, ROLE_FACULTY, ROLE_STUDENT):
            button = ctk.CTkButton(
                role_row, text=role, width=118, height=36, corner_radius=7,
                font=FONTS["body_bold"], command=lambda r=role: self._select_role(r))
            button.pack(side="left", padx=(0, 8))
            self._role_buttons[role] = button
        self._select_role(ROLE_ADMIN)

        # ---- username ----------------------------------------------------
        ctk.CTkLabel(form, text="USERNAME", font=FONTS["small_bold"],
                     text_color=color("text_muted")).pack(anchor="w", pady=(0, 5))
        self.username_entry = ctk.CTkEntry(
            form, width=378, height=42, corner_radius=8, font=FONTS["body"],
            placeholder_text="Enter your username")
        self.username_entry.pack(pady=(0, 14))

        # ---- password ----------------------------------------------------
        ctk.CTkLabel(form, text="PASSWORD", font=FONTS["small_bold"],
                     text_color=color("text_muted")).pack(anchor="w", pady=(0, 5))

        password_row = ctk.CTkFrame(form, fg_color="transparent")
        password_row.pack(fill="x")

        self.password_entry = ctk.CTkEntry(
            password_row, width=310, height=42, corner_radius=8, font=FONTS["body"],
            placeholder_text="Enter your password", show="*")
        self.password_entry.pack(side="left")

        self._show_password = False
        self.reveal_button = ctk.CTkButton(
            password_row, text="Show", width=62, height=42, corner_radius=8,
            font=FONTS["small_bold"], fg_color="transparent", border_width=1,
            border_color=color("border"), text_color=color("text_muted"),
            hover_color=color("surface_alt"), command=self._toggle_password)
        self.reveal_button.pack(side="left", padx=(6, 0))

        # ---- forgot password --------------------------------------------
        ctk.CTkButton(form, text="Forgot password?", command=self.forgot_password,
                      width=140, height=22, corner_radius=5,
                      font=(FONTS["small"][0], 11), fg_color="transparent",
                      text_color=color("primary"),
                      hover_color=color("surface_alt")).pack(anchor="e", pady=(6, 0))

        # ---- feedback ----------------------------------------------------
        self.message_label = ctk.CTkLabel(
            form, text="", font=FONTS["small_bold"], text_color=SEMANTIC["danger"],
            wraplength=378, justify="left", anchor="w")
        self.message_label.pack(fill="x", pady=(6, 0))

        # ---- submit ------------------------------------------------------
        self.login_button = ctk.CTkButton(
            form, text="Sign In", width=378, height=44, corner_radius=8,
            font=(FONTS["body"][0], 14, "bold"), command=self._attempt_login)
        self.login_button.pack(pady=(14, 0))

        # ---- demo credentials -------------------------------------------
        helper = ctk.CTkFrame(form, height=0, corner_radius=8, fg_color=color("surface"),
                              border_width=1, border_color=color("border"))
        helper.pack(fill="x", pady=(20, 0))

        ctk.CTkLabel(helper, text="DEMO CREDENTIALS", font=FONTS["small_bold"],
                     text_color=color("primary"), anchor="w").pack(
                         fill="x", padx=14, pady=(10, 5))

        for role, username, password in self._demo_accounts():
            row = ctk.CTkFrame(helper, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=1)
            ctk.CTkLabel(row, text=role, font=FONTS["small"],
                         text_color=color("text_muted"), width=94,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=f"{username}  /  {password}", font=FONTS["mono"],
                         text_color=color("text"), anchor="w").pack(side="left")
            ctk.CTkButton(row, text="Use", width=42, height=22, corner_radius=5,
                          font=(FONTS["small"][0], 10),
                          fg_color="transparent", border_width=1,
                          border_color=color("border"), text_color=color("primary"),
                          hover_color=color("surface_alt"),
                          command=lambda u=username, p=password, r=role:
                              self._fill(u, p, r)).pack(side="right")

        ctk.CTkFrame(helper, height=8, fg_color="transparent").pack()

        # Enter submits from either field.
        for widget in (self.username_entry, self.password_entry):
            widget.bind("<Return>", lambda _e: self._attempt_login())

    # ------------------------------------------------------------------
    def _demo_accounts(self) -> list[tuple[str, str, str]]:
        """Read one real account per role straight from the database.

        Hard-coding these drifts the moment the seed data changes -- which is
        exactly how the sample student username went stale.  Looking them up
        means the hint on screen is always a username that actually exists.
        """
        defaults = [("Administrator", "admin", "admin123"),
                    ("Faculty", "fac001", "faculty123"),
                    ("Student", "-", "student123")]
        try:
            from core.database import get_db
            db = get_db()
            accounts = []
            for label, role, password in (("Administrator", ROLE_ADMIN, "admin123"),
                                          ("Faculty", ROLE_FACULTY, "faculty123"),
                                          ("Student", ROLE_STUDENT, "student123")):
                row = db.fetch_one(
                    "SELECT username FROM users WHERE role = ? AND is_active = 1 "
                    "ORDER BY user_id LIMIT 1", (role,))
                if row:
                    accounts.append((label, row["username"], password))
            return accounts or defaults
        except Exception as exc:                # noqa: BLE001 - hint only
            logger.warning("Could not read demo accounts: %s", exc)
            return defaults

    def _select_role(self, role: str) -> None:
        self.role_var.set(role)
        for name, button in self._role_buttons.items():
            active = name == role
            button.configure(
                fg_color=color("primary") if active else "transparent",
                text_color="#FFFFFF" if active else color("text_muted"),
                border_width=0 if active else 1,
                border_color=color("border"),
                hover_color=color("primary_hover") if active else color("surface_alt"))

    def _toggle_password(self) -> None:
        self._show_password = not self._show_password
        self.password_entry.configure(show="" if self._show_password else "*")
        self.reveal_button.configure(text="Hide" if self._show_password else "Show")

    def _fill(self, username: str, password: str, role_label: str = "") -> None:
        self.username_entry.delete(0, "end")
        self.username_entry.insert(0, username)
        self.password_entry.delete(0, "end")
        self.password_entry.insert(0, password)

        # Keep the role selector consistent with the account being filled in,
        # otherwise the role gate rejects an otherwise-correct password.
        self._select_role({"Administrator": ROLE_ADMIN,
                           "Faculty": ROLE_FACULTY,
                           "Student": ROLE_STUDENT}.get(role_label, ROLE_ADMIN))

    def _focus_first(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.username_entry.focus_set()
        except tk.TclError:
            pass

    def _set_message(self, text: str, kind: str = "error") -> None:
        colours = {"error": SEMANTIC["danger"], "success": SEMANTIC["success"],
                   "info": color("text_muted")}
        self.message_label.configure(text=text, text_color=colours.get(kind, SEMANTIC["danger"]))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.login_button.configure(
            text="Signing in..." if busy else "Sign In",
            state="disabled" if busy else "normal")
        state = "disabled" if busy else "normal"
        self.username_entry.configure(state=state)
        self.password_entry.configure(state=state)

    # ------------------------------------------------------------------
    def _attempt_login(self) -> None:
        if self._busy:
            return

        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        role = self.role_var.get()

        if not username:
            self._set_message("Please enter your username.")
            self.username_entry.focus_set()
            return
        if not password:
            self._set_message("Please enter your password.")
            self.password_entry.focus_set()
            return

        self._set_message("")
        self._set_busy(True)

        # bcrypt is intentionally slow; keep it off the UI thread.
        def worker() -> None:
            try:
                user = authenticate(username, password, expected_role=role)
                self.after(0, lambda u=user: self._login_succeeded(u))
            except AuthError as exc:
                # Python unbinds `exc` when the except block ends, so the
                # message must be captured before the lambda is scheduled.
                message = str(exc)
                self.after(0, lambda m=message: self._login_failed(m))
            except Exception as exc:            # noqa: BLE001
                logger.error("Unexpected login error: %s", exc, exc_info=True)
                message = ("An unexpected error occurred. "
                           "Check logs/error.log for details.")
                self.after(0, lambda m=message: self._login_failed(m))

        threading.Thread(target=worker, daemon=True, name="login").start()

    def _login_succeeded(self, user: dict) -> None:
        self._set_busy(False)
        self._set_message("Signed in successfully.", "success")

        if user.get("must_change_pw"):
            show_info(self, "Password Change Required",
                      "You are signing in with a temporary password.\n\n"
                      "Please change it from Settings once your dashboard opens.")

        self.after(160, lambda: self._finish(user))

    def _finish(self, user: dict) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.on_success(user)

    def _login_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_message(message)
        self.password_entry.delete(0, "end")
        self.password_entry.focus_set()

        # Nudge the window so a failure is felt, not just read.
        try:
            x, y = self.winfo_x(), self.winfo_y()
            for offset in (-9, 9, -6, 6, -3, 3, 0):
                self.geometry(f"+{x + offset}+{y}")
                self.update()
                self.after(18)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Forgot password
    # ------------------------------------------------------------------
    def forgot_password(self) -> None:
        """Two-step self-service reset: identify, then verify and set."""
        from core.auth import get_reset_challenge, reset_password_self_service
        from ui.widgets.dialogs import FormDialog, FormField

        prefill = self.username_entry.get().strip()

        step_one = FormDialog(
            self, "Reset Your Password",
            [FormField("username", "Username", required=True, default=prefill,
                       span=2,
                       hint="Students: your enrollment number in lower case. "
                            "Faculty: the username your administrator gave you.")],
            submit_text="Continue", width=620, height=300,
            description="Step 1 of 2 - identify your account").show()

        if not step_one:
            return

        username = step_one["username"].strip()
        ok, message, challenge = get_reset_challenge(username)

        if not ok:
            show_error(self, "Cannot Reset This Account", message)
            return

        # Only ask for details the record actually holds.
        fields = []
        if challenge["has_mobile"]:
            fields.append(FormField(
                "mobile", "Registered Mobile Number", placeholder="9876543210",
                hint="The mobile number on your college record"))
        if challenge["has_dob"]:
            fields.append(FormField(
                "dob", "Date of Birth", "date",
                hint="As recorded by the college office"))

        fields += [
            FormField("password", "New Password", "password", required=True, span=2,
                      hint="At least 8 characters, with a letter and a digit"),
            FormField("confirm", "Confirm New Password", "password", required=True,
                      span=2),
        ]

        def _check(values: dict) -> tuple[bool, str]:
            if values.get("password") != values.get("confirm"):
                return False, "The two passwords do not match."
            if not (values.get("mobile") or values.get("dob")):
                return False, ("Please supply your mobile number or date of birth "
                               "so we can confirm your identity.")
            return True, ""

        step_two = FormDialog(
            self, "Reset Your Password", fields,
            submit_text="Reset Password", width=680, height=520,
            on_validate=_check,
            description=(f"Step 2 of 2 - verify you are "
                         f"{challenge['full_name']} ({challenge['role']})")).show()

        if not step_two:
            return

        ok, message = reset_password_self_service(
            username, step_two.get("mobile", ""), step_two.get("dob", ""),
            step_two["password"])

        if ok:
            show_info(self, "Password Reset", message)
            self._fill(username, step_two["password"],
                       {"Admin": "Administrator"}.get(challenge["role"],
                                                      challenge["role"]))
            self._set_message("Password reset. You can sign in now.", "success")
        else:
            show_error(self, "Could Not Reset Password", message)

    def _quit(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.master.destroy()
