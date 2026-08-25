from __future__ import annotations

from typing import Any

import pytest

from labnet.device_manager import (
    DeviceAvailability,
    DeviceManager,
    DeviceTypeMismatchError,
    LeaseInvalidError,
    StaticLeaseValidator,
)


class _TypedDevice:
    def __init__(self, device_id: str, device_type: str) -> None:
        self._device_id = device_id
        self.device_type = device_type
        self.connected = False
        self.read_count = 0

    @property
    def device_id(self) -> str:
        return self._device_id

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def status(self) -> str:
        return "idle" if self.connected else "disconnected"

    def availability(self) -> DeviceAvailability:
        return (
            DeviceAvailability.AVAILABLE
            if self.connected
            else DeviceAvailability.UNAVAILABLE
        )

    def metadata(self) -> dict[str, Any]:
        return {}

    def allowed_methods(self) -> list[str]:
        return ["read"]

    def read(self) -> float:
        self.read_count += 1
        return 1.25


@pytest.mark.asyncio
async def test_executor_rejects_a_valid_lease_at_the_wrong_service_family() -> None:
    device = _TypedDevice("typed-device", "wavemeter")
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "typed-device"}),
    )
    await manager.start()
    try:
        with pytest.raises(DeviceTypeMismatchError) as caught:
            await manager.executor.execute(
                "lease",
                "typed-device",
                "read",
                expected_device_type="camera",
            )

        assert caught.value.code == "DEVICE_TYPE_MISMATCH"
        assert caught.value.retryable is False
        assert caught.value.details == {
            "device_id": "typed-device",
            "expected_device_type": "camera",
            "actual_device_type": "wavemeter",
        }
        assert device.read_count == 0
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_lease_validation_precedes_device_type_disclosure() -> None:
    device = _TypedDevice("typed-device", "wavemeter")
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"valid-lease": "typed-device"}),
    )
    await manager.start()
    try:
        with pytest.raises(LeaseInvalidError):
            await manager.executor.execute(
                "invalid-lease",
                "typed-device",
                "read",
                expected_device_type="camera",
            )

        assert device.read_count == 0
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_authorized_session_remains_bound_to_its_service_family() -> None:
    device = _TypedDevice("typed-device", "wavemeter")
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "typed-device"}),
    )
    await manager.start()
    try:
        session = await manager.executor.authorize(
            "lease",
            "typed-device",
            expected_device_type="wavemeter",
        )
        assert session.expected_device_type == "wavemeter"
        assert await session.execute("read") == 1.25

        # Device types are expected to be stable. If a faulty adapter changes
        # it at runtime, an existing streaming session must not cross into the
        # newly advertised family between lease revalidations.
        device.device_type = "camera"
        await manager.refresh_snapshots()

        with pytest.raises(DeviceTypeMismatchError):
            await session.execute("read")
        with pytest.raises(DeviceTypeMismatchError):
            await session.revalidate()
        assert device.read_count == 1
    finally:
        await manager.shutdown()

