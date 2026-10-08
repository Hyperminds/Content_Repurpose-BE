"""URL safety checks — prevents SSRF via user-supplied media URLs.

Defense-in-depth approach:
  1. Block known dangerous hostnames (metadata endpoints, localhost)
  2. Block URLs with explicit private IP addresses
  3. Block internal-looking hostnames (.internal, .local)
  4. DNS-level check: resolve the hostname and reject if it resolves to a private IP

All publisher image/media fetch operations must call `is_safe_url()` (sync)
or `is_safe_url_async()` (async, with DNS check) before making outbound requests.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

# Private/internal network ranges that should never be fetched
_BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",  # AWS/GCP metadata endpoint
    "metadata.google",
    "metadata",
}

# TLDs and suffixes that indicate internal networks
_BLOCKED_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
    ".corp",
    ".lan",
    ".intranet",
)


def is_safe_url(url: str) -> bool:
    """
    Synchronous URL safety check (no DNS resolution).

    Blocks obvious private targets but cannot catch hostnames that resolve
    to private IPs. Use `is_safe_url_async()` for full protection.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = (parsed.hostname or "").lower()

    if not hostname:
        return False

    if hostname in _BLOCKED_HOSTS:
        return False

    if any(hostname.endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        return False

    # Block explicit private IPs in the URL
    if _is_private_ip(hostname):
        return False

    return True


async def is_safe_url_async(url: str) -> bool:
    """
    Async URL safety check WITH DNS resolution.

    Resolves the hostname to IP addresses and rejects if any resolved IP
    is in a private/reserved range. This catches attacks like:
      - attacker.com → 127.0.0.1
      - evil.example.com → 10.0.0.5

    Should be called before any outbound fetch of user-supplied URLs.
    """
    # First pass: fast synchronous checks
    if not is_safe_url(url):
        return False

    # Second pass: DNS resolution
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        # Skip DNS check for IP addresses (already checked above)
        if _is_ip_address(hostname):
            return True

        # Resolve hostname to IPs
        resolved_ips = await _resolve_hostname(hostname)

        # Check all resolved addresses
        for ip_str in resolved_ips:
            if _is_private_ip(ip_str):
                return False

    except Exception:
        # DNS resolution failed — block the request (fail-closed)
        return False

    return True


async def _resolve_hostname(hostname: str) -> list[str]:
    """
    Resolve a hostname to a list of IP address strings.

    Uses asyncio's event loop threadpool to avoid blocking.
    """
    loop = asyncio.get_running_loop()
    try:
        # getaddrinfo returns list of (family, type, proto, canonname, sockaddr)
        results = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        )
        ips: list[str] = []
        for result in results:
            sockaddr = result[4]
            ip = sockaddr[0]  # IP is the first element of the sockaddr tuple
            if ip not in ips:
                ips.append(ip)
        return ips
    except (socket.gaierror, OSError):
        # Cannot resolve — treat as unsafe
        return []


def _is_private_ip(ip_str: str) -> bool:
    """Check if an IP address string is in a private/reserved range."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        return False


def _is_ip_address(hostname: str) -> bool:
    """Check if a string is a valid IP address (v4 or v6)."""
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False
