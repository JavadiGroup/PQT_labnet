"""Lab-PC process: physical-device workers, Central registration, gRPC, health.

Run it with ``labnet-lab-pc --env-file PATH``.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import os
import ssl
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx
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
from labnet.identifiers import validate_labnet_identifier

from .config import LabPCSettings


LOGGER = logging.getLogger(__name__)
DeviceFactory = Callable[[], Sequence[PhysicalDevice]]

try:
    _VERSION = version("labnet")
except PackageNotFoundError:  # running from a source tree without installing
    _VERSION = "unknown"


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
    device_factory: DeviceFactory | None = None,
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
            if resolved_settings.lab_pc_id is None:
                resolved_settings = replace(
                    resolved_settings,
                    lab_pc_id=await resolve_lab_pc_id(resolved_settings),
                )
            factory = device_factory or load_device_factory(
                resolved_settings.device_inventory
            )
            devices = tuple(factory())
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
                "Lab PC %s started: grpc=%s (%s) http=%s:%s ready_devices=%s "
                "failed_devices=%s",
                resolved_settings.lab_pc_id,
                resolved_settings.grpc_bind_target,
                resolved_settings.grpc_tls_mode,
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
        version=_VERSION,
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


def load_device_factory(spec: str | None) -> DeviceFactory:
    """Return the device inventory named by ``spec``.

    ``spec`` is ``package.module:function`` or ``path/to/file.py:function``.
    A file path needs nothing on ``sys.path``, so a reviewed inventory can live
    anywhere on the Lab PC. A Lab PC serves only what its inventory names, so
    there is no default.
    """

    spec = (spec or "").strip()
    if not spec:
        raise ValueError(
            "LABNET_DEVICE_INVENTORY is not set: name the function that creates this "
            "Lab PC's devices, e.g. C:/ProgramData/LabNet/site/inventories/"
            "lab_pc_01.py:create_devices. For simulated devices only, use "
            "scripts/dev/inventories/dev_lab_pc_01.py:create_devices."
        )

    # Split on the last colon so Windows drive letters (C:/...) survive.
    location, separator, attribute = spec.rpartition(":")
    if not separator or not location or not attribute:
        raise ValueError(
            "LABNET_DEVICE_INVENTORY must be package.module:function or "
            "path/to/file.py:function."
        )
    if location.endswith(".py") or "/" in location or "\\" in location:
        path = Path(location).expanduser()
        if not path.is_file():
            raise ValueError(f"LABNET_DEVICE_INVENTORY file not found: {path}")
        module_spec = importlib.util.spec_from_file_location(
            f"labnet_inventory_{path.stem}", path
        )
        if module_spec is None or module_spec.loader is None:
            raise ValueError(f"LABNET_DEVICE_INVENTORY cannot load {path}.")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    else:
        module = import_module(location)

    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise ValueError(
            f"LABNET_DEVICE_INVENTORY {spec!r} does not name a callable."
        )
    return factory


def central_verify(settings: LabPCSettings) -> ssl.SSLContext | bool:
    """TLS verification for Central: LABNET_CA_FILE or the system trust."""

    if settings.ca_file is None:
        return True
    return ssl.create_default_context(cafile=str(settings.ca_file))


async def resolve_lab_pc_id(
    settings: LabPCSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Use the Central username that owns the device-manager key as the ID."""

    assert settings.device_manager_api_key is not None
    try:
        async with httpx.AsyncClient(
            base_url=settings.central_server_url,
            verify=central_verify(settings),
            timeout=10.0,
            transport=transport,
        ) as client:
            response = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {settings.device_manager_api_key}"},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Could not reach Central at {settings.central_server_url} to look up "
            "this Lab PC's ID. Set LABNET_LAB_PC_ID to start without that lookup."
        ) from exc
    if response.status_code == 401:
        raise RuntimeError("Central rejected LABNET_DEVICE_MANAGER_API_KEY.")
    response.raise_for_status()
    identity = response.json()
    if identity.get("role") != "device_manager":
        raise RuntimeError(
            "LABNET_DEVICE_MANAGER_API_KEY belongs to "
            f"{identity.get('user')!r}, whose role is {identity.get('role')!r}; "
            "a Lab PC needs a device_manager key."
        )
    return validate_labnet_identifier(identity.get("user"), name="Central username")


def _create_device_manager(
    settings: LabPCSettings,
    devices: Sequence[PhysicalDevice],
) -> DeviceManager:
    # Reject duplicate or malformed IDs before constructing HTTP clients.
    DeviceCatalog(devices)

    if not settings.central_enabled:
        return DeviceManager(
            devices,
            DevelopmentLeaseValidator(allow_insecure=True),
            queue_capacity=settings.device_queue_capacity,
            worker_start_timeout=settings.device_start_timeout_seconds,
            shutdown_timeout=settings.grpc_shutdown_grace_seconds,
            require_generated_contracts=True,
        )

    assert settings.device_manager_api_key is not None
    assert settings.lab_pc_id is not None
    verify = central_verify(settings)
    lease_validator = HttpLeaseValidator(
        server_url=settings.central_server_url,
        api_key=settings.device_manager_api_key,
        verify=verify,
    )
    central_client = HttpCentralRegistryClient(
        manager_id=settings.lab_pc_id,
        server_url=settings.central_server_url,
        api_key=settings.device_manager_api_key,
        verify=verify,
    )
    endpoint = EndpointDescriptor(
        transport="grpc",
        target=settings.grpc_advertised_target,
        api_version="labnet.devices.v1",
        secure=settings.grpc_tls_enabled,
        tls_server_name=(
            settings.grpc_tls_server_name if settings.grpc_tls_enabled else None
        ),
    )
    return DeviceManager(
        devices,
        lease_validator,
        queue_capacity=settings.device_queue_capacity,
        central_client=central_client,
        host=settings.lab_pc_host,
        endpoint=endpoint,
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
        "lab_pc_id": runtime.settings.lab_pc_id,
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


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="labnet-lab-pc",
        description="Serve this computer's laboratory devices to the LabNet network.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Settings file (default: ./.env if present, then the process environment).",
    )
    parser.add_argument(
        "--inventory",
        help="Device inventory, package.module:function or file.py:function "
        "(overrides LABNET_DEVICE_INVENTORY).",
    )
    arguments = parser.parse_args(argv)
    if arguments.inventory:
        # The process environment takes precedence over the settings file.
        os.environ["LABNET_DEVICE_INVENTORY"] = arguments.inventory
    try:
        settings = LabPCSettings.from_env(arguments.env_file)
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"labnet-lab-pc: error: {exc}\n")
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(settings=settings),
        host=settings.http_bind_host,
        port=settings.http_port,
        reload=False,
    )


__all__ = [
    "LabPCRuntime",
    "central_verify",
    "create_app",
    "load_device_factory",
    "main",
    "resolve_lab_pc_id",
]
