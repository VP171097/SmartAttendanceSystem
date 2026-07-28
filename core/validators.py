"""
Input validation helpers.

Each validator returns ``(is_valid, message)``.  Forms collect the messages and
show them next to the offending field, which keeps validation logic out of the
UI layer and makes it unit-testable.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from config.settings import ALLOWED_DOC_EXTENSIONS, MAX_DOC_SIZE_MB

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MOBILE_RE = re.compile(r"^[6-9]\d{9}$")            # Indian 10-digit mobile
ENROLLMENT_RE = re.compile(r"^[A-Za-z0-9\-/]{4,20}$")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,59}$")
CODE_RE = re.compile(r"^[A-Za-z0-9\-]{2,15}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SESSION_RE = re.compile(r"^\d{4}\s*[-–]\s*\d{2,4}$")   # 2026-27 / 2026-2027
BATCH_RE = re.compile(r"^\d{4}\s*[-–]\s*\d{4}$")       # 2026-2029

Result = tuple[bool, str]

OK: Result = (True, "")


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------
def required(value, field: str = "Field") -> Result:
    if value is None or str(value).strip() == "":
        return False, f"{field} is required."
    return OK


def max_length(value: str, limit: int, field: str = "Field") -> Result:
    if value and len(str(value)) > limit:
        return False, f"{field} must be {limit} characters or fewer."
    return OK


def is_number(value, field: str = "Value", minimum=None, maximum=None) -> Result:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False, f"{field} must be a number."
    if minimum is not None and number < minimum:
        return False, f"{field} must be at least {minimum}."
    if maximum is not None and number > maximum:
        return False, f"{field} must not exceed {maximum}."
    return OK


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
def validate_name(value: str, field: str = "Name") -> Result:
    value = (value or "").strip()
    if not value:
        return False, f"{field} is required."
    if not NAME_RE.match(value):
        return False, (f"{field} must start with a letter and may contain only "
                       "letters, spaces, apostrophes, dots and hyphens.")
    return OK


def validate_email(value: str, allow_blank: bool = True) -> Result:
    value = (value or "").strip()
    if not value:
        return OK if allow_blank else (False, "Email is required.")
    if not EMAIL_RE.match(value):
        return False, "Enter a valid email address (e.g. name@college.ac.in)."
    return OK


def validate_mobile(value: str, allow_blank: bool = True) -> Result:
    value = (value or "").strip().replace(" ", "").replace("-", "")
    if value.startswith("+91"):
        value = value[3:]
    if not value:
        return OK if allow_blank else (False, "Mobile number is required.")
    if not MOBILE_RE.match(value):
        return False, "Mobile must be 10 digits starting with 6, 7, 8 or 9."
    return OK


def validate_enrollment(value: str) -> Result:
    value = (value or "").strip()
    if not value:
        return False, "Enrollment number is required."
    if not ENROLLMENT_RE.match(value):
        return False, ("Enrollment number must be 4-20 characters "
                       "(letters, digits, hyphen or slash).")
    return OK


def validate_roll_no(value: str) -> Result:
    value = (value or "").strip()
    if not value:
        return False, "Roll number is required."
    if len(value) > 15:
        return False, "Roll number must be 15 characters or fewer."
    return OK


def validate_code(value: str, field: str = "Code") -> Result:
    value = (value or "").strip()
    if not value:
        return False, f"{field} is required."
    if not CODE_RE.match(value):
        return False, f"{field} must be 2-15 letters, digits or hyphens."
    return OK


# ---------------------------------------------------------------------------
# Dates & times
# ---------------------------------------------------------------------------
def parse_date(value) -> date | None:
    """Accept the handful of formats users actually type."""
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def validate_date(value, field: str = "Date", allow_blank: bool = True,
                  allow_future: bool = True, allow_past: bool = True) -> Result:
    if not str(value or "").strip():
        return OK if allow_blank else (False, f"{field} is required.")
    parsed = parse_date(value)
    if parsed is None:
        return False, f"{field} must be a valid date (YYYY-MM-DD)."
    today = date.today()
    if not allow_future and parsed > today:
        return False, f"{field} cannot be in the future."
    if not allow_past and parsed < today:
        return False, f"{field} cannot be in the past."
    return OK


def validate_dob(value) -> Result:
    """Date of birth with a sane age window for a diploma student."""
    ok, msg = validate_date(value, "Date of birth", allow_blank=False, allow_future=False)
    if not ok:
        return False, msg
    dob = parse_date(value)
    age = (date.today() - dob).days / 365.25
    if age < 12:
        return False, "Date of birth implies an age below 12 years."
    if age > 75:
        return False, "Date of birth implies an age above 75 years."
    return OK


def validate_date_range(from_value, to_value, field: str = "Date range") -> Result:
    start, end = parse_date(from_value), parse_date(to_value)
    if start is None:
        return False, "From date is invalid."
    if end is None:
        return False, "To date is invalid."
    if end < start:
        return False, f"{field}: 'To' date cannot be before the 'From' date."
    return OK


def validate_time(value, field: str = "Time") -> Result:
    if not TIME_RE.match(str(value or "").strip()):
        return False, f"{field} must be in 24-hour HH:MM format (e.g. 09:30)."
    return OK


def validate_time_range(start: str, end: str) -> Result:
    for value, label in ((start, "Start time"), (end, "End time")):
        ok, msg = validate_time(value, label)
        if not ok:
            return False, msg
    if end <= start:                       # lexical compare is safe for HH:MM
        return False, "End time must be after start time."
    return OK


# ---------------------------------------------------------------------------
# Academic identifiers
# ---------------------------------------------------------------------------
def validate_session_name(value: str) -> Result:
    value = (value or "").strip()
    if not SESSION_RE.match(value):
        return False, "Academic session must look like 2026-27."
    return OK


def validate_batch_name(value: str) -> Result:
    value = (value or "").strip()
    if not BATCH_RE.match(value):
        return False, "Batch must look like 2026-2029."
    parts = re.split(r"[-–]", value)
    try:
        start, end = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return False, "Batch years could not be read."
    if end <= start:
        return False, "Batch end year must be after the start year."
    if end - start > 8:
        return False, "Batch span looks too long (maximum 8 years)."
    return OK


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def validate_username(value: str) -> Result:
    value = (value or "").strip()
    if len(value) < 3:
        return False, "Username must be at least 3 characters."
    if len(value) > 30:
        return False, "Username must be 30 characters or fewer."
    if not re.match(r"^[A-Za-z0-9._\-]+$", value):
        return False, "Username may contain only letters, digits, dot, underscore and hyphen."
    return OK


def validate_password(value: str, strict: bool = True) -> Result:
    """Password policy.

    ``strict`` is relaxed for the seeded demo accounts so a classroom demo is
    not blocked by policy, but any password a user *sets* goes through the full
    check.
    """
    value = value or ""
    if len(value) < 6:
        return False, "Password must be at least 6 characters."
    if not strict:
        return OK
    if len(value) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Za-z]", value):
        return False, "Password must contain at least one letter."
    if not re.search(r"\d", value):
        return False, "Password must contain at least one digit."
    return OK


def passwords_match(first: str, second: str) -> Result:
    if first != second:
        return False, "Passwords do not match."
    return OK


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def validate_document(path: str | Path, allow_blank: bool = True) -> Result:
    """Check a supporting document (medical certificate, etc.)."""
    if not path:
        return OK if allow_blank else (False, "A document is required.")
    file_path = Path(path)
    if not file_path.exists():
        return False, "The selected file could not be found."
    if file_path.suffix.lower() not in ALLOWED_DOC_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".").upper() for e in ALLOWED_DOC_EXTENSIONS))
        return False, f"Unsupported file type. Allowed: {allowed}."
    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_DOC_SIZE_MB:
        return False, f"File is {size_mb:.1f} MB; the limit is {MAX_DOC_SIZE_MB} MB."
    return OK


def validate_image(path: str | Path, allow_blank: bool = True) -> Result:
    if not path:
        return OK if allow_blank else (False, "An image is required.")
    file_path = Path(path)
    if not file_path.exists():
        return False, "The selected image could not be found."
    if file_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp"}:
        return False, "Image must be JPG, JPEG, PNG or BMP."
    return OK


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def validate_all(*results: Result) -> Result:
    """Return the first failure among several validator results."""
    for ok, message in results:
        if not ok:
            return False, message
    return OK


def collect_errors(checks: dict[str, Result]) -> dict[str, str]:
    """Map field name -> error message for every failing check."""
    return {field: msg for field, (ok, msg) in checks.items() if not ok}


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------
def safe_filename(value: str, replacement: str = "_") -> str:
    """Make a string safe for use in a filename on Windows and POSIX."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', replacement, str(value or "").strip())
    cleaned = re.sub(rf"{re.escape(replacement)}{{2,}}", replacement, cleaned)
    return cleaned.strip(" .")[:120] or "unnamed"


def normalise_mobile(value: str) -> str:
    """Strip formatting and the +91 prefix so numbers compare equal."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits[-10:] if len(digits) >= 10 else digits


def title_case(value: str) -> str:
    """Title-case a person's name without mangling initials."""
    return " ".join(word.capitalize() if len(word) > 2 else word.upper()
                    for word in str(value or "").split())
