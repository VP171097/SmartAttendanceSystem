"""
Application-wide configuration.

Two kinds of configuration live here:

*   **Static paths / constants** -- resolved from the application root at import
    time.  These never change while the program runs.
*   **Branding & tunables** -- persisted in ``config/app_config.json`` so the
    college can rebrand the software and change thresholds without touching a
    single line of source code.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Application root
# --------------------------------------------------------------------------
# When frozen by PyInstaller the executable lives next to the storage folders,
# otherwise we walk one level up from ``config/``.
if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parent.parent

APP_NAME = "Smart Attendance Management System"
APP_VERSION = "1.0.0"
APP_TAGLINE = "AI-Based Attendance Automation using Face Recognition"

# --------------------------------------------------------------------------
# Directory layout  (see docs/INSTALLATION.md for the full tree)
# --------------------------------------------------------------------------
DATABASE_DIR = APP_ROOT / "database"
STORAGE_DIR = APP_ROOT / "storage"

STUDENT_IMAGE_DIR = STORAGE_DIR / "students" / "student_images"
FACE_DATASET_DIR = STORAGE_DIR / "students" / "face_dataset"
ID_CARD_DIR = STORAGE_DIR / "students" / "id_cards"
FACULTY_IMAGE_DIR = STORAGE_DIR / "faculty" / "faculty_images"
SNAPSHOT_DIR = STORAGE_DIR / "attendance" / "snapshots"
MEDICAL_CERT_DIR = STORAGE_DIR / "medical_certificates"
LEAVE_DOC_DIR = STORAGE_DIR / "leave_documents"
REPORT_PDF_DIR = STORAGE_DIR / "reports" / "pdf"
REPORT_EXCEL_DIR = STORAGE_DIR / "reports" / "excel"
REPORT_CSV_DIR = STORAGE_DIR / "reports" / "csv"
IMPORT_DIR = STORAGE_DIR / "imports"
EXPORT_DIR = STORAGE_DIR / "exports"

LOG_DIR = APP_ROOT / "logs"
BACKUP_DIR = APP_ROOT / "backups"
TEMP_DIR = APP_ROOT / "temp"
ASSET_DIR = APP_ROOT / "assets"
ICON_DIR = ASSET_DIR / "icons"
MODEL_DIR = APP_ROOT / "trained_models"

DB_PATH = DATABASE_DIR / "attendance.db"
CONFIG_FILE = APP_ROOT / "config" / "app_config.json"
LOGO_PATH = ASSET_DIR / "college_logo.png"

ALL_DIRS = [
    DATABASE_DIR, STORAGE_DIR, STUDENT_IMAGE_DIR, FACE_DATASET_DIR, ID_CARD_DIR,
    FACULTY_IMAGE_DIR, SNAPSHOT_DIR, MEDICAL_CERT_DIR, LEAVE_DOC_DIR,
    REPORT_PDF_DIR, REPORT_EXCEL_DIR, REPORT_CSV_DIR, IMPORT_DIR, EXPORT_DIR,
    LOG_DIR, BACKUP_DIR, TEMP_DIR, ASSET_DIR, ICON_DIR, MODEL_DIR,
]


def ensure_directories() -> None:
    """Create every storage directory the application expects.

    Called once at start-up so a fresh checkout (or a fresh install on a
    college machine) is immediately usable.
    """
    for directory in ALL_DIRS:
        directory.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Defaults for the JSON-backed configuration
# --------------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    # ---- College branding -------------------------------------------------
    "college_name": "Government Polytechnic College",
    "college_short_name": "GPC",
    "college_code": "GPC-1024",
    "college_address": "Civil Lines Road, Bhopal, Madhya Pradesh - 462001",
    "college_phone": "+91 755 400 1024",
    "college_email": "principal@gpcollege.ac.in",
    "college_website": "www.gpcollege.ac.in",
    "principal_name": "Dr. R. K. Sharma",
    "affiliation": "Affiliated to State Board of Technical Education",
    "college_established": "",         # founding year, shown in About/reports
    "logo_path": str(LOGO_PATH),

    # ---- Academic policy --------------------------------------------------
    "attendance_threshold": 75.0,      # % required for exam eligibility
    "late_threshold_minutes": 10,      # arrive after this -> marked Late
    "medical_cert_mandatory_days": 3,  # > this many days -> certificate required
    "current_academic_session": "2026-27",

    # ---- Face recognition tunables ---------------------------------------
    "face_tolerance": 0.45,            # dlib distance; lower = stricter
    "face_min_confidence": 55.0,       # % confidence needed to accept a match
    "face_dataset_size": 60,           # images captured per student
    "face_detection_model": "hog",     # "hog" (CPU) or "cnn" (GPU)
    "camera_index": 0,
    "save_attendance_snapshot": True,

    # ---- Application behaviour -------------------------------------------
    "theme_mode": "Light",             # Light | Dark | System
    "color_theme": "blue",             # blue | green | dark-blue
    "auto_backup": True,
    "auto_backup_days": 1,
    "backup_retention": 20,            # keep N most recent backups
    "rows_per_page": 25,
    "enforce_timetable_window": False,  # True -> attendance only during class
    "session_timeout_minutes": 120,

    # ---- Self-marked attendance ------------------------------------------
    "allow_self_attendance": True,     # students may raise their own request
    # Minutes after a class ends during which a student may still self-mark.
    # 240 keeps the window open for the rest of the teaching day, which suits
    # a college where students often mark up during a free period; tighten it
    # to 15-30 for strict, in-class-only marking.
    "self_mark_grace_minutes": 240,
}


class _Config:
    """Small dict-like wrapper around ``app_config.json``.

    Reads are attribute- or key-based; writes are persisted immediately so a
    crash never loses a settings change.
    """

    def __init__(self) -> None:
        self._data: dict = dict(DEFAULT_CONFIG)
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                # Merge so newly added defaults appear for existing installs.
                self._data.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
            except (json.JSONDecodeError, OSError):
                # A corrupt config must never stop the application booting.
                pass

    def save(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=4)

    # -- access ------------------------------------------------------------
    def get(self, key: str, default=None):
        return self._data.get(key, DEFAULT_CONFIG.get(key, default))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def update(self, values: dict) -> None:
        self._data.update(values)
        self.save()

    def as_dict(self) -> dict:
        return dict(self._data)

    def __getitem__(self, key: str):
        return self.get(key)

    def __setitem__(self, key: str, value) -> None:
        self.set(key, value)


# Singleton used across the whole application.
config = _Config()


# --------------------------------------------------------------------------
# Domain enumerations
# --------------------------------------------------------------------------
ROLE_ADMIN = "Admin"
ROLE_FACULTY = "Faculty"
ROLE_STUDENT = "Student"
ROLES = [ROLE_ADMIN, ROLE_FACULTY, ROLE_STUDENT]

STATUS_PRESENT = "Present"
STATUS_ABSENT = "Absent"
STATUS_LATE = "Late"
STATUS_LEAVE = "Leave"
STATUS_MEDICAL = "Medical Leave"
ATTENDANCE_STATUSES = [
    STATUS_PRESENT, STATUS_ABSENT, STATUS_LATE, STATUS_LEAVE, STATUS_MEDICAL,
]

# Statuses that count towards the attendance percentage numerator.
PRESENT_LIKE = (STATUS_PRESENT, STATUS_LATE)

LEAVE_TYPES = [
    "Casual Leave", "Medical Leave", "Sports Leave",
    "Official Leave", "Academic Leave", "Other Leave",
]

LEAVE_PENDING = "Pending"
LEAVE_APPROVED = "Approved"
LEAVE_REJECTED = "Rejected"
LEAVE_RETURNED = "Returned for Correction"
LEAVE_STATUSES = [LEAVE_PENDING, LEAVE_APPROVED, LEAVE_REJECTED, LEAVE_RETURNED]

MARK_METHOD_FACE = "Face Recognition"
MARK_METHOD_MANUAL = "Manual"
MARK_METHOD_LEAVE = "Leave System"
MARK_METHOD_AUTO = "Auto (Absent)"

DAYS_OF_WEEK = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]

GENDERS = ["Male", "Female", "Other"]
STUDENT_STATUSES = ["Active", "Inactive", "Alumni", "Dropped"]
FACULTY_STATUSES = ["Active", "Inactive", "On Leave"]

ALLOWED_DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_DOC_SIZE_MB = 10
