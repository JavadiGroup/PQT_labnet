from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Iterable
from typing import Any

from labnet.devices.core.protocol import PhysicalDevice

from .catalog import DeviceCatalog
from .central import CentralRegistryClient
from .errors import (
    DeviceDeadlineExceededError,
    DeviceQueueFullError,
    DeviceShutdownError,
    InvalidConfigurationError,
    ManagerClosedError,
)
from .executor import DeviceExecutor
from .leases import LeaseValidator
from .models import (
    DeviceSnapshot,
    DeviceState,
    EndpointDescriptor,
    ManagerState,
    StartupReport,
    utc_now,
)
from .worker import DeviceWorker


class DeviceManager:
    """Own device workers, execution, registration, and graceful shutdown."""

    def __init__(
        self,
        devices: Iterable[PhysicalDevice],
        lease_validator: LeaseValidator,
        *,
        queue_capacity: int = 32,
        central_client: CentralRegistryClient | None = None,
        host: str | None = None,
        endpoint: EndpointDescriptor | None = None,
        heartbeat_interval_seconds: float = 5.0,
        worker_start_timeout: float | None = None,
        shutdown_timeout: float | None = 10.0,
        require_generated_contracts: bool = False,
    ) -> None:
        if not isinstance(require_generated_contracts, bool):
            raise InvalidConfigurationError(
                "require_generated_contracts must be a boolean."
            )
        if (
            not math.isfinite(heartbeat_interval_seconds)
            or heartbeat_interval_seconds <= 0
        ):
            raise InvalidConfigurationError(
                "heartbeat_interval_seconds must be positive."
            )
        if worker_start_timeout is not None and (
            not math.isfinite(worker_start_timeout) or worker_start_timeout <= 0
        ):
            raise InvalidConfigurationError("worker_start_timeout must be positive.")
        if shutdown_timeout is not None and (
            not math.isfinite(shutdown_timeout) or shutdown_timeout <= 0
        ):
            raise InvalidConfigurationError("shutdown_timeout must be positive.")
        if central_client is not None and (not host or endpoint is None):
            raise InvalidConfigurationError(
                "host and endpoint are required when central_client is configured."
            )

        self.catalog = DeviceCatalog(devices)
        self._workers = {
            device_id: DeviceWorker(
                device_id,
                device,
                queue_capacity=queue_capacity,
                require_generated_contract=require_generated_contracts,
            )
            for device_id, device in self.catalog.items()
        }
        self.executor = DeviceExecutor(self._workers, lease_validator)
        self.lease_validator = lease_validator
        self.central_client = central_client
        self.host = host
        self.endpoint = endpoint
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.worker_start_timeout = worker_start_timeout
        self.shutdown_timeout = shutdown_timeout

        self._state = ManagerState.NEW
        self._lifecycle_lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._registered = False
        self._last_central_error: Exception | None = None

    @property
    def state(self) -> ManagerState:
        return self._state

    @property
    def registered(self) -> bool:
        return self._registered

    @property
    def last_central_error(self) -> Exception | None:
        return self._last_central_error

    @property
    def device_ids(self) -> tuple[str, ...]:
        return self.catalog.device_ids

    async def start(self) -> StartupReport:
        """Start all devices without failing the manager for one bad device."""
        async with self._lifecycle_lock:
            if self._state is ManagerState.RUNNING:
                return StartupReport(self.snapshots())
            if self._state in {ManagerState.STOPPING, ManagerState.STOPPED}:
                raise ManagerClosedError("A stopped DeviceManager cannot be restarted.")

            self._state = ManagerState.STARTING
            for worker in self._workers.values():
                worker.start()

            startup_results = await asyncio.gather(
                *(
                    worker.wait_started(timeout=self.worker_start_timeout)
                    for worker in self._workers.values()
                ),
                return_exceptions=True,
            )
            for worker, result in zip(
                self._workers.values(),
                startup_results,
                strict=True,
            ):
                if isinstance(result, DeviceDeadlineExceededError):
                    worker.mark_startup_timeout()
            self._state = ManagerState.RUNNING

            if self.central_client is not None:
                self._stop_event.clear()
                self._heartbeat_task = asyncio.create_task(
                    self.heartbeat_loop(),
                    name="labnet-central-heartbeat",
                )

            return StartupReport(self.snapshots())

    def get_snapshot(self, device_id: str) -> DeviceSnapshot:
        return self.executor.get_worker(device_id).snapshot()

    def snapshots(self) -> tuple[DeviceSnapshot, ...]:
        return tuple(worker.snapshot() for worker in self._workers.values())

    async def refresh_snapshots(
        self,
        *,
        deadline_per_device: float | None = None,
    ) -> tuple[DeviceSnapshot, ...]:
        workers = [
            worker
            for worker in self._workers.values()
            if worker.snapshot().state is DeviceState.READY
        ]
        results = await asyncio.gather(
            *(worker.refresh(deadline=deadline_per_device) for worker in workers),
            return_exceptions=True,
        )
        for worker, result in zip(workers, results, strict=True):
            if not isinstance(result, Exception):
                continue
            if isinstance(result, DeviceQueueFullError):
                continue
            if isinstance(result, DeviceDeadlineExceededError) and (
                result.details.get("operation_may_have_completed") is not True
            ):
                # The health check never started because normal device work
                # occupied the queue; retain the last known health state.
                continue
            worker.mark_health_check_failed(result)
        return self.snapshots()

    async def register_devices(self) -> None:
        client, host, endpoint = self._central_configuration()
        await client.register(
            host=host,
            endpoint=endpoint,
            devices=self.snapshots(),
        )
        self._registered = True
        self._last_central_error = None

    async def send_heartbeat(self) -> None:
        client, host, endpoint = self._central_configuration()
        await client.heartbeat(
            host=host,
            endpoint=endpoint,
            devices=self.snapshots(),
            timestamp=utc_now(),
        )
        self._last_central_error = None

    async def heartbeat_loop(self) -> None:
        """Register once and retry non-fatal Central communication failures."""
        while not self._stop_event.is_set():
            try:
                if not self._registered:
                    await self.register_devices()
                await self.send_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._registered = False
                self._last_central_error = exc

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.heartbeat_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def shutdown(self) -> None:
        """Stop heartbeats, drain/disconnect devices, and close HTTP clients."""
        async with self._lifecycle_lock:
            if self._state is ManagerState.STOPPED:
                return
            self._state = ManagerState.STOPPING
            self._stop_event.set()

            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
                self._heartbeat_task = None

            results = await asyncio.gather(
                *(
                    worker.shutdown(timeout=self.shutdown_timeout)
                    for worker in self._workers.values()
                ),
                return_exceptions=True,
            )

            close_targets: list[object | None] = [self.central_client]
            if self.lease_validator is not self.central_client:
                close_targets.append(self.lease_validator)
            close_results = await asyncio.gather(
                *(_close_if_supported(value) for value in close_targets),
                return_exceptions=True,
            )
            failures = [
                item
                for item in (*results, *close_results)
                if isinstance(item, Exception)
            ]
            if failures:
                raise DeviceShutdownError(
                    "One or more device workers did not shut down cleanly.",
                    details={
                        "failure_count": len(failures),
                        "failures": [str(item) for item in failures],
                    },
                )
            self._state = ManagerState.STOPPED

    async def __aenter__(self) -> DeviceManager:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.shutdown()

    def _central_configuration(
        self,
    ) -> tuple[CentralRegistryClient, str, EndpointDescriptor]:
        if self.central_client is None or self.host is None or self.endpoint is None:
            raise InvalidConfigurationError(
                "Central Server registration is not configured."
            )
        return self.central_client, self.host, self.endpoint


async def _close_if_supported(value: object | None) -> None:
    if value is None:
        return
    close = getattr(value, "close", None)
    if close is None or not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


__all__ = ["DeviceManager"]
