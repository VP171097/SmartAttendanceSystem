"""
Smart Attendance Management System -- application entry point.

    python main.py

Start-up sequence
-----------------
1. Create the storage tree if it is missing.
2. Configure logging.
3. Open the database and verify (or create) the schema.
4. Seed the default academic structure and demo data on a first run.
5. Detect the face-recognition backend.
6. Show the login window, then the main application shell.

The root ``Tk`` window is kept hidden throughout; the splash, login and main
shell are what the user actually sees.  That keeps a single Tk main loop --
several ``mainloop()`` calls is the usual source of "window not responding"
bugs in multi-window Tkinter applications.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Make the package importable when launched from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import customtkinter as ctk

from config.settings import (APP_NAME, APP_VERSION, DB_PATH, config,
                             ensure_directories)
from config.theme import apply_theme
from core.logger import get_logger, setup_logging

logger = get_logger("main")


class Application:
    """Owns the root window and drives the start-up sequence."""

    def __init__(self) -> None:
        ensure_directories()
        setup_logging()

        apply_theme(config.get("theme_mode", "Light"),
                    config.get("color_theme", "blue"))

        self.root = ctk.CTk()
        self.root.withdraw()                 # hidden host for every other window
        self.root.title(APP_NAME)
        self._set_window_icon()

        # Any exception escaping a Tk callback lands here rather than silently
        # printing to stderr where nobody will see it.
        self.root.report_callback_exception = self._handle_tk_exception

        self.splash = None
        self.app_window = None

    # ------------------------------------------------------------------
    def _set_window_icon(self) -> None:
        icon = Path(config.get("logo_path", ""))
        if not icon.exists():
            return
        try:
            from PIL import Image, ImageTk
            self._icon_image = ImageTk.PhotoImage(Image.open(icon))
            self.root.iconphoto(True, self._icon_image)
        except Exception:                       # noqa: BLE001 - cosmetic only
            pass

    def _handle_tk_exception(self, exc_type, exc_value, exc_tb) -> None:
        logger.error("Unhandled UI exception: %s", exc_value,
                     exc_info=(exc_type, exc_value, exc_tb))
        try:
            from ui.widgets.dialogs import show_error
            show_error(self.root, "Unexpected Error",
                       f"{exc_type.__name__}: {exc_value}\n\n"
                       "The action was cancelled. Details are in logs/error.log.")
        except Exception:                       # noqa: BLE001
            traceback.print_exception(exc_type, exc_value, exc_tb)

    # ------------------------------------------------------------------
    def start(self) -> None:
        from ui.splash import SplashScreen

        self.splash = SplashScreen(self.root)
        self.root.after(120, self._boot)
        self.root.mainloop()

    def _boot(self) -> None:
        """Run the start-up steps, reporting each on the splash screen."""
        try:
            self.splash.set_status("Preparing storage folders...", 0.10)
            ensure_directories()

            self.splash.set_status("Opening database...", 0.25)
            from core.database import get_db
            database = get_db()
            database.initialise_schema()

            fresh = database.is_empty()
            if fresh:
                self.splash.set_status(
                    "First run: creating academic structure and sample data...", 0.42)
                from core.seed import seed_database
                seed_database()
                self.splash.set_status("Sample data created.", 0.72)
            else:
                self.splash.set_status("Loading records...", 0.55)

            self.splash.set_status("Detecting face recognition backend...", 0.82)
            from services.face_service import ACTIVE_BACKEND, BACKEND_LABEL
            if ACTIVE_BACKEND:
                logger.info("Face recognition backend: %s", BACKEND_LABEL)
            else:
                logger.warning("No face recognition backend available - "
                               "manual attendance only.")

            self.splash.set_status("Starting application...", 0.96)
            students = database.count("students")
            logger.info("Database ready at %s (%d students)", DB_PATH, students)

            self.splash.set_status("Ready", 1.0)
            self.root.after(420, self._show_login)

        except Exception as exc:                # noqa: BLE001
            logger.critical("Start-up failed: %s", exc, exc_info=True)
            if self.splash:
                self.splash.close()
            self._fatal(exc)

    def _fatal(self, exc: Exception) -> None:
        """Report a start-up failure the user can act on, then exit."""
        self.root.deiconify()
        try:
            from ui.widgets.dialogs import show_error
            show_error(self.root, "Could Not Start",
                       f"{APP_NAME} could not start.\n\n"
                       f"{type(exc).__name__}: {exc}\n\n"
                       "The full traceback is in logs/error.log.\n\n"
                       "If this persists, restore a backup from the backups/ folder "
                       "or delete database/attendance.db to rebuild from scratch.")
        except Exception:                       # noqa: BLE001
            print(f"FATAL: {exc}", file=sys.stderr)
            traceback.print_exc()
        self.root.destroy()
        sys.exit(1)

    # ------------------------------------------------------------------
    def _show_login(self) -> None:
        if self.splash:
            self.splash.close()
            self.splash = None

        from ui.login import LoginWindow
        LoginWindow(self.root, on_success=self._show_app)

    def _show_app(self, _user: dict) -> None:
        from ui.app_window import AppWindow

        self.root.deiconify()
        self.root.state("zoomed")               # maximised on Windows
        self.root.minsize(1180, 700)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        self.app_window = AppWindow(self.root, on_logout=self._back_to_login)

    def _back_to_login(self) -> None:
        self.app_window = None
        self.root.withdraw()
        self._show_login()

    def _quit(self) -> None:
        from ui.widgets.dialogs import ask_confirm
        if not ask_confirm(self.root, "Exit Application",
                           f"Close {APP_NAME}?", confirm_text="Exit"):
            return

        try:
            from core.auth import session
            if session.is_authenticated:
                from core.auth import logout
                logout()
            from core.database import get_db
            get_db().close()
        except Exception:                       # noqa: BLE001
            pass

        logger.info("Application closed by user")
        self.root.destroy()


def main() -> int:
    print(f"{APP_NAME} v{APP_VERSION}")
    print("Starting...\n")
    try:
        Application().start()
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:                    # noqa: BLE001
        logger.critical("Fatal error: %s", exc, exc_info=True)
        print(f"\nFATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
