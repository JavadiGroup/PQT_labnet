from __future__ import annotations

import pytest

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


def test_mtls_requires_client_ca() -> None:
    with pytest.raises(ValueError, match="client CA"):
        TLSConfig(
            mode=TLSMode.MTLS,
            certificate_chain_file="server.crt",
            private_key_file="server.key",
        )
