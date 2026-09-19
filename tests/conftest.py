"""Shared fixtures and fakes.

Windows network operations are mocked everywhere in this suite: running the
tests must never change the configuration of the machine they run on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.adapter import Adapter, IPv4Address, OperStatus
from app.network.adapter_manager import AdapterManager
from app.network.powershell import PSResult
from app.storage.database import Database
from app.storage.history import HistoryStore
from app.storage.journal import OperationJournal
from app.storage.presets import PresetStore


def make_adapter(
    name: str = "Ethernet",
    guid: str = "{11111111-1111-1111-1111-111111111111}",
    if_index: int = 4,
    mac: str = "AA:BB:CC:DD:EE:01",
    description: str = "Intel(R) Ethernet Connection I219-LM",
    if_type: int = 6,
    status: OperStatus = OperStatus.UP,
    dhcp: bool = False,
    ipv4: tuple = (("192.168.1.100", 24),),
    gateways: tuple = ("192.168.1.1",),
    dns: tuple = ("8.8.8.8",),
) -> Adapter:
    """Build an Adapter without touching Windows."""
    return Adapter(
        guid=guid,
        if_index=if_index,
        friendly_name=name,
        description=description,
        mac=mac,
        if_type=if_type,
        oper_status=status,
        dhcp_enabled=dhcp,
        ipv4=[
            # Entries may be (address, prefix) or (address, prefix, origin).
            IPv4Address(*entry) if len(entry) == 3 else IPv4Address(entry[0], entry[1])
            for entry in ipv4
        ],
        ipv6=["fe80::1"],
        gateways=list(gateways),
        dns_servers=list(dns),
    )


class FakeAdapterManager(AdapterManager):
    """Serves a scripted sequence of adapter states.

    Each call to refresh() advances to the next state, which lets a test model
    "the adapter looked like X before the change and Y afterwards".
    """

    def __init__(self, states: list) -> None:
        self.states = list(states)
        self.calls = 0

    def _current(self):
        if not self.states:
            return None
        return self.states[min(self.calls, len(self.states) - 1)]

    def list_adapters(self, include_loopback: bool = False) -> list:
        current = self._current()
        return [current] if current is not None else []

    def refresh(self, adapter):
        current = self._current()
        self.calls += 1
        return current

    def find(self, adapters=None, guid: str = "", mac: str = "", if_index=None):
        return self._current()


class FakeRunner:
    """Stands in for PowerShellRunner and records what it was asked to do."""

    def __init__(self, ok: bool = True, error: str = "", code: str = "") -> None:
        self.ok = ok
        self.error = error
        self.code = code
        self.calls: list[tuple[str, dict]] = []

    def run(self, op: str, timeout=None, **params) -> PSResult:
        self.calls.append((op, params))
        return PSResult(
            ok=self.ok,
            error=self.error,
            code=self.code,
            steps=[f"ran {op}"],
        )

    @property
    def operations(self) -> list[str]:
        return [op for op, _ in self.calls]


@pytest.fixture
def database(tmp_path) -> Database:
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def presets(database) -> PresetStore:
    return PresetStore(database)


@pytest.fixture
def history(database) -> HistoryStore:
    return HistoryStore(database)


@pytest.fixture
def journal(tmp_path) -> OperationJournal:
    return OperationJournal(tmp_path / "pending.json")


@pytest.fixture
def adapter() -> Adapter:
    return make_adapter()
