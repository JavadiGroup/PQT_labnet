from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass
from pathlib import Path

from labnet.lease_policy import DEFAULT_IDLE_TIMEOUT_SECONDS


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
    lease_default_idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    heartbeat_timeout_seconds: int = 30
    maintenance_interval_seconds: float = 5.0
    release_root: Path = _default_data_directory() / "release-store"
    release_max_upload_bytes: int = 2 * 1024 * 1024 * 1024
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
            lease_default_idle_timeout_seconds=int(
                os.getenv(
                    "LABNET_LEASE_DEFAULT_IDLE_TIMEOUT_SECONDS",
                    str(DEFAULT_IDLE_TIMEOUT_SECONDS),
                )
            ),
            heartbeat_timeout_seconds=int(
                os.getenv("LABNET_HEARTBEAT_TIMEOUT_SECONDS", "30")
            ),
            maintenance_interval_seconds=float(
                os.getenv("LABNET_MAINTENANCE_INTERVAL_SECONDS", "5")
            ),
            release_root=Path(
                os.getenv(
                    "LABNET_RELEASE_ROOT",
                    str(_default_data_directory() / "release-store"),
                )
            ).expanduser().resolve(),
            release_max_upload_bytes=int(
                os.getenv(
                    "LABNET_RELEASE_MAX_UPLOAD_BYTES",
                    str(2 * 1024 * 1024 * 1024),
                )
            ),
            bind_host=os.getenv("LABNET_CENTRAL_BIND_HOST", "127.0.0.1"),
            bind_port=int(os.getenv("LABNET_CENTRAL_BIND_PORT", "8008")),
            create_schema_on_startup=_environment_bool(
                "LABNET_CREATE_SCHEMA_ON_STARTUP", False
            ),
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
        if self.lease_default_idle_timeout_seconds <= 0:
            raise ValueError(
                "LABNET_LEASE_DEFAULT_IDLE_TIMEOUT_SECONDS must be positive."
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
        if self.release_max_upload_bytes <= 0:
            raise ValueError("LABNET_RELEASE_MAX_UPLOAD_BYTES must be positive.")
        if not str(self.release_root):
            raise ValueError("LABNET_RELEASE_ROOT must not be empty.")
        resolved_release_root = self.release_root.expanduser().resolve()
        if resolved_release_root in {
            Path(resolved_release_root.anchor),
            Path.home().resolve(),
        }:
            raise ValueError(
                "LABNET_RELEASE_ROOT must be a dedicated data directory."
            )
        return self


def _is_loopback(host: str) -> bool:
    value = host.strip().strip("[]")
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _environment_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value.")
