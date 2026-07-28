"""
About window.

Shows the college branding, the application identity and -- for administrators
only -- live database statistics.

Technical diagnostics (library versions, file paths) are deliberately absent:
they mean nothing to a student or a lecturer, and the file paths in particular
are information a general user has no reason to see.  An administrator who
needs the recognition-engine status has Settings -> Face Recognition.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from config.settings import APP_NAME, APP_TAGLINE, APP_VERSION, DB_PATH, config
from config.theme import FONTS, SEMANTIC, color
from core.auth import session
from core.database import get_db
from core.logger import get_logger
from ui.widgets.components import PageHeader, SectionCard, StatCard

logger = get_logger("ui.about")

PROJECT_CREDIT = "A Project Developed by Ankita Pandey - CSE - IInd Year"


class AboutView(ctk.CTkFrame):
    """Application and institution information."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app

        self._build()
        self.refresh()

    # ==================================================================
    def _build(self) -> None:
        header = PageHeader(self, title="About",
                            subtitle="Application and institution information",
                            icon="ⓘ")
        header.pack(fill="x", padx=18, pady=(14, 10))

        self.page = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.page.pack(fill="both", expand=True, padx=18, pady=(0, 14))

    def refresh(self) -> None:
        for widget in self.page.winfo_children():
            widget.destroy()

        self._build_banner()
        self._build_institution()
        # Record counts are administrative information, not general-user content.
        if session.is_admin:
            self._build_statistics()
        self._build_overview()
        self._build_credit()

    # ------------------------------------------------------------------
    def _build_banner(self) -> None:
        banner = ctk.CTkFrame(self.page, height=0, corner_radius=12, fg_color="#0F2A4A")
        banner.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(banner, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=18)

        logo_path = Path(config.get("logo_path", ""))
        if logo_path.exists():
            try:
                from PIL import Image
                image = ctk.CTkImage(Image.open(logo_path), size=(74, 74))
                ctk.CTkLabel(inner, image=image, text="").pack(side="left", padx=(0, 20))
            except Exception:                   # noqa: BLE001
                self._monogram(inner)
        else:
            self._monogram(inner)

        text = ctk.CTkFrame(inner, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(text, text=config.get("college_name", "College"),
                     font=(FONTS["title"][0], 19, "bold"), text_color="#FFFFFF",
                     anchor="w", wraplength=700, justify="left").pack(fill="x")
        ctk.CTkLabel(text, text=config.get("affiliation", ""),
                     font=(FONTS["small"][0], 11), text_color="#BACDE6",
                     anchor="w", wraplength=700, justify="left").pack(fill="x",
                                                                      pady=(1, 9))

        ctk.CTkLabel(text, text=APP_NAME, font=(FONTS["heading"][0], 15, "bold"),
                     text_color="#FFFFFF", anchor="w").pack(fill="x")
        ctk.CTkLabel(text, text=f"{APP_TAGLINE}   |   Version {APP_VERSION}",
                     font=(FONTS["small"][0], 11), text_color="#BACDE6",
                     anchor="w").pack(fill="x")

    def _monogram(self, parent) -> None:
        short = config.get("college_short_name", "C")[:4].upper()
        ctk.CTkLabel(parent, text=short, font=(FONTS["title"][0], 24, "bold"),
                     text_color="#0F2A4A", fg_color="#FFFFFF", corner_radius=37,
                     width=74, height=74).pack(side="left", padx=(0, 20))

    # ------------------------------------------------------------------
    def _build_institution(self) -> None:
        card = SectionCard(self.page, "Institution",
                           "Branding shown on reports, ID cards and attendance sheets")
        card.pack(fill="x", pady=(0, 12))

        grid = ctk.CTkFrame(card.body, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure(0, weight=1, uniform="grid")
        grid.grid_columnconfigure(1, weight=1, uniform="grid")

        rows = [
            ("College Name", config.get("college_name")),
            ("College Code", config.get("college_code")),
            ("Address", config.get("college_address")),
            ("Phone", config.get("college_phone")),
            ("Email", config.get("college_email")),
            ("Website", config.get("college_website")),
            ("Principal", config.get("principal_name")),
            ("Affiliation", config.get("affiliation")),
            ("Established", config.get("college_established", "-")),
            ("Academic Session", config.get("current_academic_session")),
            ("Attendance Threshold", f"{config.get('attendance_threshold', 75)}%"),
        ]

        for index, (label, value) in enumerate(rows):
            row = ctk.CTkFrame(grid, fg_color="transparent")
            row.grid(row=index // 2, column=index % 2, sticky="ew",
                     padx=(0, 18), pady=2)
            ctk.CTkLabel(row, text=label, font=FONTS["small_bold"],
                         text_color=color("text_muted"), width=150,
                         anchor="nw", justify="left").pack(side="left", anchor="n")
            ctk.CTkLabel(row, text=str(value or "-"), font=FONTS["body"],
                         text_color=color("text"), anchor="w", wraplength=290,
                         justify="left").pack(side="left", fill="x", expand=True)

    # ------------------------------------------------------------------
    def _build_statistics(self) -> None:
        card = SectionCard(self.page, "Database Statistics",
                           "Live record counts - visible to administrators only")
        card.pack(fill="x", pady=(0, 12))

        try:
            stats = get_db().table_stats()
            size_mb = DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0
        except Exception as exc:                # noqa: BLE001
            ctk.CTkLabel(card.body, text=f"Could not read statistics: {exc}",
                         font=FONTS["body"], text_color=SEMANTIC["danger"]).pack(pady=12)
            return

        tiles = ctk.CTkFrame(card.body, fg_color="transparent")
        tiles.pack(fill="x", pady=(0, 12))
        for index in range(5):
            tiles.grid_columnconfigure(index, weight=1, uniform="tiles")

        headline = [
            ("Students", stats.get("students", 0), "☺", SEMANTIC["info"]),
            ("Faculty", stats.get("faculty", 0), "★", SEMANTIC["purple"]),
            ("Subjects", stats.get("subjects", 0), "▣", SEMANTIC["teal"]),
            ("Attendance Records", stats.get("attendance", 0), "≡", SEMANTIC["success"]),
            ("Database Size", f"{size_mb:.1f} MB", "▤", SEMANTIC["warning"]),
        ]
        for index, (label, value, icon, accent) in enumerate(headline):
            StatCard(tiles, label,
                     f"{value:,}" if isinstance(value, int) else str(value),
                     icon, accent).grid(row=0, column=index, sticky="ew", padx=3)

        detail = ctk.CTkFrame(card.body, fg_color="transparent")
        detail.pack(fill="x")
        for index in range(3):
            detail.grid_columnconfigure(index, weight=1, uniform="detail")

        interesting = [(name, count) for name, count in sorted(stats.items()) if count]
        for index, (name, count) in enumerate(interesting):
            row = ctk.CTkFrame(detail, fg_color="transparent")
            row.grid(row=index // 3, column=index % 3, sticky="ew", padx=(0, 16), pady=1)
            ctk.CTkLabel(row, text=name.replace("_", " ").title(),
                         font=(FONTS["small"][0], 11),
                         text_color=color("text_muted"), anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=f"{count:,}", font=FONTS["small_bold"],
                         text_color=color("text")).pack(side="right")

    # ------------------------------------------------------------------
    def _build_overview(self) -> None:
        card = SectionCard(self.page, "About This Project", "")
        card.pack(fill="x", pady=(0, 12))

        text = (
            f"{APP_NAME} automates classroom attendance using face recognition, "
            "while keeping every manual control a college actually needs: manual "
            "marking, student self-marking with teacher verification, corrections "
            "with a recorded reason, leave workflow, attendance locking and a "
            "complete audit trail.\n\n"
            "Key capabilities:\n"
            "  -  Face recognition attendance with multi-face detection, duplicate "
            "prevention, unknown-face logging and confidence scoring\n"
            "  -  One-click manual marking by teachers, and student self-marking "
            "that a teacher must verify before it counts\n"
            "  -  Leave management with medical certificate rules and faculty review\n"
            "  -  Eleven report types exported to PDF, Excel or CSV with college branding\n"
            "  -  Analytics across branch, subject, faculty, semester and time\n"
            "  -  Bulk student promotion preserving attendance history\n"
            "  -  Printable student ID cards with barcode\n"
            "  -  Holiday calendar, exam eligibility checking and automatic backups\n\n"
            "Attendance is recorded by face recognition only - no QR codes, RFID "
            "cards or one-time passwords - exactly as the project specification "
            "requires. The barcode on the ID card is for office and library use, "
            "never for marking attendance."
        )

        ctk.CTkLabel(card.body, text=text, font=FONTS["body"],
                     text_color=color("text_muted"), anchor="w", justify="left",
                     wraplength=980).pack(fill="x")

    # ------------------------------------------------------------------
    def _build_credit(self) -> None:
        """Centred project credit -- the footer of the About page."""
        footer = ctk.CTkFrame(self.page, height=0, corner_radius=10,
                              fg_color=color("surface"), border_width=1,
                              border_color=color("border"))
        footer.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(footer, text=PROJECT_CREDIT,
                     font=(FONTS["subhead"][0], 14, "bold"),
                     text_color=color("primary")).pack(pady=(14, 3))
        ctk.CTkLabel(footer,
                     text=f"{config.get('college_name', '')}   |   "
                          f"Version {APP_VERSION}   |   "
                          f"{datetime.now():%d %B %Y}",
                     font=(FONTS["small"][0], 11),
                     text_color=color("text_muted")).pack(pady=(0, 14))
