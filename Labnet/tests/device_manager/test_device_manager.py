from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any

import pytest

from labnet.device_manager import (
    DeviceCatalog,
    DeviceDeadlineExceededError,
    DeviceManager,
    DeviceQueueFullError,
    DeviceShutdownError,
    DeviceState,
    DeviceUnavailableError,
    DuplicateDeviceIdError,
    InvalidConfigurationError,
    LeaseInvalidError,
    MethodNotAllowedError,
    ManagerState,
    StaticLeaseValidator,
)
from labnet.device_manager.worker import _await_future


class RecordingDevice:
    device_type = "recording"

    def __init__(
        self,
        device_id: str,
        *,
        fail_connect: bool = False,
    ) -> None:
        self._device_id = device_id
        self.fail_connect = fail_connect
        self.connected = False
        self.thread_ids: list[int] = []
        self.calls: list[str] = []
        self.entered_slow = threading.Event()
        self.release_slow = threading.Event()
        self._active = 0
        self.max_active = 0
        self._activity_lock = threading.Lock()

    @property
    def device_id(self) -> str:
        return self._device_id

    def _record(self, name: str) -> None:
        self.thread_ids.append(threading.get_ident())
        self.calls.append(name)

    def connect(self) -> None:
        self._record("connect")
        if self.fail_connect:
            raise RuntimeError("not attached")
        self.connected = True

    def disconnect(self) -> None:
        self._record("disconnect")
        self.connected = False

    def status(self) -> str:
        self._record("status")
        return "idle" if self.connected else "disconnected"

    def metadata(self) -> dict[str, Any]:
        self._record("metadata")
        return {"model": "test"}

    def allowed_methods(self) -> list[str]:
        self._record("allowed_methods")
        return ["read", "slow"]

    def read(self) -> float:
        self._record("read")
        with self._activity_lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            return 1.25
        finally:
            with self._activity_lock:
                self._active -= 1

    def slow(self) -> str:
        self._record("slow")
        with self._activity_lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        self.entered_slow.set()
        try:
            if not self.release_slow.wait(timeout=2):
                raise RuntimeError("test did not release slow call")
            return "done"
        finally:
            with self._activity_lock:
                self._active -= 1


def test_catalog_rejects_duplicate_device_ids() -> None:
    with pytest.raises(DuplicateDeviceIdError) as caught:
        DeviceCatalog([RecordingDevice("same"), RecordingDevice("same")])

    assert caught.value.code == "DUPLICATE_DEVICE_ID"
    assert caught.value.details["device_id"] == "same"


@pytest.mark.parametrize(
    "device_id",
    [" leading-space", "trailing-space ", "path/device", "", "a" * 129],
)
def test_catalog_rejects_ids_outside_the_wire_contract(device_id: str) -> None:
    with pytest.raises(InvalidConfigurationError, match="device_id"):
        DeviceCatalog([RecordingDevice(device_id)])


@pytest.mark.asyncio
async def test_partial_startup_and_allow_list_enforcement() -> None:
    good = RecordingDevice("good")
    bad = RecordingDevice("bad", fail_connect=True)
    manager = DeviceManager(
        [good, bad],
        StaticLeaseValidator({"lease-good": "good", "lease-bad": "bad"}),
    )

    report = await manager.start()
    try:
        assert report.ready_device_ids == ("good",)
        assert report.failed_device_ids == ("bad",)
        assert manager.get_snapshot("good").state is DeviceState.READY
        assert manager.get_snapshot("bad").state is DeviceState.FAILED
        assert manager.get_snapshot("bad").failure is not None

        assert (
            await manager.executor.execute("lease-good", "good", "read")
            == 1.25
        )
        with pytest.raises(MethodNotAllowedError):
            await manager.executor.execute("lease-good", "good", "disconnect")
        with pytest.raises(LeaseInvalidError):
            await manager.executor.execute("wrong", "good", "read")
        # Invalid credentials must not reveal whether a requested device exists.
        with pytest.raises(LeaseInvalidError):
            await manager.executor.execute("wrong", "missing", "read")
    finally:
        await manager.shutdown()

    # connect/status/metadata/allowed method lookup/call/disconnect are all
    # confined to the device's single persistent worker thread.
    assert len(set(good.thread_ids)) == 1
    assert "disconnect" in good.calls
    assert len(set(bad.thread_ids)) == 1
    assert "disconnect" in bad.calls


@pytest.mark.asyncio
async def test_timeout_never_allows_overlapping_physical_calls() -> None:
    device = RecordingDevice("dev")
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "dev"}),
    )
    await manager.start()

    first = asyncio.create_task(
        manager.executor.execute(
            "lease",
            "dev",
            "slow",
            deadline=0.05,
        )
    )
    await asyncio.to_thread(device.entered_slow.wait, 1)
    with pytest.raises(DeviceDeadlineExceededError) as caught:
        await first
    assert caught.value.details["operation_may_have_completed"] is True

    # This request queues behind the timed-out physical call. It times out and
    # is cancelled before starting rather than running concurrently.
    with pytest.raises(DeviceDeadlineExceededError):
        await manager.executor.execute(
            "lease",
            "dev",
            "read",
            deadline=0.05,
        )
    assert device.max_active == 1
    assert "read" not in device.calls

    device.release_slow.set()
    for _ in range(100):
        if manager.executor.get_worker("dev").pending_count == 0:
            break
        await asyncio.sleep(0.01)

    assert await manager.executor.execute("lease", "dev", "read") == 1.25
    assert device.max_active == 1
    await manager.shutdown()
    assert len(set(device.thread_ids)) == 1


@pytest.mark.asyncio
async def test_device_command_queue_is_bounded() -> None:
    device = RecordingDevice("bounded")
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "bounded"}),
        queue_capacity=1,
    )
    await manager.start()

    running = asyncio.create_task(
        manager.executor.execute("lease", "bounded", "slow")
    )
    await asyncio.to_thread(device.entered_slow.wait, 1)
    queued = asyncio.create_task(
        manager.executor.execute("lease", "bounded", "read")
    )
    for _ in range(100):
        if manager.executor.get_worker("bounded").pending_count == 1:
            break
        await asyncio.sleep(0.001)

    with pytest.raises(DeviceQueueFullError):
        await manager.executor.execute("lease", "bounded", "read")

    device.release_slow.set()
    assert await running == "done"
    assert await queued == 1.25
    await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_reserves_disconnect_when_command_queue_is_full() -> None:
    device = RecordingDevice("shutdown-bounded")
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "shutdown-bounded"}),
        queue_capacity=1,
        shutdown_timeout=0.05,
    )
    await manager.start()

    running = asyncio.create_task(
        manager.executor.execute("lease", "shutdown-bounded", "slow")
    )
    await asyncio.to_thread(device.entered_slow.wait, 1)
    queued = asyncio.create_task(
        manager.executor.execute("lease", "shutdown-bounded", "read")
    )
    for _ in range(100):
        if manager.executor.get_worker("shutdown-bounded").pending_count == 1:
            break
        await asyncio.sleep(0.001)

    with pytest.raises(DeviceShutdownError):
        await manager.shutdown()
    assert manager.state is ManagerState.STOPPING
    with pytest.raises(asyncio.CancelledError):
        await queued

    device.release_slow.set()
    assert await running == "done"
    for _ in range(100):
        if not device.connected:
            break
        await asyncio.sleep(0.01)
    assert device.connected is False

    # A timed-out shutdown remains retryable and reaches a stable STOPPED state.
    await manager.shutdown()
    assert manager.state is ManagerState.STOPPED


@pytest.mark.asyncio
async def test_zero_budget_reports_an_already_completed_operation_as_ambiguous() -> None:
    future: concurrent.futures.Future[str] = concurrent.futures.Future()
    future.set_result("applied")

    with pytest.raises(DeviceDeadlineExceededError) as caught:
        await _await_future(
            future,
            timeout=0.0,
            device_id="completed",
            operation="write_value",
            cancel_if_pending=True,
        )

    assert caught.value.details["operation_may_have_completed"] is True


@pytest.mark.asyncio
async def test_disconnect_failure_remains_an_explicit_cleanup_failure() -> None:
    class FailingDisconnectDevice(RecordingDevice):
        def disconnect(self) -> None:
            self._record("disconnect")
            raise RuntimeError("transient cleanup failure")

    device = FailingDisconnectDevice("disconnect-failure")
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "disconnect-failure"}),
    )
    await manager.start()

    with pytest.raises(DeviceShutdownError):
        await manager.shutdown()
    assert manager.state is ManagerState.STOPPING
    snapshot = manager.get_snapshot("disconnect-failure")
    assert snapshot.state is DeviceState.FAILED
    assert snapshot.failure is not None
    assert snapshot.failure.code == "DEVICE_SHUTDOWN_FAILED"

    with pytest.raises(DeviceShutdownError):
        await manager.shutdown()
    assert manager.state is ManagerState.STOPPING
    assert device.calls.count("disconnect") == 1


@pytest.mark.asyncio
async def test_late_connect_stays_failed_after_startup_timeout() -> None:
    class SlowConnectDevice(RecordingDevice):
        def __init__(self) -> None:
            super().__init__("slow-connect")
            self.connect_started = threading.Event()
            self.release_connect = threading.Event()

        def connect(self) -> None:
            self._record("connect")
            self.connect_started.set()
            self.release_connect.wait(timeout=2)
            self.connected = True

    device = SlowConnectDevice()
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "slow-connect"}),
        worker_start_timeout=0.05,
    )

    report = await manager.start()
    assert report.failed_device_ids == ("slow-connect",)
    assert manager.get_snapshot("slow-connect").state is DeviceState.FAILED

    device.release_connect.set()
    await asyncio.sleep(0.05)
    assert manager.get_snapshot("slow-connect").state is DeviceState.FAILED
    await manager.shutdown()


@pytest.mark.asyncio
async def test_started_hung_health_check_marks_device_unresponsive() -> None:
    class HungHealthDevice(RecordingDevice):
        def __init__(self) -> None:
            super().__init__("hung-health")
            self.hang_status = False
            self.status_started = threading.Event()
            self.release_status = threading.Event()

        def status(self) -> str:
            self._record("status")
            if self.hang_status:
                self.status_started.set()
                self.release_status.wait(timeout=2)
            return "idle" if self.connected else "disconnected"

    device = HungHealthDevice()
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "hung-health"}),
    )
    await manager.start()
    device.hang_status = True

    await manager.refresh_snapshots(deadline_per_device=0.02)
    snapshot = manager.get_snapshot("hung-health")
    assert snapshot.status == "unresponsive"
    assert snapshot.available is False

    device.hang_status = False
    device.release_status.set()
    for _ in range(100):
        if manager.get_snapshot("hung-health").available:
            break
        await asyncio.sleep(0.01)
    assert manager.get_snapshot("hung-health").available is True
    await manager.shutdown()


@pytest.mark.asyncio
async def test_quiesce_counts_running_work_and_rejects_new_submissions() -> None:
    device = RecordingDevice("drain-device")
    manager = DeviceManager(
        [device],
        StaticLeaseValidator({"lease": "drain-device"}),
    )
    await manager.start()
    try:
        running = asyncio.create_task(
            manager.executor.execute("lease", "drain-device", "slow")
        )
        await asyncio.to_thread(device.entered_slow.wait, 1)

        activity = manager.begin_quiesce()["drain-device"]
        assert activity.running_count == 1
        assert activity.idle is False
        with pytest.raises(DeviceUnavailableError, match="draining"):
            await manager.executor.execute("lease", "drain-device", "read")

        waiting = asyncio.create_task(manager.wait_quiescent())
        await asyncio.sleep(0)
        assert waiting.done() is False
        device.release_slow.set()
        assert await running == "done"
        drained = await waiting
        assert drained["drain-device"].idle is True

        manager.resume()
        assert (
            await manager.executor.execute("lease", "drain-device", "read")
            == 1.25
        )
    finally:
        device.release_slow.set()
        await manager.shutdown()
