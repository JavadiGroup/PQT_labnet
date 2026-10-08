from __future__ import annotations

from pathlib import Path

import pytest

from labnet.client import GrpcTlsConfig
from labnet.grpc import GrpcServerConfig, TLSConfig, TLSMode
from labnet.grpc.server import is_loopback_bind


@pytest.mark.parametrize(
    "bind",
    ["127.0.0.1:50051", "127.10.20.30:0", "localhost:50051", "[::1]:0"],
)
def test_plaintext_loopback_binds_are_allowed(bind: str) -> None:
    assert is_loopback_bind(bind)
    GrpcServerConfig(bind=bind)


@pytest.mark.parametrize(
    "bind",
    ["0.0.0.0:50051", "192.168.1.10:50051", "[::]:50051", "lab-pc:50051"],
)
def test_plaintext_network_binds_are_rejected(bind: str) -> None:
    assert not is_loopback_bind(bind)
    with pytest.raises(ValueError, match="loopback"):
        GrpcServerConfig(bind=bind)


def test_tls_requires_server_identity_files() -> None:
    with pytest.raises(ValueError, match="certificate chain"):
        TLSConfig(mode=TLSMode.TLS)


def test_unknown_tls_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="disabled, tls"):
        TLSConfig(mode="invalid")


def test_client_tls_config_loads_public_ca_only(tmp_path: Path) -> None:
    ca_file = tmp_path / "labnet-ca.pem"
    ca_file.write_bytes(b"public-ca-certificate")

    config = GrpcTlsConfig.from_files(root_certificates=ca_file)

    assert config.root_certificates == b"public-ca-certificate"
