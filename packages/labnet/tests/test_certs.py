from __future__ import annotations

import ipaddress
from pathlib import Path

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from grpc_health.v1 import health_pb2, health_pb2_grpc

from labnet.certs import (
    create_ca,
    describe_certificate,
    issue_server_certificate,
    main as certs_cli,
)
from labnet.grpc import GrpcServerConfig, LabnetGrpcServer, TLSConfig


@pytest.fixture
def ca_dir(tmp_path: Path) -> Path:
    create_ca(tmp_path, passphrase=None, days=30)
    return tmp_path


def _load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_ca_is_a_signing_only_root(ca_dir: Path) -> None:
    ca = _load(ca_dir / "labnet-ca.pem")

    constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints)
    usage = ca.extensions.get_extension_for_class(x509.KeyUsage).value
    assert constraints.critical and constraints.value.ca
    assert constraints.value.path_length == 0
    assert usage.key_cert_sign and usage.crl_sign and not usage.digital_signature


def test_ca_key_can_be_encrypted(tmp_path: Path) -> None:
    create_ca(tmp_path, passphrase=b"correct horse")

    with pytest.raises(TypeError):
        serialization.load_pem_private_key(
            (tmp_path / "labnet-ca.key").read_bytes(), password=None
        )
    issue_server_certificate(
        tmp_path, "lab-pc-01", ips=["192.168.50.20"], passphrase=b"correct horse"
    )


def test_server_certificate_matches_the_deployment_profile(ca_dir: Path) -> None:
    issue_server_certificate(ca_dir, "lab-pc-01", ips=["192.168.50.20"])
    certificate = _load(ca_dir / "lab-pc-01.crt")
    extensions = certificate.extensions

    assert not extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    usage = extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and usage.key_encipherment and not usage.key_cert_sign
    assert extensions.get_extension_for_class(x509.ExtendedKeyUsage).value == x509.ExtendedKeyUsage(
        [ExtendedKeyUsageOID.SERVER_AUTH]
    )
    names = extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.IPAddress) == [
        ipaddress.ip_address("192.168.50.20")
    ]
    certificate.verify_directly_issued_by(_load(ca_dir / "labnet-ca.pem"))
    summary, ok = describe_certificate(ca_dir / "lab-pc-01.crt", ca_dir / "labnet-ca.pem")
    assert ok and "IP:192.168.50.20" in summary


def test_issue_refuses_to_overwrite_and_needs_a_name(ca_dir: Path) -> None:
    issue_server_certificate(ca_dir, "central", ips=["192.168.50.10"])

    with pytest.raises(FileExistsError):
        issue_server_certificate(ca_dir, "central", ips=["192.168.50.10"])
    with pytest.raises(ValueError, match="at least one"):
        issue_server_certificate(ca_dir, "nameless")


def test_show_reports_a_certificate_from_another_ca(tmp_path: Path) -> None:
    create_ca(tmp_path / "a", passphrase=None)
    create_ca(tmp_path / "b", passphrase=None)
    issue_server_certificate(tmp_path / "a", "central", ips=["192.168.50.10"])

    summary, ok = describe_certificate(
        tmp_path / "a" / "central.crt", tmp_path / "b" / "labnet-ca.pem"
    )

    assert not ok and "NOT issued by" in summary


def test_cli_creates_the_full_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    certs_cli(["init-ca", "--dir", str(tmp_path), "--no-passphrase"])
    certs_cli(["issue", "central", "--dir", str(tmp_path), "--ip", "192.168.50.10"])
    certs_cli(["show", str(tmp_path / "central.crt"), "--ca", str(tmp_path / "labnet-ca.pem")])

    output = capsys.readouterr().out
    assert "never copy it off this computer" in output
    assert "Signature: valid" in output
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "central.crt", "central.key", "labnet-ca.key", "labnet-ca.pem",
    ]


async def _health_status(port: int, root_certificates: bytes) -> int:
    credentials = grpc.ssl_channel_credentials(root_certificates=root_certificates)
    async with grpc.aio.secure_channel(f"127.0.0.1:{port}", credentials) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        response = await stub.Check(health_pb2.HealthCheckRequest(), timeout=5)
        return response.status


@pytest.mark.asyncio
async def test_issued_certificate_serves_grpc_tls(tmp_path: Path) -> None:
    create_ca(tmp_path / "ca", passphrase=None)
    create_ca(tmp_path / "other", passphrase=None)
    issue_server_certificate(tmp_path / "ca", "lab-pc", ips=["127.0.0.1"])
    server = LabnetGrpcServer(
        executor=object(),  # health checks never reach the device executor
        config=GrpcServerConfig(
            bind="127.0.0.1:0",
            tls=TLSConfig(
                mode="tls",
                certificate_chain_file=tmp_path / "ca" / "lab-pc.crt",
                private_key_file=tmp_path / "ca" / "lab-pc.key",
            ),
        ),
    )
    await server.start()
    try:
        trusted = (tmp_path / "ca" / "labnet-ca.pem").read_bytes()
        assert await _health_status(server.bound_port, trusted) == (
            health_pb2.HealthCheckResponse.SERVING
        )

        untrusted = (tmp_path / "other" / "labnet-ca.pem").read_bytes()
        with pytest.raises(grpc.aio.AioRpcError) as failure:
            await _health_status(server.bound_port, untrusted)
        assert failure.value.code() == grpc.StatusCode.UNAVAILABLE
    finally:
        await server.stop(0)
