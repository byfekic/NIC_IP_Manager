"""Crash-recovery journal for in-flight network operations (section 54).

A small JSON file records an operation between the moment the snapshot is
taken and the moment the result is known. If the application dies in between,
the next start finds the file and offers - but never performs automatically -
a restore of the previous configuration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.models.configuration import ConfigurationSnapshot, IPConfiguration
from app.utils.logging_setup import get_logger
from app.utils.paths import journal_path

log = get_logger(__name__)


@dataclass
class PendingOperation:
    adapter_name: str
    adapter_guid: str
    started_at: str
    requested: dict
    snapshot: ConfigurationSnapshot

    @property
    def previous_description(self) -> str:
        return self.snapshot.describe()

    @property
    def requested_description(self) -> str:
        try:
            return IPConfiguration.from_dict(self.requested).describe()
        except Exception:
            return "unknown configuration"

    @property
    def started_local(self) -> str:
        try:
            moment = datetime.fromisoformat(self.started_at)
            if moment.tzinfo is not None:
                moment = moment.astimezone()
            return moment.strftime("%d %b %Y at %H:%M")
        except ValueError:
            return self.started_at


class OperationJournal:
    """Single-slot journal. Only one network operation may run at a time."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else journal_path()

    def begin(
        self,
        adapter_name: str,
        adapter_guid: str,
        requested: IPConfiguration,
        snapshot: ConfigurationSnapshot,
    ) -> None:
        payload = {
            "adapter_name": adapter_name,
            "adapter_guid": adapter_guid,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "requested": requested.to_dict(),
            "snapshot": snapshot.to_dict(),
        }
        try:
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            log.info("Journal opened for %s", adapter_name)
        except OSError as exc:  # pragma: no cover
            # The journal is a safety net; its absence must not block the change.
            log.warning("Could not write the operation journal: %s", exc)

    def complete(self) -> None:
        try:
            if self.path.exists():
                self.path.unlink()
                log.info("Journal closed")
        except OSError as exc:  # pragma: no cover
            log.warning("Could not remove the operation journal: %s", exc)

    def pending(self) -> Optional[PendingOperation]:
        """Return an unfinished operation from a previous run, if any."""
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            operation = PendingOperation(
                adapter_name=str(payload.get("adapter_name", "")),
                adapter_guid=str(payload.get("adapter_guid", "")),
                started_at=str(payload.get("started_at", "")),
                requested=dict(payload.get("requested", {})),
                snapshot=ConfigurationSnapshot.from_dict(payload.get("snapshot", {})),
            )
        except (OSError, ValueError, TypeError) as exc:
            log.warning("Unreadable operation journal, discarding: %s", exc)
            self.complete()
            return None
        log.warning(
            "Found an incomplete operation on %s from %s",
            operation.adapter_name,
            operation.started_at,
        )
        return operation
