from __future__ import annotations

import os
from pathlib import Path

import pytest

from labnet.lab_pc.config import LabPCSettings


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep developer settings out, and keep env files from leaking out."""

    # load_dotenv writes into os.environ; give each test a discarded copy.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def tls_files(tmp_path: Path) -> tuple[Path, Path]:
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.key"
    certificate.write_text("test", encoding="utf-8")
    private_key.write_text("test", encoding="utf-8")
    return certificate, private_key


def settings_from_env() -> LabPCSettings:
    return LabPCSettings.from_env(search_cwd=False)


def test_no_settings_means_standalone_loopback_development() -> None:
    settings = settings_from_env()

    assert settings.environment == "development"
    assert settings.central_enabled is False
    assert settings.development_lease_bypass is True
    assert settings.grpc_tls_enabled is False
    assert settings.grpc_bind_target == "127.0.0.1:50051"
    assert settings.grpc_advertised_target == "127.0.0.1:50051"
    assert settings.lab_pc_host == "127.0.0.1"
    assert settings.http_bind_host == "127.0.0.1"
    assert settings.http_port == 9001
    assert settings.device_inventory is None


def test_api_key_enables_central_registration_and_lease_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "key")
    monkeypatch.setenv("LABNET_LAB_PC_ID", "lab-pc-01")

    settings = settings_from_env()

    assert settings.central_enabled is True
    assert settings.development_lease_bypass is False


def test_lab_pc_id_may_be_omitted_when_a_key_identifies_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "key")

    assert settings_from_env().lab_pc_id is None


@pytest.mark.parametrize("manager_id", ["rack/manager", "rack manager"])
def test_lab_pc_id_rejects_values_outside_the_shared_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
    manager_id: str,
) -> None:
    monkeypatch.setenv("LABNET_LAB_PC_ID", manager_id)

    with pytest.raises(ValueError, match="LABNET_LAB_PC_ID"):
        settings_from_env()


def test_cert_and_key_enable_tls_and_listen_on_the_network(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[Path, Path],
) -> None:
    certificate, private_key = tls_files
    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "key")
    monkeypatch.setenv("LABNET_SERVER_URL", "https://192.0.2.10")
    monkeypatch.setenv("LABNET_GRPC_CERT_FILE", str(certificate))
    monkeypatch.setenv("LABNET_GRPC_KEY_FILE", str(private_key))
    monkeypatch.setenv("LABNET_GRPC_ADVERTISED_TARGET", "192.0.2.20:50051")

    settings = settings_from_env()

    assert settings.grpc_tls_enabled is True
    assert settings.grpc_tls_mode == "tls"
    assert settings.grpc_bind_host == "0.0.0.0"
    assert settings.lab_pc_host == "192.0.2.20"


def test_network_bind_requires_an_advertised_target(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[Path, Path],
) -> None:
    certificate, private_key = tls_files
    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "key")
    monkeypatch.setenv("LABNET_GRPC_CERT_FILE", str(certificate))
    monkeypatch.setenv("LABNET_GRPC_KEY_FILE", str(private_key))

    with pytest.raises(ValueError, match="ADVERTISED_TARGET is required"):
        settings_from_env()


def test_only_one_tls_file_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[Path, Path],
) -> None:
    monkeypatch.setenv("LABNET_GRPC_CERT_FILE", str(tls_files[0]))

    with pytest.raises(ValueError, match="Set both LABNET_GRPC_CERT_FILE"):
        settings_from_env()


def test_plaintext_grpc_rejects_non_loopback_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "key")
    monkeypatch.setenv("LABNET_GRPC_BIND_HOST", "192.0.2.20")

    with pytest.raises(ValueError, match="Plaintext gRPC is permitted only"):
        settings_from_env()


def test_standalone_mode_is_rejected_off_loopback(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[Path, Path],
) -> None:
    monkeypatch.setenv("LABNET_GRPC_CERT_FILE", str(tls_files[0]))
    monkeypatch.setenv("LABNET_GRPC_KEY_FILE", str(tls_files[1]))
    monkeypatch.setenv("LABNET_GRPC_ADVERTISED_TARGET", "192.0.2.20:50051")

    with pytest.raises(ValueError, match="leases are not checked"):
        settings_from_env()


def test_production_requires_a_key_and_an_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LABNET_ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="Production requires LABNET_DEVICE_MANAGER"):
        settings_from_env()

    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "key")
    with pytest.raises(ValueError, match="LABNET_DEVICE_INVENTORY is required"):
        settings_from_env()

    monkeypatch.setenv("LABNET_DEVICE_INVENTORY", "site.inventory:create_devices")
    assert settings_from_env().environment == "production"


@pytest.mark.parametrize("bind_host", ["::1", "[::1]"])
def test_ipv6_loopback_default_advertised_target_is_bracketed(
    monkeypatch: pytest.MonkeyPatch,
    bind_host: str,
) -> None:
    monkeypatch.setenv("LABNET_GRPC_BIND_HOST", bind_host)

    assert settings_from_env().grpc_advertised_target == "[::1]:50051"


def test_lan_bind_cannot_advertise_loopback(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[Path, Path],
) -> None:
    monkeypatch.setenv("LABNET_DEVICE_MANAGER_API_KEY", "key")
    monkeypatch.setenv("LABNET_GRPC_CERT_FILE", str(tls_files[0]))
    monkeypatch.setenv("LABNET_GRPC_KEY_FILE", str(tls_files[1]))
    monkeypatch.setenv("LABNET_GRPC_ADVERTISED_TARGET", "127.0.0.1:50051")

    with pytest.raises(ValueError, match="cannot advertise a loopback"):
        settings_from_env()


def test_non_finite_intervals_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LABNET_HEARTBEAT_INTERVAL_SECONDS", "nan")

    with pytest.raises(ValueError, match="must be greater"):
        settings_from_env()


def test_env_file_is_loaded_and_relative_paths_follow_it(tmp_path: Path) -> None:
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    (config_directory / "ca.pem").write_text("ca", encoding="utf-8")
    env_file = config_directory / "lab-pc.env"
    env_file.write_text(
        "LABNET_LAB_PC_ID=lab-pc-from-file\nLABNET_CA_FILE=ca.pem\n",
        encoding="utf-8",
    )

    settings = LabPCSettings.from_env(env_file)

    assert settings.lab_pc_id == "lab-pc-from-file"
    assert settings.ca_file == (config_directory / "ca.pem").resolve()


def test_process_environment_overrides_the_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "lab-pc.env"
    env_file.write_text("LABNET_LAB_PC_ID=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LABNET_LAB_PC_ID", "from-process")

    assert LabPCSettings.from_env(env_file).lab_pc_id == "from-process"


def test_dot_env_in_working_directory_is_used_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("LABNET_LAB_PC_ID=from-cwd\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert LabPCSettings.from_env().lab_pc_id == "from-cwd"


def test_explicit_env_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Settings file not found"):
        LabPCSettings.from_env(tmp_path / "missing.env")
