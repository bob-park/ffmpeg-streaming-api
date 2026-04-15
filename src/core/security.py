import ipaddress
import socket
from urllib.parse import urlparse


class UrlRejected(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"url_rejected:{reason}")


def _try_parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return an IP object if `host` is an IP literal (v4 or v6), else None."""
    # urlparse strips brackets from IPv6 hosts, so we can parse directly.
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_source_url(
    url: str,
    allow_schemes: list[str],
    deny_hostnames: list[str],
    allow_private_ips: bool = False,
) -> None:
    """Validate a source URL for SSRF safety.

    By default, any host that resolves to a private/loopback/link-local/multicast
    address is rejected. Set `allow_private_ips=True` to permit private ranges
    (LAN / dev) while still blocking loopback, link-local, multicast, reserved.
    """
    parsed = urlparse(url)
    if parsed.scheme not in allow_schemes:
        raise UrlRejected("scheme")

    host = (parsed.hostname or "").lower()
    if not host:
        raise UrlRejected("hostname")

    if host in {h.lower() for h in deny_hostnames}:
        raise UrlRejected("hostname")

    if host.endswith(".local"):
        raise UrlRejected("hostname")

    # Fast path: host is already an IP literal — no DNS needed.
    literal = _try_parse_ip(host)
    if literal is not None:
        if _is_blocked_ip(literal):
            raise UrlRejected("private_ip")
        if literal.is_private and not allow_private_ips:
            raise UrlRejected("private_ip")
        return

    # Hostname path: resolve and check every returned address.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UrlRejected("dns") from e

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise UrlRejected("private_ip")
        if ip.is_private and not allow_private_ips:
            raise UrlRejected("private_ip")
