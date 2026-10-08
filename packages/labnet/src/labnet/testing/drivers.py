"""``check_driver``: would a Lab PC accept this device? Ask the Lab PC's own checks."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from labnet.contracts import PhysicalContractError, registry, validate_implementation
from labnet.device_manager import (
    DeviceManager,
    DeviceSnapshot,
    DeviceState,
    InvalidDeviceLimitsError,
    StaticLeaseValidator,
)
from labnet.devices.core.protocol import PhysicalDevice


class DriverCheckError(AssertionError):
    """A device that a Lab PC would refuse or fail to start."""


async def acheck_driver(device: PhysicalDevice, *, timeout: float = 30.0) -> DeviceSnapshot:
    """Start ``device`` exactly as a Lab PC would, then stop it; return what it published.

    The device must have a registered contract, implement every method that
    contract names with matching arguments, connect, report a status and an
    availability, and publish JSON metadata whose hints fit the contract (site
    limits included). Raises :class:`DriverCheckError` saying what is wrong.
    """

    # A Lab PC keeps only the type of a startup error (the text could hold
    # secrets), so check the contract here and record what the driver raises.
    contract = registry.find(getattr(device, "device_type", None))
    if contract is None:
        raise DriverCheckError(
            f"No generated gRPC contract exists for device_type {getattr(device, 'device_type', None)!r}: "
            "is the driver's package installed, and its contracts built?"
        )
    try:
        validate_implementation(device, contract)
    except PhysicalContractError as exc:
        raise DriverCheckError(str(exc)) from exc

    raised: list[Exception] = []
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({}),
        worker_start_timeout=timeout,
        shutdown_timeout=timeout,
        require_generated_contracts=True,
    )
    try:
        with _recording_exceptions(device, raised):
            try:
                await manager.start()
            except InvalidDeviceLimitsError as exc:
                raise DriverCheckError(str(exc)) from exc
            snapshot = manager.get_snapshot(device.device_id)
    finally:
        await manager.shutdown()
    if snapshot.state is not DeviceState.READY:
        if raised:
            reason = f"{type(raised[-1]).__name__}: {raised[-1]}"
        else:
            reason = snapshot.failure.message if snapshot.failure else snapshot.state.value
        raise DriverCheckError(f"{device.device_id} did not start: {reason}")
    try:
        # Snapshots freeze mappings read-only (MappingProxyType); those are still JSON objects.
        json.dumps(snapshot.metadata, default=_mapping_as_dict)
    except (TypeError, ValueError) as exc:
        raise DriverCheckError(f"{device.device_id}: metadata() is not JSON-compatible ({exc})") from exc
    return snapshot


_LIFECYCLE = ("connect", "status", "availability", "metadata")


@contextlib.contextmanager
def _recording_exceptions(device: PhysicalDevice, raised: list[Exception]) -> Iterator[None]:
    """Shadow the device's lifecycle methods with ones that note what they raise."""

    def recording(method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(method)
        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                return method(*args, **kwargs)
            except Exception as exc:
                raised.append(exc)
                raise

        return call

    shadowed = []
    for name in _LIFECYCLE:
        method = getattr(device, name, None)
        if callable(method):
            try:
                setattr(device, name, recording(method))
            except (AttributeError, TypeError):  # __slots__ or a read-only attribute: no detail
                continue
            shadowed.append(name)
    try:
        yield
    finally:
        for name in shadowed:
            delattr(device, name)


def _mapping_as_dict(value: object) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def check_driver(device: PhysicalDevice, *, timeout: float = 30.0) -> DeviceSnapshot:
    """:func:`acheck_driver` for synchronous code (not inside a running event loop)."""

    return asyncio.run(acheck_driver(device, timeout=timeout))


__all__ = ["DriverCheckError", "acheck_driver", "check_driver"]
