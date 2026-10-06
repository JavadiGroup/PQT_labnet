"""Device control sessions: Play/Pause, settings order, lease release, viewers."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from fastapi.testclient import TestClient

from labnet.client import LabPCInfo, ResourceConflictError
from labnet.client.core.models import DeviceInfo, EndpointDescriptor, Measurement, MeasurementQuality
from labnet.control_center.app import create_app
from labnet.control_center.config import ControlCenterSettings
from labnet.control_center.sessions import DeviceSession, SessionError
from labnet.grpc.generated.device_contracts import SIMULATED_WAVEMETER_CONTRACT


ENDPOINT = EndpointDescriptor(
    transport="grpc", target="127.0.0.1:50052", api_version="labnet.devices.v1", secure=False
)
PLAY = {
    "settings": [{"method": "set_exposure", "value": 50.0}],
    "stream": {"method": "stream_wavelength", "sample_rate_hz": 50.0, "channel": None},
}


def _settings(**overrides) -> ControlCenterSettings:
    values = dict(
        server_url="http://127.0.0.1:8008", api_key="key", ca_file=None,
        bind_host="127.0.0.1", port=8080, refresh_seconds=60.0,
        idle_release_seconds=0.3, max_hold_minutes=30.0, script_timeout_seconds=30.0,
    )
    values.update(overrides)
    return ControlCenterSettings(**values)


class FakeStream:
    def __init__(self, log: list[str], rate: float) -> None:
        self._log = log
        self._interval = 1 / rate
        self._sequence = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> Measurement:
        await asyncio.sleep(self._interval)
        self._sequence += 1
        return Measurement(
            operation_id="op", sequence=self._sequence, timestamp_ns=time.time_ns(),
            value=780.241 + self._sequence * 1e-6, unit="nm",
            quality=MeasurementQuality.GOOD, status="OK",
        )

    async def aclose(self) -> None:
        self._log.append("stream closed")


class FakeWavemeter:
    contract = SIMULATED_WAVEMETER_CONTRACT

    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def set_exposure(self, value: float) -> Measurement:
        self._log.append(f"set_exposure {value:g}")
        return value

    def stream_wavelength(self, *, sample_rate_hz: float, **_ignored) -> FakeStream:
        self._log.append("stream opened")
        return FakeStream(self._log, sample_rate_hz)

    async def aclose(self) -> None:
        self._log.append("lease released")


class FakeCentral:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.busy = False

    async def get_device(self, device_id: str) -> DeviceInfo:
        return DeviceInfo(device_id=device_id, manager_id="lab-pc-dev-02", endpoint=ENDPOINT,
                          device_type="simulated_wavemeter")

    async def acquire_device(self, contract, device_id: str) -> FakeWavemeter:
        if self.busy:
            raise ResourceConflictError("Device already has an active lease.")
        self.log.append("lease acquired")
        return FakeWavemeter(self.log)

    async def list_lab_pcs(self):
        def device(device_id: str, leased: bool = False) -> DeviceInfo:
            return DeviceInfo(
                device_id=device_id, manager_id="lab-pc-dev-02", endpoint=ENDPOINT,
                device_type="simulated_wavemeter", runtime_state="ready", availability="available",
                effective_available=not leased, allocation_state="leased" if leased else "free",
            )

        return [
            LabPCInfo(
                manager_id="lab-pc-dev-02", host="127.0.0.1", endpoint=ENDPOINT, online=True,
                last_seen=None,
                devices=(device("dev02-wavemeter"), device("dev02-busy", leased=True)),
            )
        ]

    async def aclose(self) -> None:
        pass


def _session(central: FakeCentral, **settings) -> DeviceSession:
    return DeviceSession("dev02-wavemeter", central, _settings(**settings))


@pytest.mark.asyncio
async def test_play_applies_settings_before_streaming_and_pause_releases() -> None:
    central = FakeCentral()
    session = _session(central)
    viewer = session.subscribe()

    state = await session.play(PLAY)
    await asyncio.sleep(0.2)
    paused = await session.pause()

    assert state["state"] == "streaming" and state["leased"] is True
    assert central.log[:3] == ["lease acquired", "set_exposure 50", "stream opened"]
    assert central.log[-2:] == ["stream closed", "lease released"]
    assert paused["state"] == "idle" and paused["leased"] is False
    events = [viewer.queue.get_nowait() for _ in range(viewer.queue.qsize())]
    assert [event["type"] for event in events][:2] == ["state", "reset"]
    assert any(event["type"] == "samples" for event in events)


@pytest.mark.asyncio
async def test_play_again_starts_a_fresh_graph() -> None:
    session = _session(FakeCentral())
    await session.play(PLAY)
    await asyncio.sleep(0.15)
    await session.pause()
    assert session.snapshot_event()["samples"]

    await session.play(PLAY)
    assert session.snapshot_event()["samples"] == []
    await session.pause()


@pytest.mark.asyncio
async def test_a_device_in_use_is_reported_and_nothing_is_held() -> None:
    central = FakeCentral()
    central.busy = True
    session = _session(central)

    with pytest.raises(SessionError, match="in use") as failure:
        await session.play(PLAY)

    assert failure.value.status_code == 409
    assert session.state_event()["leased"] is False


@pytest.mark.asyncio
async def test_bad_requests_are_refused_before_leasing() -> None:
    central = FakeCentral()
    session = _session(central)
    for request, message in [
        ({**PLAY, "settings": [{"method": "read_wavelength", "value": 1}]}, "not a setting"),
        ({**PLAY, "stream": {**PLAY["stream"], "method": "set_exposure"}}, "not a stream"),
        ({**PLAY, "stream": {**PLAY["stream"], "sample_rate_hz": 5000}}, "Sample rate"),
    ]:
        with pytest.raises(SessionError, match=message):
            await session.play(request)
    assert "lease acquired" not in central.log


@pytest.mark.asyncio
async def test_the_lease_is_released_when_the_last_viewer_leaves() -> None:
    central = FakeCentral()
    session = _session(central, idle_release_seconds=0.3)
    first, second = session.subscribe(), session.subscribe()
    await session.play(PLAY)

    session.unsubscribe(first)
    await asyncio.sleep(0.5)
    assert session.state_event()["leased"] is True  # someone is still watching

    session.unsubscribe(second)
    await asyncio.sleep(0.5)
    state = session.state_event()
    assert state["leased"] is False and "nobody had the page open" in state["message"]


@pytest.mark.asyncio
async def test_play_with_no_viewer_still_releases() -> None:
    session = _session(FakeCentral(), idle_release_seconds=0.3)
    await session.play(PLAY)  # e.g. pressed through the API
    await asyncio.sleep(0.6)
    assert session.state_event()["leased"] is False


@pytest.mark.asyncio
async def test_script_and_stream_share_one_lease() -> None:
    central = FakeCentral()
    session = _session(central)
    session.subscribe()
    await session.play(PLAY)
    await session.run_script("device.set_exposure(20)")
    for _ in range(100):
        if not session.state_event()["script_running"]:
            break
        await asyncio.sleep(0.1)
    await session.pause()

    assert central.log.count("lease acquired") == 1
    assert "set_exposure 20" in central.log
    assert central.log[-1] == "lease released"


class _RunningApp:
    """The Control Center on a real port, so browser disconnects are real."""

    def __init__(self, central: FakeCentral) -> None:
        self.app = create_app(_settings(idle_release_seconds=0.3), client_factory=lambda _: central)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "_RunningApp":
        self.thread.start()
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{self.port}/health/live", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        return self

    def __exit__(self, *_exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def test_leaving_an_idle_page_unregisters_the_viewer_at_once() -> None:
    """Nothing is being sent, so only the disconnect itself can end the stream."""

    with _RunningApp(FakeCentral()) as running:
        base = f"http://127.0.0.1:{running.port}/api/devices/dev02-wavemeter"
        with httpx.stream("GET", f"{base}/events", timeout=5) as events:
            next(line for line in events.iter_lines() if line.startswith("data:"))
        session = running.app.state.sessions.get("dev02-wavemeter")
        for _ in range(30):
            if session.state_event()["viewers"] == 0:
                break
            time.sleep(0.1)
        assert session.state_event()["viewers"] == 0


def test_closing_the_browser_unregisters_the_viewer() -> None:
    """A real server: disconnecting from the event stream must free the device."""

    central = FakeCentral()
    with _RunningApp(central) as running:
        base = f"http://127.0.0.1:{running.port}/api/devices/dev02-wavemeter"
        with httpx.stream("GET", f"{base}/events", timeout=5) as events:
            lines = events.iter_lines()
            next(line for line in lines if line.startswith("data:"))  # the snapshot
            state = httpx.post(f"{base}/play", json=PLAY, timeout=5).json()
            assert state["state"] == "streaming" and state["viewers"] == 1
        # The stream is closed; after the grace period the lease must be gone.
        for _ in range(40):
            if "lease released" in central.log:
                break
            time.sleep(0.1)
        assert "lease released" in central.log


async def _until(condition, seconds: float = 10.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition never became true")


@pytest.mark.asyncio
async def test_release_stops_the_stream_and_the_script_and_frees_every_lease() -> None:
    central = FakeCentral()
    session = _session(central, idle_release_seconds=60)
    viewer = session.subscribe()
    await session.play(PLAY)
    await session.run_script("other = lease('dev01-dummy-a')\nsleep(30)")
    await _until(lambda: "dev01-dummy-a" in session.activity())

    held = session.activity()
    assert held["dev02-wavemeter"]["streaming"] and held["dev02-wavemeter"]["script_running"]
    assert held["dev02-wavemeter"]["since"] is not None
    assert held["dev01-dummy-a"]["session"] == "dev02-wavemeter"

    state = await session.release("Paused from the device list.")

    assert session.activity() == {}
    assert state["leased"] is False and state["script_running"] is False
    assert state["message"] == "Paused from the device list."
    assert central.log.count("lease acquired") == central.log.count("lease released") == 2
    events = [viewer.queue.get_nowait() for _ in range(viewer.queue.qsize())]
    assert any(event.get("text") == "Paused from the device list." for event in events)


def test_the_device_list_shows_and_pauses_what_the_control_center_holds() -> None:
    """Any viewer, on any computer, can pause a page someone left running."""

    app = create_app(_settings(idle_release_seconds=60), client_factory=lambda _: FakeCentral())
    with TestClient(app) as browser:
        def devices() -> dict[str, dict]:
            snapshot = browser.get("/api/status").json()
            return {device["id"]: device for pc in snapshot["lab_pcs"] for device in pc["devices"]}

        assert devices()["dev02-wavemeter"]["control_center"] is None
        assert browser.post("/api/devices/dev02-wavemeter/play", json=PLAY).status_code == 200

        held = devices()
        meter = held["dev02-wavemeter"]
        assert meter["state"] == "leased"
        assert meter["control_center"]["streaming"] is True
        assert meter["control_center"]["page"] == "/dev02-wavemeter"
        # Leased by some other LabNet client: shown, but not ours to pause.
        assert held["dev02-busy"]["state"] == "leased" and held["dev02-busy"]["control_center"] is None

        released = browser.post("/api/devices/dev02-wavemeter/release")
        assert released.status_code == 200 and released.json()["leased"] is False
        meter = devices()["dev02-wavemeter"]
        assert meter["state"] == "available" and meter["control_center"] is None
