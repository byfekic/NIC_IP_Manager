"""Configuration, snapshot and operation-result models.

Everything is normalised internally to a prefix length (CIDR); traditional
dotted subnet masks are produced only for display (specification section 30).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class ConfigMode(Enum):
    DHCP = "dhcp"
    STATIC = "static"

    @property
    def label(self) -> str:
        return "DHCP" if self is ConfigMode.DHCP else "Static"


def mask_to_prefix(mask: str) -> int:
    """Convert a dotted subnet mask to a prefix length.

    Accepts a prefix in either ``/24`` or ``24`` form as well. Raises
    ``ValueError`` for anything that is not a valid contiguous mask.
    """
    text = (mask or "").strip()
    if not text:
        raise ValueError("Subnet mask is empty")
    if text.startswith("/"):
        text = text[1:]
    if text.isdigit():
        prefix = int(text)
        if not 0 <= prefix <= 32:
            raise ValueError(f"Invalid prefix length: /{prefix}")
        return prefix
    # ipaddress validates that the mask is contiguous (255.255.0.255 is rejected).
    return ipaddress.IPv4Network(f"0.0.0.0/{text}").prefixlen


def prefix_to_mask(prefix: int) -> str:
    """Convert a prefix length to a dotted subnet mask."""
    if not 0 <= int(prefix) <= 32:
        raise ValueError(f"Invalid prefix length: /{prefix}")
    return str(ipaddress.IPv4Network(f"0.0.0.0/{int(prefix)}").netmask)


@dataclass
class IPConfiguration:
    """A requested IPv4 configuration for one adapter."""

    mode: ConfigMode = ConfigMode.DHCP
    ip_address: str = ""
    prefix_length: int = 24
    gateway: str = ""

    @property
    def subnet_mask(self) -> str:
        try:
            return prefix_to_mask(self.prefix_length)
        except ValueError:
            return ""

    @classmethod
    def static(cls, ip: str, mask: str, gateway: str = "") -> "IPConfiguration":
        return cls(
            mode=ConfigMode.STATIC,
            ip_address=ip.strip(),
            prefix_length=mask_to_prefix(mask),
            gateway=(gateway or "").strip(),
        )

    @classmethod
    def dhcp(cls) -> "IPConfiguration":
        return cls(mode=ConfigMode.DHCP)

    @property
    def is_dhcp(self) -> bool:
        return self.mode is ConfigMode.DHCP

    def describe(self) -> str:
        if self.is_dhcp:
            return "DHCP (automatic)"
        gw = self.gateway or "none"
        return f"{self.ip_address} / {self.subnet_mask}   Gateway: {gw}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "ip_address": self.ip_address,
            "subnet_mask": self.subnet_mask,
            "gateway": self.gateway,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IPConfiguration":
        mode = ConfigMode(str(data.get("mode", "dhcp")).lower())
        if mode is ConfigMode.DHCP:
            return cls.dhcp()
        return cls.static(
            str(data.get("ip_address", "")),
            str(data.get("subnet_mask", "255.255.255.0")),
            str(data.get("gateway", "") or ""),
        )


@dataclass
class ConfigurationSnapshot:
    """Complete pre-change state of an adapter, sufficient to restore it.

    Captures *every* IPv4 address, not just the first one, so that rollback
    restores the full previous state (specification section 14).
    """

    guid: str
    if_index: int
    friendly_name: str
    mac: str
    dhcp_enabled: bool
    addresses: list[dict[str, Any]] = field(default_factory=list)
    gateways: list[str] = field(default_factory=list)
    dns_servers: list[str] = field(default_factory=list)
    captured_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @classmethod
    def from_adapter(cls, adapter: Any) -> "ConfigurationSnapshot":
        # effective_dhcp, not the raw flag: a disconnected adapter with manual
        # addresses reports DHCP enabled, and trusting that would make rollback
        # restore DHCP instead of the operator's previous static configuration.
        return cls(
            guid=adapter.guid,
            if_index=adapter.if_index,
            friendly_name=adapter.friendly_name,
            mac=adapter.mac,
            dhcp_enabled=getattr(adapter, "effective_dhcp", adapter.dhcp_enabled),
            addresses=[
                {"address": a.address, "prefix_length": a.prefix_length}
                for a in adapter.ipv4
                if not a.is_apipa
            ],
            gateways=list(adapter.ipv4_gateways),
            dns_servers=[d for d in adapter.dns_servers if ":" not in d],
        )

    def describe(self) -> str:
        if self.dhcp_enabled:
            return "DHCP (automatic)"
        if not self.addresses:
            return "No IPv4 address"
        parts = [
            f"{a['address']} / {prefix_to_mask(a['prefix_length'])}" for a in self.addresses
        ]
        gw = self.gateways[0] if self.gateways else "none"
        return f"{'; '.join(parts)}   Gateway: {gw}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "guid": self.guid,
            "if_index": self.if_index,
            "friendly_name": self.friendly_name,
            "mac": self.mac,
            "dhcp_enabled": self.dhcp_enabled,
            "addresses": self.addresses,
            "gateways": self.gateways,
            "dns_servers": self.dns_servers,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigurationSnapshot":
        return cls(
            guid=str(data.get("guid", "")),
            if_index=int(data.get("if_index", 0)),
            friendly_name=str(data.get("friendly_name", "")),
            mac=str(data.get("mac", "")),
            dhcp_enabled=bool(data.get("dhcp_enabled", False)),
            addresses=list(data.get("addresses", [])),
            gateways=list(data.get("gateways", [])),
            dns_servers=list(data.get("dns_servers", [])),
            captured_at=str(data.get("captured_at", "")),
        )


class ResultStatus(Enum):
    SUCCESS = "success"
    VERIFY_FAILED = "verify_failed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    CANCELLED = "cancelled"
    DRY_RUN = "dry_run"

    @property
    def is_success(self) -> bool:
        return self is ResultStatus.SUCCESS


@dataclass
class OperationResult:
    """Outcome of an apply/rollback transaction (specification section 55)."""

    status: ResultStatus
    message: str = ""
    detail: str = ""
    technical: str = ""
    snapshot: Optional[ConfigurationSnapshot] = None
    resulting: Optional[Any] = None
    mismatches: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    conflict: str = ""

    @property
    def ok(self) -> bool:
        return self.status.is_success

    @property
    def can_rollback(self) -> bool:
        return (
            self.snapshot is not None
            and self.status
            in (ResultStatus.VERIFY_FAILED, ResultStatus.FAILED, ResultStatus.ROLLBACK_FAILED)
        )
