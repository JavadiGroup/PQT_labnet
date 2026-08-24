from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from labnet.device_manager import (
    DevelopmentLeaseValidator,
    DeviceCatalog,
    DeviceManager,
    DeviceSnapshot,
    EndpointDescriptor,
    HttpCentralRegistryClient,
    HttpLeaseValidator,
    ManagerState,
)
from labnet.devices.core import PhysicalDevice
from labnet.grpc import (
    GrpcServerConfig,
    LabnetGrpcServer,
    StreamPolicy,
    TLSConfig,
)

from .config import LabPCSettings
from .device_inventory import create_devices


LOGGER = logging.getLogger(__name__)
DeviceFactory = Callable[[], Sequence[PhysicalDevice]]


@dataclass(slots=True)
class LabPCRuntime:
    """Live process components exposed to the local management plane."""

    settings: LabPCSettings
    manager: DeviceManager
    grpc_server: LabnetGrpcServer

    @property
    def components_ready(self) -> bool:
        snapshots = self.manager.snapshots()
        return _components_ready(
            settings=self.settings,
            manager_state=self.manager.state,
            central_registered=self.manager.registered,
            snapshots=snapshots,
        )

    @property
    def ready(self) -> bool:
        return (
            self.components_ready
            and self.grpc_server.started
            and self.grpc_server.serving
        )


def create_app(
    *,
    settings: LabPCSettings | None = None,
    device_factory: DeviceFactory = create_devices,
) -> FastAPI:
    """Create the HTTP management app and its gRPC/device lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings = settings or LabPCSettings.from_env()
        manager: DeviceManager | None = None
        grpc_server: LabnetGrpcServer | None = None
        health_sync_task: asyncio.Task[None] | None = None
        device_refresh_task: asyncio.Task[None] | None = None
        try:
            devices = tuple(device_factory())
            manager = _create_device_manager(resolved_settings, devices)
            grpc_server = _create_grpc_server(resolved_settings, manager)
            runtime = LabPCRuntime(
                settings=resolved_settings,
                manager=manager,
                grpc_server=grpc_server,
            )
            app.state.runtime = runtime

            startup_report = await manager.start()
            await grpc_server.start()
            await grpc_server.set_serving(runtime.components_ready)
            health_sync_task = asyncio.create_task(
                _health_sync_loop(runtime),
                name="labnet-grpc-health-sync",
            )
            device_refresh_task = asyncio.create_task(
                _device_refresh_loop(runtime),
                name="labnet-device-health-refresh",
            )

            LOGGER.info(
                "Lab PC started: grpc=%s http=%s:%s ready_devices=%s failed_devices=%s",
                resolved_settings.grpc_bind_target,
                resolved_settings.http_bind_host,
                resolved_settings.http_port,
                startup_report.ready_device_ids,
                startup_report.failed_device_ids,
            )
            yield
        finally:
            if device_refresh_task is not None:
                device_refresh_task.cancel()
                try:
                    await device_refresh_task
                except asyncio.CancelledError:
                    pass
            if health_sync_task is not None:
                health_sync_task.cancel()
                try:
                    await health_sync_task
                except asyncio.CancelledError:
                    pass
            if grpc_server is not None:
                try:
                    if grpc_server.started:
                        await grpc_server.set_serving(False)
                    await grpc_server.stop()
                except Exception:
                    LOGGER.exception("The gRPC server did not stop cleanly.")
            if manager is not None:
                try:
                    await manager.shutdown()
                except Exception:
                    LOGGER.exception("The device manager did not stop cleanly.")

    app = FastAPI(
        title="Labnet Lab-PC Management Plane",
        version="0.2.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    @app.get("/health/live")
    async def liveness() -> dict[str, object]:
        return {
            "status": "alive",
            "service": "labnet-lab-pc",
        }

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        runtime: LabPCRuntime = app.state.runtime
        snapshots = runtime.manager.snapshots()
        manager_state = runtime.manager.state
        central_registered = runtime.manager.registered
        last_central_error = runtime.manager.last_central_error
        grpc_started = runtime.grpc_server.started
        grpc_serving = runtime.grpc_server.serving
        ready = (
            _components_ready(
                settings=runtime.settings,
                manager_state=manager_state,
                central_registered=central_registered,
                snapshots=snapshots,
            )
            and grpc_started
            and grpc_serving
        )
        payload = _readiness_payload(
            runtime,
            snapshots=snapshots,
            ready=ready,
            grpc_serving=grpc_serving,
            central_registered=central_registered,
            last_central_error=last_central_error,
        )
        return JSONResponse(
            payload,
            status_code=200 if ready else 503,
        )

    return app


def _create_device_manager(
    settings: LabPCSettings,
    devices: Sequence[PhysicalDevice],
) -> DeviceManager:
    # Reject duplicate or malformed IDs before constructing HTTP clients.
    DeviceCatalog(devices)

    if settings.development_lease_bypass:
        lease_validator = DevelopmentLeaseValidator(allow_insecure=True)
    else:
        assert settings.device_manager_api_key is not None
        lease_validator = HttpLeaseValidator(
            server_url=settings.central_server_url,
            api_key=settings.device_manager_api_key,
        )

    central_client = None
    if settings.central_enabled:
        assert settings.device_manager_api_key is not None
        central_client = HttpCentralRegistryClient(
            server_url=settings.central_server_url,
            api_key=settings.device_manager_api_key,
        )

    endpoint = EndpointDescriptor(
        transport="grpc",
        target=settings.grpc_advertised_target,
        api_version="labnet.devices.v1",
        secure=settings.grpc_tls_mode != "disabled",
        tls_server_name=(
            settings.grpc_tls_server_name
            if settings.grpc_tls_mode != "disabled"
            else None
        ),
    )
    return DeviceManager(
        devices,
        lease_validator,
        queue_capacity=settings.device_queue_capacity,
        central_client=central_client,
        host=settings.lab_pc_host if central_client is not None else None,
        endpoint=endpoint if central_client is not None else None,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        worker_start_timeout=settings.device_start_timeout_seconds,
        shutdown_timeout=settings.grpc_shutdown_grace_seconds,
    )


def _create_grpc_server(
    settings: LabPCSettings,
    manager: DeviceManager,
) -> LabnetGrpcServer:
    tls = TLSConfig(
        mode=settings.grpc_tls_mode,
        certificate_chain_file=settings.grpc_cert_file,
        private_key_file=settings.grpc_key_file,
        client_ca_file=settings.grpc_ca_file,
    )
    config = GrpcServerConfig(
        bind=settings.grpc_bind_target,
        tls=tls,
        maximum_receive_message_bytes=settings.grpc_max_receive_bytes,
        maximum_send_message_bytes=settings.grpc_max_send_bytes,
        graceful_shutdown_seconds=settings.grpc_shutdown_grace_seconds,
    )
    stream_policy = StreamPolicy(
        maximum_sample_rate_hz=settings.stream_max_rate_hz,
        maximum_sample_count=settings.stream_max_samples,
        maximum_duration_seconds=settings.stream_max_duration_seconds,
        lease_revalidation_interval_seconds=(
            settings.stream_lease_revalidation_seconds
        ),
        sample_timeout_seconds=settings.stream_sample_timeout_seconds,
    )
    return LabnetGrpcServer(
        manager.executor,
        config=config,
        stream_policy=stream_policy,
        default_unit=settings.default_measurement_unit,
        default_operation_timeout_seconds=(
            settings.default_operation_timeout_seconds
        ),
    )


async def _health_sync_loop(runtime: LabPCRuntime) -> None:
    while True:
        try:
            desired = runtime.components_ready
            if runtime.grpc_server.serving is not desired:
                await runtime.grpc_server.set_serving(desired)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Could not synchronize gRPC health state.")
        await asyncio.sleep(runtime.settings.health_sync_interval_seconds)


async def _device_refresh_loop(runtime: LabPCRuntime) -> None:
    while True:
        try:
            await runtime.manager.refresh_snapshots(
                deadline_per_device=runtime.settings.device_health_timeout_seconds
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Could not refresh cached device health.")
        await asyncio.sleep(
            runtime.settings.device_health_refresh_interval_seconds
        )


def _components_ready(
    *,
    settings: LabPCSettings,
    manager_state: ManagerState,
    central_registered: bool,
    snapshots: Sequence[DeviceSnapshot],
) -> bool:
    devices_ready = any(snapshot.available for snapshot in snapshots)
    central_ready = not settings.central_enabled or central_registered
    return (
        manager_state is ManagerState.RUNNING
        and devices_ready
        and central_ready
    )


def _readiness_payload(
    runtime: LabPCRuntime,
    *,
    snapshots: Sequence[DeviceSnapshot],
    ready: bool,
    grpc_serving: bool,
    central_registered: bool,
    last_central_error: Exception | None,
) -> dict[str, Any]:
    return {
        "status": "ready" if ready else "not_ready",
        "service": "labnet-lab-pc",
        "grpc": {
            "serving": grpc_serving,
            "bind": runtime.settings.grpc_bind_target,
            "advertised_target": runtime.settings.grpc_advertised_target,
            "tls_mode": runtime.settings.grpc_tls_mode,
        },
        "central": {
            "enabled": runtime.settings.central_enabled,
            "registered": central_registered,
            "last_error_type": (
                type(last_central_error).__name__
                if last_central_error is not None
                else None
            ),
        },
        "devices": [
            {
                "device_id": snapshot.device_id,
                "device_type": snapshot.device_type,
                "state": snapshot.state.value,
                "status": snapshot.status,
                "availability": snapshot.availability.value,
                "available": snapshot.available,
                "failure": (
                    snapshot.failure.to_dict()
                    if snapshot.failure is not None
                    else None
                ),
                "updated_at": snapshot.updated_at.isoformat(),
            }
            for snapshot in snapshots
        ],
    }


app = create_app()


def main() -> None:
    settings = LabPCSettings.from_env()
    uvicorn.run(
        create_app(settings=settings),
        host=settings.http_bind_host,
        port=settings.http_port,
        reload=False,
    )


if __name__ == "__main__":
    main()


__all__ = ["LabPCRuntime", "app", "create_app", "main"]
