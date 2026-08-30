"""Preset CRUD, import/export, history and adapter identity resolution."""

from __future__ import annotations

import json

import pytest

from app.models.adapter import OperStatus
from app.models.configuration import ConfigMode, IPConfiguration
from app.storage.presets import resolve_preset_adapter
from app.utils.errors import StorageError
from tests.conftest import make_adapter


# ----------------------------------------------------------------- preset CRUD
def test_create_and_read(presets, adapter):
    created = presets.create(
        "PLC Network",
        IPConfiguration.static("192.168.10.100", "255.255.255.0", "192.168.10.1"),
        adapter,
        "Line 3 controller",
    )
    assert created.id > 0
    fetched = presets.get(created.id)
    assert fetched.name == "PLC Network"
    assert fetched.ip_address == "192.168.10.100"
    assert fetched.adapter_identifier == adapter.guid
    assert fetched.adapter_mac == adapter.mac
    assert fetched.description == "Line 3 controller"


def test_dhcp_preset_stores_no_addressing(presets, adapter):
    created = presets.create("Office", IPConfiguration.dhcp(), adapter)
    assert created.mode is ConfigMode.DHCP
    assert created.ip_address == ""
    assert created.configuration.is_dhcp


def test_duplicate_names_are_rejected(presets, adapter):
    presets.create("PLC", IPConfiguration.dhcp(), adapter)
    with pytest.raises(StorageError):
        presets.create("PLC", IPConfiguration.dhcp(), adapter)


def test_empty_name_is_rejected(presets, adapter):
    with pytest.raises(StorageError):
        presets.create("   ", IPConfiguration.dhcp(), adapter)


def test_update(presets, adapter):
    created = presets.create(
        "PLC", IPConfiguration.static("10.0.0.1", "255.255.255.0"), adapter
    )
    presets.update(
        created.id,
        "PLC Line 4",
        IPConfiguration.static("10.0.4.1", "255.255.255.0", "10.0.4.254"),
        adapter,
    )
    updated = presets.get(created.id)
    assert updated.name == "PLC Line 4"
    assert updated.ip_address == "10.0.4.1"
    assert updated.gateway == "10.0.4.254"


def test_update_can_keep_the_original_adapter_link(presets, adapter):
    created = presets.create("PLC", IPConfiguration.dhcp(), adapter)
    other = make_adapter(name="Wi-Fi", guid="{OTHER}", mac="FF:FF:FF:FF:FF:FF")
    presets.update(
        created.id, "PLC", IPConfiguration.dhcp(), other, keep_adapter=True
    )
    assert presets.get(created.id).adapter_identifier == adapter.guid


def test_rename(presets, adapter):
    created = presets.create("Old", IPConfiguration.dhcp(), adapter)
    presets.rename(created.id, "New")
    assert presets.get(created.id).name == "New"


def test_duplicate_creates_a_uniquely_named_copy(presets, adapter):
    created = presets.create(
        "PLC", IPConfiguration.static("10.0.0.1", "255.255.255.0"), adapter
    )
    copy = presets.duplicate(created.id)
    assert copy.name == "PLC (copy)"
    assert copy.ip_address == "10.0.0.1"
    second = presets.duplicate(created.id)
    assert second.name == "PLC (copy) 2"


def test_delete(presets, adapter):
    created = presets.create("Temp", IPConfiguration.dhcp(), adapter)
    presets.delete(created.id)
    assert presets.get(created.id) is None
    assert presets.list_all() == []


def test_list_is_sorted_case_insensitively(presets, adapter):
    for name in ["zebra", "Alpha", "monkey"]:
        presets.create(name, IPConfiguration.dhcp(), adapter)
    assert [p.name for p in presets.list_all()] == ["Alpha", "monkey", "zebra"]


# -------------------------------------------------------------- import/export
def test_export_then_import_roundtrip(presets, adapter, tmp_path, database):
    presets.create(
        "PLC", IPConfiguration.static("192.168.10.100", "255.255.255.0", "192.168.10.1"), adapter
    )
    presets.create("Office", IPConfiguration.dhcp(), adapter)

    target = tmp_path / "export.json"
    assert presets.export_to_file(target) == 2

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["presets"]) == 2

    from app.storage.database import Database
    from app.storage.presets import PresetStore

    other = PresetStore(Database(tmp_path / "other.db"))
    imported, skipped, problems = other.import_from_file(target)
    assert (imported, skipped, problems) == (2, 0, [])
    assert other.get_by_name("PLC").ip_address == "192.168.10.100"


def test_import_skips_existing_by_default(presets, adapter, tmp_path):
    presets.create("PLC", IPConfiguration.dhcp(), adapter)
    target = tmp_path / "export.json"
    presets.export_to_file(target)

    imported, skipped, problems = presets.import_from_file(target)
    assert imported == 0
    assert skipped == 1
    assert any("already exists" in p for p in problems)


def test_import_can_overwrite(presets, adapter, tmp_path):
    presets.create("PLC", IPConfiguration.static("10.0.0.1", "255.255.255.0"), adapter)
    target = tmp_path / "export.json"
    presets.export_to_file(target)
    presets.update(
        presets.get_by_name("PLC").id,
        "PLC",
        IPConfiguration.static("10.9.9.9", "255.255.255.0"),
        adapter,
    )

    imported, _, _ = presets.import_from_file(target, overwrite=True)
    assert imported == 1
    assert presets.get_by_name("PLC").ip_address == "10.0.0.1"


def test_import_validates_every_entry_and_reports_problems(presets, tmp_path):
    """Section 35: validate before inserting; never execute anything."""
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "version": 1,
                "presets": [
                    {"name": "Bad IP", "mode": "static", "ip_address": "999.1.1.1", "subnet_mask": "255.255.255.0"},
                    {"name": "No mode"},
                    {"name": "Bad mask", "mode": "static", "ip_address": "10.0.0.1", "subnet_mask": "255.0.255.0"},
                    {"name": "Good", "mode": "static", "ip_address": "10.1.1.5", "subnet_mask": "255.255.255.0"},
                ],
            }
        ),
        encoding="utf-8",
    )
    imported, _, problems = presets.import_from_file(bad)
    assert imported == 1
    assert len(problems) == 3
    assert presets.get_by_name("Good") is not None
    assert presets.get_by_name("Bad IP") is None


def test_import_rejects_malformed_json(presets, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(StorageError):
        presets.import_from_file(bad)


def test_import_rejects_a_newer_file_version(presets, tmp_path):
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps({"version": 99, "presets": []}), encoding="utf-8")
    with pytest.raises(StorageError):
        presets.import_from_file(newer)


def test_import_rejects_a_file_without_presets(presets, tmp_path):
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"version": 1, "data": []}), encoding="utf-8")
    with pytest.raises(StorageError):
        presets.import_from_file(wrong)


def test_import_of_a_missing_file_raises_a_friendly_error(presets, tmp_path):
    with pytest.raises(StorageError):
        presets.import_from_file(tmp_path / "nope.json")


# ------------------------------------------------------- adapter resolution
def test_resolves_by_guid(presets, adapter):
    preset = presets.create("PLC", IPConfiguration.dhcp(), adapter)
    found, note = resolve_preset_adapter(preset, [adapter])
    assert found is adapter
    assert note == ""


def test_resolves_by_mac_when_the_adapter_was_renamed(presets, adapter):
    """Section 17: handle an adapter whose name or GUID changed."""
    preset = presets.create("PLC", IPConfiguration.dhcp(), adapter)
    renamed = make_adapter(name="Ethernet 7", guid="{NEW-GUID}", mac=adapter.mac)
    found, note = resolve_preset_adapter(preset, [renamed])
    assert found is renamed
    assert "MAC address" in note


def test_refuses_to_guess_when_the_adapter_is_missing(presets, adapter):
    """Section 19: never silently apply a preset to a different adapter."""
    preset = presets.create("PLC", IPConfiguration.dhcp(), adapter)
    other = make_adapter(name="Wi-Fi", guid="{OTHER}", mac="99:99:99:99:99:99")
    found, note = resolve_preset_adapter(preset, [other])
    assert found is None
    assert "Adapter not found" in note
    assert adapter.mac in note


def test_unlinked_preset_reports_that_it_has_no_adapter(presets):
    preset = presets.create("Generic", IPConfiguration.dhcp(), None)
    found, note = resolve_preset_adapter(preset, [make_adapter()])
    assert found is None
    assert "not linked" in note


# ----------------------------------------------------------------- history
def test_history_records_and_reads_back(history):
    history.record("Ethernet", "{G}", "static", "192.168.1.100", "192.168.10.100", "success")
    history.record("Wi-Fi", "{H}", "dhcp", "Static", "DHCP", "failed", "Windows refused")

    entries = history.recent()
    assert len(entries) == 2
    assert entries[0].adapter_name == "Wi-Fi"     # newest first
    assert entries[0].status == "failed"
    assert entries[1].succeeded
    assert "→" in entries[1].transition


def test_history_icons_distinguish_outcomes(history):
    history.record("A", "", "static", "", "", "success")
    history.record("B", "", "static", "", "", "failed")
    history.record("C", "", "restore", "", "", "rolled_back")
    icons = {e.adapter_name: e.icon for e in history.recent()}
    assert icons["A"] == "✓"
    assert icons["B"] == "✕"
    assert icons["C"] == "↺"


def test_history_is_trimmed_to_the_retention_limit(database):
    from app.storage.history import HistoryStore

    store = HistoryStore(database, retain=5)
    for index in range(12):
        store.record(f"Adapter{index}", "", "static", "", "", "success")
    assert len(store.recent(50)) == 5


def test_history_clear(history):
    history.record("Ethernet", "", "static", "", "", "success")
    history.clear()
    assert history.recent() == []


# ---------------------------------------------------------------- settings
def test_settings_defaults_and_persistence(tmp_path):
    from app.storage.settings import Settings

    path = tmp_path / "settings.json"
    settings = Settings(path)
    assert settings.get("theme") == "dark"

    settings.set("theme", "light")
    settings.set("window_width", 1400)
    settings.save()

    reloaded = Settings(path)
    assert reloaded.get("theme") == "light"
    assert reloaded.get("window_width") == 1400


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    from app.storage.settings import Settings

    path = tmp_path / "settings.json"
    path.write_text("not json at all", encoding="utf-8")
    assert Settings(path).get("theme") == "dark"


def test_settings_ignore_unknown_and_mistyped_keys(tmp_path):
    from app.storage.settings import Settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"theme": 123, "unknown_key": "x", "window_width": 900}),
        encoding="utf-8",
    )
    settings = Settings(path)
    assert settings.get("theme") == "dark"        # wrong type ignored
    assert settings.get("window_width") == 900    # valid value kept
