from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from labnet import AsyncLabClient, LabClient
from labnet.certs import create_ca
from labnet.client.core import grpc_client
from labnet.devices.core import remote
from labnet_sim._generated.contracts import DUMMY_DEVICE_CONTRACT


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray ./.env


@pytest.fixture
def ca_file(tmp_path: Path) -> Path:
    create_ca(tmp_path / "ca", passphrase=None, days=1)
    return tmp_path / "ca" / "labnet-ca.pem"


def test_from_env_reads_an_env_file(tmp_path: Path, ca_file: Path) -> None:
    env_file = tmp_path / "researcher.env"
    env_file.write_text(
        f"LABNET_SERVER_URL=https://192.168.50.10\nLABNET_API_KEY=secret\n"
        f"LABNET_CA_FILE={ca_file.as_posix()}\n",
        encoding="utf-8",
    )

    client = LabClient.from_env(env_file, authenticate_on_start=False)
    try:
        assert client.server_url == "https://192.168.50.10"
        assert client.api_key == "secret"
        assert client.grpc_tls is not None
        assert client.grpc_tls.root_certificates == ca_file.read_bytes()
    finally:
        client.close()


def test_labnet_env_file_names_the_settings_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "researcher.env"
    env_file.write_text("LABNET_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LABNET_ENV_FILE", str(env_file))

    client = LabClient.from_env(authenticate_on_start=False)
    try:
        assert client.api_key == "from-file"
    finally:
        client.close()


def test_from_env_without_a_key_explains_what_to_set() -> None:
    with pytest.raises(ValueError, match="LABNET_API_KEY is not set"):
        LabClient.from_env()


def test_old_grpc_ca_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LABNET_API_KEY", "secret")
    monkeypatch.setenv("LABNET_GRPC_CA_FILE", "labnet-ca.pem")

    with pytest.raises(ValueError, match="Set LABNET_CA_FILE"):
        LabClient.from_env()


def test_missing_ca_file_fails_immediately() -> None:
    with pytest.raises(FileNotFoundError, match="CA file not found"):
        LabClient("https://192.168.50.10", "secret", ca_file="missing.pem")


def test_without_a_ca_there_is_no_default_grpc_tls() -> None:
    client = LabClient("http://127.0.0.1:8008", "secret", authenticate_on_start=False)
    try:
        assert client.grpc_tls is None
    finally:
        client.close()


def _central(device_target: str = "192.168.50.20:50051") -> httpx.MockTransport:
    """Grant one lease, then accept its release."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200, json={"lease_id": "lease-1", "state": "released"})
        return httpx.Response(
            201,
            json={
                "lease": {
                    "lease_id": "lease-1",
                    "device_id": "dummy-1",
                    "user": "researcher",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "state": "active",
                },
                "device": {
                    "device_id": "dummy-1",
                    "manager_id": "lab-pc-01",
                    "device_type": "dummy_device",
                    "endpoint": {
                        "transport": "grpc",
                        "target": device_target,
                        "api_version": "labnet.v3",
                        "secure": True,
                    },
                },
            },
        )

    return httpx.MockTransport(handler)


class _Captured(Exception):
    """Stops device setup once the TLS argument has been observed."""


def test_sync_devices_inherit_the_client_ca(
    monkeypatch: pytest.MonkeyPatch,
    ca_file: Path,
) -> None:
    seen: dict[str, Any] = {}

    def capture(grant: Any, *, tls: Any = None, **kwargs: Any) -> None:
        seen["tls"] = tls
        raise _Captured

    monkeypatch.setattr(remote, "ContractDeviceGrpcClient", capture)
    client = LabClient(
        "https://central.example", "secret",
        ca_file=ca_file, authenticate_on_start=False, transport=_central(),
    )
    try:
        with pytest.raises(_Captured):
            remote.RemoteDeviceBinding(client, device_id="dummy-1", auto_renew=False, _contract=DUMMY_DEVICE_CONTRACT)
        assert seen["tls"] is client.grpc_tls
    finally:
        client.close()


@pytest.mark.asyncio
async def test_async_devices_inherit_the_client_ca(
    monkeypatch: pytest.MonkeyPatch,
    ca_file: Path,
) -> None:
    seen: dict[str, Any] = {}

    def capture(grant: Any, *, tls: Any = None, **kwargs: Any) -> None:
        seen["tls"] = tls
        raise _Captured

    monkeypatch.setattr(grpc_client, "AsyncContractDeviceClient", capture)
    client = AsyncLabClient(
        "https://central.example", "secret",
        ca_file=ca_file, authenticate_on_start=False, transport=_central(),
    )
    try:
        with pytest.raises(_Captured):
            await client.acquire_device(DUMMY_DEVICE_CONTRACT, "dummy-1", auto_renew=False)
        assert seen["tls"] is client.grpc_tls
    finally:
        await client.aclose()
