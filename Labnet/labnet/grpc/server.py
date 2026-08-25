"""Lifecycle and TLS configuration for the Labnet asynchronous gRPC server."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from labnet.device_manager.admission import (
    AdmissionGate,
    AdmissionSnapshot,
)

from .generated.device_contracts import DEVICE_CONTRACTS, DeviceContract
from .services import DeviceExecutorProtocol, PatternDeviceGrpcService, StreamPolicy


class TLSMode(str, Enum):
    DISABLED = "disabled"
    TLS = "tls"
    MTLS = "mtls"


@dataclass(frozen=True, slots=True)
class TLSConfig:
    """Certificate paths and peer-authentication policy for the gRPC listener."""

    mode: TLSMode | str = TLSMode.DISABLED
    certificate_chain_file: Path | str | None = None
    private_key_file: Path | str | None = None
    client_ca_file: Path | str | None = None

    def __post_init__(self) -> None:
        try:
            mode = self.mode if isinstance(self.mode, TLSMode) else TLSMode(self.mode)
        except ValueError as exc:
            choices = ", ".join(item.value for item in TLSMode)
            raise ValueError(f"TLS mode must be one of: {choices}.") from exc
        object.__setattr__(self, "mode", mode)

        for field_name in (
            "certificate_chain_file",
            "private_key_file",
            "client_ca_file",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, Path(value))

        if mode is TLSMode.DISABLED:
            return
        if self.certificate_chain_file is None or self.private_key_file is None:
            raise ValueError(
                "TLS and mTLS modes require a certificate chain and private key."
            )
        if mode is TLSMode.MTLS and self.client_ca_file is None:
            raise ValueError("mTLS mode requires a client CA certificate bundle.")


@dataclass(frozen=True, slots=True)
class GrpcServerConfig:
    bind: str = "127.0.0.1:50051"
    tls: TLSConfig = field(default_factory=TLSConfig)
    maximum_receive_message_bytes: int = 4 * 1024 * 1024
    maximum_send_message_bytes: int = 4 * 1024 * 1024
    maximum_concurrent_rpcs: int = 128
    graceful_shutdown_seconds: float = 10.0

    def __post_init__(self) -> None:
        bind = self.bind.strip()
        if not bind:
            raise ValueError("gRPC bind address cannot be empty.")
        object.__setattr__(self, "bind", bind)
        if not isinstance(self.tls, TLSConfig):
            raise TypeError("tls must be a TLSConfig instance.")
        if self.maximum_receive_message_bytes <= 0:
            raise ValueError("maximum_receive_message_bytes must be positive.")
        if self.maximum_send_message_bytes <= 0:
            raise ValueError("maximum_send_message_bytes must be positive.")
        if self.maximum_concurrent_rpcs <= 0:
            raise ValueError("maximum_concurrent_rpcs must be positive.")
        if self.graceful_shutdown_seconds < 0:
            raise ValueError("graceful_shutdown_seconds cannot be negative.")
        if self.tls.mode is TLSMode.DISABLED and not is_loopback_bind(bind):
            raise ValueError(
                "Plaintext gRPC is permitted only on a loopback bind. Configure "
                "TLS or mTLS before exposing a Lab PC on the network."
            )


class LabnetGrpcServer:
    """Owns a configured grpc.aio server and its standard health service."""

    def __init__(
        self,
        executor: DeviceExecutorProtocol,
        *,
        config: GrpcServerConfig | None = None,
        stream_policy: StreamPolicy | None = None,
        default_operation_timeout_seconds: float = 30.0,
        contracts: Sequence[DeviceContract] | None = None,
        admission_gate: AdmissionGate | None = None,
    ) -> None:
        self.config = config or GrpcServerConfig()
        self._admission_gate = admission_gate or AdmissionGate()
        selected_contracts = tuple(
            DEVICE_CONTRACTS if contracts is None else contracts
        )
        if not selected_contracts:
            raise ValueError("At least one generated device contract is required.")
        services: dict[str, PatternDeviceGrpcService] = {}
        service_names: set[str] = set()
        registrations: list[tuple[DeviceContract, PatternDeviceGrpcService]] = []
        for contract in selected_contracts:
            if contract.device_type in services:
                raise ValueError(
                    f"Duplicate generated device type: {contract.device_type}."
                )
            if contract.service_full_name in service_names:
                raise ValueError(
                    "Duplicate generated gRPC service name: "
                    f"{contract.service_full_name}."
                )
            service = PatternDeviceGrpcService(
                executor,
                contract,
                stream_policy=stream_policy,
                default_operation_timeout_seconds=(
                    default_operation_timeout_seconds
                ),
                admission_gate=self._admission_gate,
            )
            services[contract.device_type] = service
            service_names.add(contract.service_full_name)
            registrations.append((contract, service))

        # Validate all generated mappings before allocating a native gRPC
        # server so an unsupported contract cannot leak an unclosed listener.
        self._health = health.aio.HealthServicer()
        self._server = grpc.aio.server(
            options=(
                (
                    "grpc.max_receive_message_length",
                    self.config.maximum_receive_message_bytes,
                ),
                (
                    "grpc.max_send_message_length",
                    self.config.maximum_send_message_bytes,
                ),
            ),
            maximum_concurrent_rpcs=self.config.maximum_concurrent_rpcs,
        )
        for contract, service in registrations:
            contract.add_servicer_to_server(service, self._server)

        self._device_services = MappingProxyType(services)
        self._service_names = tuple(sorted(service_names))
        # Compatibility for the original one-family tests and applications.
        # New code should use ``device_services`` keyed by generated device type.
        self.device_service = services.get(
            "dummy_device",
            next(iter(services.values())),
        )
        health_pb2_grpc.add_HealthServicer_to_server(
            self._health,
            self._server,
        )

        self.bound_port = self._add_listener()
        self._started = False
        self._stopped = False
        self._serving = False
        self._stop_lock = asyncio.Lock()

    @property
    def raw_server(self) -> grpc.aio.Server:
        """Return the underlying server for advanced integration and testing."""

        return self._server

    @property
    def started(self) -> bool:
        return self._started

    @property
    def serving(self) -> bool:
        return self._serving

    @property
    def device_services(self) -> Mapping[str, PatternDeviceGrpcService]:
        """Return registered generated services keyed by device type."""

        return self._device_services

    @property
    def service_names(self) -> tuple[str, ...]:
        """Return the fully qualified gRPC services advertised by health."""

        return self._service_names

    @property
    def admission_gate(self) -> AdmissionGate:
        return self._admission_gate

    def activity_snapshot(self) -> AdmissionSnapshot:
        return self._admission_gate.snapshot()

    async def start(self) -> None:
        if self._stopped:
            raise RuntimeError("A stopped gRPC server cannot be restarted.")
        if self._started:
            return
        await self._server.start()
        self._started = True
        await self.set_serving(self._admission_gate.accepting)

    async def begin_drain(self) -> AdmissionSnapshot:
        snapshot = await self._admission_gate.begin_drain()
        if self._started:
            await self.set_serving(False)
        return snapshot

    async def wait_quiescent(
        self, *, timeout: float | None = None
    ) -> AdmissionSnapshot:
        return await self._admission_gate.wait_idle(timeout=timeout)

    async def resume_admission(self) -> AdmissionSnapshot:
        return await self._admission_gate.resume()

    async def set_serving(self, serving: bool) -> None:
        """Update overall and every generated device service health entry."""

        if self._stopped and serving:
            raise RuntimeError("A stopped gRPC server cannot become serving.")
        status = (
            health_pb2.HealthCheckResponse.SERVING
            if serving
            else health_pb2.HealthCheckResponse.NOT_SERVING
        )
        await self._health.set("", status)
        for service_name in self._service_names:
            await self._health.set(service_name, status)
        self._serving = serving

    async def stop(self, grace: float | None = None) -> None:
        async with self._stop_lock:
            if self._stopped:
                return
            health_error: BaseException | None = None
            if self._started:
                try:
                    await self.set_serving(False)
                except BaseException as exc:
                    health_error = exc
                try:
                    await self._health.enter_graceful_shutdown()
                except BaseException as exc:
                    if health_error is None:
                        health_error = exc

            stop_error: BaseException | None = None
            try:
                await self._server.stop(
                    self.config.graceful_shutdown_seconds
                    if grace is None
                    else grace
                )
            except BaseException as exc:
                stop_error = exc
            else:
                # Listener cleanup is the terminal lifecycle event. Health is
                # advisory and must never prevent the raw server from stopping.
                self._started = False
                self._serving = False
                self._stopped = True

            if stop_error is not None:
                if health_error is not None:
                    raise stop_error from health_error
                raise stop_error
            if health_error is not None:
                raise health_error

    async def wait_for_termination(self, timeout: float | None = None) -> bool:
        return await self._server.wait_for_termination(timeout=timeout)

    async def __aenter__(self) -> LabnetGrpcServer:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.stop()

    def _add_listener(self) -> int:
        if self.config.tls.mode is TLSMode.DISABLED:
            bound_port = self._server.add_insecure_port(self.config.bind)
        else:
            credentials = _server_credentials(self.config.tls)
            bound_port = self._server.add_secure_port(
                self.config.bind,
                credentials,
            )
        if bound_port == 0:
            raise RuntimeError(
                f"gRPC could not bind listener {self.config.bind!r}."
            )
        return bound_port


def is_loopback_bind(bind: str) -> bool:
    """Return whether a server bind is constrained to the local machine."""

    bind = bind.strip()
    if bind.startswith("unix:"):
        return True

    if bind.startswith("["):
        closing_bracket = bind.find("]")
        if closing_bracket < 0:
            return False
        host = bind[1:closing_bracket]
    else:
        try:
            host, _port = bind.rsplit(":", 1)
        except ValueError:
            return False

    host = host.strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _server_credentials(config: TLSConfig) -> grpc.ServerCredentials:
    assert config.certificate_chain_file is not None
    assert config.private_key_file is not None

    certificate_chain = _read_required_file(
        config.certificate_chain_file,
        "certificate chain",
    )
    private_key = _read_required_file(config.private_key_file, "private key")
    client_ca = (
        _read_required_file(config.client_ca_file, "client CA bundle")
        if config.client_ca_file is not None
        else None
    )
    return grpc.ssl_server_credentials(
        ((private_key, certificate_chain),),
        root_certificates=client_ca,
        require_client_auth=config.mode is TLSMode.MTLS,
    )


def _read_required_file(path: Path, description: str) -> bytes:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Could not read TLS {description} file {path}.") from exc
    if not value:
        raise ValueError(f"TLS {description} file {path} is empty.")
    return value


__all__ = [
    "GrpcServerConfig",
    "LabnetGrpcServer",
    "TLSConfig",
    "TLSMode",
    "is_loopback_bind",
]
