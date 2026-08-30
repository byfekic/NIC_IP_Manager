"""Adapter discovery and identity resolution.

Adapters are always discovered dynamically; the application never assumes an
adapter is called "Ethernet" (specification section 6). Identity is resolved
through the interface GUID first and the MAC address second, never through the
user-renameable friendly name alone (section 17).
"""

from __future__ import annotations

from typing import Optional

from app.models.adapter import Adapter, IPv4Address, OperStatus
from app.network import winapi
from app.utils.errors import IPChangerError
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


class AdapterManager:
    """Enumerates network adapters and resolves stable adapter identities."""

    def list_adapters(self, include_loopback: bool = False) -> list[Adapter]:
        """Return every adapter Windows reports, sorted for stable display."""
        try:
            raw_entries = winapi.get_adapters_addresses()
        except OSError as exc:
            log.error("Adapter enumeration failed: %s", exc)
            raise IPChangerError(
                "Unable to read the network adapters.",
                "Windows did not return the list of network interfaces. "
                "Try again, or restart the application.",
                f"GetAdaptersAddresses: {exc}",
            ) from exc

        adapters: list[Adapter] = []
        for raw in raw_entries:
            try:
                adapter = self._build(raw)
            except Exception as exc:  # one malformed entry must not break discovery
                log.warning("Skipping unreadable adapter entry: %s", exc)
                continue
            if not include_loopback and adapter.kind.value == "Loopback":
                continue
            adapters.append(adapter)

        adapters.sort(key=self._sort_key)
        log.info(
            "Discovered %d adapters: %s",
            len(adapters),
            ", ".join(f"{a.friendly_name}[{a.kind.value}/{a.oper_status.label}]" for a in adapters),
        )
        return adapters

    # ------------------------------------------------------------- building
    @staticmethod
    def _build(raw: dict) -> Adapter:
        try:
            status = OperStatus(raw["oper_status"])
        except ValueError:
            status = OperStatus.UNKNOWN

        return Adapter(
            guid=raw["guid"],
            if_index=raw["if_index"],
            friendly_name=raw["friendly_name"] or raw["description"] or raw["guid"],
            description=raw["description"],
            mac=raw["mac"],
            if_type=raw["if_type"],
            oper_status=status,
            dhcp_enabled=raw["dhcp_enabled"],
            ipv4=[IPv4Address(a, p, o) for a, p, o in raw["unicast_ipv4"]],
            ipv6=[a for a, _, _ in raw["unicast_ipv6"]],
            gateways=raw["gateways"],
            dns_servers=raw["dns_servers"],
            dhcp_server=raw["dhcp_server"],
            mtu=raw["mtu"],
            link_speed=raw["link_speed"],
        )

    @staticmethod
    def _sort_key(adapter: Adapter) -> tuple:
        """Connected physical adapters first - the ones an operator wants."""
        kind_rank = {
            "Physical": 0,
            "Wireless": 1,
            "VPN": 2,
            "Bluetooth": 3,
            "Virtual": 4,
            "Tunnel": 5,
            "Other": 6,
            "Loopback": 7,
        }
        return (
            0 if adapter.oper_status.is_up else 1,
            kind_rank.get(adapter.kind.value, 9),
            adapter.friendly_name.lower(),
        )

    # ------------------------------------------------------------- identity
    def find(
        self,
        adapters: Optional[list[Adapter]] = None,
        guid: str = "",
        mac: str = "",
        if_index: Optional[int] = None,
    ) -> Optional[Adapter]:
        """Resolve an adapter by stable identity.

        Matching is strictly ordered: GUID, then MAC, then interface index.
        The interface index alone is deliberately the weakest match because
        Windows reuses indices after adapters are removed.
        """
        pool = adapters if adapters is not None else self.list_adapters(include_loopback=True)

        if guid:
            for adapter in pool:
                if adapter.guid and adapter.guid.lower() == guid.lower():
                    return adapter
        if mac:
            for adapter in pool:
                if adapter.mac and adapter.mac.upper() == mac.upper():
                    return adapter
        if if_index is not None:
            for adapter in pool:
                if adapter.if_index == if_index:
                    return adapter
        return None

    def refresh(self, adapter: Adapter) -> Optional[Adapter]:
        """Re-read one adapter's live state. Returns None if it disappeared."""
        return self.find(guid=adapter.guid, mac=adapter.mac)

    def get_by_guid(self, guid: str) -> Adapter:
        """Fetch an adapter by GUID or raise a user-presentable error."""
        adapter = self.find(guid=guid)
        if adapter is None:
            raise IPChangerError(
                "Adapter not found.",
                "The selected network adapter is no longer available. "
                "It may have been disabled, removed or renamed.",
                f"guid={guid}",
            )
        return adapter
