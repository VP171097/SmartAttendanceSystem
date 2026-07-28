"""
Branded splash screen.

Shown while the database is opened, the schema verified, sample data seeded on
first run and the face-recognition backend detected.  Each step reports back so
the user sees genuine progress rather than a decorative animation.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

import customtkinter as ctk

from config.settings import APP_NAME, APP_TAGLINE, APP_VERSION, config
from config.theme import FONTS

WIDTH, HEIGHT = 620, 400

NAVY = "#0F2A4A"
BLUE = "#1D4ED8"
LIGHT_TEXT = "#BACDE6"


class SplashScreen(ctk.CTkToplevel):
    """Borderless start-up window with a progress bar."""

    def __init__(self, master):
        super().__init__(master)

        self.overrideredirect(True)          # no title bar
        self.configure(fg_color=NAVY)
        self._centre()
        self.attributes("-topmost", True)

        self._build()
        self.update()

    def _centre(self) -> None:
        x = (self.winfo_screenwidth() - WIDTH) // 2
        y = (self.winfo_screenheight() - HEIGHT) // 2
        self.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def _build(self) -> None:
        # Accent bar along the top edge.
        ctk.CTkFrame(self, height=5, corner_radius=0, fg_color=BLUE).pack(fill="x")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=44, pady=(30, 22))

        # ---- logo --------------------------------------------------------
        logo_path = Path(config.get("logo_path", ""))
        if logo_path.exists():
            try:
                from PIL import Image
                image = ctk.CTkImage(Image.open(logo_path), size=(74, 74))
                ctk.CTkLabel(body, image=image, text="").pack(pady=(0, 12))
            except Exception:                   # noqa: BLE001
                self._logo_placeholder(body)
        else:
            self._logo_placeholder(body)

        # ---- college identity -------------------------------------------
        college_name = config.get("college_name", "College")
        ctk.CTkLabel(body, text=college_name,
                     font=(FONTS["title"][0], 21 if len(college_name) < 40 else 17, "bold"),
                     text_color="#FFFFFF", wraplength=520).pack()
        ctk.CTkLabel(body, text=config.get("college_address", ""),
                     font=(FONTS["small"][0], 10), text_color=LIGHT_TEXT,
                     wraplength=500).pack(pady=(3, 0))

        ctk.CTkFrame(body, height=1, fg_color="#2A4A72").pack(fill="x", pady=16)

        # ---- application identity ---------------------------------------
        ctk.CTkLabel(body, text=APP_NAME, font=(FONTS["heading"][0], 16, "bold"),
                     text_color="#FFFFFF").pack()
        ctk.CTkLabel(body, text=APP_TAGLINE, font=(FONTS["small"][0], 10),
                     text_color=LIGHT_TEXT).pack(pady=(2, 0))

        # ---- progress ----------------------------------------------------
        progress_area = ctk.CTkFrame(self, fg_color="transparent")
        progress_area.pack(fill="x", padx=44, pady=(0, 8))

        self.progress = ctk.CTkProgressBar(progress_area, height=5, corner_radius=3,
                                           progress_color=BLUE, fg_color="#1B3F6B")
        self.progress.pack(fill="x")
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(progress_area, text="Starting...",
                                         font=(FONTS["small"][0], 10),
                                         text_color=LIGHT_TEXT)
        self.status_label.pack(pady=(7, 0))

        # ---- footer ------------------------------------------------------
        footer = ctk.CTkFrame(self, fg_color="transparent", height=30)
        footer.pack(fill="x", padx=44, pady=(0, 14))
        ctk.CTkLabel(footer, text=f"Version {APP_VERSION}",
                     font=(FONTS["small"][0], 9), text_color="#7A93B5").pack(side="left")
        ctk.CTkLabel(footer, text=f"Code: {config.get('college_code','')}",
                     font=(FONTS["small"][0], 9), text_color="#7A93B5").pack(side="right")

    def _logo_placeholder(self, parent) -> None:
        """Circular monogram used until the college supplies a logo file."""
        short = config.get("college_short_name", "C")[:3].upper()
        ctk.CTkLabel(parent, text=short, font=(FONTS["title"][0], 26, "bold"),
                     text_color=NAVY, fg_color="#FFFFFF", corner_radius=37,
                     width=74, height=74).pack(pady=(0, 12))

    # ------------------------------------------------------------------
    def set_status(self, message: str, progress: float) -> None:
        """Update the caption and bar.  ``progress`` is 0.0-1.0."""
        try:
            self.status_label.configure(text=message)
            self.progress.set(max(0.0, min(1.0, progress)))
            self.update_idletasks()
            self.update()
        except tk.TclError:
            pass          # window already closed

    def close(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass
