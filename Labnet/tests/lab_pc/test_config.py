from __future__ import annotations

import os
from pathlib import Path

import pytest

from lab_pc.config import LabPCSettings


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent developer or CI configuration from leaking into these tests."""

    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def missing_env_file(tmp_path: Path) -> Path:
    return tmp_path / "does-not-exist.env"


def test_local_development_defaults_are_safe(missing_env_file: Path) -> None:
    settings = LabPCSettings.from_env(missing_env_file)

    assert settings.environment == "development"
    assert settings.central_enabled is False
    assert settings.development_lease_bypass is True
    assert settings.grpc_bind_host == "127.0.0.1"
    assert settings.grpc_bind_target == "127.0.0.1:50051"
    assert settings.grpc_advertised_target == "127.0.0.1:50051"
    assert settings.grpc_tls_mode == "disabled"
    assert settings.http_bind_host == "127.0.0.1"
    assert settings.http_port == 9001
    assert settings.stream_max_rate_hz == 1000.0
    assert settings.default_measurement_unit == "Hz"
    assert settings.device_start_timeout_seconds == 10.0


def test_plaintext_grpc_rejects_non_loopback_bind(
    monkeypatch: pytest.MonkeyPatch,
    missing_env_file: Path,
) -> None:
    monkeypatch.setenv("LABNET_GRPC_BIND_HOST", "0.0.0.0")

    with pytest.raises(
        ValueError,
        match="Plaintext gRPC is permitted only on a loopback bind address",
    ):
        LabPCSettings.from_env(missing_env_file)


def test_tls_requires_certificate_and_key(
    monkeypatch: pytest.MonkeyPatch,
    missing_env_file: Path,
) -> None:
    monkeypatch.setenv("LABNET_GRPC_TLS_MODE", "tls")

    with pytest.raises(
        ValueError,
        match="TLS mode requires certificate and key files",
    ):
        LabPCSettings.from_env(missing_env_file)


def test_non_finite_intervals_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    missing_env_file: Path,
) -> None:
    monkeypatch.setenv("LABNET_HEARTBEAT_INTERVAL_SECONDS", "nan")

    with pytest.raises(ValueError, match="must be greater"):
        LabPCSettings.from_env(missing_env_file)


def test_lan_bind_cannot_advertise_loopback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_env_file: Path,
) -> None:
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.key"
    certificate.write_text("test", encoding="utf-8")
    private_key.write_text("test", encoding="utf-8")
    monkeypatch.setenv("LABNET_GRPC_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("LABNET_GRPC_ADVERTISED_TARGET", "127.0.0.1:50051")
    monkeypatch.setenv("LABNET_GRPC_TLS_MODE", "tls")
    monkeypatch.setenv("LABNET_GRPC_CERT_FILE", str(certificate))
    monkeypatch.setenv("LABNET_GRPC_KEY_FILE", str(private_key))
    monkeypatch.setenv("LABNET_LEASE_VALIDATION_MODE", "central")
    monkeypatch.setenv("LABNET_CENTRAL_ENABLED", "true")
    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "test-key")

    with pytest.raises(ValueError, match="cannot advertise a loopback"):
        LabPCSettings.from_env(missing_env_file)
