"""Post-change verification and duplicate-address detection (sections 13, 20).

Verification never assumes an operation succeeded: the adapter is re-read and
the live state is compared field by field against what was requested.
"""

from __future__ import annotations

import ctypes
import socket
import struct
from dataclasses import dataclass, field
from enum import Enum

from app.models.adapter import Adapter
from app.models.configuration import ConfigMode, IPConfiguration, prefix_to_mask
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class VerificationOutcome:
    verified: bool
    mismatches: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return "; ".join(self.mismatches) if self.mismatches else "matches the request"


def verify_configuration(adapter: Adapter, expected: IPConfiguration) -> VerificationOutcome:
    """Compare an adapter's live state against the requested configuration."""
    mismatches: list[str] = []
    notes: list[str] = []

    if expected.mode is ConfigMode.DHCP:
        if not adapter.effective_dhcp:
            mismatches.append("The adapter is not in DHCP mode.")
        address = adapter.primary_ipv4
        if address is None:
            notes.append("No address has been obtained yet.")
        elif address.is_apipa:
            notes.append(
                f"Windows assigned the automatic address {address.address} (APIPA). "
                "No DHCP server answered."
            )
        else:
            notes.append(f"Obtained {address.address} / {address.subnet_mask}.")
        return VerificationOutcome(not mismatches, mismatches, notes)

    # ---- static ---------------------------------------------------------
    # Windows keeps reporting DHCP as enabled on a media-disconnected adapter
    # even after static addresses have been assigned - GetAdaptersAddresses,
    # Get-NetIPInterface and WMI all agree on that. On such an adapter the
    # configured addresses are the reliable evidence, so the DHCP flag becomes
    # a note instead of a failure. While the link is up it still means what it
    # says, and a lease really could overwrite the static address.
    if adapter.effective_dhcp:
        if adapter.oper_status.is_up:
            mismatches.append("The adapter is still in DHCP mode.")
        else:
            notes.append(
                "Windows still reports DHCP for this adapter because the link "
                "is down. The static addresses below are configured and will "
                "take effect when the adapter connects."
            )

    addresses = {a.address: a for a in adapter.ipv4}
    actual = addresses.get(expected.ip_address)
    if actual is None:
        present = ", ".join(a.address for a in adapter.routable_ipv4) or "none"
        mismatches.append(
            f"The address {expected.ip_address} is not configured "
            f"(the adapter currently has: {present})."
        )
    elif int(actual.prefix_length) != int(expected.prefix_length):
        mismatches.append(
            f"The subnet mask is {actual.subnet_mask}, "
            f"but {prefix_to_mask(expected.prefix_length)} was requested."
        )

    if expected.gateway:
        if expected.gateway not in adapter.ipv4_gateways:
            found = ", ".join(adapter.ipv4_gateways) or "none"
            mismatches.append(
                f"The default gateway is {found}, but {expected.gateway} was requested."
            )

    extra = [a.address for a in adapter.routable_ipv4 if a.address != expected.ip_address]
    if extra:
        notes.append(f"Additional addresses preserved: {', '.join(extra)}.")

    outcome = VerificationOutcome(not mismatches, mismatches, notes)
    log.info(
        "Verification of %s: %s", adapter.friendly_name, outcome.describe()
    )
    return outcome


# --------------------------------------------------------------------------
# Duplicate address detection
# --------------------------------------------------------------------------

class ConflictStatus(Enum):
    NO_CONFLICT = "no_conflict"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            ConflictStatus.NO_CONFLICT: "No conflict detected",
            ConflictStatus.CONFLICT: "Address already in use",
            ConflictStatus.UNKNOWN: "Unable to determine",
        }[self]


@dataclass
class ConflictResult:
    status: ConflictStatus
    mac: str = ""
    detail: str = ""

    def describe(self) -> str:
        if self.status is ConflictStatus.CONFLICT:
            return f"IP conflict check: {self.status.label} (MAC {self.mac})"
        return f"IP conflict check: {self.status.label}"


def _ip_to_dword(address: str) -> int:
    return struct.unpack("<L", socket.inet_aton(address))[0]


def check_ip_conflict(ip_address: str, own_macs: set[str] | None = None) -> ConflictResult:
    """Ask the network whether an address is already claimed, using ARP.

    ARP is used rather than ICMP because a device may legitimately block ping
    while still holding the address. Even so, a negative ARP result is *not*
    proof that an address is free (the host may be off, or on another VLAN),
    so absence of a reply is reported as NO_CONFLICT only in the weak sense
    the specification requires - and any failure is reported as UNKNOWN rather
    than as a claim that the address is available.
    """
    try:
        mac_buffer = ctypes.create_string_buffer(6)
        length = ctypes.c_ulong(6)
        iphlpapi = ctypes.WinDLL("iphlpapi.dll")
        ret = iphlpapi.SendARP(
            ctypes.c_ulong(_ip_to_dword(ip_address)),
            ctypes.c_ulong(0),
            mac_buffer,
            ctypes.byref(length),
        )
    except (OSError, socket.error, struct.error) as exc:
        log.debug("ARP probe for %s failed: %s", ip_address, exc)
        return ConflictResult(ConflictStatus.UNKNOWN, detail=str(exc))

    if ret != 0 or length.value == 0:
        # No reply. The address may be free, or simply unreachable from here.
        return ConflictResult(
            ConflictStatus.NO_CONFLICT,
            detail=(
                "No device answered an ARP request for this address. "
                "This does not guarantee the address is free."
            ),
        )

    mac = ":".join(f"{b:02X}" for b in mac_buffer.raw[: length.value])
    if own_macs and mac.upper() in {m.upper() for m in own_macs}:
        # The reply came from this computer's own adapter.
        return ConflictResult(
            ConflictStatus.NO_CONFLICT,
            mac=mac,
            detail="The address is held by this computer.",
        )
    if mac in ("00:00:00:00:00:00", ""):
        return ConflictResult(ConflictStatus.UNKNOWN, detail="An empty ARP reply was received.")

    log.warning("Address %s appears to be in use by %s", ip_address, mac)
    return ConflictResult(
        ConflictStatus.CONFLICT,
        mac=mac,
        detail=f"A device with hardware address {mac} answered for {ip_address}.",
    )
