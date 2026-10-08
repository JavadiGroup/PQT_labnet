from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import queue
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any, Generic, TypeVar

from labnet.devices.core.limits import (
    DeviceLimits,
    DeviceLimitsError,
    get_site_limits,
    resolve_limits,
)
from labnet.devices.core.protocol import (
    AvailabilityAwareDevice,
    DeviceAvailability,
    PhysicalDevice,
)

from .errors import (
    ArgumentOutsideLimitsError,
    DeviceDeadlineExceededError,
    DeviceManagerError,
    DeviceOperationError,
    DeviceQueueFullError,
    DeviceShutdownError,
    DeviceStartupError,
    DeviceUnavailableError,
    InvalidConfigurationError,
    InvalidDeviceArgumentError,
    InvalidDeviceLimitsError,
    MethodNotAllowedError,
)
from .models import DeviceSnapshot, DeviceState, FailureInfo, utc_now


T = TypeVar("T")

_LEGACY_AVAILABLE_STATUSES = frozenset(
    {"available", "busy", "connected", "healthy", "idle", "in_use", "ok", "ready"}
)


@dataclass(slots=True)
class _WorkItem(Generic[T]):
    operation: Callable[[], T]
    future: concurrent.futures.Future[T]
    name: str
    is_shutdown: bool = False


class DeviceWorker:
    """One bounded command queue and persistent thread for one device.

    A caller timeout or cancellation only cancels work that has not started.
    Python cannot safely interrupt a blocking driver call. Once a physical call
    begins, this worker remains occupied until it finishes, ensuring a later
    call can never overlap it.
    """

    def __init__(
        self,
        device_id: str,
        device: PhysicalDevice,
        *,
        queue_capacity: int = 32,
        thread_name_prefix: str = "labnet-device",
        require_generated_contract: bool = False,
    ) -> None:
        if queue_capacity < 1:
            raise InvalidConfigurationError("queue_capacity must be at least 1.")
        self.device_id = device_id
        self._device = device
        self._queue_capacity = queue_capacity
        self._require_generated_contract = require_generated_contract
        self._queue: queue.Queue[_WorkItem[Any]] = queue.Queue(
            maxsize=queue_capacity
        )
        safe_id = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in device_id
        )
        self._thread = threading.Thread(
            target=self._run,
            name=f"{thread_name_prefix}-{safe_id}",
            daemon=True,
        )
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._started_future: concurrent.futures.Future[DeviceSnapshot] = (
            concurrent.futures.Future()
        )
        self._thread_started = False
        self._accepting = False
        self._state = DeviceState.NEW
        self._device_type = "unknown"
        self._status = "disconnected"
        self._availability = DeviceAvailability.UNAVAILABLE
        self._metadata: dict[str, Any] = {}
        self._allowed_methods: tuple[str, ...] = ()
        # Effective site and driver limits, enforced before every call.
        self._limits: DeviceLimits | None = None
        self._failure: FailureInfo | None = None
        self._startup_abandoned = False
        self._shutdown_future: concurrent.futures.Future[None] | None = None
        self._updated_at = utc_now()

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    @property
    def state(self) -> DeviceState:
        with self._state_lock:
            return self._state

    @property
    def allowed_methods(self) -> tuple[str, ...]:
        with self._state_lock:
            return self._allowed_methods

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def snapshot(self) -> DeviceSnapshot:
        """Return only state cached by the worker thread."""
        with self._state_lock:
            return DeviceSnapshot(
                device_id=self.device_id,
                device_type=self._device_type,
                state=self._state,
                status=self._status,
                availability=self._availability,
                metadata=self._metadata,
                allowed_methods=self._allowed_methods,
                failure=self._failure,
                updated_at=self._updated_at,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread_started:
                return
            self._thread_started = True
            self._accepting = True
            self._set_state(DeviceState.STARTING)
            try:
                self._thread.start()
            except Exception as exc:
                self._thread_started = False
                self._accepting = False
                error = DeviceStartupError(
                    f"Device worker thread failed to start: {self.device_id}",
                    details={
                        "device_id": self.device_id,
                        "exception_type": type(exc).__name__,
                        "reason": str(exc),
                    },
                )
                self._record_failure(error, exc)
                self._started_future.set_result(self.snapshot())

    async def wait_started(self, *, timeout: float | None = None) -> DeviceSnapshot:
        if not self._thread_started:
            raise DeviceUnavailableError(
                f"Device worker has not been started: {self.device_id}",
                details={"device_id": self.device_id},
            )
        return await _await_future(
            self._started_future,
            timeout=timeout,
            device_id=self.device_id,
            operation="connect",
            cancel_if_pending=False,
        )

    def mark_startup_timeout(self) -> None:
        """Keep a late connection result unavailable after startup timed out."""
        error = DeviceStartupError(
            f"Device startup timed out: {self.device_id}",
            details={"device_id": self.device_id},
        )
        with self._state_lock:
            self._startup_abandoned = True
            self._failure = FailureInfo(
                code=error.code,
                message=error.message,
                exception_type="TimeoutError",
            )
            self._state = DeviceState.FAILED
            self._status = "disconnected"
            self._availability = DeviceAvailability.UNAVAILABLE
            self._updated_at = utc_now()

    def mark_health_check_failed(self, error: Exception) -> None:
        """Mark cached health unavailable without touching the driver thread."""

        status = (
            "unresponsive"
            if isinstance(error, DeviceDeadlineExceededError)
            else "unknown"
        )
        code = (
            error.code
            if isinstance(error, DeviceManagerError)
            else "DEVICE_HEALTH_CHECK_FAILED"
        )
        with self._state_lock:
            self._status = status
            self._availability = DeviceAvailability.UNKNOWN
            self._failure = FailureInfo(
                code=code,
                message=f"Device health check failed: {self.device_id}",
                exception_type=type(error).__name__,
            )
            self._updated_at = utc_now()

    async def invoke(
        self,
        method: str,
        *,
        args: Collection[Any] = (),
        kwargs: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        """Invoke one allow-listed device method.

        ``deadline`` is a relative remaining budget in seconds. Timing out a
        running physical call does not terminate it; the worker stays occupied
        until that call really returns.
        """
        self._require_available()
        self._require_allowed(method)
        positional = tuple(args)
        keyword = dict(kwargs or {})
        self._require_within_limits(method, positional, keyword)

        def operation() -> Any:
            self._require_allowed(method)
            target = getattr(self._device, method, None)
            if target is None or not callable(target):
                raise MethodNotAllowedError(
                    f"Allowed device method is not callable: {method}",
                    details={"device_id": self.device_id, "method": method},
                )
            self._set_runtime_status("in_use")
            try:
                return target(*positional, **keyword)
            except DeviceManagerError:
                raise
            except Exception as exc:
                raise DeviceOperationError(
                    f"Device operation failed: {method}",
                    details={
                        "device_id": self.device_id,
                        "method": method,
                        "exception_type": type(exc).__name__,
                        "reason": str(exc),
                    },
                ) from exc
            finally:
                self._refresh_status_best_effort()

        future = self._submit(operation, name=method)
        return await _await_future(
            future,
            timeout=deadline,
            device_id=self.device_id,
            operation=method,
            cancel_if_pending=True,
        )

    async def refresh(self, *, deadline: float | None = None) -> DeviceSnapshot:
        self._require_ready()

        def operation() -> DeviceSnapshot:
            self._refresh_description()
            return self.snapshot()

        future = self._submit(operation, name="refresh")
        return await _await_future(
            future,
            timeout=deadline,
            device_id=self.device_id,
            operation="refresh",
            cancel_if_pending=True,
        )

    async def shutdown(self, *, timeout: float | None = 10.0) -> None:
        started_at = monotonic()
        with self._lifecycle_lock:
            if not self._thread_started:
                self._set_state(DeviceState.STOPPED)
                return
            if self.state is DeviceState.STOPPED:
                return
            self._accepting = False
            future = self._shutdown_future
            if future is None:
                self._set_state(DeviceState.STOPPING)
                future = concurrent.futures.Future()
                self._shutdown_future = future

                def disconnect() -> None:
                    try:
                        self._device.disconnect()
                    except Exception as exc:
                        raise DeviceShutdownError(
                            f"Device disconnect failed: {self.device_id}",
                            details={
                                "device_id": self.device_id,
                                "exception_type": type(exc).__name__,
                                "reason": str(exc),
                            },
                        ) from exc

                # Once the network listener has stopped, queued device calls
                # are no longer useful. Cancelling them reserves a queue slot
                # for the disconnect sentinel even when the command queue was
                # full, while a call already running is still allowed to end.
                self._discard_pending_work()
                self._queue.put_nowait(
                    _WorkItem(
                        operation=disconnect,
                        future=future,
                        name="disconnect",
                        is_shutdown=True,
                    )
                )

        remaining = _remaining(timeout, started_at)
        try:
            await _await_future(
                future,
                timeout=remaining,
                device_id=self.device_id,
                operation="disconnect",
                cancel_if_pending=False,
            )
        except DeviceDeadlineExceededError as exc:
            raise DeviceShutdownError(
                f"Timed out while stopping device worker: {self.device_id}",
                details={"device_id": self.device_id},
            ) from exc

        remaining = _remaining(timeout, started_at)
        await asyncio.to_thread(self._thread.join, remaining)
        if self._thread.is_alive():
            raise DeviceShutdownError(
                f"Device worker did not stop: {self.device_id}",
                details={"device_id": self.device_id},
            )

    def _run(self) -> None:
        try:
            self._device.connect()
            self._refresh_description()
            with self._state_lock:
                if not self._startup_abandoned:
                    self._state = DeviceState.READY
                    self._failure = None
                    self._updated_at = utc_now()
        except InvalidDeviceLimitsError as exc:
            self._record_failure(exc, exc)
        except Exception as exc:
            error = DeviceStartupError(
                f"Device startup failed: {self.device_id}",
                details={
                    "device_id": self.device_id,
                    "exception_type": type(exc).__name__,
                    "reason": str(exc),
                },
            )
            self._record_failure(error, exc)
        finally:
            self._started_future.set_result(self.snapshot())

        while True:
            item = self._queue.get()
            try:
                if not item.future.set_running_or_notify_cancel():
                    if item.is_shutdown:
                        self._set_state(DeviceState.STOPPED)
                        return
                    continue
                operation_error: Exception | None = None
                try:
                    result = item.operation()
                except Exception as exc:
                    operation_error = exc
                    # Publish the failed cleanup state before completing the
                    # future. Awaiters may resume as soon as set_exception()
                    # is called and must observe a consistent snapshot.
                    if item.is_shutdown:
                        self._record_shutdown_failure(exc)
                    item.future.set_exception(exc)
                else:
                    item.future.set_result(result)
                if item.is_shutdown:
                    if operation_error is None:
                        self._set_state(DeviceState.STOPPED)
                    return
            finally:
                self._queue.task_done()

    def _submit(
        self,
        operation: Callable[[], T],
        *,
        name: str,
    ) -> concurrent.futures.Future[T]:
        with self._lifecycle_lock:
            if not self._accepting:
                raise DeviceUnavailableError(
                    f"Device worker is not accepting calls: {self.device_id}",
                    details={"device_id": self.device_id},
                )
            future: concurrent.futures.Future[T] = concurrent.futures.Future()
            try:
                self._queue.put_nowait(
                    _WorkItem(operation=operation, future=future, name=name)
                )
            except queue.Full as exc:
                raise DeviceQueueFullError(
                    f"Device command queue is full: {self.device_id}",
                    details={
                        "device_id": self.device_id,
                        "queue_capacity": self._queue_capacity,
                    },
                ) from exc
            return future

    def _discard_pending_work(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                item.future.cancel()
            finally:
                self._queue.task_done()

    def _require_ready(self) -> None:
        snapshot = self.snapshot()
        if snapshot.state is not DeviceState.READY:
            raise DeviceUnavailableError(
                f"Device is not available: {self.device_id}",
                details={
                    "device_id": self.device_id,
                    "state": snapshot.state.value,
                },
            )

    def _require_available(self) -> None:
        snapshot = self.snapshot()
        if not snapshot.available:
            raise DeviceUnavailableError(
                f"Device is not available: {self.device_id}",
                details={
                    "device_id": self.device_id,
                    "state": snapshot.state.value,
                    "status": snapshot.status,
                },
            )

    def _require_allowed(self, method: str) -> None:
        if not isinstance(method, str) or method not in self.allowed_methods:
            raise MethodNotAllowedError(
                f"Method is not allowed: {method}",
                details={"device_id": self.device_id, "method": method},
            )

    @property
    def limits(self) -> DeviceLimits | None:
        """The effective limits enforced on this device's calls, once connected."""

        with self._state_lock:
            return self._limits

    def _require_within_limits(
        self,
        method: str,
        positional: tuple[Any, ...],
        keyword: dict[str, Any],
    ) -> None:
        """Refuse a call outside the device's site or driver limits.

        This is the one place every remote call passes before it reaches the
        driver, so the published hints are never merely advisory.
        """

        limits = self.limits
        if limits is None or not limits.limits_method(method):
            return
        arguments: dict[str, Any] = dict(keyword)
        if positional:
            target = getattr(self._device, method, None)
            try:
                bound = inspect.signature(target).bind_partial(*positional, **keyword)
            except (TypeError, ValueError) as exc:
                raise InvalidDeviceArgumentError(
                    f"The arguments of {method} can't be checked against its limits.",
                    details={"device_id": self.device_id, "method": method},
                ) from exc
            arguments = dict(bound.arguments)
        problem = limits.check(method, arguments)
        if problem is not None:
            raise ArgumentOutsideLimitsError(
                problem,
                details={"device_id": self.device_id, "method": method},
            )

    def _refresh_description(self) -> None:
        device_type = self._device.device_type
        status = self._device.status()
        if not isinstance(device_type, str) or not device_type.strip():
            raise InvalidConfigurationError("device_type must be a non-empty string.")
        if not isinstance(status, str) or not status.strip():
            raise InvalidConfigurationError("status() must return a non-empty string.")
        availability = _read_device_availability(self._device, status)
        metadata = self._device.metadata()
        if not isinstance(metadata, Mapping):
            raise InvalidConfigurationError("metadata() must return a mapping.")

        # A generated proto contract is authoritative whenever one exists.
        # This both removes the duplicate handwritten allow-list from drivers
        # and makes an API/physical signature mismatch fail during startup,
        # before the device is advertised to Central.
        from labnet.grpc.generated.device_contracts import (
            DEVICE_CONTRACT_BY_TYPE,
            validate_implementation,
        )

        contract = DEVICE_CONTRACT_BY_TYPE.get(device_type.strip())
        if contract is None:
            if self._require_generated_contract:
                raise InvalidConfigurationError(
                    f"No generated gRPC contract exists for device_type "
                    f"{device_type.strip()!r}. Add its annotated proto and run "
                    "tools/generate_protos.py before starting the Lab PC."
                )
            # Keep the transport-neutral manager usable for legacy/test-only
            # physical devices that have no network service contract.
            methods = self._device.allowed_methods()
            limits = None
            if getattr(self._device, "labnet_site_limits", None) is not None:
                raise InvalidDeviceLimitsError(
                    f"{self.device_id} has site limits but no generated contract "
                    "to check them against."
                )
        else:
            validate_implementation(self._device, contract)
            methods = contract.allowed_physical_methods
            metadata = dict(metadata)
            metadata["capabilities"] = [
                method.python_name for method in contract.methods
            ]
            try:
                limits = resolve_limits(
                    contract,
                    metadata.get("hints"),
                    get_site_limits(self._device),
                    device_id=self.device_id,
                )
            except DeviceLimitsError as exc:
                raise InvalidDeviceLimitsError(
                    str(exc),
                    details={"device_id": self.device_id},
                ) from exc
            # Publish what is enforced: the driver's and the site's limits
            # together, with where each comes from.
            if limits.hints:
                metadata["hints"] = dict(limits.hints)
            else:
                metadata.pop("hints", None)
        allowed = _normalize_allowed_methods(methods)
        with self._state_lock:
            self._device_type = device_type.strip()
            self._status = status.strip()
            self._availability = availability
            self._metadata = dict(metadata)
            self._allowed_methods = allowed
            self._limits = limits
            self._failure = None
            self._updated_at = utc_now()

    def _refresh_status_best_effort(self) -> None:
        try:
            status = self._device.status()
            if isinstance(status, str) and status.strip():
                availability = _read_device_availability(self._device, status)
                self._set_runtime_health(status.strip(), availability)
        except Exception:
            self._set_runtime_health("unknown", DeviceAvailability.UNKNOWN)

    def _set_runtime_status(self, status: str) -> None:
        with self._state_lock:
            self._status = status
            self._updated_at = utc_now()

    def _set_runtime_health(
        self,
        status: str,
        availability: DeviceAvailability,
    ) -> None:
        with self._state_lock:
            self._status = status
            self._availability = availability
            self._updated_at = utc_now()

    def _set_state(self, state: DeviceState) -> None:
        with self._state_lock:
            self._state = state
            if state in {
                DeviceState.FAILED,
                DeviceState.STOPPED,
                DeviceState.STOPPING,
            }:
                self._status = "disconnected"
                self._availability = DeviceAvailability.UNAVAILABLE
            elif state in {DeviceState.NEW, DeviceState.STARTING}:
                self._availability = DeviceAvailability.UNKNOWN
            self._updated_at = utc_now()

    def _record_failure(
        self,
        error: DeviceManagerError,
        cause: Exception,
    ) -> None:
        with self._state_lock:
            self._failure = FailureInfo(
                code=error.code,
                message=error.message,
                exception_type=type(cause).__name__,
            )
            self._state = DeviceState.FAILED
            self._status = "disconnected"
            self._availability = DeviceAvailability.UNAVAILABLE
            self._updated_at = utc_now()

    def _record_shutdown_failure(self, error: Exception) -> None:
        """Retain failed cleanup state after the worker thread exits.

        A disconnect exception means the physical cleanup outcome is unknown.
        The thread cannot safely be restarted, so later shutdown calls must
        continue to surface the completed shutdown future's exception instead
        of reporting a false clean STOPPED state.
        """

        cause = error.__cause__ if error.__cause__ is not None else error
        code = (
            error.code
            if isinstance(error, DeviceManagerError)
            else DeviceShutdownError.code
        )
        message = (
            error.message
            if isinstance(error, DeviceManagerError)
            else f"Device disconnect failed: {self.device_id}"
        )
        with self._state_lock:
            self._failure = FailureInfo(
                code=code,
                message=message,
                exception_type=type(cause).__name__,
            )
            self._state = DeviceState.FAILED
            self._status = "disconnect_failed"
            self._availability = DeviceAvailability.UNAVAILABLE
            self._updated_at = utc_now()


async def _await_future(
    future: concurrent.futures.Future[T],
    *,
    timeout: float | None,
    device_id: str,
    operation: str,
    cancel_if_pending: bool,
) -> T:
    if timeout is not None and timeout <= 0:
        cancelled = future.cancel() if cancel_if_pending else future.cancelled()
        raise DeviceDeadlineExceededError(
            f"Device deadline exceeded before {operation} could complete.",
            details={
                "device_id": device_id,
                "operation": operation,
                "operation_may_have_completed": not cancelled,
            },
        )

    wrapped = asyncio.wrap_future(future)
    try:
        if timeout is None:
            if cancel_if_pending:
                return await wrapped
            return await asyncio.shield(wrapped)
        done, _ = await asyncio.wait({wrapped}, timeout=timeout)
        if done:
            return wrapped.result()

        cancelled = future.cancel() if cancel_if_pending else False
        if cancel_if_pending:
            wrapped.cancel()
        else:
            wrapped.add_done_callback(_consume_asyncio_future)
        raise DeviceDeadlineExceededError(
            f"Device deadline exceeded while waiting for {operation}.",
            details={
                "device_id": device_id,
                "operation": operation,
                "operation_may_have_completed": not cancelled,
            },
        )
    except asyncio.CancelledError:
        if cancel_if_pending:
            future.cancel()
            wrapped.cancel()
        else:
            wrapped.add_done_callback(_consume_asyncio_future)
        raise


def _consume_asyncio_future(future: asyncio.Future[Any]) -> None:
    """Retrieve a late result/exception after a non-cancellable wait ends."""
    if not future.cancelled():
        future.exception()


def _normalize_allowed_methods(methods: Collection[str]) -> tuple[str, ...]:
    if isinstance(methods, (str, bytes)) or not isinstance(methods, Collection):
        raise InvalidConfigurationError(
            "allowed_methods() must return a collection of method names."
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for method in methods:
        if not isinstance(method, str):
            raise InvalidConfigurationError(
                "Allowed method names must be non-empty public strings."
            )
        method = method.strip()
        if not method or method.startswith("_"):
            raise InvalidConfigurationError(
                "Allowed method names must be non-empty public strings."
            )
        if method not in seen:
            seen.add(method)
            normalized.append(method)
    return tuple(normalized)


def _read_device_availability(
    device: PhysicalDevice,
    status: str,
) -> DeviceAvailability:
    """Read the explicit health contract, with a fail-closed legacy adapter."""

    if isinstance(device, AvailabilityAwareDevice):
        availability = device.availability()
        if not isinstance(availability, DeviceAvailability):
            raise InvalidConfigurationError(
                "availability() must return a DeviceAvailability value."
            )
        return availability

    normalized = status.strip().casefold().replace(" ", "_")
    if normalized in _LEGACY_AVAILABLE_STATUSES:
        return DeviceAvailability.AVAILABLE
    return DeviceAvailability.UNKNOWN


def _remaining(timeout: float | None, started_at: float) -> float | None:
    if timeout is None:
        return None
    return max(0.0, timeout - (monotonic() - started_at))


__all__ = ["DeviceWorker"]
