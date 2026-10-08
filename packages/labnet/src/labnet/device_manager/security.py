from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from .errors import InvalidConfigurationError


def validate_central_server_url(server_url: str) -> str:
    """Validate a Central Server base URL and return its normalized form.

    Bearer API keys and lease credentials may use plaintext HTTP only over a
    loopback connection. A LAN or routed Central Server must use HTTPS.
    """

    value = server_url.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise InvalidConfigurationError(
            "The Central Server URL is malformed."
        ) from exc

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidConfigurationError(
            "The Central Server URL must use http:// or https://."
        )
    if parsed.username is not None or parsed.password is not None:
        raise InvalidConfigurationError(
            "The Central Server URL cannot contain user information."
        )
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise InvalidConfigurationError(
            "The Central Server URL must be an origin without a path, query, or fragment."
        )
    if port is not None and not 1 <= port <= 65535:
        raise InvalidConfigurationError(
            "The Central Server URL port must be between 1 and 65535."
        )
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise InvalidConfigurationError(
            "Plaintext HTTP to the Central Server is permitted only on loopback; "
            "use HTTPS for a remote Central Server."
        )
    return value


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


__all__ = ["validate_central_server_url"]
