"""Lab-PC settings: read from an env file and the process environment.

Two inputs decide how a Lab PC runs, so there are no separate mode switches:

* ``LABNET_DEVICE_MANAGER_API_KEY`` set: register with Central and validate
  every lease with it. Unset: standalone development (loopback only, leases
  are not checked).
* ``LABNET_GRPC_CERT_FILE`` and ``LABNET_GRPC_KEY_FILE`` set: serve gRPC over
  TLS. Unset: plaintext, which is permitted only on loopback.
"""

from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from labnet.envfile import load_env_file
from labnet.identifiers import validate_labnet_identifier


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True

    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_wildcard_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    return normalized in {"0.0.0.0", "::"}


def _format_target(host: str, port: int) -> str:
    host = host.strip("[]")
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _parse_grpc_target(target: str) -> tuple[str, int]:
    value = target.strip()
    if "://" in value:
        raise ValueError(
            "LABNET_GRPC_ADVERTISED_TARGET must be host:port, not a URL."
        )
    if value.startswith("["):
        closing = value.find("]")
        if closing <= 1 or value[closing + 1 : closing + 2] != ":":
            raise ValueError(
                "LABNET_GRPC_ADVERTISED_TARGET has an invalid IPv6 target."
            )
        host = value[1:closing]
        port_text = value[closing + 2 :]
    else:
        try:
            host, port_text = value.rsplit(":", 1)
        except ValueError as exc:
            raise ValueError(
                "LABNET_GRPC_ADVERTISED_TARGET must be host:port."
            ) from exc
        if ":" in host:
            raise ValueError(
                "An IPv6 LABNET_GRPC_ADVERTISED_TARGET must use [host]:port syntax."
            )

    if (
        not host
        or any(character.isspace() for character in host)
        or any(character in "/\\@?#" for character in host)
    ):
        raise ValueError(
            "LABNET_GRPC_ADVERTISED_TARGET must contain a valid host."
        )
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(
            "LABNET_GRPC_ADVERTISED_TARGET must contain a numeric port."
        ) from exc
    if not 1 <= port <= 65535:
        raise ValueError(
            "LABNET_GRPC_ADVERTISED_TARGET port must be between 1 and 65535."
        )
    return host, port


def _validate_central_server_url(value: str) -> None:
    """Require an absolute HTTP(S) URL and encrypt every remote connection."""

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("LABNET_SERVER_URL is not a valid URL.") from exc

    if parsed.scheme not in {"http", "https"} or hostname is None:
        raise ValueError("LABNET_SERVER_URL must be an absolute HTTP(S) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("LABNET_SERVER_URL cannot contain embedded credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("LABNET_SERVER_URL cannot contain a query or fragment.")
    if parsed.path not in {"", "/"}:
        raise ValueError("LABNET_SERVER_URL cannot contain a path prefix.")
    if parsed.scheme == "http" and not _is_loopback_host(hostname):
        raise ValueError(
            "A non-loopback LABNET_SERVER_URL must use HTTPS to protect "
            "credentials and lease validation."
        )


def _get_str(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _get_int(name: str, default: int, *, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return value


def _get_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= minimum:
        raise ValueError(f"{name} must be greater than {minimum}.")
    return value


def _get_path(name: str, base_directory: Path) -> Path | None:
    raw = _get_str(name)
    if raw is None:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base_directory / path).resolve()
    return path


@dataclass(frozen=True, slots=True)
class LabPCSettings:
    """Validated process configuration for one Lab PC deployment."""

    environment: str
    # None means "use the username that owns LABNET_DEVICE_MANAGER_API_KEY".
    lab_pc_id: str | None
    lab_pc_host: str
    device_inventory: str | None

    central_server_url: str
    device_manager_api_key: str | None
    ca_file: Path | None
    heartbeat_interval_seconds: float

    grpc_bind_host: str
    grpc_port: int
    grpc_advertised_target: str
    grpc_tls_server_name: str | None
    grpc_cert_file: Path | None
    grpc_key_file: Path | None
    grpc_max_receive_bytes: int
    grpc_max_send_bytes: int
    grpc_shutdown_grace_seconds: float

    stream_max_rate_hz: float
    stream_max_samples: int
    stream_max_duration_seconds: float
    stream_lease_revalidation_seconds: float
    stream_sample_timeout_seconds: float

    http_bind_host: str
    http_port: int

    device_queue_capacity: int
    device_start_timeout_seconds: float
    device_health_refresh_interval_seconds: float
    device_health_timeout_seconds: float
    default_operation_timeout_seconds: float
    health_sync_interval_seconds: float

    @property
    def central_enabled(self) -> bool:
        """A device-manager key means: register with Central, check leases."""

        return self.device_manager_api_key is not None

    @property
    def development_lease_bypass(self) -> bool:
        return not self.central_enabled

    @property
    def grpc_tls_enabled(self) -> bool:
        return self.grpc_cert_file is not None and self.grpc_key_file is not None

    @property
    def grpc_tls_mode(self) -> str:
        return "tls" if self.grpc_tls_enabled else "disabled"

    @property
    def grpc_bind_target(self) -> str:
        return _format_target(self.grpc_bind_host, self.grpc_port)

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = None,
        *,
        search_cwd: bool = True,
    ) -> "LabPCSettings":
        loaded = load_env_file(env_file, search_cwd=search_cwd)
        # Relative paths in a settings file are relative to that file.
        base_directory = loaded.parent if loaded is not None else Path.cwd()

        cert_file = _get_path("LABNET_GRPC_CERT_FILE", base_directory)
        key_file = _get_path("LABNET_GRPC_KEY_FILE", base_directory)
        tls_enabled = cert_file is not None and key_file is not None
        grpc_port = _get_int("LABNET_GRPC_PORT", 50051)
        grpc_bind_host = _get_str("LABNET_GRPC_BIND_HOST") or (
            "0.0.0.0" if tls_enabled else "127.0.0.1"
        )

        advertised_target = _get_str("LABNET_GRPC_ADVERTISED_TARGET")
        if advertised_target is None:
            if _is_wildcard_host(grpc_bind_host):
                raise ValueError(
                    "LABNET_GRPC_ADVERTISED_TARGET is required when gRPC listens "
                    "on all interfaces. Use the host:port clients dial; with TLS "
                    "the host must be in the Lab PC certificate."
                )
            advertised_target = _format_target(grpc_bind_host, grpc_port)

        lab_pc_host = _get_str("LABNET_LAB_PC_HOST")
        if lab_pc_host is None:
            lab_pc_host = _parse_grpc_target(advertised_target)[0]

        api_key = _get_str("LABNET_DEVICE_MANAGER_API_KEY")
        lab_pc_id = _get_str("LABNET_LAB_PC_ID")
        if lab_pc_id is None and api_key is None:
            lab_pc_id = "standalone-lab-pc"

        settings = cls(
            environment=os.getenv("LABNET_ENVIRONMENT", "development").strip().lower(),
            lab_pc_id=lab_pc_id,
            lab_pc_host=lab_pc_host,
            device_inventory=_get_str("LABNET_DEVICE_INVENTORY"),
            central_server_url=os.getenv(
                "LABNET_SERVER_URL", "http://127.0.0.1:8008"
            ).strip().rstrip("/"),
            device_manager_api_key=api_key,
            ca_file=_get_path("LABNET_CA_FILE", base_directory),
            heartbeat_interval_seconds=_get_float(
                "LABNET_HEARTBEAT_INTERVAL_SECONDS", 5.0
            ),
            grpc_bind_host=grpc_bind_host,
            grpc_port=grpc_port,
            grpc_advertised_target=advertised_target,
            grpc_tls_server_name=_get_str("LABNET_GRPC_TLS_SERVER_NAME"),
            grpc_cert_file=cert_file,
            grpc_key_file=key_file,
            grpc_max_receive_bytes=_get_int(
                "LABNET_GRPC_MAX_RECEIVE_BYTES", 4 * 1024 * 1024
            ),
            grpc_max_send_bytes=_get_int(
                "LABNET_GRPC_MAX_SEND_BYTES", 4 * 1024 * 1024
            ),
            grpc_shutdown_grace_seconds=_get_float(
                "LABNET_GRPC_SHUTDOWN_GRACE_SECONDS", 5.0
            ),
            stream_max_rate_hz=_get_float("LABNET_STREAM_MAX_RATE_HZ", 1000.0),
            stream_max_samples=_get_int("LABNET_STREAM_MAX_SAMPLES", 1_000_000),
            stream_max_duration_seconds=_get_float(
                "LABNET_STREAM_MAX_DURATION_SECONDS", 3600.0
            ),
            stream_lease_revalidation_seconds=_get_float(
                "LABNET_STREAM_LEASE_REVALIDATION_SECONDS", 5.0
            ),
            stream_sample_timeout_seconds=_get_float(
                "LABNET_STREAM_SAMPLE_TIMEOUT_SECONDS", 5.0
            ),
            http_bind_host=os.getenv("LABNET_HTTP_BIND_HOST", "127.0.0.1").strip(),
            http_port=_get_int("LABNET_HTTP_PORT", 9001),
            device_queue_capacity=_get_int("LABNET_DEVICE_QUEUE_CAPACITY", 128),
            device_start_timeout_seconds=_get_float(
                "LABNET_DEVICE_START_TIMEOUT_SECONDS", 10.0
            ),
            device_health_refresh_interval_seconds=_get_float(
                "LABNET_DEVICE_HEALTH_REFRESH_INTERVAL_SECONDS", 5.0
            ),
            device_health_timeout_seconds=_get_float(
                "LABNET_DEVICE_HEALTH_TIMEOUT_SECONDS", 2.0
            ),
            default_operation_timeout_seconds=_get_float(
                "LABNET_DEFAULT_OPERATION_TIMEOUT_SECONDS", 30.0
            ),
            health_sync_interval_seconds=_get_float(
                "LABNET_HEALTH_SYNC_INTERVAL_SECONDS", 1.0
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError(
                "LABNET_ENVIRONMENT must be development, test, or production."
            )

        if self.lab_pc_id is not None:
            validate_labnet_identifier(self.lab_pc_id, name="LABNET_LAB_PC_ID")
        elif not self.central_enabled:
            raise ValueError(
                "LABNET_LAB_PC_ID can be omitted only when "
                "LABNET_DEVICE_MANAGER_API_KEY is set."
            )
        if not self.lab_pc_host:
            raise ValueError("LABNET_LAB_PC_HOST cannot be empty.")

        _validate_central_server_url(self.central_server_url)

        if not 1 <= self.grpc_port <= 65535:
            raise ValueError("LABNET_GRPC_PORT must be between 1 and 65535.")
        if not 1 <= self.http_port <= 65535:
            raise ValueError("LABNET_HTTP_PORT must be between 1 and 65535.")
        if not self.grpc_bind_host:
            raise ValueError("LABNET_GRPC_BIND_HOST cannot be empty.")
        if not self.http_bind_host:
            raise ValueError("LABNET_HTTP_BIND_HOST cannot be empty.")

        if (self.grpc_cert_file is None) != (self.grpc_key_file is None):
            raise ValueError(
                "Set both LABNET_GRPC_CERT_FILE and LABNET_GRPC_KEY_FILE to "
                "enable TLS, or neither for loopback-only plaintext."
            )
        for path in (self.grpc_cert_file, self.grpc_key_file, self.ca_file):
            if path is not None and not path.is_file():
                raise ValueError(f"File does not exist: {path}")

        advertised_host, _advertised_port = _parse_grpc_target(
            self.grpc_advertised_target
        )
        if _is_wildcard_host(advertised_host):
            raise ValueError(
                "LABNET_GRPC_ADVERTISED_TARGET must be a client-reachable host, "
                "not a wildcard bind address."
            )
        if self.grpc_tls_server_name is not None and (
            len(self.grpc_tls_server_name) > 253
            or any(character.isspace() for character in self.grpc_tls_server_name)
            or any(character in "/\\@?#" for character in self.grpc_tls_server_name)
        ):
            raise ValueError(
                "LABNET_GRPC_TLS_SERVER_NAME must be a valid DNS name or IP address."
            )

        grpc_is_loopback = _is_loopback_host(self.grpc_bind_host)
        if not grpc_is_loopback and _is_loopback_host(advertised_host):
            raise ValueError(
                "A LAN-facing gRPC bind cannot advertise a loopback target."
            )
        if not self.grpc_tls_enabled and not grpc_is_loopback:
            raise ValueError(
                "Plaintext gRPC is permitted only on a loopback bind address. "
                "Set LABNET_GRPC_CERT_FILE and LABNET_GRPC_KEY_FILE for TLS."
            )

        if not _is_loopback_host(self.http_bind_host):
            raise ValueError(
                "The plaintext HTTP management plane must bind to loopback. "
                "Use an HTTPS reverse proxy before exposing it to the LAN."
            )

        if not self.central_enabled:
            if self.environment == "production":
                raise ValueError(
                    "Production requires LABNET_DEVICE_MANAGER_API_KEY so that "
                    "Central validates every lease."
                )
            if not grpc_is_loopback:
                raise ValueError(
                    "Without LABNET_DEVICE_MANAGER_API_KEY leases are not checked, "
                    "which is permitted only on a loopback gRPC bind."
                )

        if self.environment == "production" and self.device_inventory is None:
            raise ValueError(
                "LABNET_DEVICE_INVENTORY is required in production, e.g. "
                "C:/ProgramData/LabNet/site/inventories/lab_pc_01.py:create_devices."
            )


__all__ = ["LabPCSettings"]
