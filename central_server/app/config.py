from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass
from pathlib import Path


def _default_data_directory() -> Path:
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA")
        if base:
            return Path(base) / "LabNet" / "Central"
    xdg_data = os.getenv("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "labnet" / "central"
    return Path.home() / ".local" / "share" / "labnet" / "central"


DEFAULT_DATABASE_URL = (
    f"sqlite:///{(_default_data_directory() / 'labnet.db').as_posix()}"
)


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for the Central management plane."""

    database_url: str = DEFAULT_DATABASE_URL
    lease_min_seconds: int = 5
    lease_max_seconds: int = 86_400
    heartbeat_timeout_seconds: int = 30
    maintenance_interval_seconds: float = 5.0
    bind_host: str = "127.0.0.1"
    bind_port: int = 8008
    # Development/tests may opt in. Normal deployments use `python -m
    # central_server.manage db upgrade` so startup never mutates an old schema.
    create_schema_on_startup: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("LABNET_DATABASE_URL", DEFAULT_DATABASE_URL),
            lease_min_seconds=int(os.getenv("LABNET_LEASE_MIN_SECONDS", "5")),
            lease_max_seconds=int(os.getenv("LABNET_LEASE_MAX_SECONDS", "86400")),
            heartbeat_timeout_seconds=int(
                os.getenv("LABNET_HEARTBEAT_TIMEOUT_SECONDS", "30")
            ),
            maintenance_interval_seconds=float(
                os.getenv("LABNET_MAINTENANCE_INTERVAL_SECONDS", "5")
            ),
            bind_host=os.getenv("LABNET_CENTRAL_BIND_HOST", "127.0.0.1"),
            bind_port=int(os.getenv("LABNET_CENTRAL_BIND_PORT", "8008")),
            create_schema_on_startup=os.getenv(
                "LABNET_CREATE_SCHEMA_ON_STARTUP", "false"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
        ).validated()

    def validated(self) -> "Settings":
        if not self.database_url.strip():
            raise ValueError("LABNET_DATABASE_URL must not be empty.")
        if self.lease_min_seconds <= 0:
            raise ValueError("LABNET_LEASE_MIN_SECONDS must be positive.")
        if self.lease_max_seconds < self.lease_min_seconds:
            raise ValueError(
                "LABNET_LEASE_MAX_SECONDS must be at least the minimum."
            )
        if self.heartbeat_timeout_seconds <= 0:
            raise ValueError("LABNET_HEARTBEAT_TIMEOUT_SECONDS must be positive.")
        if (
            not math.isfinite(self.maintenance_interval_seconds)
            or self.maintenance_interval_seconds <= 0
        ):
            raise ValueError(
                "LABNET_MAINTENANCE_INTERVAL_SECONDS must be finite and positive."
            )
        if not self.bind_host.strip():
            raise ValueError("LABNET_CENTRAL_BIND_HOST must not be empty.")
        if not _is_loopback(self.bind_host):
            raise ValueError(
                "The built-in plaintext Central server may bind only to loopback. "
                "Use an external HTTPS reverse proxy for remote access."
            )
        if not 1 <= self.bind_port <= 65535:
            raise ValueError("LABNET_CENTRAL_BIND_PORT must be between 1 and 65535.")
        return self


def _is_loopback(host: str) -> bool:
    value = host.strip().strip("[]")
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False
