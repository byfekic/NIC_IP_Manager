"""SQLite storage: schema creation, migration and connection handling.

Every query in this package is parameterized; no SQL is ever built by string
formatting with user data (specification section 36).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from app.utils.errors import StorageError
from app.utils.logging_setup import get_logger
from app.utils.paths import database_path

log = get_logger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS presets (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL UNIQUE,
    adapter_identifier TEXT    NOT NULL DEFAULT '',
    adapter_mac        TEXT    NOT NULL DEFAULT '',
    adapter_name       TEXT    NOT NULL DEFAULT '',
    mode               TEXT    NOT NULL CHECK (mode IN ('dhcp','static')),
    ip_address         TEXT    NOT NULL DEFAULT '',
    subnet_mask        TEXT    NOT NULL DEFAULT '',
    gateway            TEXT    NOT NULL DEFAULT '',
    description        TEXT    NOT NULL DEFAULT '',
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT NOT NULL,
    adapter_name   TEXT NOT NULL DEFAULT '',
    adapter_guid   TEXT NOT NULL DEFAULT '',
    action         TEXT NOT NULL DEFAULT '',
    previous_state TEXT NOT NULL DEFAULT '',
    new_state      TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '',
    message        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_presets_name      ON presets (name);
"""


class Database:
    """Thread-safe SQLite wrapper.

    A single connection is shared behind a lock. Network operations run on
    worker threads, so serialising database access here keeps the storage
    layer safe without every caller having to think about it.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else database_path()
        self._lock = threading.RLock()
        self._connection: Optional[sqlite3.Connection] = None
        self.initialise()

    # ------------------------------------------------------------ lifecycle
    def initialise(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(self.path), check_same_thread=False, timeout=10.0
            )
            self._connection.row_factory = sqlite3.Row
            with self._lock:
                self._connection.execute("PRAGMA journal_mode=WAL")
                self._connection.execute("PRAGMA foreign_keys=ON")
                self._connection.executescript(_SCHEMA)
                self._connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._connection.commit()
            log.info("Database ready at %s", self.path)
        except sqlite3.Error as exc:
            log.error("Database initialisation failed: %s", exc)
            raise StorageError(
                "The configuration database could not be opened.",
                "Saved presets and history are unavailable. "
                "Network configuration still works normally.",
                f"{self.path}: {exc}",
            ) from exc

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.commit()
                    self._connection.close()
                except sqlite3.Error:  # pragma: no cover
                    pass
                self._connection = None

    # --------------------------------------------------------------- access
    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:  # pragma: no cover - defensive
            self.initialise()
        assert self._connection is not None
        return self._connection

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return list(self.connection.execute(sql, tuple(params)))
            except sqlite3.Error as exc:
                raise self._wrap(exc) from exc

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run a statement and return lastrowid (or the affected row count)."""
        with self._lock:
            try:
                cursor = self.connection.execute(sql, tuple(params))
                self.connection.commit()
                return cursor.lastrowid if cursor.lastrowid else cursor.rowcount
            except sqlite3.Error as exc:
                self.connection.rollback()
                raise self._wrap(exc) from exc

    @staticmethod
    def _wrap(exc: sqlite3.Error) -> StorageError:
        if isinstance(exc, sqlite3.IntegrityError) and "UNIQUE" in str(exc):
            return StorageError(
                "A saved configuration with that name already exists.",
                "Choose a different name.",
                str(exc),
            )
        return StorageError(
            "The configuration database could not be updated.",
            "The operation was not saved. Network configuration is unaffected.",
            str(exc),
        )
