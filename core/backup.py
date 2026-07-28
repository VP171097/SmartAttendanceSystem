"""
Database backup and restore.

Backups use SQLite's own ``Connection.backup()`` API rather than a file copy,
so a backup taken while the application is running is guaranteed consistent
(a plain copy of a WAL-mode database can miss committed pages).

Naming follows ``attendance_backup_YYYYMMDD_HHMMSS.db``.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from config.settings import BACKUP_DIR, DB_PATH, config
from core.audit import ACTION_BACKUP, ACTION_RESTORE, log_audit
from core.database import get_db
from core.logger import get_logger

logger = get_logger("core.backup")

BACKUP_PREFIX = "attendance_backup_"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def create_backup(backup_type: str = "Manual", user: dict | None = None) -> tuple[bool, str, Path | None]:
    """Create a consistent snapshot of the database.

    Returns ``(success, message, path)``.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{BACKUP_PREFIX}{_timestamp()}.db"
    target = BACKUP_DIR / filename

    try:
        source = get_db().connection
        destination = sqlite3.connect(str(target))
        with destination:
            source.backup(destination)
        destination.close()

        size_kb = target.stat().st_size / 1024

        get_db().insert("backup_history", {
            "file_name": filename,
            "file_path": str(target),
            "file_size_kb": round(size_kb, 2),
            "backup_type": backup_type,
            "created_by": (user or {}).get("user_id"),
        })
        log_audit(user, ACTION_BACKUP, module="Backup",
                  new_value=filename, reason=f"{backup_type} backup")
        logger.info("Backup created: %s (%.1f KB)", filename, size_kb)

        prune_backups()
        return True, f"Backup created: {filename} ({size_kb:.1f} KB)", target

    except (sqlite3.Error, OSError) as exc:
        logger.error("Backup failed: %s", exc)
        if target.exists():
            target.unlink(missing_ok=True)
        return False, f"Backup failed: {exc}", None


def restore_backup(backup_path: str | Path, user: dict | None = None) -> tuple[bool, str]:
    """Replace the live database with a backup file.

    A safety copy of the current database is taken first, so a restore from a
    corrupt file is itself reversible.
    """
    source = Path(backup_path)
    if not source.exists():
        return False, "Backup file not found."

    # Verify the file really is a readable SQLite database with our schema.
    try:
        probe = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        tables = {r[0] for r in probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        probe.close()
        required = {"users", "students", "attendance", "subjects"}
        missing = required - tables
        if missing:
            return False, f"Not a valid backup: missing tables {', '.join(sorted(missing))}."
    except sqlite3.Error as exc:
        return False, f"Backup file is unreadable or corrupt: {exc}"

    # Safety copy of what we are about to overwrite.
    safety = BACKUP_DIR / f"pre_restore_{_timestamp()}.db"
    try:
        db = get_db()
        db.close()
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, safety)

        # WAL sidecar files must go, or SQLite may replay stale pages.
        for sidecar in (DB_PATH.with_suffix(".db-wal"), DB_PATH.with_suffix(".db-shm")):
            sidecar.unlink(missing_ok=True)

        shutil.copy2(source, DB_PATH)
        db.connection.execute("PRAGMA foreign_keys = ON")   # reopen

        log_audit(user, ACTION_RESTORE, module="Backup",
                  new_value=source.name,
                  reason=f"Restored from backup; safety copy {safety.name}")
        logger.warning("Database restored from %s", source.name)
        return True, (f"Database restored from {source.name}.\n"
                      f"A safety copy of the previous database was saved as {safety.name}.\n"
                      "Please sign in again.")

    except (OSError, sqlite3.Error) as exc:
        logger.error("Restore failed: %s", exc)
        # Roll back to the safety copy if we managed to make one.
        if safety.exists():
            try:
                shutil.copy2(safety, DB_PATH)
            except OSError:
                pass
        return False, f"Restore failed: {exc}"


def list_backups() -> list[dict]:
    """Backups present on disk, newest first, joined with their history row."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    history = {}
    try:
        for row in get_db().fetch_all("SELECT * FROM backup_history"):
            history[row["file_name"]] = dict(row)
    except Exception:                              # noqa: BLE001
        pass

    entries = []
    for path in BACKUP_DIR.glob(f"{BACKUP_PREFIX}*.db"):
        stat = path.stat()
        meta = history.get(path.name, {})
        entries.append({
            "file_name": path.name,
            "file_path": str(path),
            "size_kb": round(stat.st_size / 1024, 2),
            "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "backup_type": meta.get("backup_type", "Manual"),
        })
    return sorted(entries, key=lambda e: e["created_at"], reverse=True)


def prune_backups(keep: int | None = None) -> int:
    """Delete the oldest backups beyond the retention limit."""
    keep = keep if keep is not None else int(config.get("backup_retention", 20))
    if keep <= 0:
        return 0
    backups = list_backups()
    removed = 0
    for entry in backups[keep:]:
        try:
            Path(entry["file_path"]).unlink()
            get_db().delete("backup_history", "file_name = ?", (entry["file_name"],))
            removed += 1
        except OSError as exc:
            logger.warning("Could not delete old backup %s: %s", entry["file_name"], exc)
    if removed:
        logger.info("Pruned %d old backup(s), retaining %d", removed, keep)
    return removed


def delete_backup(file_name: str) -> tuple[bool, str]:
    path = BACKUP_DIR / file_name
    if not path.exists():
        return False, "Backup file not found."
    try:
        path.unlink()
        get_db().delete("backup_history", "file_name = ?", (file_name,))
        return True, f"Deleted {file_name}."
    except OSError as exc:
        return False, f"Could not delete backup: {exc}"


def auto_backup_if_due(user: dict | None = None) -> bool:
    """Run a scheduled backup when one is due.  Called at start-up.

    Returns True when a backup was actually taken.
    """
    if not config.get("auto_backup", True):
        return False

    interval_days = max(1, int(config.get("auto_backup_days", 1)))
    try:
        last = get_db().fetch_value(
            "SELECT created_at FROM backup_history "
            "WHERE backup_type = 'Automatic' ORDER BY backup_id DESC LIMIT 1"
        )
        if last:
            elapsed = datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            if elapsed.days < interval_days:
                return False
    except (ValueError, Exception):                # noqa: BLE001
        pass   # no history or unparseable date -> take a backup

    success, message, _ = create_backup("Automatic", user)
    logger.info("Automatic backup: %s", message)
    return success
