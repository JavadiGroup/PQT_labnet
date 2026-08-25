from __future__ import annotations

import asyncio
import importlib
import logging
import secrets
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
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
from labnet.release import contract_digest, installed_version

from .config import LabPCSettings
from .device_inventory import create_devices
from .update_control import StopCallback, UpdateRuntimeCoordinator


LOGGER = logging.getLogger(__name__)
DeviceFactory = Callable[[], Sequence[PhysicalDevice]]


@dataclass(slots=True)
class LabPCRuntime:
    """Live process components exposed to the local management plane."""

    settings: LabPCSettings
    manager: DeviceManager
    grpc_server: LabnetGrpcServer
    update_coordinator: UpdateRuntimeCoordinator
    package_version: str
    contract_digest: str

    @property
    def components_ready(self) -> bool:
        snapshots = self.manager.snapshots()
        return (
            self.update_coordinator.accepting
            and _components_ready(
                settings=self.settings,
                manager_state=self.manager.state,
                central_registered=self.manager.registered,
                snapshots=snapshots,
            )
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
    device_factory: DeviceFactory | None = None,
    stop_callback: StopCallback | None = None,
) -> FastAPI:
    """Create the HTTP management app and its gRPC/device lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings = settings or LabPCSettings.from_env()
        manager: DeviceManager | None = None
        grpc_server: LabnetGrpcServer | None = None
        health_sync_task: asyncio.Task[None] | None = None
        device_refresh_task: asyncio.Task[None] | None = None
        update_coordinator: UpdateRuntimeCoordinator | None = None
        try:
            resolved_factory = device_factory or _load_device_factory(
                resolved_settings.device_factory_path
            )
            devices = tuple(resolved_factory())
            package_release = installed_version()
            contracts_hash = contract_digest()
            manager = _create_device_manager(
                resolved_settings,
                devices,
                package_version=package_release,
                contracts_hash=contracts_hash,
            )
            grpc_server = _create_grpc_server(resolved_settings, manager)
            update_coordinator = UpdateRuntimeCoordinator(
                manager,
                grpc_server,
                stop_callback=stop_callback,
            )
            runtime = LabPCRuntime(
                settings=resolved_settings,
                manager=manager,
                grpc_server=grpc_server,
                update_coordinator=update_coordinator,
                package_version=package_release,
                contract_digest=contracts_hash,
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
            if update_coordinator is not None:
                await update_coordinator.close()
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
        version=installed_version(),
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
            and runtime.update_coordinator.accepting
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

    @app.get("/internal/v1/update/status")
    async def update_status(request: Request) -> dict[str, object]:
        runtime: LabPCRuntime = app.state.runtime
        _require_updater(request, runtime.settings)
        return {
            "package_version": runtime.package_version,
            "contract_digest": runtime.contract_digest,
            **runtime.update_coordinator.snapshot().to_dict(),
        }

    @app.post("/internal/v1/update/drain")
    async def update_drain(request: Request) -> JSONResponse:
        runtime: LabPCRuntime = app.state.runtime
        _require_updater(request, runtime.settings)
        snapshot = await runtime.update_coordinator.request_drain()
        return JSONResponse(
            {
                "package_version": runtime.package_version,
                **snapshot.to_dict(),
            },
            status_code=200 if snapshot.quiescent else 202,
        )

    @app.post("/internal/v1/update/resume")
    async def update_resume(request: Request) -> dict[str, object]:
        runtime: LabPCRuntime = app.state.runtime
        _require_updater(request, runtime.settings)
        snapshot = await runtime.update_coordinator.resume()
        await runtime.grpc_server.set_serving(runtime.components_ready)
        return {
            "package_version": runtime.package_version,
            **snapshot.to_dict(),
        }

    @app.post("/internal/v1/update/stop")
    async def update_stop(request: Request) -> JSONResponse:
        runtime: LabPCRuntime = app.state.runtime
        _require_updater(request, runtime.settings)
        snapshot = await runtime.update_coordinator.request_stop()
        return JSONResponse(
            {
                "package_version": runtime.package_version,
                **snapshot.to_dict(),
            },
            status_code=202,
        )

    return app


def _create_device_manager(
    settings: LabPCSettings,
    devices: Sequence[PhysicalDevice],
    *,
    package_version: str,
    contracts_hash: str,
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
            manager_id=settings.lab_pc_id,
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
        attributes={
            "package_version": package_version,
            "contract_digest": contracts_hash,
        },
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
        require_generated_contracts=True,
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
            if runtime.update_coordinator.accepting:
                await runtime.manager.refresh_snapshots(
                    deadline_per_device=(
                        runtime.settings.device_health_timeout_seconds
                    )
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
        "ready": ready,
        "service": "labnet-lab-pc",
        "package_version": runtime.package_version,
        "contract_digest": runtime.contract_digest,
        "update": runtime.update_coordinator.snapshot().to_dict(),
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
    server_holder: dict[str, uvicorn.Server] = {}

    def request_stop() -> None:
        server = server_holder.get("server")
        if server is None:
            raise RuntimeError("The Lab-PC HTTP server has not started.")
        server.should_exit = True

    application = create_app(
        settings=settings,
        stop_callback=request_stop,
    )
    config = uvicorn.Config(
        application,
        host=settings.http_bind_host,
        port=settings.http_port,
        reload=False,
    )
    server = uvicorn.Server(config)
    server_holder["server"] = server
    server.run()


def _load_device_factory(import_path: str | None) -> DeviceFactory:
    if import_path is None:
        return create_devices
    module_name, _, attribute_name = import_path.partition(":")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute_name, None)
    if factory is None or not callable(factory):
        raise ValueError(
            f"LABNET_DEVICE_FACTORY does not identify a callable: {import_path}"
        )
    return factory


def _require_updater(request: Request, settings: LabPCSettings) -> None:
    expected = settings.updater_control_token
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail="Updater-controlled lifecycle endpoints are disabled.",
        )
    supplied = request.headers.get("x-labnet-updater-token", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Updater authentication failed.")


if __name__ == "__main__":
    main()


__all__ = ["LabPCRuntime", "app", "create_app", "main"]
