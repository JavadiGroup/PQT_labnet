"""device_calls: the call helpers device sessions and the grant gateway share."""

from __future__ import annotations

import math

import grpc
import pytest

from labnet.client import DevicePermissionDeniedError, DeviceUnavailableError, LeaseAuthorityError
from labnet.client.core.models import Measurement, MeasurementQuality
from labnet_control_center.device_calls import (
    READ_PATTERNS,
    STREAM_PATTERN,
    call_device,
    device_error,
    find_method,
    json_safe,
    lease_lost_before_call,
    lease_rejected,
    plain,
)
from labnet.grpc.generated.device_contracts import DUMMY_AOM_CONTRACT


LEASE = "lease_" + "ab" * 16


def _measurement(value: float) -> Measurement:
    return Measurement(operation_id="op", sequence=1, timestamp_ns=1, value=value, unit="MHz",
                       quality=MeasurementQuality.GOOD, status="OK")


class EndlessStream:
    """A misbehaving device that never ends its stream."""

    def __init__(self) -> None:
        self.closed = False
        self.sent = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> Measurement:
        self.sent += 1
        return _measurement(float(self.sent))

    async def aclose(self) -> None:
        self.closed = True


class FakeAOM:
    contract = DUMMY_AOM_CONTRACT

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.stream = EndlessStream()

    async def read_frequency(self, *args, **kwargs) -> Measurement:
        self.calls.append(("read_frequency", args, kwargs))
        return _measurement(80.0)

    async def set_frequency(self, *args, **kwargs) -> Measurement:
        self.calls.append(("set_frequency", args, kwargs))
        return _measurement(kwargs.get("value", args[0] if args else 0.0))

    async def set_rf_enabled(self, **kwargs) -> bool:
        self.calls.append(("set_rf_enabled", (), kwargs))
        return kwargs["enabled"]

    def stream_diffraction_efficiency(self, **kwargs) -> EndlessStream:
        self.calls.append(("stream_diffraction_efficiency", (), kwargs))
        return self.stream


def _spec(name: str):
    spec = find_method(DUMMY_AOM_CONTRACT, name)
    assert spec is not None
    return spec


def test_find_method_and_plain() -> None:
    assert find_method(DUMMY_AOM_CONTRACT, "set_frequency").python_name == "set_frequency"
    assert find_method(DUMMY_AOM_CONTRACT, "fly") is None
    assert plain(_measurement(1.5)) == 1.5
    assert plain((1, 2)) == (1, 2) and plain(None) is None
    assert _spec("stream_diffraction_efficiency").pattern == STREAM_PATTERN
    assert _spec("read_frequency").pattern in READ_PATTERNS and _spec("set_frequency").pattern not in READ_PATTERNS


@pytest.mark.asyncio
async def test_reads_writes_and_commands_return_plain_values() -> None:
    device = FakeAOM()
    assert await call_device(device, _spec("read_frequency"), {}) == 80.0
    assert await call_device(device, _spec("set_frequency"), {"value": 75.0}) == 75.0
    assert await call_device(device, _spec("set_frequency"), {}, args=(71.0,)) == 71.0  # the script box's positional form
    assert await call_device(device, _spec("set_rf_enabled"), {"enabled": False}) is False
    assert device.calls[1] == ("set_frequency", (), {"value": 75.0})
    assert device.calls[2] == ("set_frequency", (71.0,), {})


@pytest.mark.asyncio
async def test_a_stream_collects_sample_count_values_and_closes() -> None:
    device = FakeAOM()
    values = await call_device(
        device, _spec("stream_diffraction_efficiency"), {"sample_rate_hz": 10.0, "sample_count": 5}
    )
    assert values == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert device.stream.closed and device.stream.sent == 5  # stops at sample_count even if the device doesn't
    assert device.calls[0][2] == {"sample_rate_hz": 10.0, "sample_count": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [None, 0, -1, True, 2.0, "3"])
async def test_a_stream_needs_a_whole_positive_sample_count(count) -> None:
    device = FakeAOM()
    kwargs = {"sample_rate_hz": 10.0} if count is None else {"sample_rate_hz": 10.0, "sample_count": count}
    with pytest.raises(ValueError, match="sample_count"):
        await call_device(device, _spec("stream_diffraction_efficiency"), kwargs)
    assert device.calls == []  # refused before the stream opened


def test_json_safe_values() -> None:
    assert json_safe(_measurement(2.0)) == 2.0
    assert json_safe(float("nan")) is None and json_safe(math.inf) is None
    assert json_safe((1, 2, float("-inf"))) == [1, 2, None]
    assert json_safe(True) is True and json_safe("ok") == "ok" and json_safe(None) is None
    assert json_safe(object()).startswith("<object")
    assert json_safe(f"<thing {LEASE}>") == f"<thing {LEASE}>"  # text results are values, not errors
    assert LEASE not in json_safe(type("Odd", (), {"__str__": lambda self: f"odd {LEASE}"})())


def test_device_errors_are_redacted() -> None:
    message = device_error(RuntimeError(f"Lease {LEASE} expired; token lng_{'x' * 40}"))
    assert message.startswith("RuntimeError: ")
    assert LEASE not in message and "lease_…" in message
    assert "x" * 40 not in message and "lng_…" in message


def test_lease_errors_are_told_apart() -> None:
    assert lease_lost_before_call(LeaseAuthorityError(f"Lease {LEASE} expired before it could be renewed."))
    assert lease_lost_before_call(RuntimeError("The async contract device gRPC client is closed."))
    assert not lease_lost_before_call(RuntimeError("something else"))
    assert not lease_lost_before_call(ValueError("The async contract device gRPC client is closed."))
    denied = DevicePermissionDeniedError(grpc.StatusCode.PERMISSION_DENIED, "lease not valid")
    assert lease_rejected(denied) and not lease_lost_before_call(denied)
    assert not lease_rejected(DeviceUnavailableError(grpc.StatusCode.UNAVAILABLE, "down"))
