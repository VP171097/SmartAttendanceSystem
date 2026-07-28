"""
SQLite access layer.

Everything that touches the database goes through :class:`Database`.  It gives
the rest of the application:

*   a **thread-local connection** -- the face-recognition loop runs on a worker
    thread and SQLite connections cannot be shared across threads;
*   ``sqlite3.Row`` results so callers can use ``row["column"]``;
*   a ``transaction()`` context manager for multi-statement writes;
*   ``fetch_one`` / ``fetch_all`` / ``execute`` / ``executemany`` helpers that
    keep call sites free of cursor boilerplate.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Sequence

from config.settings import DB_PATH, DATABASE_DIR
from core.logger import get_logger

logger = get_logger("core.database")

_SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


class DatabaseError(Exception):
    """Raised for any unrecoverable database problem."""


class Database:
    """Thread-safe SQLite wrapper (singleton)."""

    _instance: "Database | None" = None
    _lock = threading.Lock()

    def __new__(cls, db_path: Path | None = None) -> "Database":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialised = False
            return cls._instance

    def __init__(self, db_path: Path | None = None) -> None:
        if self._initialised:
            return
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._local = threading.local()   # one connection per thread
        self._write_lock = threading.RLock()
        self._initialised = True
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        """The calling thread's connection, created on first access."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            # WAL lets the UI read while a background thread writes.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close this thread's connection if one is open."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def initialise_schema(self) -> None:
        """Create tables, indexes and views if they do not already exist."""
        if not _SCHEMA_FILE.exists():
            raise DatabaseError(f"Schema file missing: {_SCHEMA_FILE}")
        sql = _SCHEMA_FILE.read_text(encoding="utf-8")
        with self._write_lock:
            self.connection.executescript(sql)
            self.connection.commit()
        logger.info("Database schema verified at %s", self.db_path)

    def is_empty(self) -> bool:
        """True when the database has no users -- i.e. a fresh install."""
        try:
            row = self.fetch_one("SELECT COUNT(*) AS n FROM users")
            return (row["n"] if row else 0) == 0
        except sqlite3.Error:
            return True

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------
    def fetch_all(self, query: str, params: Sequence = ()) -> list[sqlite3.Row]:
        try:
            cur = self.connection.execute(query, params)
            return cur.fetchall()
        except sqlite3.Error as exc:
            logger.error("fetch_all failed: %s | SQL: %s", exc, query.strip()[:200])
            raise DatabaseError(str(exc)) from exc

    def fetch_one(self, query: str, params: Sequence = ()) -> sqlite3.Row | None:
        try:
            cur = self.connection.execute(query, params)
            return cur.fetchone()
        except sqlite3.Error as exc:
            logger.error("fetch_one failed: %s | SQL: %s", exc, query.strip()[:200])
            raise DatabaseError(str(exc)) from exc

    def fetch_value(self, query: str, params: Sequence = (), default: Any = None) -> Any:
        """Return the first column of the first row, or ``default``."""
        row = self.fetch_one(query, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    def execute(self, query: str, params: Sequence = ()) -> int:
        """Run a write statement and return ``lastrowid`` (or rowcount for updates)."""
        with self._write_lock:
            try:
                cur = self.connection.execute(query, params)
                self.connection.commit()
                return cur.lastrowid if cur.lastrowid else cur.rowcount
            except sqlite3.Error as exc:
                self.connection.rollback()
                logger.error("execute failed: %s | SQL: %s", exc, query.strip()[:200])
                raise DatabaseError(str(exc)) from exc

    def executemany(self, query: str, seq: Iterable[Sequence]) -> int:
        with self._write_lock:
            try:
                cur = self.connection.executemany(query, seq)
                self.connection.commit()
                return cur.rowcount
            except sqlite3.Error as exc:
                self.connection.rollback()
                logger.error("executemany failed: %s | SQL: %s", exc, query.strip()[:200])
                raise DatabaseError(str(exc)) from exc

    @contextmanager
    def transaction(self):
        """Group several writes into one atomic unit.

        Example::

            with db.transaction() as cur:
                cur.execute(...)
                cur.execute(...)
        """
        with self._write_lock:
            conn = self.connection
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()

    # ------------------------------------------------------------------
    # Convenience CRUD
    # ------------------------------------------------------------------
    def insert(self, table: str, data: dict) -> int:
        """Insert a dict as a row; returns the new primary key."""
        columns = ", ".join(data)
        placeholders = ", ".join("?" * len(data))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        return self.execute(sql, tuple(data.values()))

    def update(self, table: str, data: dict, where: str, where_params: Sequence = ()) -> int:
        """Update rows matching ``where``; returns affected row count."""
        assignments = ", ".join(f"{col} = ?" for col in data)
        sql = f"UPDATE {table} SET {assignments} WHERE {where}"
        return self.execute(sql, tuple(data.values()) + tuple(where_params))

    def delete(self, table: str, where: str, where_params: Sequence = ()) -> int:
        return self.execute(f"DELETE FROM {table} WHERE {where}", tuple(where_params))

    def exists(self, table: str, where: str, where_params: Sequence = ()) -> bool:
        row = self.fetch_one(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", where_params)
        return row is not None

    def count(self, table: str, where: str = "1=1", where_params: Sequence = ()) -> int:
        return int(self.fetch_value(f"SELECT COUNT(*) FROM {table} WHERE {where}",
                                    where_params, 0))

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def vacuum(self) -> None:
        """Reclaim free pages -- run after bulk deletions."""
        with self._write_lock:
            self.connection.execute("VACUUM")

    def table_stats(self) -> dict[str, int]:
        """Row counts for every user table -- shown in the About window."""
        tables = self.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return {t["name"]: self.count(t["name"]) for t in tables}


# Module-level singleton used throughout the application.
db = Database()


def get_db() -> Database:
    """Accessor kept for readability at call sites and easier test injection."""
    return db
