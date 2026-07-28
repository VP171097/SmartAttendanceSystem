"""
Settings.

Five tabs: college branding, academic policy, face recognition, appearance and
backup/restore.  Everything here writes to ``config/app_config.json``, so a
college rebrands the software and tunes its thresholds without touching source.

User management (create, deactivate, unlock, reset password) also lives here
for administrators.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from config.settings import (APP_VERSION, ASSET_DIR, BACKUP_DIR, LOGO_PATH,
                             ROLES, config)
from config.theme import FONTS, SEMANTIC, apply_theme, color
from core.audit import ACTION_SETTINGS, log_audit
from core.auth import (Permission, change_password, create_user, reset_password,
                       session, set_user_active, unlock_user)
from core.backup import (create_backup, delete_backup, list_backups, restore_backup)
from core.database import get_db
from core.logger import get_logger
from core.validators import validate_email, validate_mobile
from services import face_service
from ui.widgets.components import PageHeader, SectionCard, StatCard
from ui.widgets.dialogs import (FormDialog, FormField, ask_confirm, ask_reason,
                                pick_file, show_error, show_info, show_success,
                                show_warning)
from ui.widgets.table import DataTable, column, dash_format

logger = get_logger("ui.settings")


class SettingsView(ctk.CTkFrame):
    """Application configuration screen."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._is_admin = session.is_admin
        self._vars: dict[str, ctk.Variable] = {}
        self._selected_user: dict | None = None
        self._selected_backup: dict | None = None

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(
            self, title="Settings",
            subtitle=("Branding, academic policy, recognition tuning, appearance "
                      "and backups" if self._is_admin
                      else "Appearance and your account"),
            icon="⚙")
        header.pack(fill="x", padx=18, pady=(14, 10))

        self.tabs = ctk.CTkTabview(
            self, corner_radius=10,
            segmented_button_selected_color=color("primary"),
            segmented_button_selected_hover_color=color("primary_hover"))
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.tabs.add("Appearance")
        self.tabs.add("My Account")
        if self._is_admin:
            self.tabs.add("College Branding")
            self.tabs.add("Academic Policy")
            self.tabs.add("Face Recognition")
            self.tabs.add("Backup & Restore")
            self.tabs.add("User Accounts")

        self._build_appearance()
        self._build_account()
        if self._is_admin:
            self._build_branding()
            self._build_policy()
            self._build_face()
            self._build_backup()
            self._build_users()

        self.tabs.set("College Branding" if self._is_admin else "Appearance")

    # ------------------------------------------------------------------
    def _page(self, tab_name: str) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(self.tabs.tab(tab_name), fg_color="transparent")
        frame.pack(fill="both", expand=True)
        return frame

    def _field(self, parent, key: str, label: str, hint: str = "",
               kind: str = "text", options: list | None = None,
               width: int = 420) -> ctk.CTkBaseClass:
        """One labelled setting bound to ``config[key]``."""
        holder = ctk.CTkFrame(parent, fg_color="transparent")
        holder.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(holder, text=label, font=FONTS["small_bold"],
                     text_color=color("text_muted"), anchor="w").pack(fill="x")

        value = config.get(key, "")

        if kind == "select":
            variable = ctk.StringVar(value=str(value))
            widget = ctk.CTkOptionMenu(holder, variable=variable,
                                       values=[str(o) for o in (options or [])],
                                       width=width, height=34, corner_radius=7,
                                       font=FONTS["body"])
            widget.pack(anchor="w")
        elif kind == "checkbox":
            variable = ctk.BooleanVar(value=bool(value))
            widget = ctk.CTkCheckBox(holder, text=hint or label, variable=variable,
                                     font=FONTS["body"], corner_radius=5)
            widget.pack(anchor="w", pady=(4, 0))
            hint = ""
        elif kind == "textarea":
            widget = ctk.CTkTextbox(holder, height=70, width=width, corner_radius=7,
                                    font=FONTS["body"], border_width=1,
                                    border_color=color("border"))
            widget.pack(anchor="w")
            widget.insert("1.0", str(value))
            variable = None
        else:
            variable = ctk.StringVar(value=str(value))
            widget = ctk.CTkEntry(holder, textvariable=variable, width=width, height=34,
                                  corner_radius=7, font=FONTS["body"])
            widget.pack(anchor="w")

        if hint:
            ctk.CTkLabel(holder, text=hint, font=FONTS["small"],
                         text_color=color("text_muted"), anchor="w",
                         wraplength=620, justify="left").pack(fill="x", pady=(2, 0))

        self._vars[key] = variable if variable is not None else widget
        return widget

    def _read(self, key: str):
        holder = self._vars.get(key)
        if holder is None:
            return None
        if isinstance(holder, ctk.CTkTextbox):
            return holder.get("1.0", "end").strip()
        return holder.get()

    def _save_button(self, parent, command, text: str = "Save Changes") -> None:
        ctk.CTkButton(parent, text=text, command=command, width=170, height=38,
                      corner_radius=7, font=FONTS["body_bold"]).pack(anchor="w",
                                                                     pady=(8, 20))

    # ==================================================================
    # College branding
    # ==================================================================
    def _build_branding(self) -> None:
        page = self._page("College Branding")

        card = SectionCard(page, "Institution Details",
                           "Appears on the splash screen, login, dashboard, every "
                           "report, PDF, ID card and attendance sheet")
        card.pack(fill="x", pady=(6, 14))

        self._field(card.body, "college_name", "College Name",
                    "Full official name as it should appear on printed reports", width=560)
        self._field(card.body, "college_short_name", "Short Name / Abbreviation",
                    "Used for the logo placeholder when no image is set", width=220)
        self._field(card.body, "college_code", "College Code",
                    "Institution code printed on reports and ID cards", width=220)
        self._field(card.body, "college_address", "Address", kind="textarea", width=560)
        self._field(card.body, "college_phone", "Phone", width=280)
        self._field(card.body, "college_email", "Email", width=380)
        self._field(card.body, "college_website", "Website", width=380)
        self._field(card.body, "principal_name", "Principal's Name",
                    "Printed in the signature block of every report", width=380)
        self._field(card.body, "affiliation", "Affiliation", width=560)

        # ---- logo ------------------------------------------------------
        logo_card = SectionCard(page, "College Logo",
                                "PNG with a transparent background works best; "
                                "square images at 512x512 or larger look sharpest")
        logo_card.pack(fill="x", pady=(0, 14))

        row = ctk.CTkFrame(logo_card.body, fg_color="transparent")
        row.pack(fill="x")

        self.logo_preview = ctk.CTkLabel(row, text="No logo set", width=110, height=110,
                                         fg_color=color("surface_alt"), corner_radius=8,
                                         font=FONTS["small"],
                                         text_color=color("text_muted"))
        self.logo_preview.pack(side="left", padx=(0, 16))

        buttons = ctk.CTkFrame(row, fg_color="transparent")
        buttons.pack(side="left", fill="y")

        ctk.CTkButton(buttons, text="Choose Logo Image", command=self.choose_logo,
                      width=190, height=36, corner_radius=7,
                      font=FONTS["small_bold"]).pack(anchor="w", pady=(4, 6))
        ctk.CTkButton(buttons, text="Remove Logo", command=self.remove_logo,
                      width=190, height=34, corner_radius=7, font=FONTS["small_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt")).pack(anchor="w")

        ctk.CTkLabel(buttons,
                     text=f"Stored at: {LOGO_PATH}",
                     font=FONTS["small"], text_color=color("text_muted"),
                     wraplength=420, justify="left").pack(anchor="w", pady=(8, 0))

        self._save_button(page, self.save_branding, "Save Branding")
        self._load_logo_preview()

    def _load_logo_preview(self) -> None:
        path = Path(config.get("logo_path", ""))
        if path.exists():
            try:
                from PIL import Image
                image = ctk.CTkImage(Image.open(path), size=(100, 100))
                self.logo_preview.configure(image=image, text="")
                return
            except Exception:                   # noqa: BLE001
                pass
        self.logo_preview.configure(image=None, text="No logo set")

    def choose_logo(self) -> None:
        path = pick_file(self, "Select the college logo",
                         [("Images", "*.png *.jpg *.jpeg *.bmp")])
        if not path:
            return

        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        target = ASSET_DIR / f"college_logo{Path(path).suffix.lower()}"

        try:
            shutil.copy2(path, target)
        except OSError as exc:
            show_error(self, "Could Not Copy Logo", str(exc))
            return

        config.set("logo_path", str(target))
        self._load_logo_preview()

        log_audit(session.user, ACTION_SETTINGS, "Settings", "config", None,
                  new_value={"logo_path": str(target)})

        show_success(self, "Logo Updated",
                     "The logo has been saved.\n\n"
                     "It appears immediately on reports and ID cards. "
                     "Restart the application to see it on the splash and login screens.")

    def remove_logo(self) -> None:
        if not ask_confirm(self, "Remove Logo",
                           "Remove the college logo?\n\n"
                           "The abbreviation will be shown instead.",
                           confirm_text="Remove"):
            return
        config.set("logo_path", str(LOGO_PATH))
        self._load_logo_preview()
        show_info(self, "Logo Removed", "The logo reference has been cleared.")

    def save_branding(self) -> None:
        updates = {key: self._read(key) for key in (
            "college_name", "college_short_name", "college_code", "college_address",
            "college_phone", "college_email", "college_website", "principal_name",
            "affiliation")}

        if not (updates.get("college_name") or "").strip():
            show_warning(self, "Name Required", "College name cannot be blank.")
            return

        email = updates.get("college_email", "")
        ok, message = validate_email(email)
        if not ok:
            show_warning(self, "Invalid Email", message)
            return

        config.update(updates)
        log_audit(session.user, ACTION_SETTINGS, "Settings", "config", None,
                  new_value=updates, reason="College branding updated")

        show_success(self, "Branding Saved",
                     "The college details have been saved.\n\n"
                     "Reports, PDFs and ID cards use them immediately. "
                     "Restart to refresh the splash, login and title bar.")

    # ==================================================================
    # Academic policy
    # ==================================================================
    def _build_policy(self) -> None:
        page = self._page("Academic Policy")

        card = SectionCard(page, "Attendance Rules",
                           "Thresholds applied across dashboards, reports and "
                           "the eligibility checker")
        card.pack(fill="x", pady=(6, 14))

        self._field(card.body, "attendance_threshold", "Exam Eligibility Threshold (%)",
                    "Students below this are listed as defaulters. Most polytechnic "
                    "boards use 75%.", width=180)
        self._field(card.body, "late_threshold_minutes", "Late Arrival Grace (minutes)",
                    "A student recognised after this many minutes into the class is "
                    "marked Late instead of Present.", width=180)
        self._field(card.body, "medical_cert_mandatory_days",
                    "Medical Certificate Required Beyond (days)",
                    "Medical leave longer than this cannot be submitted without a "
                    "certificate.", width=180)

        schedule_card = SectionCard(page, "Class Scheduling",
                                    "How strictly the timetable governs attendance")
        schedule_card.pack(fill="x", pady=(0, 14))

        self._field(schedule_card.body, "enforce_timetable_window",
                    "Restrict Attendance to Scheduled Class Time", kind="checkbox",
                    hint=("Only allow attendance during the timetabled slot "
                          "(with a 15-minute grace either side). Leave this off for "
                          "demonstrations and classroom testing."))
        self._field(schedule_card.body, "session_timeout_minutes",
                    "Session Idle Timeout (minutes)",
                    "0 disables the timeout.", width=180)
        self._field(schedule_card.body, "rows_per_page", "Default Rows Per Page",
                    kind="select", options=[10, 25, 50, 100, 200], width=180)

        # ---- self-marked attendance --------------------------------------
        self_card = SectionCard(
            page, "Student Self-Marking",
            "Students may declare their own presence; faculty must verify it "
            "before it counts")
        self_card.pack(fill="x", pady=(0, 14))

        self._field(self_card.body, "allow_self_attendance",
                    "Allow Students to Mark Their Own Attendance", kind="checkbox",
                    hint=("When on, students see 'Mark My Attendance' and can raise a "
                          "request for a scheduled class. Nothing is recorded until "
                          "the subject's faculty approves it, and every decision is "
                          "written to the audit trail."))
        self._field(self_card.body, "self_mark_grace_minutes",
                    "Self-Marking Window After Class Ends (minutes)",
                    "How long after a class finishes a student may still mark "
                    "themselves. Use 15-30 for strict in-class marking, or 240 to "
                    "leave it open for the rest of the teaching day.", width=180)

        self._save_button(page, self.save_policy, "Save Policy")

    def save_policy(self) -> None:
        try:
            threshold = float(self._read("attendance_threshold"))
            late = int(float(self._read("late_threshold_minutes")))
            certificate_days = int(float(self._read("medical_cert_mandatory_days")))
            timeout = int(float(self._read("session_timeout_minutes")))
            rows = int(self._read("rows_per_page"))
            self_grace = int(float(self._read("self_mark_grace_minutes")))
        except (TypeError, ValueError):
            show_warning(self, "Invalid Input",
                         "Thresholds, minutes and day counts must all be numbers.")
            return

        if not 0 <= self_grace <= 1440:
            show_warning(self, "Invalid Window",
                         "The self-marking window must be between 0 and 1440 minutes.")
            return

        if not 0 < threshold <= 100:
            show_warning(self, "Invalid Threshold",
                         "The attendance threshold must be between 1 and 100.")
            return
        if not 0 <= late <= 120:
            show_warning(self, "Invalid Grace Period",
                         "The late-arrival grace must be between 0 and 120 minutes.")
            return
        if not 0 <= certificate_days <= 30:
            show_warning(self, "Invalid Day Count",
                         "The medical certificate limit must be between 0 and 30 days.")
            return

        updates = {
            "attendance_threshold": threshold,
            "late_threshold_minutes": late,
            "medical_cert_mandatory_days": certificate_days,
            "session_timeout_minutes": timeout,
            "rows_per_page": rows,
            "enforce_timetable_window": bool(self._read("enforce_timetable_window")),
            "allow_self_attendance": bool(self._read("allow_self_attendance")),
            "self_mark_grace_minutes": self_grace,
        }
        config.update(updates)

        log_audit(session.user, ACTION_SETTINGS, "Settings", "config", None,
                  new_value=updates, reason="Academic policy updated")

        show_success(self, "Policy Saved",
                     "Academic policy updated.\n\n"
                     f"Exam eligibility threshold is now {threshold:.0f}%. "
                     "Dashboards and reports reflect this immediately.")
        self.app.invalidate()

    # ==================================================================
    # Face recognition
    # ==================================================================
    def _build_face(self) -> None:
        page = self._page("Face Recognition")

        status = face_service.backend_status()

        engine = SectionCard(page, "Recognition Engine",
                             "Detected automatically at start-up")
        engine.pack(fill="x", pady=(6, 14))

        available = status["active"] is not None
        banner = ctk.CTkFrame(engine.body, height=0, corner_radius=8,
                              fg_color=SEMANTIC["success"] if available
                              else SEMANTIC["danger"])
        banner.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(banner, text=status["label"], font=FONTS["subhead"],
                     text_color="#FFFFFF").pack(pady=(10, 2))
        ctk.CTkLabel(banner,
                     text=("Face recognition is available." if available
                           else "No backend installed - manual attendance only."),
                     font=FONTS["small"], text_color="#FFFFFF").pack(pady=(0, 10))

        for label, value in (
                ("face_recognition (dlib)",
                 "Installed" if status["dlib_available"] else "Not installed"),
                ("OpenCV LBPH (contrib)",
                 "Available" if status["lbph_available"] else "Not available"),
                ("OpenCV version", status["opencv_version"]),
                ("Haar cascade", "Found" if status["cascade_found"] else "Missing"),
                ("Registered students",
                 get_db().count("students", "face_registered = 1"))):
            row = ctk.CTkFrame(engine.body, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), width=220,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=str(value), font=FONTS["small"],
                         text_color=color("text"), anchor="w").pack(side="left")

        # ---- tuning ----------------------------------------------------
        tuning = SectionCard(page, "Recognition Tuning",
                             "Higher thresholds mean fewer false matches but more "
                             "students missed")
        tuning.pack(fill="x", pady=(0, 14))

        self._field(tuning.body, "face_min_confidence", "Minimum Confidence (%)",
                    "A face scoring below this is treated as unknown. 55 is a good "
                    "starting point; raise it if strangers get marked.", width=180)
        self._field(tuning.body, "face_tolerance", "Match Tolerance (dlib only)",
                    "Lower is stricter. 0.45 is a sensible default; 0.6 is lenient.",
                    width=180)
        self._field(tuning.body, "face_dataset_size", "Images Per Student",
                    "How many samples to capture when registering a face. More "
                    "samples improve accuracy but take longer.", width=180)
        self._field(tuning.body, "face_detection_model", "Detection Model (dlib only)",
                    kind="select", options=["hog", "cnn"], width=180)
        self._field(tuning.body, "save_attendance_snapshot",
                    "Save Attendance Snapshots", kind="checkbox",
                    hint="Store a cropped photo each time a face is matched, as proof "
                         "of attendance. Uses disk space but is valuable evidence.")

        # ---- camera ------------------------------------------------------
        camera = SectionCard(page, "Camera", "Which device to use for capture")
        camera.pack(fill="x", pady=(0, 14))

        self._field(camera.body, "camera_index", "Camera Index",
                    "0 is the built-in webcam. Try 1 or 2 for an external camera.",
                    width=180)

        button_row = ctk.CTkFrame(camera.body, fg_color="transparent")
        button_row.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(button_row, text="Test Camera", command=self.test_camera,
                      width=150, height=34, corner_radius=7,
                      font=FONTS["small_bold"]).pack(side="left", padx=(0, 8))
        ctk.CTkButton(button_row, text="Detect Cameras", command=self.detect_cameras,
                      width=160, height=34, corner_radius=7, font=FONTS["small_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt")).pack(side="left")

        # ---- model ----------------------------------------------------------
        model = SectionCard(page, "Recognition Model",
                            "Rebuild after adding or removing face datasets")
        model.pack(fill="x", pady=(0, 14))

        ctk.CTkLabel(model.body,
                     text=("The model is retrained automatically whenever a student's "
                           "face dataset is captured or cleared. Use this only if "
                           "recognition starts behaving unexpectedly."),
                     font=FONTS["small"], text_color=color("text_muted"),
                     wraplength=620, justify="left", anchor="w").pack(fill="x", pady=(0, 10))

        ctk.CTkButton(model.body, text="Retrain Recognition Model",
                      command=self.retrain_model, width=230, height=36,
                      corner_radius=7, font=FONTS["small_bold"],
                      fg_color=SEMANTIC["purple"], hover_color="#6D28D9").pack(anchor="w")

        self._save_button(page, self.save_face_settings, "Save Recognition Settings")

    def save_face_settings(self) -> None:
        try:
            confidence = float(self._read("face_min_confidence"))
            tolerance = float(self._read("face_tolerance"))
            dataset_size = int(float(self._read("face_dataset_size")))
            camera_index = int(float(self._read("camera_index")))
        except (TypeError, ValueError):
            show_warning(self, "Invalid Input",
                         "Confidence, tolerance, dataset size and camera index must "
                         "all be numbers.")
            return

        if not 0 <= confidence <= 100:
            show_warning(self, "Invalid Confidence",
                         "Minimum confidence must be between 0 and 100.")
            return
        if not 0.1 <= tolerance <= 1.0:
            show_warning(self, "Invalid Tolerance",
                         "Match tolerance must be between 0.1 and 1.0.")
            return
        if not 10 <= dataset_size <= 300:
            show_warning(self, "Invalid Dataset Size",
                         "Images per student must be between 10 and 300.")
            return

        updates = {
            "face_min_confidence": confidence,
            "face_tolerance": tolerance,
            "face_dataset_size": dataset_size,
            "camera_index": camera_index,
            "face_detection_model": self._read("face_detection_model"),
            "save_attendance_snapshot": bool(self._read("save_attendance_snapshot")),
        }
        config.update(updates)

        log_audit(session.user, ACTION_SETTINGS, "Settings", "config", None,
                  new_value=updates, reason="Face recognition settings updated")

        show_success(self, "Settings Saved",
                     "Recognition settings updated. They take effect on the next "
                     "attendance session.")

    def test_camera(self) -> None:
        try:
            index = int(float(self._read("camera_index")))
        except (TypeError, ValueError):
            show_warning(self, "Invalid Index", "Camera index must be a number.")
            return

        self.app.set_status("Testing camera...", "info")
        ok, message = face_service.test_camera(index)

        if ok:
            show_success(self, "Camera Working", message)
        else:
            show_error(self, "Camera Problem",
                       f"{message}\n\n"
                       "Check that:\n"
                       "  - the camera is connected\n"
                       "  - no other application (Teams, Zoom, Camera app) is using it\n"
                       "  - Windows camera privacy settings allow desktop apps")

    def detect_cameras(self) -> None:
        self.app.set_status("Probing for cameras...", "info")
        cameras = face_service.list_cameras()

        if not cameras:
            show_warning(self, "No Cameras Found",
                         "No working camera was detected.\n\n"
                         "Connect a webcam and try again, or use Manual Attendance.")
            return

        show_info(self, "Cameras Detected",
                  "Working camera index(es): " + ", ".join(str(c) for c in cameras) +
                  f"\n\nCurrently selected: {config.get('camera_index', 0)}")

    def retrain_model(self) -> None:
        if not ask_confirm(self, "Retrain Model",
                           "Rebuild the face recognition model from every registered "
                           "student dataset?\n\nThis may take a moment.",
                           confirm_text="Retrain"):
            return

        self.app.set_status("Retraining recognition model...", "info")
        try:
            ok, message, count = face_service.train_all()
        except Exception as exc:                # noqa: BLE001
            logger.error("Retrain failed: %s", exc, exc_info=True)
            show_error(self, "Training Failed", str(exc))
            return

        if ok:
            show_success(self, "Model Retrained", message)
        else:
            show_warning(self, "Could Not Retrain", message)

    # ==================================================================
    # Appearance
    # ==================================================================
    def _build_appearance(self) -> None:
        page = self._page("Appearance")

        card = SectionCard(page, "Theme", "Applies immediately across the application")
        card.pack(fill="x", pady=(6, 14))

        ctk.CTkLabel(card.body, text="Appearance Mode", font=FONTS["small_bold"],
                     text_color=color("text_muted"), anchor="w").pack(fill="x")

        self.theme_var = ctk.StringVar(value=config.get("theme_mode", "Light"))
        row = ctk.CTkFrame(card.body, fg_color="transparent")
        row.pack(fill="x", pady=(4, 14))

        for mode in ("Light", "Dark", "System"):
            ctk.CTkRadioButton(row, text=mode, variable=self.theme_var, value=mode,
                               font=FONTS["body"],
                               command=self.apply_appearance).pack(side="left", padx=(0, 22))

        ctk.CTkLabel(card.body, text="Colour Accent", font=FONTS["small_bold"],
                     text_color=color("text_muted"), anchor="w").pack(fill="x")

        self.accent_var = ctk.StringVar(value=config.get("color_theme", "blue"))
        accent_row = ctk.CTkFrame(card.body, fg_color="transparent")
        accent_row.pack(fill="x", pady=(4, 0))

        for name, label in (("blue", "Professional Blue"), ("dark-blue", "Deep Blue"),
                            ("green", "Green")):
            ctk.CTkRadioButton(accent_row, text=label, variable=self.accent_var,
                               value=name, font=FONTS["body"],
                               command=self.apply_appearance).pack(side="left", padx=(0, 22))

        ctk.CTkLabel(card.body,
                     text=("The colour accent is applied by CustomTkinter when the "
                           "application starts. Restart to see it fully."),
                     font=FONTS["small"], text_color=color("text_muted"),
                     anchor="w").pack(fill="x", pady=(12, 0))

    def apply_appearance(self) -> None:
        mode = self.theme_var.get()
        accent = self.accent_var.get()

        apply_theme(mode, accent)
        config.update({"theme_mode": mode, "color_theme": accent})

        self.app.set_status(f"{mode} theme applied.", "success")

    # ==================================================================
    # My account
    # ==================================================================
    def _build_account(self) -> None:
        page = self._page("My Account")

        card = SectionCard(page, "Signed-in User", "Your account details")
        card.pack(fill="x", pady=(6, 14))

        for label, value in (
                ("Full Name", session.full_name),
                ("Username", session.username),
                ("Role", session.role),
                ("Signed in at", session.login_time.strftime("%d %b %Y, %I:%M %p")
                 if session.login_time else "-")):
            row = ctk.CTkFrame(card.body, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), width=140,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=str(value), font=FONTS["body"],
                         text_color=color("text"), anchor="w").pack(side="left")

        password_card = SectionCard(page, "Change Password",
                                    "At least 8 characters, containing a letter and a digit")
        password_card.pack(fill="x", pady=(0, 14))

        self._password_entries = {}
        for key, label, in (("current", "Current Password"),
                            ("new", "New Password"),
                            ("confirm", "Confirm New Password")):
            ctk.CTkLabel(password_card.body, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), anchor="w").pack(
                             fill="x", pady=(6, 3))
            entry = ctk.CTkEntry(password_card.body, width=340, height=34,
                                 corner_radius=7, font=FONTS["body"], show="*")
            entry.pack(anchor="w")
            self._password_entries[key] = entry

        ctk.CTkButton(password_card.body, text="Change Password",
                      command=self.change_own_password, width=190, height=38,
                      corner_radius=7, font=FONTS["body_bold"]).pack(anchor="w",
                                                                     pady=(14, 0))

    def change_own_password(self) -> None:
        current = self._password_entries["current"].get()
        new = self._password_entries["new"].get()
        confirm = self._password_entries["confirm"].get()

        if not current or not new:
            show_warning(self, "Missing Fields",
                         "Enter your current password and the new one.")
            return
        if new != confirm:
            show_warning(self, "Passwords Do Not Match",
                         "The new password and its confirmation are different.")
            return

        ok, message = change_password(session.user_id, current, new)

        if ok:
            for entry in self._password_entries.values():
                entry.delete(0, "end")
            show_success(self, "Password Changed", message)
        else:
            show_error(self, "Could Not Change Password", message)

    # ==================================================================
    # Backup & restore
    # ==================================================================
    def _build_backup(self) -> None:
        page = self._page("Backup & Restore")

        settings_card = SectionCard(page, "Automatic Backup",
                                    "Backups are taken quietly at start-up when due")
        settings_card.pack(fill="x", pady=(6, 14))

        self._field(settings_card.body, "auto_backup", "Enable Automatic Backup",
                    kind="checkbox",
                    hint="Take a backup when the application starts, if one is due")
        self._field(settings_card.body, "auto_backup_days", "Backup Every (days)",
                    width=180)
        self._field(settings_card.body, "backup_retention", "Keep Most Recent Backups",
                    "Older backups beyond this count are deleted automatically.",
                    width=180)

        ctk.CTkButton(settings_card.body, text="Save Backup Settings",
                      command=self.save_backup_settings, width=190, height=36,
                      corner_radius=7, font=FONTS["small_bold"]).pack(anchor="w",
                                                                      pady=(8, 0))

        # ---- actions -------------------------------------------------------
        actions_card = SectionCard(page, "Backup Now",
                                   f"Backups are stored in {BACKUP_DIR}")
        actions_card.pack(fill="x", pady=(0, 14))

        row = ctk.CTkFrame(actions_card.body, fg_color="transparent")
        row.pack(fill="x")

        ctk.CTkButton(row, text="Create Backup Now", command=self.backup_now,
                      width=180, height=38, corner_radius=7, font=FONTS["body_bold"],
                      fg_color=SEMANTIC["success"],
                      hover_color="#15803D").pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Restore Selected", command=self.restore_selected,
                      width=170, height=38, corner_radius=7, font=FONTS["body_bold"],
                      fg_color=SEMANTIC["warning"],
                      hover_color="#D97706").pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Delete Selected", command=self.delete_selected_backup,
                      width=160, height=38, corner_radius=7, font=FONTS["body_bold"],
                      fg_color=SEMANTIC["danger"],
                      hover_color="#B91C1C").pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Open Folder", command=self.open_backup_folder,
                      width=140, height=38, corner_radius=7, font=FONTS["body_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt")).pack(side="left")

        # ---- list ------------------------------------------------------------
        list_card = SectionCard(page, "Available Backups",
                                "Newest first. Restoring replaces the live database.")
        list_card.pack(fill="both", expand=True)

        self.backup_table = DataTable(
            list_card.body,
            columns=[
                column("file_name", "File Name", 300, stretch=True),
                column("created_at", "Created", 170),
                column("size_kb", "Size (KB)", 110, "center",
                       format=lambda v, _r: f"{float(v):,.1f}" if v else "-"),
                column("backup_type", "Type", 110, "center"),
            ],
            on_select=lambda row: setattr(self, "_selected_backup", row),
            height=9)
        self.backup_table.pack(fill="both", expand=True)

    def save_backup_settings(self) -> None:
        try:
            days = max(1, int(float(self._read("auto_backup_days"))))
            retention = max(1, int(float(self._read("backup_retention"))))
        except (TypeError, ValueError):
            show_warning(self, "Invalid Input",
                         "Backup interval and retention must be numbers.")
            return

        config.update({
            "auto_backup": bool(self._read("auto_backup")),
            "auto_backup_days": days,
            "backup_retention": retention,
        })
        show_success(self, "Settings Saved", "Backup settings updated.")

    def backup_now(self) -> None:
        self.app.set_status("Creating backup...", "info")
        ok, message, path = create_backup("Manual", session.user)

        if ok:
            show_success(self, "Backup Created", f"{message}\n\nSaved to:\n{path}")
            self._load_backups()
        else:
            show_error(self, "Backup Failed", message)

    def restore_selected(self) -> None:
        if not self._selected_backup:
            show_info(self, "No Selection", "Select a backup from the list first.")
            return

        entry = self._selected_backup
        reason = ask_reason(
            self, "Restore Database",
            f"Restore the database from:\n\n{entry['file_name']}\n"
            f"({entry['created_at']}, {entry['size_kb']:,.1f} KB)\n\n"
            "EVERY change made since that backup will be lost - students, "
            "attendance, leave applications and audit entries.\n\n"
            "A safety copy of the current database is taken first, so this is "
            "reversible.\n\n"
            "You will need to sign in again afterwards.\n\n"
            "Please state why you are restoring.",
            min_length=10, confirm_text="Restore Database", danger=True)

        if not reason:
            return

        ok, message = restore_backup(entry["file_path"], session.user)

        if ok:
            show_success(self, "Database Restored",
                         f"{message}\n\nThe application will now return to the "
                         "sign-in screen.")
            self.app.sign_out()
        else:
            show_error(self, "Restore Failed", message)

    def delete_selected_backup(self) -> None:
        if not self._selected_backup:
            show_info(self, "No Selection", "Select a backup from the list first.")
            return

        entry = self._selected_backup
        if not ask_confirm(self, "Delete Backup",
                           f"Permanently delete {entry['file_name']}?\n\n"
                           "This backup cannot be recovered afterwards.",
                           confirm_text="Delete", danger=True):
            return

        ok, message = delete_backup(entry["file_name"])
        if ok:
            show_success(self, "Backup Deleted", message)
            self._selected_backup = None
            self._load_backups()
        else:
            show_error(self, "Could Not Delete", message)

    def open_backup_folder(self) -> None:
        try:
            os.startfile(str(BACKUP_DIR))
        except Exception as exc:                # noqa: BLE001
            show_info(self, "Backup Folder",
                      f"Backups are stored in:\n\n{BACKUP_DIR}\n\n"
                      f"(Could not open automatically: {exc})")

    def _load_backups(self) -> None:
        if hasattr(self, "backup_table"):
            self.backup_table.set_data(list_backups())

    # ==================================================================
    # User accounts
    # ==================================================================
    def _build_users(self) -> None:
        page = self._page("User Accounts")

        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", pady=(6, 12))

        ctk.CTkButton(bar, text="+  Create User", command=self.create_user_account,
                      width=150, height=36, corner_radius=7,
                      font=FONTS["small_bold"]).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text="Reset Password", command=self.reset_user_password,
                      width=155, height=36, corner_radius=7, font=FONTS["small_bold"],
                      fg_color=SEMANTIC["warning"],
                      hover_color="#D97706").pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text="Toggle Active", command=self.toggle_user_active,
                      width=140, height=36, corner_radius=7, font=FONTS["small_bold"],
                      fg_color=SEMANTIC["purple"],
                      hover_color="#6D28D9").pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text="Unlock Account", command=self.unlock_user_account,
                      width=150, height=36, corner_radius=7, font=FONTS["small_bold"],
                      fg_color="transparent", border_width=1,
                      border_color=color("border"), text_color=color("text"),
                      hover_color=color("surface_alt")).pack(side="left")

        self.users_table = DataTable(
            page,
            columns=[
                column("username", "Username", 150),
                column("full_name", "Full Name", 210, stretch=True),
                column("role", "Role", 100, "center"),
                column("email", "Email", 220, format=dash_format),
                column("last_login", "Last Sign-in", 160, format=dash_format),
                column("failed_attempts", "Failed", 70, "center"),
                column("locked_until", "Locked Until", 150, format=dash_format),
                column("is_active", "Active", 75, "center",
                       format=lambda v, _r: "Yes" if v else "No"),
            ],
            on_select=lambda row: setattr(self, "_selected_user", row),
            height=15)
        self.users_table.pack(fill="both", expand=True)

    def _load_users(self) -> None:
        if hasattr(self, "users_table"):
            self.users_table.set_data(get_db().fetch_all(
                "SELECT * FROM users ORDER BY role, username"))

    def create_user_account(self) -> None:
        values = FormDialog(
            self, "Create User Account",
            [
                FormField("username", "Username", required=True,
                          placeholder="jsmith", hint="Letters, digits, dot, hyphen"),
                FormField("full_name", "Full Name", required=True),
                FormField("role", "Role", "select", required=True,
                          options={r: r for r in ROLES}),
                FormField("email", "Email", validator=validate_email),
                FormField("password", "Initial Password", "password", required=True,
                          span=2,
                          hint="At least 8 characters with a letter and a digit. "
                               "The user must change it at first sign-in."),
            ],
            submit_text="Create Account", width=680, height=430).show()

        if not values:
            return

        try:
            create_user(values["username"], values["password"], values["role"],
                        values["full_name"], values.get("email", ""),
                        must_change=True)
        except Exception as exc:                # noqa: BLE001
            show_error(self, "Could Not Create Account", str(exc))
            return

        show_success(self, "Account Created",
                     f"Account '{values['username']}' created with role "
                     f"{values['role']}.\n\n"
                     "The user must change their password at first sign-in.")
        self._load_users()

    def reset_user_password(self) -> None:
        if not self._selected_user:
            show_info(self, "No Selection", "Select a user from the list first.")
            return

        user = self._selected_user
        values = FormDialog(
            self, f"Reset Password - {user['username']}",
            [
                FormField("password", "New Password", "password", required=True, span=2,
                          hint="At least 8 characters with a letter and a digit"),
                FormField("reason", "Reason", "textarea", required=True, span=2,
                          hint="Recorded in the audit trail"),
            ],
            submit_text="Reset Password", width=640, height=400,
            description=f"{user['full_name']} ({user['role']})").show()

        if not values:
            return

        ok, message = reset_password(user["user_id"], values["password"],
                                     values.get("reason", ""))

        if ok:
            show_success(self, "Password Reset",
                         f"{message}\n\nTell {user['full_name']} their new password "
                         "through a secure channel.")
            self._load_users()
        else:
            show_error(self, "Could Not Reset", message)

    def toggle_user_active(self) -> None:
        if not self._selected_user:
            show_info(self, "No Selection", "Select a user from the list first.")
            return

        user = self._selected_user
        if user["user_id"] == session.user_id:
            show_warning(self, "Cannot Deactivate Yourself",
                         "You cannot deactivate the account you are signed in with.")
            return

        activating = not user["is_active"]
        action = "Activate" if activating else "Deactivate"

        reason = ask_reason(
            self, f"{action} Account",
            f"{action} the account '{user['username']}' ({user['full_name']})?\n\n"
            + ("They will be able to sign in again." if activating
               else "They will be unable to sign in until reactivated.")
            + "\n\nPlease state why.",
            min_length=10, confirm_text=action, danger=not activating)

        if not reason:
            return

        set_user_active(user["user_id"], activating, reason)
        show_success(self, f"Account {action}d",
                     f"'{user['username']}' has been {action.lower()}d.")
        self._load_users()

    def unlock_user_account(self) -> None:
        if not self._selected_user:
            show_info(self, "No Selection", "Select a user from the list first.")
            return

        user = self._selected_user
        if not user.get("locked_until") and not user.get("failed_attempts"):
            show_info(self, "Not Locked",
                      f"'{user['username']}' is not locked out.")
            return

        if not ask_confirm(self, "Unlock Account",
                           f"Clear the lockout on '{user['username']}'?\n\n"
                           f"Failed attempts: {user.get('failed_attempts', 0)}\n"
                           "They will be able to sign in immediately.",
                           confirm_text="Unlock"):
            return

        unlock_user(user["user_id"])
        show_success(self, "Account Unlocked",
                     f"'{user['username']}' can sign in again.")
        self._load_users()

    # ==================================================================
    def refresh(self) -> None:
        if self._is_admin:
            self._load_backups()
            self._load_users()
