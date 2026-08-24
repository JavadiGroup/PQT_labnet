from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from labnet.identifiers import validate_labnet_identifier


_LAB_PC_DIRECTORY = Path(__file__).resolve().parent


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


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value.")


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


def _get_optional_path(name: str) -> Path | None:
    raw = os.getenv(name)
    if not raw:
        return None

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (_LAB_PC_DIRECTORY / path).resolve()
    return path


@dataclass(frozen=True, slots=True)
class LabPCSettings:
    """Validated process configuration for one Lab PC deployment."""

    environment: str
    lab_pc_id: str
    lab_pc_host: str

    central_enabled: bool
    central_server_url: str
    device_manager_api_key: str | None
    heartbeat_interval_seconds: float
    lease_validation_mode: str

    grpc_bind_host: str
    grpc_port: int
    grpc_advertised_target: str
    grpc_tls_server_name: str | None
    grpc_tls_mode: str
    grpc_cert_file: Path | None
    grpc_key_file: Path | None
    grpc_ca_file: Path | None
    grpc_max_receive_bytes: int
    grpc_max_send_bytes: int
    grpc_shutdown_grace_seconds: float

    stream_max_rate_hz: float
    stream_max_samples: int
    stream_max_duration_seconds: float
    stream_lease_revalidation_seconds: float
    stream_sample_timeout_seconds: float
    default_measurement_unit: str

    http_bind_host: str
    http_port: int

    device_queue_capacity: int
    device_start_timeout_seconds: float
    device_health_refresh_interval_seconds: float
    device_health_timeout_seconds: float
    default_operation_timeout_seconds: float
    health_sync_interval_seconds: float

    @property
    def grpc_bind_target(self) -> str:
        host = self.grpc_bind_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{self.grpc_port}"

    @property
    def development_lease_bypass(self) -> bool:
        return self.lease_validation_mode == "development"

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "LabPCSettings":
        path = Path(env_file) if env_file is not None else _LAB_PC_DIRECTORY / ".env"
        load_dotenv(path, override=False)

        environment = os.getenv("LABNET_ENVIRONMENT", "development").strip().lower()
        lab_pc_id = os.getenv("LABNET_LAB_PC_ID", "lab-pc-dev-01")
        lab_pc_host = os.getenv("LABNET_LAB_PC_HOST", "lab-pc-test").strip()
        grpc_bind_host = os.getenv("LABNET_GRPC_BIND_HOST", "127.0.0.1").strip()
        grpc_port = _get_int("LABNET_GRPC_PORT", 50051)
        default_advertised_host = (
            lab_pc_host
            if grpc_bind_host in {"0.0.0.0", "::", "[::]"}
            else grpc_bind_host.strip("[]")
        )
        default_advertised_target_host = (
            f"[{default_advertised_host}]"
            if ":" in default_advertised_host
            else default_advertised_host
        )

        settings = cls(
            environment=environment,
            lab_pc_id=lab_pc_id,
            lab_pc_host=lab_pc_host,
            central_enabled=_get_bool("LABNET_CENTRAL_ENABLED", False),
            central_server_url=os.getenv(
                "LABNET_SERVER_URL", "http://127.0.0.1:8008"
            ).strip().rstrip("/"),
            device_manager_api_key=os.getenv("LABNET_DEVICE_MANAGER_API_KEY"),
            heartbeat_interval_seconds=_get_float(
                "LABNET_HEARTBEAT_INTERVAL_SECONDS", 5.0
            ),
            lease_validation_mode=os.getenv(
                "LABNET_LEASE_VALIDATION_MODE", "development"
            ).strip().lower(),
            grpc_bind_host=grpc_bind_host,
            grpc_port=grpc_port,
            grpc_advertised_target=os.getenv(
                "LABNET_GRPC_ADVERTISED_TARGET",
                f"{default_advertised_target_host}:{grpc_port}",
            ).strip(),
            grpc_tls_server_name=(
                os.getenv("LABNET_GRPC_TLS_SERVER_NAME", "").strip() or None
            ),
            grpc_tls_mode=os.getenv(
                "LABNET_GRPC_TLS_MODE", "disabled"
            ).strip().lower(),
            grpc_cert_file=_get_optional_path("LABNET_GRPC_CERT_FILE"),
            grpc_key_file=_get_optional_path("LABNET_GRPC_KEY_FILE"),
            grpc_ca_file=_get_optional_path("LABNET_GRPC_CA_FILE"),
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
            default_measurement_unit=os.getenv(
                "LABNET_DEFAULT_MEASUREMENT_UNIT", "Hz"
            ).strip(),
            http_bind_host=os.getenv(
                "LABNET_HTTP_BIND_HOST", "127.0.0.1"
            ).strip(),
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

        try:
            validate_labnet_identifier(
                self.lab_pc_id,
                name="LABNET_LAB_PC_ID",
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        if not self.lab_pc_host:
            raise ValueError("LABNET_LAB_PC_HOST cannot be empty.")

        _validate_central_server_url(self.central_server_url)

        if not 1 <= self.grpc_port <= 65535:
            raise ValueError("LABNET_GRPC_PORT must be between 1 and 65535.")
        if not 1 <= self.http_port <= 65535:
            raise ValueError("LABNET_HTTP_PORT must be between 1 and 65535.")

        if self.grpc_tls_mode not in {"disabled", "tls", "mtls"}:
            raise ValueError(
                "LABNET_GRPC_TLS_MODE must be disabled, tls, or mtls."
            )

        if not self.grpc_bind_host:
            raise ValueError("LABNET_GRPC_BIND_HOST cannot be empty.")
        if not self.grpc_advertised_target:
            raise ValueError("LABNET_GRPC_ADVERTISED_TARGET cannot be empty.")
        if not self.http_bind_host:
            raise ValueError("LABNET_HTTP_BIND_HOST cannot be empty.")
        if (
            not self.default_measurement_unit
            or len(self.default_measurement_unit) > 32
            or any(ord(character) < 0x20 for character in self.default_measurement_unit)
        ):
            raise ValueError(
                "LABNET_DEFAULT_MEASUREMENT_UNIT must be 1-32 printable characters."
            )

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
        if self.grpc_tls_mode == "disabled" and not grpc_is_loopback:
            raise ValueError(
                "Plaintext gRPC is permitted only on a loopback bind address."
            )

        if self.grpc_tls_mode in {"tls", "mtls"}:
            if self.grpc_cert_file is None or self.grpc_key_file is None:
                raise ValueError("TLS mode requires certificate and key files.")
            for path in (self.grpc_cert_file, self.grpc_key_file):
                if not path.is_file():
                    raise ValueError(f"TLS file does not exist: {path}")

        if self.grpc_tls_mode == "mtls":
            if self.grpc_ca_file is None or not self.grpc_ca_file.is_file():
                raise ValueError("mTLS mode requires an existing CA certificate file.")

        if not _is_loopback_host(self.http_bind_host):
            raise ValueError(
                "The plaintext HTTP management plane must bind to loopback. "
                "Use an HTTPS reverse proxy before exposing it to the LAN."
            )

        if self.lease_validation_mode not in {"central", "development"}:
            raise ValueError(
                "LABNET_LEASE_VALIDATION_MODE must be central or development."
            )

        if self.lease_validation_mode == "development":
            if self.environment == "production" or not grpc_is_loopback:
                raise ValueError(
                    "Development lease validation is forbidden in production "
                    "and on non-loopback gRPC binds."
                )

        if self.lease_validation_mode == "central":
            if not self.central_enabled:
                raise ValueError(
                    "Central lease validation requires LABNET_CENTRAL_ENABLED=true."
                )
            if not self.device_manager_api_key:
                raise ValueError(
                    "Central lease validation requires LABNET_DEVICE_MANAGER_API_KEY."
                )

        if self.central_enabled and not self.device_manager_api_key:
            raise ValueError(
                "LABNET_CENTRAL_ENABLED=true requires LABNET_DEVICE_MANAGER_API_KEY."
            )


__all__ = ["LabPCSettings"]
