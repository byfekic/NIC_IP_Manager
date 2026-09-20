"""SQLite storage: schema creation, migration and connection handling.

Every query in this package is parameterized; no SQL is ever built by string
formatting with user data (specification section 36).

Schema versioning
-----------------
The database carries its version in ``PRAGMA user_version``. On every open the
stored version is compared with :data:`SCHEMA_VERSION`:

* **equal** - nothing to do.
* **lower** - the registered migrations are applied in order, one transaction
  each, after a copy of the file is put aside.
* **higher** - the file was written by a newer build. Opening it would risk
  silently dropping whatever that build added, so it is refused with an
  explanation instead.

A brand new file is created at :data:`_BASELINE_VERSION` and then migrated
forward like any other, so new and upgraded installations end up running the
same statements and cannot drift apart. That is why :data:`_BASELINE` is never
edited once a version has shipped - changing the schema means adding a
migration, not rewriting history.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Optional

from app.utils.errors import StorageError
from app.utils.logging_setup import get_logger
from app.utils.paths import database_path

log = get_logger(__name__)

# The schema this build expects. Bump it when adding a migration below.
SCHEMA_VERSION = 2

# The version _BASELINE creates. This does not move when SCHEMA_VERSION does.
_BASELINE_VERSION = 1

_BASELINE = """
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

Migration = Callable[[sqlite3.Connection], None]

def _v2_preset_folders(connection: sqlite3.Connection) -> None:
    """Group saved configurations under an optional folder name.

    Existing presets default to the empty string, which the panel shows as
    ungrouped, so an upgraded database looks exactly as it did before.
    """
    connection.execute("ALTER TABLE presets ADD COLUMN folder TEXT NOT NULL DEFAULT ''")


# Upgrade steps keyed by the version each one produces. A step receives the
# open connection and runs inside a transaction that the caller opens, so it
# must not commit and must not use a statement SQLite refuses inside one
# (VACUUM, or toggling PRAGMA foreign_keys). user_version is set for it once
# the step returns. Add a step here and bump SCHEMA_VERSION; never edit
# _BASELINE.
_MIGRATIONS: dict[int, Migration] = {
    2: _v2_preset_folders,
}


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
        except (sqlite3.Error, OSError) as exc:
            log.error("Database initialisation failed: %s", exc)
            raise StorageError(
                "The configuration database could not be opened.",
                "Saved presets and history are unavailable. "
                "Network configuration still works normally.",
                f"{self.path}: {exc}",
            ) from exc

        # Schema work raises StorageError itself, with a message describing the
        # particular problem, so it stays outside the generic wrapper above.
        with self._lock:
            self._apply_schema()

        log.info("Database ready at %s (schema v%s)", self.path, self.schema_version())

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.commit()
                    self._connection.close()
                except sqlite3.Error:  # pragma: no cover
                    pass
                self._connection = None

    # ---------------------------------------------------------------- schema
    def schema_version(self) -> int:
        """The schema version recorded in the file."""
        with self._lock:
            row = self.connection.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row is not None else 0

    def _apply_schema(self) -> None:
        current = self.schema_version()
        # Read at call time rather than captured at import, so the version and
        # the registry can be varied together under test.
        target = SCHEMA_VERSION

        if current > target:
            log.error("Database schema v%s is newer than v%s", current, target)
            raise StorageError(
                "The configuration database was written by a newer version of "
                "IP CHANGER.",
                "Update IP CHANGER, or move the file aside to start with an "
                "empty database. It has been left untouched.",
                f"{self.path}: schema v{current}, this build understands v{target}",
            )

        if current == 0:
            self._create_baseline()
            current = _BASELINE_VERSION

        if current < target:
            self._migrate(current, target)

    def _create_baseline(self) -> None:
        """Write the original schema into a new (or pre-versioning) file.

        Every statement is ``IF NOT EXISTS``, so this is equally safe on an
        empty file and on one written before versioning existed.
        """
        try:
            self.connection.executescript(_BASELINE)
            self.connection.execute(f"PRAGMA user_version = {int(_BASELINE_VERSION)}")
            self.connection.commit()
        except sqlite3.Error as exc:
            log.error("Could not create the database schema: %s", exc)
            raise StorageError(
                "The configuration database could not be prepared.",
                "Saved presets and history are unavailable. "
                "Network configuration still works normally.",
                f"{self.path}: {exc}",
            ) from exc
        log.info("Database schema created at v%s", _BASELINE_VERSION)

    def _migrate(self, current: int, target: int) -> None:
        pending = range(current + 1, target + 1)
        missing = [version for version in pending if version not in _MIGRATIONS]
        if missing:
            log.error("No migration registered for schema v%s", missing)
            raise StorageError(
                "The configuration database could not be upgraded.",
                "This build of IP CHANGER is missing an upgrade step. "
                "The database has been left untouched.",
                f"{self.path}: no migration registered for "
                f"v{', v'.join(str(v) for v in missing)}",
            )

        self._copy_aside(current)

        connection = self.connection
        connection.commit()
        for version in pending:
            log.info("Upgrading database schema v%s -> v%s", version - 1, version)
            try:
                # Python's sqlite3 opens a transaction implicitly for DML only,
                # so DDL would otherwise run in autocommit mode and an ALTER
                # TABLE would stand even after the rollback below.
                connection.execute("BEGIN")
                _MIGRATIONS[version](connection)
                connection.execute(f"PRAGMA user_version = {int(version)}")
                connection.commit()
            except Exception as exc:
                # A migration may fail in any number of ways; whatever happened,
                # the file must stay on the last version that completed.
                connection.rollback()
                log.exception("Migration to schema v%s failed", version)
                raise StorageError(
                    "The configuration database could not be upgraded.",
                    f"The upgrade to version {version} was rolled back and a "
                    "copy of the database was saved next to it. Saved presets "
                    "and history are unavailable; network configuration still "
                    "works normally.",
                    f"{self.path}: migration to v{version} failed: {exc}",
                ) from exc

        log.info("Database schema upgraded to v%s", target)

    def _copy_aside(self, from_version: int) -> None:
        """Put a copy of the database next to it before upgrading.

        Best effort, like the operation journal: each migration is already
        transactional, so this guards against a migration that succeeds but
        does the wrong thing, and must not block the upgrade if it fails.
        """
        destination = self.path.with_name(f"{self.path.name}.v{from_version}.bak")
        try:
            copy = sqlite3.connect(str(destination))
            try:
                self.connection.backup(copy)
            finally:
                copy.close()
        except (sqlite3.Error, OSError) as exc:  # pragma: no cover - defensive
            log.warning("Could not copy the database before upgrading: %s", exc)
        else:
            log.info("Database copied to %s before upgrading", destination.name)

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
