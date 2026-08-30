"""Adapter classification, identity and the special cases of section 12."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.adapter import Adapter, AdapterKind, IPv4Address, OperStatus
from app.network.adapter_manager import AdapterManager
from app.network.powershell import PowerShellRunner, translate_error
from tests.conftest import make_adapter


# ------------------------------------------------------------ classification
@pytest.mark.parametrize(
    "description,if_type,expected",
    [
        ("Intel(R) Ethernet Connection I219-LM", 6, AdapterKind.PHYSICAL),
        ("Realtek PCIe GbE Family Controller", 6, AdapterKind.PHYSICAL),
        ("Intel(R) Wi-Fi 6E AX211 160MHz", 71, AdapterKind.WIRELESS),
        ("Hyper-V Virtual Ethernet Adapter", 6, AdapterKind.VIRTUAL),
        ("VMware Virtual Ethernet Adapter for VMnet1", 6, AdapterKind.VIRTUAL),
        ("VirtualBox Host-Only Ethernet Adapter", 6, AdapterKind.VIRTUAL),
        ("Docker Host Network Adapter", 6, AdapterKind.VIRTUAL),
        ("Microsoft Wi-Fi Direct Virtual Adapter", 71, AdapterKind.VIRTUAL),
        ("Fortinet SSL VPN Virtual Ethernet Adapter", 6, AdapterKind.VPN),
        ("Cisco AnyConnect Secure Mobility Client", 6, AdapterKind.VPN),
        ("WireGuard Tunnel", 6, AdapterKind.VPN),
        ("TAP-Windows Adapter V9", 6, AdapterKind.VIRTUAL),
        ("Software Loopback Interface 1", 24, AdapterKind.LOOPBACK),
        ("Bluetooth Device (Personal Area Network)", 6, AdapterKind.BLUETOOTH),
    ],
)
def test_adapter_kind_is_derived_from_the_hardware_description(
    description, if_type, expected
):
    """Classification must never depend on the renameable friendly name."""
    adapter = make_adapter(
        name="Some User Chosen Name", description=description, if_type=if_type
    )
    assert adapter.kind is expected


def test_loopback_is_not_configurable():
    loopback = make_adapter(description="Software Loopback Interface 1", if_type=24)
    assert not loopback.is_configurable


def test_physical_and_virtual_adapters_are_configurable():
    assert make_adapter().is_configurable
    assert make_adapter(description="VMware Virtual Ethernet Adapter").is_configurable


# ----------------------------------------------------------- special cases
def test_adapter_with_no_address():
    adapter = make_adapter(ipv4=(), gateways=())
    assert adapter.primary_ipv4 is None
    assert adapter.display_address == "No IP address"


def test_apipa_address_is_flagged():
    adapter = make_adapter(ipv4=(("169.254.10.20", 16),), gateways=())
    assert adapter.has_apipa
    assert adapter.primary_ipv4.is_apipa
    assert "APIPA" in adapter.display_address


def test_multiple_addresses_are_all_kept():
    adapter = make_adapter(
        ipv4=(("192.168.10.100", 24), ("192.168.10.101", 24), ("192.168.10.102", 24))
    )
    assert adapter.has_multiple_ipv4
    assert len(adapter.routable_ipv4) == 3
    assert "+2 more" in adapter.display_address


def test_apipa_does_not_count_as_a_second_address():
    """A real address plus a Windows autoconfiguration address is not 'multiple'."""
    adapter = make_adapter(ipv4=(("11.200.2.249", 22), ("169.254.108.212", 16)))
    assert not adapter.has_multiple_ipv4
    assert adapter.primary_ipv4.address == "11.200.2.249"
    assert adapter.has_apipa


def test_disconnected_adapter_reports_its_state():
    adapter = make_adapter(status=OperStatus.DOWN)
    assert not adapter.oper_status.is_up
    assert adapter.oper_status.label == "Disconnected"


@pytest.mark.parametrize(
    "status,label",
    [
        (OperStatus.UP, "Connected"),
        (OperStatus.DOWN, "Disconnected"),
        (OperStatus.NOT_PRESENT, "Not present"),
        (OperStatus.LOWER_LAYER_DOWN, "Lower layer down"),
        (OperStatus.DORMANT, "Dormant"),
    ],
)
def test_operational_status_labels(status, label):
    assert make_adapter(status=status).oper_status.label == label


def test_ipv6_only_gateway_is_not_returned_as_ipv4():
    adapter = make_adapter(gateways=("fe80::1", "192.168.1.1"))
    assert adapter.primary_gateway == "192.168.1.1"
    assert adapter.ipv4_gateways == ["192.168.1.1"]


def test_subnet_mask_derived_from_prefix():
    assert IPv4Address("10.0.0.1", 22).subnet_mask == "255.255.252.0"
    assert IPv4Address("10.0.0.1", 8).subnet_mask == "255.0.0.0"


# ------------------------------------------------------------- identity
def test_identity_matches_on_guid_or_mac():
    adapter = make_adapter()
    assert adapter.identity_matches(guid=adapter.guid)
    assert adapter.identity_matches(guid=adapter.guid.lower())
    assert adapter.identity_matches(mac=adapter.mac.lower())
    assert not adapter.identity_matches(guid="{NOPE}", mac="00:00:00:00:00:00")


def test_find_prefers_guid_over_mac():
    by_guid = make_adapter(name="Correct", guid="{AAA}", mac="11:11:11:11:11:11")
    by_mac = make_adapter(name="Wrong", guid="{BBB}", mac="22:22:22:22:22:22")
    manager = AdapterManager()
    found = manager.find([by_mac, by_guid], guid="{AAA}", mac="22:22:22:22:22:22")
    assert found.friendly_name == "Correct"


def test_find_returns_none_rather_than_guessing():
    manager = AdapterManager()
    assert manager.find([make_adapter()], guid="{MISSING}", mac="00:00:00:00:00:00") is None


def test_find_by_index_is_the_weakest_match():
    manager = AdapterManager()
    adapter = make_adapter(if_index=7)
    assert manager.find([adapter], if_index=7) is adapter
    assert manager.find([adapter], if_index=99) is None


# --------------------------------------------------- error message translation
@pytest.mark.parametrize(
    "raw,expected_fragment",
    [
        ("Access is denied", "Administrator"),
        ("The requested operation requires elevation", "Administrator"),
        ("The object already exists", "already configured"),
        ("No matching MSFT_NetIPAddress objects found", "could not find"),
        ("Invalid parameter", "rejected"),
    ],
)
def test_windows_errors_become_readable(raw, expected_fragment):
    """Section 22: no raw subprocess errors ever reach the operator."""
    message = translate_error(raw)
    assert expected_fragment in message
    assert "CalledProcessError" not in message
    assert "Traceback" not in message


def test_timeout_and_missing_powershell_have_dedicated_messages():
    assert "did not respond" in translate_error("", "timeout")
    assert "PowerShell could not be started" in translate_error("", "no_powershell")


def test_unknown_errors_are_still_reported_not_swallowed():
    assert "something odd" in translate_error("something odd")


# ------------------------------------------------- privileged layer hardening
def test_the_script_never_varies_with_input(tmp_path):
    """Injection is structurally impossible: the script embeds no parameters.

    Whatever the operator types, the script written to disk is byte-identical,
    because values travel separately as JSON on stdin.
    """
    from app.network.powershell import NET_OPS_SCRIPT

    runner = PowerShellRunner()
    try:
        written = Path(runner._ensure_script()).read_text(encoding="utf-8-sig")
    finally:
        runner._cleanup()
    assert written == NET_OPS_SCRIPT

    # The script text is a module-level constant with no format placeholders,
    # so it cannot be templated with operator input.
    assert "%s" not in NET_OPS_SCRIPT
    assert ".format(" not in NET_OPS_SCRIPT


def test_the_script_is_cleaned_up():
    runner = PowerShellRunner()
    path = Path(runner._ensure_script())
    assert path.is_file()
    runner._cleanup()
    assert not path.exists()
    assert not path.parent.exists()


def test_the_script_is_not_passed_on_the_command_line(monkeypatch):
    """Regression guard for WinError 206.

    The script outgrew the 32767-character Windows command-line limit once
    already (base64 of UTF-16LE is ~2.7x the source), which made every network
    operation fail with "The filename or extension is too long". It must be
    referenced by path, never inlined.
    """
    captured = {}

    class _Completed:
        returncode = 0
        stdout = '{"ok":true,"data":null,"steps":[],"error":"","code":""}'
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        return _Completed()

    monkeypatch.setattr("app.network.powershell.subprocess.run", fake_run)
    runner = PowerShellRunner()
    try:
        runner.run("ping")
        command = captured["command"]
        assert "-File" in command
        assert "-EncodedCommand" not in command
        assert len(" ".join(command)) < 32767
    finally:
        runner._cleanup()


def test_parameters_are_sent_as_json_not_spliced_into_the_command(monkeypatch):
    """A hostile value must arrive as data on stdin, never in the argv."""
    captured = {}

    class _Completed:
        returncode = 0
        stdout = '{"ok":true,"data":null,"steps":[],"error":"","code":""}'
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        captured["shell"] = kwargs.get("shell")
        return _Completed()

    monkeypatch.setattr("app.network.powershell.subprocess.run", fake_run)

    hostile = "'; Remove-NetIPAddress -Confirm:$false; #"
    PowerShellRunner().run("get_state", ifIndex=1, ipAddress=hostile)

    assert captured["shell"] is False
    assert all(hostile not in part for part in captured["command"])
    assert hostile in captured["input"]


def test_envelope_parsing_tolerates_surrounding_noise():
    runner = PowerShellRunner()
    assert runner._extract_envelope('{"ok":true,"data":1}')["ok"] is True
    assert runner._extract_envelope('WARNING: x\n{"ok":false}')["ok"] is False
    assert runner._extract_envelope("no json here") is None
    assert runner._extract_envelope("") is None


# ------------------------------------------- DHCP mode detection (real bug)
def test_prefix_origin_beats_the_interface_dhcp_flag():
    """Windows reports DHCP enabled on a disconnected adapter that is static.

    Observed on real hardware: a media-disconnected adapter holding a manually
    assigned address reports the interface DHCP flag as enabled via
    GetAdaptersAddresses, Get-NetIPInterface *and* WMI. Trusting the flag made
    the snapshot record "DHCP", so a rollback would have replaced the
    operator's static configuration with DHCP.
    """
    adapter = make_adapter(
        status=OperStatus.DOWN,
        dhcp=True,                                   # what the flag claims
        ipv4=(("192.168.10.10", 24, "Manual"),),     # what is actually configured
    )
    assert adapter.dhcp_enabled is True
    assert adapter.effective_dhcp is False
    assert adapter.mode_label == "Static"


def test_dhcp_assigned_address_is_reported_as_dhcp():
    adapter = make_adapter(dhcp=True, ipv4=(("172.20.10.2", 28, "Dhcp"),))
    assert adapter.effective_dhcp is True
    assert adapter.mode_label == "DHCP"


def test_apipa_only_adapter_falls_back_to_the_flag():
    """A WellKnown APIPA address says nothing about the configured mode."""
    dhcp_adapter = make_adapter(dhcp=True, ipv4=(("169.254.1.1", 16, "WellKnown"),))
    assert dhcp_adapter.effective_dhcp is True

    static_adapter = make_adapter(dhcp=False, ipv4=(("169.254.1.1", 16, "WellKnown"),))
    assert static_adapter.effective_dhcp is False


def test_snapshot_records_the_real_mode_not_the_flag():
    from app.models.configuration import ConfigurationSnapshot

    adapter = make_adapter(
        status=OperStatus.DOWN,
        dhcp=True,
        ipv4=(("192.168.10.10", 24, "Manual"), ("192.168.10.11", 24, "Manual")),
    )
    snapshot = ConfigurationSnapshot.from_adapter(adapter)
    assert snapshot.dhcp_enabled is False, "rollback would otherwise restore DHCP"
    assert [a["address"] for a in snapshot.addresses] == [
        "192.168.10.10",
        "192.168.10.11",
    ]


def test_static_verification_passes_on_a_disconnected_adapter():
    """The DHCP flag must not fail verification when the link is down."""
    from app.models.configuration import IPConfiguration
    from app.network.verifier import verify_configuration

    adapter = make_adapter(
        status=OperStatus.DOWN,
        dhcp=True,
        ipv4=(("192.168.10.10", 24, "Manual"),),
        gateways=("192.168.10.1",),
    )
    outcome = verify_configuration(
        adapter, IPConfiguration.static("192.168.10.10", "255.255.255.0", "192.168.10.1")
    )
    assert outcome.verified
