"""Operation history stored in SQLite (specification section 24)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.storage.database import Database
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class HistoryEntry:
    id: int
    timestamp: str
    adapter_name: str
    adapter_guid: str
    action: str
    previous_state: str
    new_state: str
    status: str
    message: str

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    @property
    def icon(self) -> str:
        return {
            "success": "✓",
            "dry_run": "○",
            "rolled_back": "↺",
        }.get(self.status, "✕")

    @property
    def local_time(self) -> str:
        try:
            moment = datetime.fromisoformat(self.timestamp)
            if moment.tzinfo is not None:
                moment = moment.astimezone()
            return moment.strftime("%H:%M")
        except ValueError:
            return self.timestamp[:5]

    @property
    def local_date(self) -> str:
        try:
            moment = datetime.fromisoformat(self.timestamp)
            if moment.tzinfo is not None:
                moment = moment.astimezone()
            return moment.strftime("%d %b")
        except ValueError:
            return ""

    @property
    def transition(self) -> str:
        if self.previous_state and self.new_state:
            return f"{self.previous_state}  →  {self.new_state}"
        return self.new_state or self.previous_state or ""


class HistoryStore:
    def __init__(self, database: Database, retain: int = 200) -> None:
        self.db = database
        self.retain = retain

    def record(
        self,
        adapter_name: str,
        adapter_guid: str,
        action: str,
        previous_state: str,
        new_state: str,
        status: str,
        message: str = "",
    ) -> None:
        """Append an entry. Failing to record history must never break an
        operation, so storage errors are logged and swallowed here."""
        try:
            self.db.execute(
                """
                INSERT INTO history
                    (timestamp, adapter_name, adapter_guid, action,
                     previous_state, new_state, status, message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    adapter_name,
                    adapter_guid,
                    action,
                    previous_state,
                    new_state,
                    status,
                    message,
                ),
            )
            self._trim()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Could not record history entry: %s", exc)

    def _trim(self) -> None:
        self.db.execute(
            """
            DELETE FROM history
             WHERE id NOT IN (
                 SELECT id FROM history ORDER BY id DESC LIMIT ?
             )
            """,
            (self.retain,),
        )

    def recent(self, limit: int = 20) -> list[HistoryEntry]:
        try:
            rows = self.db.query(
                "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
            )
        except Exception as exc:  # pragma: no cover
            log.warning("Could not read history: %s", exc)
            return []
        return [
            HistoryEntry(
                id=r["id"],
                timestamp=r["timestamp"],
                adapter_name=r["adapter_name"],
                adapter_guid=r["adapter_guid"],
                action=r["action"],
                previous_state=r["previous_state"],
                new_state=r["new_state"],
                status=r["status"],
                message=r["message"],
            )
            for r in rows
        ]

    def clear(self) -> None:
        self.db.execute("DELETE FROM history")
        log.info("History cleared")
