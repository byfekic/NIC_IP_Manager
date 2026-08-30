"""Thin ctypes binding for the Windows IP Helper API.

Adapter information is read through ``GetAdaptersAddresses`` rather than by
parsing command-line output, because that output is localized and unstable
(specification sections 4 and 29). This module contains only structure
definitions and the raw enumeration call; interpretation lives in
``adapter_manager``.
"""

from __future__ import annotations

import ctypes
import socket
from typing import Any, Iterator

# ---------------------------------------------------------------- constants

AF_UNSPEC = 0
AF_INET = socket.AF_INET
AF_INET6 = socket.AF_INET6

GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_INCLUDE_PREFIX = 0x0010
GAA_FLAG_INCLUDE_GATEWAYS = 0x0080

ERROR_SUCCESS = 0
ERROR_BUFFER_OVERFLOW = 111
ERROR_NO_DATA = 232

# IP_ADAPTER_ADDRESSES Flags bits.
IP_ADAPTER_DHCP_ENABLED = 0x0004
IP_ADAPTER_IPV4_ENABLED = 0x0080
IP_ADAPTER_IPV6_ENABLED = 0x0100

# IP_PREFIX_ORIGIN. This is how an address was obtained, and unlike the
# interface-wide DHCP flag it stays correct on a disconnected adapter.
PREFIX_ORIGIN = {
    0: "Other",
    1: "Manual",
    2: "WellKnown",
    3: "Dhcp",
    4: "RouterAdvertisement",
}


# --------------------------------------------------------------- structures

class SOCKADDR(ctypes.Structure):
    _fields_ = [
        ("sa_family", ctypes.c_ushort),
        ("sa_data", ctypes.c_ubyte * 26),
    ]


class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("lpSockaddr", ctypes.POINTER(SOCKADDR)),
        ("iSockaddrLength", ctypes.c_int),
    ]


class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", ctypes.c_ulong),
    ("PreferredLifetime", ctypes.c_ulong),
    ("LeaseLifetime", ctypes.c_ulong),
    ("OnLinkPrefixLength", ctypes.c_ubyte),
]


class IP_ADAPTER_GATEWAY_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_GATEWAY_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Reserved", ctypes.c_ulong),
    ("Next", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
]


class IP_ADAPTER_DNS_SERVER_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_DNS_SERVER_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Reserved", ctypes.c_ulong),
    ("Next", ctypes.POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
]


class IP_ADAPTER_PREFIX(ctypes.Structure):
    pass


IP_ADAPTER_PREFIX._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Next", ctypes.POINTER(IP_ADAPTER_PREFIX)),
    ("Address", SOCKET_ADDRESS),
    ("PrefixLength", ctypes.c_ulong),
]


class IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.POINTER(IP_ADAPTER_DNS_SERVER_ADDRESS)),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", ctypes.c_ulong),
    ("ZoneIndices", ctypes.c_ulong * 16),
    ("FirstPrefix", ctypes.POINTER(IP_ADAPTER_PREFIX)),
    ("TransmitLinkSpeed", ctypes.c_uint64),
    ("ReceiveLinkSpeed", ctypes.c_uint64),
    ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
    ("Ipv4Metric", ctypes.c_ulong),
    ("Ipv6Metric", ctypes.c_ulong),
    ("Luid", ctypes.c_uint64),
    ("Dhcpv4Server", SOCKET_ADDRESS),
    ("CompartmentId", ctypes.c_uint32),
    ("NetworkGuid", ctypes.c_ubyte * 16),
    ("ConnectionType", ctypes.c_int),
    ("TunnelType", ctypes.c_int),
    ("Dhcpv6Server", SOCKET_ADDRESS),
    ("Dhcpv6ClientDuid", ctypes.c_ubyte * 130),
    ("Dhcpv6ClientDuidLength", ctypes.c_ulong),
    ("Dhcpv6Iaid", ctypes.c_ulong),
    ("FirstDnsSuffix", ctypes.c_void_p),
]


# ----------------------------------------------------------------- helpers

def sockaddr_to_str(sockaddr_ptr) -> str | None:
    """Render a SOCKADDR pointer as a printable IPv4/IPv6 address."""
    if not sockaddr_ptr:
        return None
    try:
        sa = sockaddr_ptr.contents
    except ValueError:  # pragma: no cover - null pointer dereference guard
        return None
    family = sa.sa_family
    try:
        if family == AF_INET:
            return socket.inet_ntop(AF_INET, bytes(sa.sa_data[2:6]))
        if family == AF_INET6:
            return socket.inet_ntop(AF_INET6, bytes(sa.sa_data[6:22]))
    except (OSError, ValueError):  # pragma: no cover
        return None
    return None


def _iterate(first) -> Iterator[Any]:
    node = first
    while node:
        yield node.contents
        node = node.contents.Next


def get_adapters_addresses(family: int = AF_UNSPEC) -> list[dict[str, Any]]:
    """Enumerate adapters and return plain dictionaries.

    Returning dictionaries rather than ctypes objects keeps every pointer
    dereference inside this function, where the backing buffer is guaranteed
    to be alive.
    """
    iphlpapi = ctypes.WinDLL("iphlpapi.dll")
    flags = (
        GAA_FLAG_INCLUDE_PREFIX
        | GAA_FLAG_INCLUDE_GATEWAYS
        | GAA_FLAG_SKIP_MULTICAST
        | GAA_FLAG_SKIP_ANYCAST
    )

    size = ctypes.c_ulong(15000)
    buffer = ctypes.create_string_buffer(size.value)
    ret = iphlpapi.GetAdaptersAddresses(family, flags, None, buffer, ctypes.byref(size))

    # Windows reports the required size; retry once (twice at most, in case the
    # adapter set changed between calls).
    for _ in range(2):
        if ret != ERROR_BUFFER_OVERFLOW:
            break
        buffer = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetAdaptersAddresses(family, flags, None, buffer, ctypes.byref(size))

    if ret == ERROR_NO_DATA:
        return []
    if ret != ERROR_SUCCESS:
        raise OSError(ret, f"GetAdaptersAddresses failed with Windows error {ret}")

    results: list[dict[str, Any]] = []
    node = ctypes.cast(buffer, ctypes.POINTER(IP_ADAPTER_ADDRESSES))
    while node:
        entry = node.contents

        unicast_v4: list[tuple[str, int, str]] = []
        unicast_v6: list[tuple[str, int, str]] = []
        for addr in _iterate(entry.FirstUnicastAddress):
            text = sockaddr_to_str(addr.Address.lpSockaddr)
            if text is None:
                continue
            family_of = addr.Address.lpSockaddr.contents.sa_family
            origin = PREFIX_ORIGIN.get(int(addr.PrefixOrigin), "Other")
            record = (text, int(addr.OnLinkPrefixLength), origin)
            if family_of == AF_INET:
                unicast_v4.append(record)
            elif family_of == AF_INET6:
                unicast_v6.append(record)

        gateways = [
            text
            for g in _iterate(entry.FirstGatewayAddress)
            if (text := sockaddr_to_str(g.Address.lpSockaddr)) is not None
        ]
        dns_servers = [
            text
            for d in _iterate(entry.FirstDnsServerAddress)
            if (text := sockaddr_to_str(d.Address.lpSockaddr)) is not None
        ]

        mac = ":".join(
            f"{b:02X}" for b in entry.PhysicalAddress[: entry.PhysicalAddressLength]
        )
        guid = entry.AdapterName.decode("ascii", "replace") if entry.AdapterName else ""
        dhcp_server = sockaddr_to_str(entry.Dhcpv4Server.lpSockaddr)
        if dhcp_server in ("0.0.0.0", None):
            dhcp_server = None

        results.append(
            {
                "guid": guid,
                "if_index": int(entry.IfIndex),
                "friendly_name": entry.FriendlyName or "",
                "description": entry.Description or "",
                "mac": mac,
                "if_type": int(entry.IfType),
                "oper_status": int(entry.OperStatus),
                "flags": int(entry.Flags),
                "dhcp_enabled": bool(entry.Flags & IP_ADAPTER_DHCP_ENABLED),
                "ipv4_enabled": bool(entry.Flags & IP_ADAPTER_IPV4_ENABLED),
                "ipv6_enabled": bool(entry.Flags & IP_ADAPTER_IPV6_ENABLED),
                "unicast_ipv4": unicast_v4,
                "unicast_ipv6": unicast_v6,
                "gateways": gateways,
                "dns_servers": dns_servers,
                "dhcp_server": dhcp_server,
                "mtu": int(entry.Mtu),
                "link_speed": int(entry.TransmitLinkSpeed),
            }
        )
        node = entry.Next

    return results
