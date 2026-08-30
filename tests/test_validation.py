"""IP, subnet and gateway validation (specification section 39)."""

from __future__ import annotations

import pytest

from app.models.adapter import OperStatus
from app.models.configuration import (
    ConfigMode,
    IPConfiguration,
    mask_to_prefix,
    prefix_to_mask,
)
from app.network.validator import (
    Severity,
    validate,
    validate_ip_text,
    validate_mask_text,
    validate_preset_payload,
)
from tests.conftest import make_adapter


# --------------------------------------------------------------- IPv4 parsing
@pytest.mark.parametrize(
    "address",
    ["192.168.1.100", "10.0.0.10", "172.16.10.50", "1.1.1.1", "255.255.255.254"],
)
def test_valid_ipv4_accepted(address):
    ok, _ = validate_ip_text(address)
    assert ok


@pytest.mark.parametrize(
    "address",
    [
        "192.168.1.999",  # octet out of range
        "192.168.1",      # too few octets
        "hello",          # not numeric
        "",               # empty
        "192.168.1.1.1",  # too many octets
        "192.168.-1.1",   # negative
        " ",              # whitespace only
        "192,168,1,1",    # wrong separator
    ],
)
def test_invalid_ipv4_rejected(address):
    ok, message = validate_ip_text(address)
    assert not ok
    assert message


# -------------------------------------------------------------- subnet masks
@pytest.mark.parametrize(
    "mask,prefix",
    [
        ("255.0.0.0", 8),
        ("255.255.0.0", 16),
        ("255.255.255.0", 24),
        ("255.255.252.0", 22),
        ("255.255.255.252", 30),
        ("/24", 24),
        ("24", 24),
    ],
)
def test_mask_to_prefix(mask, prefix):
    assert mask_to_prefix(mask) == prefix


@pytest.mark.parametrize("prefix,mask", [(8, "255.0.0.0"), (16, "255.255.0.0"), (24, "255.255.255.0"), (30, "255.255.255.252")])
def test_prefix_to_mask(prefix, mask):
    assert prefix_to_mask(prefix) == mask


@pytest.mark.parametrize(
    "mask", ["255.255.0.255", "255.0.255.0", "999.0.0.0", "not a mask", "", "/33", "33"]
)
def test_invalid_masks_rejected(mask):
    ok, _ = validate_mask_text(mask)
    assert not ok


def test_mask_roundtrip_is_stable():
    for prefix in range(0, 33):
        assert mask_to_prefix(prefix_to_mask(prefix)) == prefix


# ------------------------------------------------------------ special address
def test_network_address_is_an_error():
    result = validate(IPConfiguration.static("192.168.1.0", "255.255.255.0"))
    assert not result.is_valid
    assert any("network address" in e.message for e in result.errors)


def test_broadcast_address_is_an_error():
    result = validate(IPConfiguration.static("192.168.1.255", "255.255.255.0"))
    assert not result.is_valid
    assert any("broadcast address" in e.message for e in result.errors)


def test_loopback_is_an_error():
    result = validate(IPConfiguration.static("127.0.0.1", "255.0.0.0"))
    assert not result.is_valid


def test_multicast_is_an_error():
    result = validate(IPConfiguration.static("224.0.0.5", "255.255.255.0"))
    assert not result.is_valid


def test_apipa_is_a_warning_not_an_error():
    """169.254.x.x is unusual but technically assignable (section 12)."""
    result = validate(IPConfiguration.static("169.254.5.5", "255.255.0.0"))
    assert result.is_valid
    assert any("APIPA" in w.message for w in result.warnings)


def test_public_address_is_a_warning_not_an_error():
    result = validate(IPConfiguration.static("8.8.8.8", "255.255.255.0"))
    assert result.is_valid
    assert any("public" in w.message for w in result.warnings)


def test_slash_31_point_to_point_is_allowed():
    """RFC 3021 links have no network/broadcast address to reject."""
    result = validate(IPConfiguration.static("10.0.0.1", "255.255.255.254"))
    assert result.is_valid


def test_slash_30_host_addresses_are_valid():
    assert validate(IPConfiguration.static("10.0.0.1", "255.255.255.252")).is_valid
    assert validate(IPConfiguration.static("10.0.0.2", "255.255.255.252")).is_valid


def test_slash_30_network_and_broadcast_rejected():
    assert not validate(IPConfiguration.static("10.0.0.0", "255.255.255.252")).is_valid
    assert not validate(IPConfiguration.static("10.0.0.3", "255.255.255.252")).is_valid


# ------------------------------------------------------------------- gateway
def test_gateway_in_same_subnet_is_clean():
    result = validate(
        IPConfiguration.static("192.168.1.100", "255.255.255.0", "192.168.1.1")
    )
    assert result.is_valid
    assert not result.warnings


def test_gateway_outside_subnet_warns_but_does_not_block():
    """Section 31: unusual but potentially legitimate, so a warning."""
    result = validate(
        IPConfiguration.static("192.168.10.50", "255.255.255.0", "192.168.20.1")
    )
    assert result.is_valid
    assert any("outside the configured subnet" in w.message for w in result.warnings)
    assert all(w.severity is Severity.WARNING for w in result.warnings)


def test_gateway_equal_to_ip_is_an_error():
    result = validate(
        IPConfiguration.static("10.0.0.5", "255.255.255.0", "10.0.0.5")
    )
    assert not result.is_valid


def test_invalid_gateway_is_an_error():
    config = IPConfiguration(
        mode=ConfigMode.STATIC,
        ip_address="10.0.0.5",
        prefix_length=24,
        gateway="10.0.0.999",
    )
    result = validate(config)
    assert not result.is_valid


def test_empty_gateway_is_allowed():
    result = validate(IPConfiguration.static("10.0.0.5", "255.255.255.0", ""))
    assert result.is_valid


# ------------------------------------------------------------------ adapters
def test_dhcp_configuration_needs_no_addressing():
    assert validate(IPConfiguration.dhcp()).is_valid


def test_loopback_adapter_cannot_be_configured():
    loopback = make_adapter(
        name="Loopback Pseudo-Interface 1",
        description="Software Loopback Interface 1",
        if_type=24,
    )
    result = validate(IPConfiguration.static("10.0.0.5", "255.255.255.0"), loopback)
    assert not result.is_valid


def test_disconnected_adapter_warns_but_allows():
    down = make_adapter(status=OperStatus.DOWN)
    result = validate(IPConfiguration.static("10.0.0.5", "255.255.255.0"), down)
    assert result.is_valid
    assert any("disconnected" in w.message for w in result.warnings)


def test_virtual_adapter_warns():
    virtual = make_adapter(
        name="vEthernet (Default Switch)",
        description="Hyper-V Virtual Ethernet Adapter",
    )
    result = validate(IPConfiguration.static("10.0.0.5", "255.255.255.0"), virtual)
    assert result.is_valid
    assert any("virtual adapter" in w.message for w in result.warnings)


def test_multiple_addresses_warns_that_they_are_preserved():
    multi = make_adapter(ipv4=(("192.168.1.100", 24), ("192.168.1.101", 24)))
    result = validate(IPConfiguration.static("192.168.2.5", "255.255.255.0"), multi)
    assert any("preserved" in w.message for w in result.warnings)


# ------------------------------------------------------------ preset payloads
def test_preset_payload_validation_accepts_good_entries():
    ok, _ = validate_preset_payload(
        {
            "name": "PLC",
            "mode": "static",
            "ip_address": "192.168.10.100",
            "subnet_mask": "255.255.255.0",
            "gateway": "192.168.10.1",
        }
    )
    assert ok


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": "X"},                                              # no mode
        {"name": "X", "mode": "bogus"},
        {"name": "X", "mode": "static", "ip_address": "999.1.1.1", "subnet_mask": "255.255.255.0"},
        {"name": "X", "mode": "static", "ip_address": "10.0.0.1", "subnet_mask": "255.0.255.0"},
        {"name": "X" * 200, "mode": "dhcp"},
    ],
)
def test_preset_payload_validation_rejects_bad_entries(payload):
    ok, message = validate_preset_payload(payload)
    assert not ok
    assert message


def test_preset_payload_dhcp_needs_no_addressing():
    ok, _ = validate_preset_payload({"name": "Office", "mode": "dhcp"})
    assert ok
