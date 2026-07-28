"""
Centralised colour palette and typography.

Every widget pulls its colours from :func:`palette` rather than hard-coding hex
values, so switching between Light and Dark mode is a single call.

CustomTkinter accepts a ``(light, dark)`` tuple for most colour options; we
return plain strings here and let the caller pick, which keeps the dashboards
readable when we mix in Matplotlib charts (Matplotlib has no notion of
CustomTkinter's appearance mode).
"""

from __future__ import annotations

import customtkinter as ctk

# --------------------------------------------------------------------------
# Professional Blue -- the default institutional palette
# --------------------------------------------------------------------------
LIGHT = {
    "bg":            "#F1F5F9",   # window background
    "surface":       "#FFFFFF",   # cards, panels
    "surface_alt":   "#E2E8F0",   # table stripes, hover
    "sidebar":       "#0F2A4A",   # deep navy navigation rail
    "sidebar_hover": "#1B3F6B",
    "sidebar_active": "#1D4ED8",
    "primary":       "#1D4ED8",
    "primary_hover": "#1E40AF",
    "secondary":     "#0EA5E9",
    "text":          "#0F172A",
    "text_muted":    "#64748B",
    "text_invert":   "#FFFFFF",
    "border":        "#CBD5E1",
    "header":        "#0F2A4A",
}

DARK = {
    "bg":            "#0B1220",
    "surface":       "#151E31",
    "surface_alt":   "#1E293B",
    "sidebar":       "#080E1A",
    "sidebar_hover": "#16233A",
    "sidebar_active": "#2563EB",
    "primary":       "#2563EB",
    "primary_hover": "#3B82F6",
    "secondary":     "#38BDF8",
    "text":          "#E2E8F0",
    "text_muted":    "#94A3B8",
    "text_invert":   "#FFFFFF",
    "border":        "#334155",
    "header":        "#080E1A",
}

# --------------------------------------------------------------------------
# Semantic colours -- identical in both modes so status is never ambiguous
# --------------------------------------------------------------------------
SEMANTIC = {
    "success":  "#16A34A",
    "warning":  "#F59E0B",
    "danger":   "#DC2626",
    "info":     "#0EA5E9",
    "purple":   "#7C3AED",
    "teal":     "#0D9488",
    "neutral":  "#64748B",
}

# Attendance status -> colour.  Used by tables, badges and charts alike so a
# "Late" cell is the same amber everywhere in the application.
STATUS_COLORS = {
    "Present":       SEMANTIC["success"],
    "Absent":        SEMANTIC["danger"],
    "Late":          SEMANTIC["warning"],
    "Leave":         SEMANTIC["info"],
    "Medical Leave": SEMANTIC["purple"],
}

LEAVE_STATUS_COLORS = {
    "Pending":                SEMANTIC["warning"],
    "Approved":               SEMANTIC["success"],
    "Rejected":               SEMANTIC["danger"],
    "Returned for Correction": SEMANTIC["info"],
}

# Ordered categorical palette for charts (branch/subject comparisons).
CHART_COLORS = [
    "#1D4ED8", "#0EA5E9", "#16A34A", "#F59E0B", "#DC2626",
    "#7C3AED", "#0D9488", "#DB2777", "#65A30D", "#EA580C",
]

# --------------------------------------------------------------------------
# Typography -- tuples are created lazily because CTkFont needs a live Tk root
# --------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI"

FONTS = {
    "display":  (FONT_FAMILY, 30, "bold"),
    "title":    (FONT_FAMILY, 22, "bold"),
    "heading":  (FONT_FAMILY, 17, "bold"),
    "subhead":  (FONT_FAMILY, 14, "bold"),
    "body":     (FONT_FAMILY, 13),
    "body_bold": (FONT_FAMILY, 13, "bold"),
    "small":    (FONT_FAMILY, 11),
    "small_bold": (FONT_FAMILY, 11, "bold"),
    "mono":     ("Consolas", 12),
    "metric":   (FONT_FAMILY, 28, "bold"),
}


def palette(mode: str | None = None) -> dict:
    """Return the colour dictionary for the requested appearance mode.

    Args:
        mode: ``"Light"``, ``"Dark"`` or ``None`` to follow CustomTkinter's
            current appearance mode.
    """
    if mode is None:
        mode = ctk.get_appearance_mode()
    return DARK if str(mode).lower() == "dark" else LIGHT


def color(key: str) -> tuple[str, str]:
    """Return a ``(light, dark)`` tuple that CustomTkinter can theme itself."""
    return (LIGHT[key], DARK[key])


def apply_theme(mode: str = "Light", color_theme: str = "blue") -> None:
    """Apply the global CustomTkinter appearance settings."""
    if mode not in ("Light", "Dark", "System"):
        mode = "Light"
    ctk.set_appearance_mode(mode)
    try:
        ctk.set_default_color_theme(color_theme)
    except (ValueError, FileNotFoundError):
        ctk.set_default_color_theme("blue")


def status_color(status: str) -> str:
    """Colour for an attendance status, falling back to neutral grey."""
    return STATUS_COLORS.get(status, SEMANTIC["neutral"])


def percentage_color(pct: float, threshold: float = 75.0) -> str:
    """Traffic-light colour for an attendance percentage.

    Green at or above the threshold, amber within 10 points below it, red
    otherwise -- matching how defaulter lists are read in practice.
    """
    if pct >= threshold:
        return SEMANTIC["success"]
    if pct >= threshold - 10:
        return SEMANTIC["warning"]
    return SEMANTIC["danger"]
