"""Calling one method of a leased device: shared by device sessions and the grant gateway.

``call_device(device, spec, kwargs)`` runs one contract method on an
``AsyncContractDeviceClient`` (or a test double with the same methods):

* a polled stream needs ``sample_count``; its samples are collected into a
  list of values (at most ``sample_count`` of them) and the stream is closed;
* every other method's result passes through :func:`plain` (a Measurement
  becomes its value).

What each caller allows (the script box's sample cap, a grant's bounds) is
checked by the caller before the call. :func:`device_error` is how a failure
is described to anyone outside the Control Center: redacted, so a lease id
quoted by a Lab PC or Central never leaves this process.

:func:`lease_lost_before_call` and :func:`lease_rejected` tell the grant
gateway when a lease lapsed (Central's idle timeout): the first means the call
never left this process, so it is safe to re-acquire the lease and try again.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from labnet.client import DeviceAuthenticationError, DevicePermissionDeniedError, LeaseAuthorityError
from labnet.client.core.models import Measurement
from labnet.contracts import DeviceContract, RpcSpec

from ..redaction import redact


STREAM_PATTERN = "RPC_PATTERN_POLLED_SCALAR_STREAM"
SETTING_PATTERNS = frozenset({"RPC_PATTERN_SCALAR_WRITE", "RPC_PATTERN_UNARY_COMMAND"})
# Methods that only read: retrying one after a lapsed lease cannot change the device twice.
READ_PATTERNS = frozenset({"RPC_PATTERN_SCALAR_READ", STREAM_PATTERN})


def find_method(contract: DeviceContract, name: str) -> RpcSpec | None:
    """The contract's method called ``name`` (its Python name), or None."""

    return next((spec for spec in contract.methods if spec.python_name == name), None)


def plain(value: Any) -> Any:
    """Measurements become their value; everything else passes through."""

    if isinstance(value, Measurement):
        return value.value
    return value


def json_safe(value: Any) -> Any:
    """``value`` as JSON can carry it: NaN and infinities become None, tuples lists."""

    value = plain(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return redact(str(value))


async def call_device(device: Any, spec: RpcSpec, kwargs: dict[str, Any], *, args: Sequence[Any] = ()) -> Any:
    """Run ``spec`` on ``device``; a stream returns its samples' values (``sample_count`` of them)."""

    method = getattr(device, spec.python_name)
    if spec.pattern == STREAM_PATTERN:
        count = kwargs.get("sample_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"{spec.python_name}() needs a whole sample_count of at least 1.")
        stream = method(*args, **kwargs)
        values: list[Any] = []
        try:
            async for sample in stream:
                values.append(sample.value)
                if len(values) >= count:
                    break
        finally:
            await stream.aclose()
        return values
    return plain(await method(*args, **kwargs))


def device_error(exc: BaseException) -> str:
    """How a failed call is reported outside the Control Center (no lease ids, keys or tokens)."""

    return redact(f"{type(exc).__name__}: {exc}")


def lease_lost_before_call(exc: BaseException) -> bool:
    """The lease had lapsed and the client refused before sending anything to the device."""

    if isinstance(exc, LeaseAuthorityError):
        return True
    return type(exc) is RuntimeError and "client is closed" in str(exc)


def lease_rejected(exc: BaseException) -> bool:
    """The Lab PC refused the lease. Whether the call ran is not certain, so only reads are retried."""

    return isinstance(exc, (DevicePermissionDeniedError, DeviceAuthenticationError))


__all__ = [
    "READ_PATTERNS",
    "SETTING_PATTERNS",
    "STREAM_PATTERN",
    "call_device",
    "device_error",
    "find_method",
    "json_safe",
    "lease_lost_before_call",
    "lease_rejected",
    "plain",
]
