from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping
from time import monotonic
from typing import Any

from .errors import (
    DeviceDeadlineExceededError,
    DeviceNotFoundError,
    DeviceTypeMismatchError,
    DeviceUnavailableError,
    LeaseError,
    LeaseInvalidError,
    LeaseValidationUnavailableError,
)
from .leases import LeaseGrant, LeaseValidator
from .worker import DeviceWorker


class AuthorizedDeviceSession:
    """Lease-bound execution capability used by trusted transport services.

    A session avoids a Central Server round trip for every element of a data
    stream. It still enforces the device allow-list, routes every operation
    through the same per-device worker, and rejects locally expired grants.
    Long-lived transports must call :meth:`revalidate` periodically.
    """

    def __init__(
        self,
        *,
        executor: DeviceExecutor,
        worker: DeviceWorker,
        grant: LeaseGrant,
        operation_id: str | None,
        expected_device_type: str | None,
    ) -> None:
        self._executor = executor
        self._worker = worker
        self._grant = grant
        self.operation_id = operation_id
        self._expected_device_type = expected_device_type

    @property
    def lease_id(self) -> str:
        return self._grant.lease_id

    @property
    def device_id(self) -> str:
        return self._grant.device_id

    @property
    def grant(self) -> LeaseGrant:
        return self._grant

    @property
    def expected_device_type(self) -> str | None:
        """Return the service family this authorization is bound to."""

        return self._expected_device_type

    async def revalidate(self, *, deadline: float | None = None) -> LeaseGrant:
        self._grant = await self._executor.validate_lease(
            self.lease_id,
            self.device_id,
            deadline=deadline,
            expected_device_type=self._expected_device_type,
        )
        return self._grant

    async def execute(
        self,
        method: str,
        args: Collection[Any] = (),
        kwargs: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> Any:
        if self._grant.expired:
            raise LeaseInvalidError(
                "The authorized device session has expired.",
                details={
                    "lease_id": self.lease_id,
                    "device_id": self.device_id,
                },
            )
        # A long-lived session remains bound to the service family that
        # originally authorized it. Re-check cached device metadata before
        # every physical call so a faulty runtime type change cannot cross a
        # service boundary between periodic Central revalidations.
        self._executor._require_available(
            self.device_id,
            expected_device_type=self._expected_device_type,
        )
        return await self._worker.invoke(
            method,
            args=args,
            kwargs=kwargs,
            deadline=deadline,
        )


class DeviceExecutor:
    """Transport-neutral lease validation and physical-call dispatcher."""

    def __init__(
        self,
        workers: Mapping[str, DeviceWorker],
        lease_validator: LeaseValidator,
    ) -> None:
        self._workers = dict(workers)
        self._lease_validator = lease_validator

    @property
    def device_ids(self) -> tuple[str, ...]:
        return tuple(self._workers)

    def get_worker(self, device_id: str) -> DeviceWorker:
        try:
            return self._workers[device_id]
        except KeyError as exc:
            raise DeviceNotFoundError(
                f"Device not found: {device_id}",
                details={"device_id": device_id},
            ) from exc

    async def validate_lease(
        self,
        lease_id: str,
        device_id: str,
        *,
        deadline: float | None = None,
        expected_device_type: str | None = None,
    ) -> LeaseGrant:
        async def validate() -> LeaseGrant:
            try:
                grant = await self._lease_validator.validate(
                    lease_id=lease_id,
                    device_id=device_id,
                )
            except LeaseError:
                raise
            except Exception as exc:
                raise LeaseValidationUnavailableError(
                    "Lease validation failed unexpectedly.",
                    details={"exception_type": type(exc).__name__},
                ) from exc

            if grant.lease_id != lease_id or grant.device_id != device_id:
                raise LeaseValidationUnavailableError(
                    "The lease validator returned a mismatched grant."
                )
            if grant.expired:
                raise LeaseInvalidError(
                    "The lease has expired.",
                    details={"lease_id": lease_id, "device_id": device_id},
                )
            return grant

        grant = await _await_authorization(
            validate(),
            deadline=deadline,
            device_id=device_id,
        )
        # Authorize before disclosing whether a device exists, is healthy, or
        # belongs to the service family handling this request.
        self._require_available(
            device_id,
            expected_device_type=expected_device_type,
        )
        return grant

    async def authorize(
        self,
        lease_id: str,
        device_id: str,
        operation_id: str | None = None,
        *,
        deadline: float | None = None,
        expected_device_type: str | None = None,
    ) -> AuthorizedDeviceSession:
        grant = await self.validate_lease(
            lease_id,
            device_id,
            deadline=deadline,
            expected_device_type=expected_device_type,
        )
        # Re-check after the network validation to avoid racing a shutdown.
        worker = self._require_available(
            device_id,
            expected_device_type=expected_device_type,
        )
        return AuthorizedDeviceSession(
            executor=self,
            worker=worker,
            grant=grant,
            operation_id=operation_id,
            expected_device_type=expected_device_type,
        )

    async def execute(
        self,
        lease_id: str,
        device_id: str,
        method: str,
        args: Collection[Any] = (),
        kwargs: Mapping[str, Any] | None = None,
        deadline: float | None = None,
        operation_id: str | None = None,
        expected_device_type: str | None = None,
    ) -> Any:
        """Validate a lease and invoke one allow-listed physical operation.

        ``deadline`` is a relative budget in seconds covering both Central
        lease validation and time spent queued/executing on the device worker.
        ``operation_id`` is carried by the authorized session for transport
        audit/deduplication layers; this executor does not retry operations.
        """
        started_at = monotonic()
        session = await self.authorize(
            lease_id,
            device_id,
            operation_id,
            deadline=deadline,
            expected_device_type=expected_device_type,
        )
        remaining = _remaining(deadline, started_at)
        return await session.execute(
            method,
            args=args,
            kwargs=kwargs,
            deadline=remaining,
        )

    def _require_available(
        self,
        device_id: str,
        *,
        expected_device_type: str | None = None,
    ) -> DeviceWorker:
        worker = self.get_worker(device_id)
        snapshot = worker.snapshot()
        if not snapshot.available:
            raise DeviceUnavailableError(
                f"Device is not available: {device_id}",
                details={
                    "device_id": device_id,
                    "state": snapshot.state.value,
                    "status": snapshot.status,
                },
            )
        if (
            expected_device_type is not None
            and snapshot.device_type != expected_device_type
        ):
            raise DeviceTypeMismatchError(
                "The leased device is not compatible with the requested "
                "gRPC service.",
                details={
                    "device_id": device_id,
                    "expected_device_type": expected_device_type,
                    "actual_device_type": snapshot.device_type,
                },
            )
        return worker


async def _await_authorization(
    awaitable: Any,
    *,
    deadline: float | None,
    device_id: str,
) -> LeaseGrant:
    try:
        if deadline is None:
            return await awaitable
        if deadline <= 0:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError
        return await asyncio.wait_for(awaitable, timeout=deadline)
    except asyncio.TimeoutError as exc:
        raise DeviceDeadlineExceededError(
            "Deadline exceeded during lease validation.",
            details={"device_id": device_id, "operation": "lease_validation"},
        ) from exc


def _remaining(deadline: float | None, started_at: float) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - (monotonic() - started_at))


__all__ = ["AuthorizedDeviceSession", "DeviceExecutor"]
