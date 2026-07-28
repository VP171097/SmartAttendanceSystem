"""
Application logging.

Writes a rotating daily log to ``logs/`` and mirrors WARNING and above to the
console.  Every module obtains its logger through :func:`get_logger` so log
lines carry the originating module name.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from config.settings import LOG_DIR, APP_NAME, APP_VERSION

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger.  Safe to call more than once."""
    global _configured
    if _configured:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "application.log"

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # 2 MB per file, 10 files kept -> roughly a semester of activity.
    file_handler = RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(levelname)-8s | %(message)s"))
    console.setLevel(logging.WARNING)
    root.addHandler(console)

    # A dedicated error log makes post-mortem support calls much quicker.
    error_handler = RotatingFileHandler(
        LOG_DIR / "error.log", maxBytes=1024 * 1024, backupCount=5, encoding="utf-8"
    )
    error_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    error_handler.setLevel(logging.ERROR)
    root.addHandler(error_handler)

    _configured = True
    logging.getLogger("startup").info("=" * 70)
    logging.getLogger("startup").info("%s v%s starting", APP_NAME, APP_VERSION)


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger, configuring logging on first use."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)


def log_exception(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Log an exception with its traceback in a consistent format."""
    logger.error("%s: %s: %s", message, type(exc).__name__, exc, exc_info=True)
