"""Validation of requested IPv4 configurations (specification sections 11, 31).

Two severities are distinguished and must not be conflated:

* ERROR   - objectively invalid, the configuration cannot be applied.
* WARNING - unusual but potentially legitimate; the operator may override.

All parsing is delegated to the standard library ``ipaddress`` module; no
custom IP parsing is implemented.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import Enum

from app.models.configuration import ConfigMode, IPConfiguration, mask_to_prefix


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    field: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def is_valid(self) -> bool:
        """True when nothing blocks application. Warnings do not block."""
        return not self.errors

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)

    def add_error(self, field_name: str, message: str) -> None:
        self.issues.append(Issue(Severity.ERROR, field_name, message))

    def add_warning(self, field_name: str, message: str) -> None:
        self.issues.append(Issue(Severity.WARNING, field_name, message))

    def errors_for(self, field_name: str) -> list[Issue]:
        return [i for i in self.errors if i.field == field_name]

    def error_text(self) -> str:
        return "\n".join(f"- {i.message}" for i in self.errors)

    def warning_text(self) -> str:
        return "\n".join(f"- {i.message}" for i in self.warnings)


# --------------------------------------------------------------------- atoms

def parse_ipv4(value: str) -> ipaddress.IPv4Address:
    """Strict IPv4 parse. Rejects three-octet shorthand and other loose forms."""
    text = (value or "").strip()
    if not text:
        raise ValueError("empty")
    if text.count(".") != 3:
        raise ValueError("an IPv4 address must have four octets")
    # ipaddress accepts only a full dotted quad without leading zeros.
    return ipaddress.IPv4Address(text)


def validate_ip_text(value: str) -> tuple[bool, str]:
    """Field-level check used for live feedback while the operator types."""
    if not (value or "").strip():
        return False, "Required."
    try:
        parse_ipv4(value)
    except Exception:
        return False, "Not a valid IPv4 address."
    return True, ""


def validate_mask_text(value: str) -> tuple[bool, str]:
    if not (value or "").strip():
        return False, "Required."
    try:
        mask_to_prefix(value)
    except Exception:
        return False, "Not a valid subnet mask (example: 255.255.255.0)."
    return True, ""


# ------------------------------------------------------------------ full set

def validate(config: IPConfiguration, adapter=None) -> ValidationResult:
    """Validate a complete requested configuration before touching the system."""
    result = ValidationResult()

    if config.mode is ConfigMode.DHCP:
        _validate_adapter(result, adapter)
        return result

    # ---- IP address -----------------------------------------------------
    ip = None
    try:
        ip = parse_ipv4(config.ip_address)
    except Exception:
        result.add_error("ip", "IP address is not a valid IPv4 address.")

    # ---- prefix / subnet mask -------------------------------------------
    prefix = None
    try:
        prefix = int(config.prefix_length)
        if not 0 <= prefix <= 32:
            raise ValueError
    except Exception:
        result.add_error("mask", "Subnet mask is not valid.")
        prefix = None

    if ip is None or prefix is None:
        _validate_adapter(result, adapter)
        return result

    if ip.is_loopback:
        result.add_error("ip", "Loopback addresses (127.x.x.x) cannot be assigned to an adapter.")
    if ip.is_multicast:
        result.add_error("ip", "Multicast addresses cannot be assigned to an adapter.")
    if ip.is_unspecified:
        result.add_error("ip", "0.0.0.0 cannot be assigned to an adapter.")
    elif ip.is_reserved:
        result.add_error("ip", "This address is reserved and cannot be assigned.")

    network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)

    # A /31 point-to-point link (RFC 3021) and a /32 host route have no usable
    # network or broadcast address in the usual sense, so skip those checks.
    if prefix <= 30:
        if ip == network.network_address:
            result.add_error(
                "ip",
                f"{ip} is the network address of {network} and cannot be used as a host address.",
            )
        if ip == network.broadcast_address:
            result.add_error(
                "ip",
                f"{ip} is the broadcast address of {network} and cannot be used as a host address.",
            )

    if prefix < 8:
        result.add_warning("mask", f"A /{prefix} subnet is unusually large. Check the subnet mask.")
    if prefix == 32:
        result.add_warning(
            "mask",
            "A /32 mask defines a single host with no local subnet. This is rarely intended.",
        )

    if ip.is_link_local:
        result.add_warning(
            "ip",
            "169.254.x.x is the Windows automatic private range (APIPA). "
            "Assigning it statically is unusual.",
        )
    elif not ip.is_private:
        result.add_warning("ip", f"{ip} is a public Internet address. Confirm this is intended.")

    # ---- gateway --------------------------------------------------------
    gateway_text = (config.gateway or "").strip()
    if gateway_text:
        try:
            gw = parse_ipv4(gateway_text)
        except Exception:
            result.add_error("gateway", "Gateway is not a valid IPv4 address.")
        else:
            if gw == ip:
                result.add_error(
                    "gateway", "The gateway cannot be the same address as the adapter."
                )
            elif gw.is_multicast or gw.is_loopback or gw.is_unspecified:
                result.add_error("gateway", "The gateway address is not usable.")
            elif gw not in network:
                result.add_warning(
                    "gateway",
                    f"Gateway {gw} is outside the configured subnet {network}. "
                    "It will normally be unreachable.",
                )
            elif prefix <= 30 and gw == network.broadcast_address:
                result.add_warning(
                    "gateway", f"Gateway {gw} is the broadcast address of {network}."
                )

    _validate_adapter(result, adapter)
    return result


def _validate_adapter(result: ValidationResult, adapter) -> None:
    """Adapter-aware checks shared by both DHCP and static validation."""
    if adapter is None:
        return

    if not getattr(adapter, "is_configurable", True):
        result.add_error("adapter", "This adapter type cannot be configured by this application.")

    status = getattr(adapter, "oper_status", None)
    if status is not None and not getattr(status, "is_up", True):
        result.add_warning(
            "adapter",
            f"The adapter is currently {status.label.lower()}. The configuration will be "
            "stored by Windows and take effect when the link comes up.",
        )

    kind = getattr(adapter, "kind", None)
    if kind is not None and kind.value in ("Virtual", "VPN"):
        article = "a" if kind.value == "Virtual" else "an"
        noun = "virtual" if kind.value == "Virtual" else "SSL/VPN"
        result.add_warning(
            "adapter",
            f"'{adapter.friendly_name}' is {article} {noun} adapter. "
            "Changing it may disrupt virtual machines or VPN connectivity.",
        )

    if getattr(adapter, "has_multiple_ipv4", False):
        result.add_warning(
            "adapter",
            f"This adapter currently has {len(adapter.routable_ipv4)} IPv4 addresses. "
            "The additional addresses will be preserved.",
        )


def validate_preset_payload(data: dict) -> tuple[bool, str]:
    """Validate one preset entry from an imported JSON file (section 35)."""
    if not isinstance(data, dict):
        return False, "Entry is not a valid preset object."
    name = str(data.get("name", "")).strip()
    if not name:
        return False, "Preset has no name."
    if len(name) > 100:
        return False, f"Preset name is too long: {name[:30]}..."
    mode_text = str(data.get("mode", "")).strip().lower()
    if mode_text not in ("dhcp", "static"):
        return False, f"'{name}': mode must be 'dhcp' or 'static'."
    if mode_text == "dhcp":
        return True, ""
    try:
        config = IPConfiguration.static(
            str(data.get("ip_address", "")),
            str(data.get("subnet_mask", "")),
            str(data.get("gateway", "") or ""),
        )
    except Exception:
        return False, f"'{name}': IP address or subnet mask is not valid."
    result = validate(config)
    if not result.is_valid:
        return False, f"'{name}': {result.errors[0].message}"
    return True, ""
