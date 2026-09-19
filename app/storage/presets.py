"""Named configuration presets: CRUD, JSON import/export, adapter resolution.

Presets record the adapter's stable identity (interface GUID plus MAC address)
alongside the friendly name, so a preset is never silently applied to the wrong
adapter after a rename (specification sections 17 and 19).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.models.adapter import Adapter
from app.models.configuration import ConfigMode, IPConfiguration
from app.network.validator import validate_preset_payload
from app.storage.database import Database
from app.utils.errors import StorageError
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

EXPORT_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Preset:
    id: int
    name: str
    adapter_identifier: str
    adapter_mac: str
    adapter_name: str
    mode: ConfigMode
    ip_address: str
    subnet_mask: str
    gateway: str
    description: str
    created_at: str
    updated_at: str

    @property
    def configuration(self) -> IPConfiguration:
        if self.mode is ConfigMode.DHCP:
            return IPConfiguration.dhcp()
        return IPConfiguration.static(self.ip_address, self.subnet_mask, self.gateway)

    @property
    def summary(self) -> str:
        if self.mode is ConfigMode.DHCP:
            return "DHCP (automatic)"
        return f"{self.ip_address} / {self.subnet_mask}"

    @property
    def gateway_summary(self) -> str:
        return f"Gateway {self.gateway}" if self.gateway else "No gateway"

    def matches_adapter(self, adapter: Adapter) -> bool:
        return adapter.identity_matches(self.adapter_identifier, self.adapter_mac)

    def to_export_dict(self) -> dict[str, Any]:
        """Export shape. Adapter identity is included but never required."""
        return {
            "name": self.name,
            "mode": self.mode.value,
            "ip_address": self.ip_address,
            "subnet_mask": self.subnet_mask,
            "gateway": self.gateway,
            "description": self.description,
            "adapter_name": self.adapter_name,
            "adapter_identifier": self.adapter_identifier,
            "adapter_mac": self.adapter_mac,
        }


class PresetStore:
    """All preset persistence. Every statement is parameterized."""

    def __init__(self, database: Database) -> None:
        self.db = database

    # ------------------------------------------------------------- reading
    @staticmethod
    def _row_to_preset(row) -> Preset:
        return Preset(
            id=row["id"],
            name=row["name"],
            adapter_identifier=row["adapter_identifier"],
            adapter_mac=row["adapter_mac"],
            adapter_name=row["adapter_name"],
            mode=ConfigMode(row["mode"]),
            ip_address=row["ip_address"],
            subnet_mask=row["subnet_mask"],
            gateway=row["gateway"],
            description=row["description"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_all(self) -> list[Preset]:
        rows = self.db.query("SELECT * FROM presets ORDER BY name COLLATE NOCASE")
        return [self._row_to_preset(r) for r in rows]

    def get(self, preset_id: int) -> Optional[Preset]:
        row = self.db.query_one("SELECT * FROM presets WHERE id = ?", (preset_id,))
        return self._row_to_preset(row) if row else None

    def get_by_name(self, name: str) -> Optional[Preset]:
        row = self.db.query_one("SELECT * FROM presets WHERE name = ?", (name,))
        return self._row_to_preset(row) if row else None

    # ------------------------------------------------------------- writing
    def create(
        self,
        name: str,
        configuration: IPConfiguration,
        adapter: Optional[Adapter] = None,
        description: str = "",
    ) -> Preset:
        name = name.strip()
        if not name:
            raise StorageError("The configuration needs a name.", "Enter a name and try again.")
        now = _now()
        preset_id = self.db.execute(
            """
            INSERT INTO presets
                (name, adapter_identifier, adapter_mac, adapter_name, mode,
                 ip_address, subnet_mask, gateway, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                adapter.guid if adapter else "",
                adapter.mac if adapter else "",
                adapter.friendly_name if adapter else "",
                configuration.mode.value,
                configuration.ip_address if not configuration.is_dhcp else "",
                configuration.subnet_mask if not configuration.is_dhcp else "",
                configuration.gateway if not configuration.is_dhcp else "",
                description.strip(),
                now,
                now,
            ),
        )
        log.info("Preset created: %s (id=%s)", name, preset_id)
        created = self.get(int(preset_id))
        assert created is not None
        return created

    def update(
        self,
        preset_id: int,
        name: str,
        configuration: IPConfiguration,
        adapter: Optional[Adapter] = None,
        description: str = "",
        keep_adapter: bool = False,
    ) -> Preset:
        existing = self.get(preset_id)
        if existing is None:
            raise StorageError("That saved configuration no longer exists.")

        if keep_adapter or adapter is None:
            guid, mac, adapter_name = (
                existing.adapter_identifier,
                existing.adapter_mac,
                existing.adapter_name,
            )
        else:
            guid, mac, adapter_name = adapter.guid, adapter.mac, adapter.friendly_name

        self.db.execute(
            """
            UPDATE presets
               SET name = ?, adapter_identifier = ?, adapter_mac = ?, adapter_name = ?,
                   mode = ?, ip_address = ?, subnet_mask = ?, gateway = ?,
                   description = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                name.strip(),
                guid,
                mac,
                adapter_name,
                configuration.mode.value,
                configuration.ip_address if not configuration.is_dhcp else "",
                configuration.subnet_mask if not configuration.is_dhcp else "",
                configuration.gateway if not configuration.is_dhcp else "",
                description.strip(),
                _now(),
                preset_id,
            ),
        )
        log.info("Preset updated: id=%s name=%s", preset_id, name)
        updated = self.get(preset_id)
        assert updated is not None
        return updated

    def rename(self, preset_id: int, new_name: str) -> None:
        new_name = new_name.strip()
        if not new_name:
            raise StorageError("The configuration needs a name.")
        self.db.execute(
            "UPDATE presets SET name = ?, updated_at = ? WHERE id = ?",
            (new_name, _now(), preset_id),
        )
        log.info("Preset renamed: id=%s -> %s", preset_id, new_name)

    def duplicate(self, preset_id: int) -> Preset:
        source = self.get(preset_id)
        if source is None:
            raise StorageError("That saved configuration no longer exists.")
        name = self._unique_name(f"{source.name} (copy)")
        now = _now()
        new_id = self.db.execute(
            """
            INSERT INTO presets
                (name, adapter_identifier, adapter_mac, adapter_name, mode,
                 ip_address, subnet_mask, gateway, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                source.adapter_identifier,
                source.adapter_mac,
                source.adapter_name,
                source.mode.value,
                source.ip_address,
                source.subnet_mask,
                source.gateway,
                source.description,
                now,
                now,
            ),
        )
        log.info("Preset duplicated: %s -> %s", source.name, name)
        created = self.get(int(new_id))
        assert created is not None
        return created

    def delete(self, preset_id: int) -> None:
        self.db.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
        log.info("Preset deleted: id=%s", preset_id)

    def _unique_name(self, base: str) -> str:
        candidate, counter = base, 2
        while self.get_by_name(candidate) is not None:
            candidate = f"{base} {counter}"
            counter += 1
        return candidate

    # ------------------------------------------------------- import/export
    def export_to_file(self, path: Path | str) -> int:
        presets = self.list_all()
        payload = {
            "version": EXPORT_VERSION,
            "exported_at": _now(),
            "presets": [p.to_export_dict() for p in presets],
        }
        try:
            Path(path).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            raise StorageError(
                "The presets could not be exported.",
                "The file could not be written. Check the location and try again.",
                str(exc),
            ) from exc
        log.info("Exported %d presets to %s", len(presets), path)
        return len(presets)

    def import_from_file(self, path: Path | str, overwrite: bool = False) -> tuple[int, int, list[str]]:
        """Import presets from JSON.

        Every entry is validated before anything is written, and nothing from
        the file is ever executed (specification section 35). Returns
        (imported, skipped, problems).
        """
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise StorageError(
                "The file could not be read.",
                "Check that the file exists and is accessible.",
                str(exc),
            ) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StorageError(
                "The file is not a valid preset file.",
                "The selected file is not valid JSON.",
                str(exc),
            ) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("presets"), list):
            raise StorageError(
                "The file is not a valid preset file.",
                "It does not contain a list of presets.",
            )

        version = payload.get("version", 1)
        if not isinstance(version, int) or version > EXPORT_VERSION:
            raise StorageError(
                "This preset file was created by a newer version.",
                f"File version {version} is not supported by this application.",
            )

        entries = payload["presets"]
        problems: list[str] = []
        valid: list[dict[str, Any]] = []

        # Validate everything first; a partially imported file is never written.
        for entry in entries:
            ok, problem = validate_preset_payload(entry if isinstance(entry, dict) else {})
            if ok:
                valid.append(entry)
            else:
                problems.append(problem)

        imported = skipped = 0
        for entry in valid:
            name = str(entry["name"]).strip()
            mode = ConfigMode(str(entry["mode"]).lower())
            if mode is ConfigMode.DHCP:
                configuration = IPConfiguration.dhcp()
            else:
                configuration = IPConfiguration.static(
                    str(entry.get("ip_address", "")),
                    str(entry.get("subnet_mask", "")),
                    str(entry.get("gateway", "") or ""),
                )
            existing = self.get_by_name(name)
            if existing is not None and not overwrite:
                skipped += 1
                problems.append(f"'{name}': already exists, skipped.")
                continue

            adapter_identifier = str(entry.get("adapter_identifier", "") or "")
            adapter_mac = str(entry.get("adapter_mac", "") or "")
            adapter_name = str(entry.get("adapter_name", "") or "")
            description = str(entry.get("description", "") or "")
            now = _now()

            if existing is not None:
                self.db.execute(
                    """
                    UPDATE presets
                       SET adapter_identifier = ?, adapter_mac = ?, adapter_name = ?,
                           mode = ?, ip_address = ?, subnet_mask = ?, gateway = ?,
                           description = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (
                        adapter_identifier,
                        adapter_mac,
                        adapter_name,
                        configuration.mode.value,
                        configuration.ip_address if not configuration.is_dhcp else "",
                        configuration.subnet_mask if not configuration.is_dhcp else "",
                        configuration.gateway if not configuration.is_dhcp else "",
                        description,
                        now,
                        existing.id,
                    ),
                )
            else:
                self.db.execute(
                    """
                    INSERT INTO presets
                        (name, adapter_identifier, adapter_mac, adapter_name, mode,
                         ip_address, subnet_mask, gateway, description, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        adapter_identifier,
                        adapter_mac,
                        adapter_name,
                        configuration.mode.value,
                        configuration.ip_address if not configuration.is_dhcp else "",
                        configuration.subnet_mask if not configuration.is_dhcp else "",
                        configuration.gateway if not configuration.is_dhcp else "",
                        description,
                        now,
                        now,
                    ),
                )
            imported += 1

        log.info(
            "Imported %d presets from %s (%d skipped, %d problems)",
            imported,
            path,
            skipped,
            len(problems),
        )
        return imported, skipped, problems


def resolve_preset_adapter(
    preset: Preset, adapters: list[Adapter]
) -> tuple[Optional[Adapter], str]:
    """Find the adapter a preset was saved for.

    Returns (adapter, explanation). The adapter is None when it cannot be
    identified with confidence - the caller must then stop and ask the
    operator rather than guessing (specification sections 19 and 41).
    """
    if not preset.adapter_identifier and not preset.adapter_mac:
        return None, "This configuration is not linked to a specific adapter."

    for adapter in adapters:
        if adapter.guid and adapter.guid.lower() == preset.adapter_identifier.lower():
            return adapter, ""

    for adapter in adapters:
        if adapter.mac and preset.adapter_mac and adapter.mac.upper() == preset.adapter_mac.upper():
            return (
                adapter,
                f"Matched by MAC address; the adapter is now called '{adapter.friendly_name}'.",
            )

    return None, (
        "Adapter not found.\n\n"
        f"This configuration was saved for:\n{preset.adapter_name or 'unknown adapter'}\n"
        f"MAC: {preset.adapter_mac or 'unknown'}\n\n"
        "Please select another adapter or edit the preset."
    )
