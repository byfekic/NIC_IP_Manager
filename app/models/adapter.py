"""Domain model describing a network adapter and its live IPv4/IPv6 state."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OperStatus(Enum):
    """IF_OPER_STATUS values returned by GetAdaptersAddresses."""

    UP = 1
    DOWN = 2
    TESTING = 3
    UNKNOWN = 4
    DORMANT = 5
    NOT_PRESENT = 6
    LOWER_LAYER_DOWN = 7

    @property
    def label(self) -> str:
        return {
            OperStatus.UP: "Connected",
            OperStatus.DOWN: "Disconnected",
            OperStatus.TESTING: "Testing",
            OperStatus.UNKNOWN: "Unknown",
            OperStatus.DORMANT: "Dormant",
            OperStatus.NOT_PRESENT: "Not present",
            OperStatus.LOWER_LAYER_DOWN: "Lower layer down",
        }[self]

    @property
    def is_up(self) -> bool:
        return self is OperStatus.UP


class AdapterKind(Enum):
    """How the adapter should be presented to the operator (section 12)."""

    PHYSICAL = "Physical"
    WIRELESS = "Wireless"
    VIRTUAL = "Virtual"
    VPN = "VPN"
    LOOPBACK = "Loopback"
    TUNNEL = "Tunnel"
    BLUETOOTH = "Bluetooth"
    OTHER = "Other"

    @property
    def configurable(self) -> bool:
        """Loopback interfaces cannot meaningfully be reconfigured."""
        return self is not AdapterKind.LOOPBACK


# IANA ifType values reported by Windows.
IF_TYPE_ETHERNET_CSMACD = 6
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_PPP = 23
IF_TYPE_TUNNEL = 131
IF_TYPE_IEEE80211 = 71
IF_TYPE_IEEE1394 = 144

# Substrings that identify virtual/VPN adapters by description. Matching is
# done on the hardware description, never on the user-renameable friendly name.
_VIRTUAL_MARKERS = (
    "hyper-v",
    "vethernet",
    "vmware",
    "virtualbox",
    "vbox",
    "docker",
    "npcap",
    "loopback adapter",
    "wi-fi direct",
    "virtual adapter",
    "virtual ethernet",
    "tap-windows",
)
_VPN_MARKERS = (
    "vpn",
    "fortinet",
    "openvpn",
    "wireguard",
    "anyconnect",
    "cisco",
    "zscaler",
    "globalprotect",
    "pangp",
    "sonicwall",
    "checkpoint",
    "juniper",
    "nordlynx",
    "tailscale",
    "zerotier",
)


@dataclass(frozen=True)
class IPv4Address:
    """A single IPv4 address bound to an adapter, with its prefix length.

    ``prefix_origin`` is how Windows says the address was obtained ("Manual",
    "Dhcp", "WellKnown", ...). It is the trustworthy signal for whether an
    adapter is really using DHCP.
    """

    address: str
    prefix_length: int
    prefix_origin: str = ""

    @property
    def is_manual(self) -> bool:
        return self.prefix_origin == "Manual"

    @property
    def is_dhcp_assigned(self) -> bool:
        return self.prefix_origin == "Dhcp"

    @property
    def subnet_mask(self) -> str:
        try:
            return str(ipaddress.IPv4Network(f"0.0.0.0/{self.prefix_length}").netmask)
        except ValueError:
            return ""

    @property
    def is_apipa(self) -> bool:
        """169.254.x.x - Windows assigned it because no address was obtained."""
        try:
            return ipaddress.IPv4Address(self.address).is_link_local
        except ValueError:
            return False

    def __str__(self) -> str:
        return f"{self.address}/{self.prefix_length}"


@dataclass
class Adapter:
    """Live snapshot of one network adapter.

    ``guid`` (the Windows interface GUID) is the primary stable identity;
    ``mac`` is the fallback used when an adapter is reinstalled and the GUID
    changes. The friendly name is display-only and must never be used alone
    to identify an adapter (specification section 17).
    """

    guid: str
    if_index: int
    friendly_name: str
    description: str
    mac: str
    if_type: int
    oper_status: OperStatus
    dhcp_enabled: bool
    ipv4: list[IPv4Address] = field(default_factory=list)
    ipv6: list[str] = field(default_factory=list)
    gateways: list[str] = field(default_factory=list)
    dns_servers: list[str] = field(default_factory=list)
    dhcp_server: Optional[str] = None
    mtu: int = 0
    link_speed: int = 0

    # ------------------------------------------------------------------ kind
    @property
    def kind(self) -> AdapterKind:
        if self.if_type == IF_TYPE_SOFTWARE_LOOPBACK:
            return AdapterKind.LOOPBACK
        desc = (self.description or "").lower()
        if any(m in desc for m in _VPN_MARKERS):
            return AdapterKind.VPN
        if self.if_type == IF_TYPE_IEEE80211:
            # Wi-Fi Direct pseudo-adapters are virtual even though ifType is 802.11.
            if any(m in desc for m in _VIRTUAL_MARKERS):
                return AdapterKind.VIRTUAL
            return AdapterKind.WIRELESS
        if any(m in desc for m in _VIRTUAL_MARKERS):
            return AdapterKind.VIRTUAL
        if self.if_type in (IF_TYPE_TUNNEL, IF_TYPE_PPP):
            return AdapterKind.TUNNEL
        if "bluetooth" in desc:
            return AdapterKind.BLUETOOTH
        if self.if_type in (IF_TYPE_ETHERNET_CSMACD, IF_TYPE_IEEE1394):
            return AdapterKind.PHYSICAL
        return AdapterKind.OTHER

    @property
    def is_configurable(self) -> bool:
        return self.kind.configurable

    # ------------------------------------------------------------- addresses
    @property
    def routable_ipv4(self) -> list[IPv4Address]:
        """IPv4 addresses excluding Windows-managed APIPA autoconfiguration."""
        return [a for a in self.ipv4 if not a.is_apipa]

    @property
    def primary_ipv4(self) -> Optional[IPv4Address]:
        routable = self.routable_ipv4
        if routable:
            return routable[0]
        return self.ipv4[0] if self.ipv4 else None

    @property
    def has_multiple_ipv4(self) -> bool:
        return len(self.routable_ipv4) > 1

    @property
    def has_apipa(self) -> bool:
        return any(a.is_apipa for a in self.ipv4)

    @property
    def primary_gateway(self) -> Optional[str]:
        for gw in self.gateways:
            if ":" not in gw:
                return gw
        return None

    @property
    def ipv4_gateways(self) -> list[str]:
        return [g for g in self.gateways if ":" not in g]

    # --------------------------------------------------------------- display
    @property
    def display_address(self) -> str:
        primary = self.primary_ipv4
        if primary is None:
            return "No IP address"
        suffix = ""
        if self.has_multiple_ipv4:
            extra = len(self.routable_ipv4) - 1
            suffix = f"  (+{extra} more)"
        if primary.is_apipa:
            return f"{primary.address}  (APIPA){suffix}"
        return f"{primary.address}{suffix}"

    @property
    def effective_dhcp(self) -> bool:
        """Whether IPv4 is genuinely configured by DHCP.

        The interface-wide DHCP flag is not reliable: on a media-disconnected
        adapter Windows reports it as enabled even when the addresses were
        assigned manually (GetAdaptersAddresses, Get-NetIPInterface and WMI all
        do this). The per-address PrefixOrigin stays correct, so it wins
        wherever it is available.
        """
        origins = {a.prefix_origin for a in self.routable_ipv4 if a.prefix_origin}
        if "Manual" in origins:
            return False
        if "Dhcp" in origins:
            return True
        return self.dhcp_enabled

    @property
    def mode_label(self) -> str:
        return "DHCP" if self.effective_dhcp else "Static"

    def identity_matches(self, guid: str = "", mac: str = "") -> bool:
        """Stable-identity comparison used when resolving saved presets."""
        if guid and self.guid and guid.lower() == self.guid.lower():
            return True
        if mac and self.mac and mac.upper() == self.mac.upper():
            return True
        return False

    def summary_line(self) -> str:
        return f"{self.friendly_name} - {self.oper_status.label} - {self.display_address}"
