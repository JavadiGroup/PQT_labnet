"""Device grants: the per-call rules (check_call), the GrantManager's lifecycle, the gateway and stop routes."""

from __future__ import annotations

import asyncio
import copy
import functools
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import grpc
import pytest
from fastapi.testclient import TestClient

from labnet.client import (
    DevicePermissionDeniedError,
    DeviceUnavailableError,
    LabPCInfo,
    LeaseAuthorityError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from labnet.client.core.models import DeviceInfo, Measurement, MeasurementQuality
from labnet.control_center.app import create_app
from labnet.control_center.automation_grants import (
    MAX_REFUSED_IN_A_ROW,
    PUBLIC_KEYS,
    GrantCallState,
    GrantError,
    GrantManager,
    check_call,
    step_key,
)
from labnet.control_center.automation_runs import RunGrant
from labnet.control_center.automation_store import AutomationStore
from labnet.control_center.device_calls import STREAM_PATTERN, find_method
from labnet.control_center.redaction import redact

from .test_automation import ENDPOINT, _settings


PROJECT = "aom-study-abc234"
RUN = "run-20261007-120000-abcd"
RUN2 = "run-20261007-120001-efgh"
LEASE = "lease_" + "0f" * 16
RUNNER = "/api/automation/runner"
GATEWAY = f"{RUNNER}/gateway/calls"

PROTOCOL: dict[str, Any] = {
    "schema_version": 1,
    "task": "Tasks/Task1.md",
    "title": "AOM efficiency vs RF frequency",
    "duration_minutes": 20.0,
    "devices": [
        {
            "device_id": "dev01-aom",
            "device_type": "dummy_aom",
            "methods": {
                "set_frequency": {
                    "args": {"value": {"min": 70.0, "max": 90.0, "max_step": 2.0}},
                    "max_calls": 200.0, "max_rate_hz": 5.0,
                },
                "set_rf_enabled": {"args": {"enabled": {"choices": [True, False]}}, "max_calls": 10.0,
                                   "max_rate_hz": 1.0},
                "read_diffraction_efficiency": {"args": {}, "max_calls": 500.0, "max_rate_hz": 10.0},
                "stream_diffraction_efficiency": {
                    "args": {"channel": {"choices": ["CH1"]}},
                    "stream": {"max_sample_rate_hz": 20.0, "max_samples": 1000.0},
                    "max_calls": 50.0, "max_rate_hz": 1.0,
                },
            },
        },
        {
            "device_id": "dev01-awg",
            "device_type": "dummy_awg",
            "methods": {
                "set_waveform": {"args": {"waveform": {"choices": ["sine", "square"]}}, "max_calls": 5.0,
                                 "max_rate_hz": 1.0},
                # channel is optional and unbounded here, so it may not be sent.
                "set_amplitude": {"args": {"value": {"min": 0.0, "max": 1.0}}, "max_calls": 5.0, "max_rate_hz": 1.0},
                "read_amplitude": {"args": {}, "max_calls": 100.0, "max_rate_hz": 100.0},
            },
        },
        {
            "device_id": "dev01-sacher",
            "device_type": "sacher_laser",
            "methods": {
                # No max_step: the Sacher can't report where a jog leaves it (see step_reader).
                "jog_steps": {"args": {"relative_steps": {"min": -100.0, "max": 100.0}},
                              "max_calls": 5.0, "max_rate_hz": 1.0},
            },
        },
    ],
    "safe_state": [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False}}],
}
AOM_ONLY: dict[str, Any] = {**PROTOCOL, "devices": [PROTOCOL["devices"][0]]}
AWG_ONLY: dict[str, Any] = {**PROTOCOL, "devices": [PROTOCOL["devices"][1]], "safe_state": []}
TYPES = {"dev01-aom": "dummy_aom", "dev01-awg": "dummy_awg", "dev01-sacher": "sacher_laser"}


def _req(device: str = "dev01-aom", method: str = "set_frequency", **args: Any) -> dict[str, Any]:
    return {"device_id": device, "method": method, "args": args}


def _stream(rate: Any = 10.0, count: Any = 50, **args: Any) -> dict[str, Any]:
    args.setdefault("channel", "CH1")  # bounded, so always sent
    return {**_req(method="stream_diffraction_efficiency", **args), "sample_rate_hz": rate, "sample_count": count}


FREQ = "dev01-aom.set_frequency.value"  # where max_step of set_frequency is measured from


def _at(request: Any) -> GrantCallState:
    """A state in which each number the request sends is already where it asks to go (as if the
    gateway had just read it there), so a check isolates the rule under test from max_step."""

    state = GrantCallState()
    try:
        args = request["args"]
        for name, value in args.items():
            if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value):
                state.last_values[step_key(request["device_id"], request["method"], name, args)] = (value, value)
    except Exception:  # noqa: BLE001 - a malformed request: nothing to place
        pass
    return state


def _check(request: Any, state: GrantCallState | None = None, *, protocol: dict | None = None,
           now: float = 1000.0, seconds_left: float = 600.0) -> str | None:
    return check_call(protocol or PROTOCOL, state if state is not None else _at(request), request, now=now,
                      seconds_left=seconds_left)


# ---- check_call: the rules, one by one ---------------------------------------------------------


def test_an_allowed_call_passes_and_check_call_changes_nothing() -> None:
    state = GrantCallState(last_values={FREQ: (79.0, 79.0)})
    before = copy.deepcopy(state)
    assert _check(_req(value=80.0), state) is None
    assert state == before  # pure


@pytest.mark.parametrize("value", [70.0, 90.0, 70, 90, 80.5, 89.99999999])
def test_numbers_exactly_at_the_bounds_are_allowed(value) -> None:
    assert _check(_req(value=value)) is None


@pytest.mark.parametrize("value", [69.99999999, 90.00000001, -80.0, 1e308, 91, -(10**20)])
def test_numbers_outside_the_range_are_refused(value) -> None:
    assert "outside the approved range" in _check(_req(value=value))


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_integers_too_large_for_a_float_are_refused(value) -> None:
    assert _check(_req(value=value)) is not None


@pytest.mark.parametrize("value, message", [
    (True, "must be a number, not a boolean"),
    (False, "must be a number, not a boolean"),
    ("80", "must be a number, not text"),
    (None, "must be a number, not null"),
    ([80.0], "must be a number, not a list"),
    ({"v": 80.0}, "must be a number, not an object"),
    (math.nan, "finite"),
    (math.inf, "finite"),
    (-math.inf, "finite"),
])
def test_only_real_finite_json_numbers_are_numbers(value, message) -> None:
    assert message in _check(_req(value=value))


def test_max_step_is_measured_from_the_last_value_set() -> None:
    state = GrantCallState(last_values={FREQ: (80.0, 80.0)})
    assert _check(_req(value=82.0), state) is None  # exactly max_step
    assert _check(_req(value=78.0), state) is None
    assert "at most 2 per call" in _check(_req(value=82.000001), state)
    assert "at most 2 per call" in _check(_req(value=77.9), state)
    assert "at most 2 per call" in _check(_req(value=90.0), state)
    # Where it is now unknown (nothing set, nothing read): a stepped change is refused (the gateway reads first).
    assert "isn't known" in _check(_req(value=90.0), GrantCallState())
    assert "isn't known" in _check(_req(value=80.0), GrantCallState())
    # Float rounding (70.1 → 72.1 is 2.0000000000000x) is not a violation...
    rounding = GrantCallState(last_values={FREQ: (70.1, 70.1)})
    assert _check(_req(value=72.1), rounding) is None
    # ...but anything measurably more is.
    assert _check(_req(value=72.1 + 1e-9), rounding) is not None


def test_a_call_that_succeeded_sets_the_position_and_one_that_failed_widens_it() -> None:
    state = GrantCallState()
    request = _req(value=80.0)
    state.started(request, 1000.0)
    state.finished(request, succeeded=False)
    assert state.last_values == {} and state.busy == set()  # unknown before, still unknown (read next time)
    assert state.calls == {"dev01-aom.set_frequency": 1}  # it still counts as a call
    state.started(request, 1001.0)
    state.finished(request, succeeded=True)
    assert state.last_values == {FREQ: (80.0, 80.0)}
    assert _check(_req(value=83.0), state, now=1002.0) is not None
    # 82 was sent, then the call failed: it may be at 80 or at 82, or anywhere between.
    state.started(_req(value=82.0), 1003.0)
    state.finished(_req(value=82.0), succeeded=False)
    assert state.last_values == {FREQ: (80.0, 82.0)}
    assert "somewhere from 80 to 82" in _check(_req(value=78.0), state, now=1010.0)  # 2 from 80, 4 from 82
    assert _check(_req(value=84.0), state, now=1010.0) is not None  # 2 from 82, 4 from 80
    assert _check(_req(value=81.0), state, now=1010.0) is None  # within 2 of both ends
    assert _check(_req(value=80.0), state, now=1010.0) is None and _check(_req(value=82.0), state, now=1010.0) is None
    # A success narrows it back to the value.
    state.started(_req(value=81.0), 1011.0)
    state.finished(_req(value=81.0), succeeded=True)
    assert state.last_values == {FREQ: (81.0, 81.0)}


@pytest.mark.parametrize("value, allowed", [(True, True), (False, True), (1, False), (0, False), ("true", False),
                                            (None, False)])
def test_booleans_must_be_real_json_booleans_from_the_choices(value, allowed) -> None:
    result = _check(_req(method="set_rf_enabled", enabled=value))
    assert (result is None) is allowed, result
    only_off = copy.deepcopy(PROTOCOL)
    only_off["devices"][0]["methods"]["set_rf_enabled"]["args"]["enabled"]["choices"] = [False]
    assert "not one of the approved choices" in _check(_req(method="set_rf_enabled", enabled=True), protocol=only_off)


@pytest.mark.parametrize("value, allowed", [("sine", True), ("square", True), ("triangle", False), ("SINE", False),
                                            (1, False), (True, False), (None, False)])
def test_text_must_be_one_of_the_choices(value, allowed) -> None:
    assert (_check(_req("dev01-awg", "set_waveform", waveform=value)) is None) is allowed


def test_whole_number_fields_take_only_json_integers() -> None:
    jog = functools.partial(_req, "dev01-sacher", "jog_steps")
    assert _check(jog(relative_steps=5)) is None
    assert _check(jog(relative_steps=-100)) is None and _check(jog(relative_steps=100)) is None
    assert "whole number" in _check(jog(relative_steps=5.0))
    assert "not a boolean" in _check(jog(relative_steps=True))
    assert "outside" in _check(jog(relative_steps=101))


def test_args_are_exactly_the_bounded_keyword_arguments() -> None:
    assert "unit may not be sent" in _check(_req(value=80.0, unit="MHz"))
    assert "takes no argument 'speed'" in _check(_req(value=80.0, speed=1))
    assert "needs value" in _check(_req())
    assert "needs enabled" in _check(_req(method="set_rf_enabled"))
    # channel is optional and unbounded for set_amplitude: leave it out, or the call is refused.
    assert _check(_req("dev01-awg", "set_amplitude", value=0.5)) is None
    assert "not bounded" in _check(_req("dev01-awg", "set_amplitude", value=0.5, channel="CH1"))
    assert "not bounded" in _check(_req("dev01-awg", "read_amplitude", channel="CH1"))
    # Positional arguments don't exist here.
    for args in ([80.0], "80", None, 80.0):
        assert "keyword arguments" in _check({"device_id": "dev01-aom", "method": "set_frequency", "args": args})
    assert "keyword arguments" in _check({"device_id": "dev01-aom", "method": "read_diffraction_efficiency"})


def test_the_request_itself_must_be_well_formed() -> None:
    assert "JSON object" in _check([1, 2])
    assert "JSON object" in _check("set_frequency(80)")
    assert "Unknown field" in _check({**_req(value=80.0), "kwargs": {}})
    assert "Unknown field" in _check({**_req(value=80.0), "lease_id": LEASE})
    assert "as text" in _check({"device_id": 1, "method": "set_frequency", "args": {"value": 80.0}})
    assert "as text" in _check({"method": "set_frequency", "args": {"value": 80.0}})


def test_only_the_grants_devices_and_methods() -> None:
    assert "not one of this grant's devices" in _check(_req("dev02-wavemeter", "read_wavelength"))
    assert "not allowed on dev01-aom" in _check(_req(method="read_frequency"))  # a real method, not listed
    assert "not allowed on dev01-aom" in _check(_req(method="set_rf_power", value=1.0))
    odd = copy.deepcopy(PROTOCOL)
    odd["devices"][0]["methods"]["fly"] = {"args": {}, "max_calls": 1.0, "max_rate_hz": 1.0}
    assert "not a method of dummy_aom" in _check(_req(method="fly"), protocol=odd)
    unknown = copy.deepcopy(PROTOCOL)
    unknown["devices"][0]["device_type"] = "warp_drive"
    assert _check(_req(value=80.0), protocol=unknown) is not None


def test_stream_limits() -> None:
    assert _check(_stream()) is None
    assert _check(_stream(channel="CH1")) is None
    unchanneled = _stream()
    del unchanneled["args"]["channel"]
    assert "every stream_diffraction_efficiency call must send it" in _check(unchanneled)
    assert "not one of the approved choices" in _check(_stream(channel="CH2"))
    assert _check(_stream(rate=20.0)) is None and _check(_stream(rate=20)) is None
    assert "above the approved 20 Hz" in _check(_stream(rate=20.000001))
    for rate in (0, -1.0, True, "10", None, math.nan, math.inf):
        assert "sample_rate_hz" in _check(_stream(rate=rate)), rate
    assert _check(_stream(count=1)) is None and _check(_stream(count=1000)) is None
    assert "outside the approved 1 … 1000" in _check(_stream(count=1001))
    assert "outside the approved 1 … 1000" in _check(_stream(count=0))
    for count in (5.0, True, "5", None):
        assert "whole number" in _check(_stream(count=count)), count
    request = _stream()
    del request["sample_count"]
    assert "sample_count" in _check(request)
    request = _stream()
    del request["sample_rate_hz"]
    assert "sample_rate_hz" in _check(request)


def test_a_stream_must_fit_in_the_time_left() -> None:
    assert _check(_stream(rate=10.0, count=1000), seconds_left=100.0) is None  # exactly 100 s
    assert "only 99.9 s left" in _check(_stream(rate=10.0, count=1000), seconds_left=99.9)
    assert _check(_stream(rate=20.0, count=1), seconds_left=0.05) is None


def test_non_streams_send_no_stream_options() -> None:
    assert "not a stream" in _check({**_req(value=80.0), "sample_rate_hz": 10.0})
    assert "not a stream" in _check({**_req(value=80.0), "sample_count": 5})
    assert "not a stream" in _check({**_req(value=80.0), "sample_count": None})


def test_call_counts() -> None:
    nine = GrantCallState(calls={"dev01-aom.set_rf_enabled": 9})
    ten = GrantCallState(calls={"dev01-aom.set_rf_enabled": 10}, last_values={FREQ: (80.0, 80.0)})
    assert _check(_req(method="set_rf_enabled", enabled=False), nine) is None
    assert "used all 10 approved calls" in _check(_req(method="set_rf_enabled", enabled=False), ten)
    assert _check(_req(value=80.0), ten) is None  # counted per method


def test_call_rates_with_an_injected_clock() -> None:
    state = GrantCallState(last_call_at={"dev01-aom.set_frequency": 0.0},  # 5 Hz: one call per 0.2 s
                           last_values={FREQ: (80.0, 80.0)})
    assert "at most 5 times per second" in _check(_req(value=80.0), state, now=0.1)
    assert "at most 5 times per second" in _check(_req(value=80.0), state, now=0.19999)
    assert _check(_req(value=80.0), state, now=0.2) is None
    assert _check(_req(method="read_diffraction_efficiency"), state, now=0.1) is None  # per method


def test_one_call_at_a_time_per_device() -> None:
    state = GrantCallState(busy={"dev01-aom"})
    assert "busy" in _check(_req(value=80.0), state)
    assert _check(_req("dev01-awg", "read_amplitude"), state) is None


@pytest.mark.parametrize("seconds_left", [0.0, -1.0, math.nan, -math.inf])
def test_no_time_left_no_calls(seconds_left) -> None:
    assert "time is up" in _check(_req(value=80.0), seconds_left=seconds_left)


@pytest.mark.parametrize("damage", [
    lambda p: p.update(devices=None),
    lambda p: p.update(devices="dev01-aom"),
    lambda p: p["devices"][0].update(methods=None),
    lambda p: p["devices"][0]["methods"].update(set_frequency=None),
    lambda p: p["devices"][0]["methods"]["set_frequency"].pop("max_calls"),
    lambda p: p["devices"][0]["methods"]["set_frequency"].update(max_rate_hz=0),
    lambda p: p["devices"][0]["methods"]["set_frequency"].update(max_rate_hz=True),
    lambda p: p["devices"][0]["methods"]["set_frequency"]["args"]["value"].pop("max"),
    lambda p: p["devices"][0]["methods"]["set_frequency"]["args"]["value"].update(min=95.0),
    lambda p: p["devices"][0]["methods"]["set_frequency"]["args"]["value"].update(max=math.inf),
    lambda p: p["devices"][0]["methods"]["set_frequency"]["args"]["value"].update(max_step=0.0),
    lambda p: p["devices"][0]["methods"]["set_frequency"]["args"]["value"].update(max_step="2"),
    lambda p: p["devices"][0]["methods"]["set_frequency"]["args"]["value"].update(choices=[80.0]),
    lambda p: p["devices"][0]["methods"]["set_frequency"].update(args=[]),
    lambda p: p["devices"][0]["methods"]["set_frequency"]["args"].update(value="70-90"),
])
def test_a_malformed_protocol_refuses_rather_than_allows(damage) -> None:
    protocol = copy.deepcopy(PROTOCOL)
    damage(protocol)
    assert _check(_req(value=80.0), protocol=protocol) is not None


def test_malformed_stream_and_choice_bounds_refuse() -> None:
    for damage in (
        lambda p: p["devices"][0]["methods"]["stream_diffraction_efficiency"].pop("stream"),
        lambda p: p["devices"][0]["methods"]["stream_diffraction_efficiency"]["stream"].update(max_samples=0),
        lambda p: p["devices"][0]["methods"]["stream_diffraction_efficiency"]["stream"].update(max_sample_rate_hz=0),
    ):
        protocol = copy.deepcopy(PROTOCOL)
        damage(protocol)
        assert _check(_stream(), protocol=protocol) is not None
    protocol = copy.deepcopy(PROTOCOL)
    protocol["devices"][0]["methods"]["set_rf_enabled"]["args"]["enabled"] = {"choices": "true"}
    assert _check(_req(method="set_rf_enabled", enabled=True), protocol=protocol) is not None


# ---- fakes for the GrantManager ------------------------------------------------------------------


def _measurement(value: float) -> Measurement:
    return Measurement(operation_id="op", sequence=1, timestamp_ns=time.time_ns(), value=value, unit="1",
                       quality=MeasurementQuality.GOOD, status="OK")


class FakeStream:
    def __init__(self, count: int) -> None:
        self.left = count

    def __aiter__(self):
        return self

    async def __anext__(self) -> Measurement:
        if self.left <= 0:
            raise StopAsyncIteration
        self.left -= 1
        await asyncio.sleep(0)
        return _measurement(0.25)

    async def aclose(self) -> None:
        pass


class FakeDevice:
    """A leased device: every call goes to the FakeLab, which logs it and may fail or delay it."""

    def __init__(self, lab: "FakeLab", device_id: str, contract) -> None:
        self.lab = lab
        self.device_id = device_id
        self.contract = contract
        self.closed = False

    def __getattr__(self, name: str):
        contract = self.__dict__.get("contract")
        spec = find_method(contract, name) if contract is not None else None
        if spec is None:
            raise AttributeError(name)
        if spec.pattern == STREAM_PATTERN:
            def stream(**kwargs):
                self.lab.calls.append((self.device_id, name, kwargs))
                return FakeStream(kwargs.get("sample_count", 3))
            return stream

        async def unary(**kwargs):
            return await self.lab.unary(self, name, kwargs)
        return unary

    async def aclose(self) -> None:
        self.closed = True
        self.lab.log.append(("release", self.device_id))


class FakeLab:
    """Central and the Lab PCs: leases, device types, calls (with scripted failures and delays)."""

    def __init__(self) -> None:
        self.types = dict(TYPES)
        self.busy: set[str] = set()
        self.log: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.failures: dict[tuple[str, str], list[BaseException]] = {}
        self.delays: dict[tuple[str, str], float] = {}
        self.devices: list[FakeDevice] = []
        self.acquire_delay = 0.0
        # Where each settable quantity is: (device, quantity, channel) → value. A set_<x> that
        # succeeds moves it; read_<x> reports it (0.5 for anything never set).
        self.positions: dict[tuple[str, str, Any], float] = {("dev01-aom", "frequency", None): 80.0}

    async def get_device(self, device_id: str) -> DeviceInfo:
        if device_id not in self.types:
            raise ResourceNotFoundError("Device not found.")
        return DeviceInfo(device_id=device_id, manager_id="lab-pc-dev-01", endpoint=ENDPOINT,
                          device_type=self.types[device_id])

    async def acquire_device(self, contract, device_id: str) -> FakeDevice:
        await asyncio.sleep(self.acquire_delay)
        if device_id in self.busy:
            raise ResourceConflictError("Device already has an active lease.")
        assert contract.device_type == self.types[device_id]
        self.log.append(("lease", device_id))
        device = FakeDevice(self, device_id, contract)
        self.devices.append(device)
        return device

    async def unary(self, device: FakeDevice, name: str, kwargs: dict) -> Any:
        assert not device.closed, "called a released lease"
        self.calls.append((device.device_id, name, kwargs))
        if (delay := self.delays.get((device.device_id, name))) is not None:
            await asyncio.sleep(delay)
        failures = self.failures.get((device.device_id, name))
        if failures:
            raise failures.pop(0)
        if name.startswith("read_"):
            return _measurement(self.positions.get((device.device_id, name[5:], kwargs.get("channel")), 0.5))
        if name.startswith("set_") and "value" in kwargs:
            self.positions[(device.device_id, name[4:], kwargs.get("channel"))] = kwargs["value"]
        return kwargs.get("value", kwargs.get("enabled", kwargs.get("waveform")))

    def held(self) -> set[str]:
        return {device.device_id for device in self.devices if not device.closed}

    def calls_to(self, method: str) -> list[dict]:
        return [kwargs for _device, name, kwargs in self.calls if name == method]

    # What the Control Center's network page needs.
    async def list_lab_pcs(self):
        def device(device_id: str) -> DeviceInfo:
            return DeviceInfo(device_id=device_id, manager_id="lab-pc-dev-01", endpoint=ENDPOINT,
                              device_type=self.types[device_id], runtime_state="ready", availability="available",
                              effective_available=True, allocation_state="free")

        return [LabPCInfo(manager_id="lab-pc-dev-01", host="127.0.0.1", endpoint=ENDPOINT, online=True,
                          last_seen=None, devices=tuple(device(name) for name in self.types))]

    async def aclose(self) -> None:
        pass


class FakeRuns:
    """The RunStore's side: each grant written must be a valid RunGrant."""

    def __init__(self) -> None:
        self.grants: list[dict] = []
        self.phases: list[str] = []
        self.status = "active"

    def set_grant(self, _project_id: str, _run_id: str, grant: dict) -> None:
        assert set(grant) == set(PUBLIC_KEYS)
        RunGrant.model_validate(grant)
        self.grants.append(grant)

    def set_phase(self, _project_id: str, _run_id: str, phase: str) -> None:
        self.phases.append(phase)

    def get(self, _project_id: str, _run_id: str) -> dict:
        return {"status": self.status}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _manager(tmp_path: Path, lab: FakeLab | None = None, runs: FakeRuns | None = None, **kwargs) -> GrantManager:
    return GrantManager(lab or FakeLab(), AutomationStore(tmp_path / "automation"), runs or FakeRuns(), None, **kwargs)


async def _grant(manager: GrantManager, protocol: dict = AOM_ONLY, minutes: float = 5.0, run_id: str = RUN):
    grant = await manager.create(project_id=PROJECT, run_id=run_id, protocol=protocol, sha256="a" * 64,
                                 duration_minutes=minutes, approved_by="alice")
    token, _ = manager.take_token(run_id)
    return grant, token


async def _until(condition, seconds: float = 10.0) -> None:
    for _ in range(int(seconds / 0.02)):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


def _audit(tmp_path: Path, grant_id: str, run_id: str = RUN) -> list[dict]:
    path = tmp_path / "automation" / "state" / PROJECT / "audit" / run_id / f"grant-{grant_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _record(tmp_path: Path, grant_id: str) -> dict:
    path = tmp_path / "automation" / "state" / PROJECT / "grants" / f"{grant_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def _refused(manager: GrantManager, token: str, request: Any) -> GrantError:
    with pytest.raises(GrantError) as failure:
        await manager.call(token, request)
    return failure.value


# ---- the GrantManager ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_takes_every_lease_and_a_call_runs_and_is_audited(tmp_path: Path) -> None:
    lab, runs = FakeLab(), FakeRuns()
    manager = _manager(tmp_path, lab, runs)
    grant, token = await _grant(manager, PROTOCOL)

    assert lab.held() == {"dev01-aom", "dev01-awg", "dev01-sacher"}
    assert grant["status"] == "active" and grant["approved_by"] == "alice"
    assert grant["devices"] == ["dev01-aom", "dev01-awg", "dev01-sacher"]
    assert runs.grants[-1]["status"] == "active" and runs.phases == []  # the approve route moves the phase
    assert token.startswith("lng_") and len(token) > 40

    reply = await manager.call(token, _req(value=80.0))
    assert reply["result"] == 80.0 and reply["calls_left"] == 199 and 0 < reply["seconds_left"] <= 300
    read = await manager.call(token, _req(method="read_diffraction_efficiency"))
    assert read["result"] == 0.5
    stream = await manager.call(token, _stream(rate=10.0, count=3))
    assert stream["result"] == [0.25, 0.25, 0.25]
    assert lab.calls_to("stream_diffraction_efficiency") == [{"channel": "CH1", "sample_rate_hz": 10.0,
                                                               "sample_count": 3}]
    assert lab.calls_to("set_frequency") == [{"value": 80.0}]
    assert lab.calls_to("read_frequency") == [{}]  # where it was, read before the first change

    lines = _audit(tmp_path, grant["id"])
    calls = [line for line in lines if line["kind"] == "call"]
    assert [line["outcome"] for line in calls] == ["ok", "ok", "ok"]
    assert calls[0]["args"] == {"value": 80.0} and calls[0]["result"] == 80.0 and "duration_ms" in calls[0]
    assert calls[2]["samples"] == 3 and calls[2]["sample_rate_hz"] == 10.0
    assert lines[0]["kind"] == "start" and lines[0]["approved_by"] == "alice"
    assert manager.grant_for_run(RUN)["calls"] == 3
    await manager.close()


@pytest.mark.asyncio
async def test_create_is_all_or_nothing(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.busy.add("dev01-awg")
    manager = _manager(tmp_path, lab)
    with pytest.raises(GrantError) as failure:
        await _grant(manager, PROTOCOL)
    assert failure.value.status_code == 409 and "dev01-awg is in use" in failure.value.detail
    assert lab.log == [("lease", "dev01-aom"), ("release", "dev01-aom")]
    assert lab.held() == set() and not manager.blocked("dev01-aom") and manager.activity() == {}
    assert not (tmp_path / "automation" / "state" / PROJECT / "grants").exists()
    with pytest.raises(GrantError):
        manager.take_token(RUN)


@pytest.mark.asyncio
async def test_a_create_cancelled_while_leasing_leaves_nothing_leased(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.acquire_delay = 0.2
    manager = _manager(tmp_path, lab)
    creating = asyncio.create_task(_grant(manager, PROTOCOL))
    await asyncio.sleep(0.05)  # the first lease is on its way
    creating.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creating
    assert lab.log == [("lease", "dev01-aom"), ("release", "dev01-aom")]  # waited for, then released
    assert lab.held() == set() and manager.activity() == {}


@pytest.mark.asyncio
async def test_create_checks_each_devices_type(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.types["dev01-awg"] = "dummy_eom"
    manager = _manager(tmp_path, lab)
    with pytest.raises(GrantError, match="dev01-awg is a dummy_eom, not the dummy_awg"):
        await _grant(manager, PROTOCOL)
    assert lab.held() == set()
    del lab.types["dev01-awg"]
    with pytest.raises(GrantError, match="No device 'dev01-awg'"):
        await _grant(manager, PROTOCOL)
    assert lab.held() == set()


@pytest.mark.asyncio
async def test_create_refuses_bad_requests(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    for minutes in (0, -1, 21, math.nan, True):
        with pytest.raises(GrantError):
            await _grant(manager, AOM_ONLY, minutes=minutes)
    with pytest.raises(GrantError, match="approved"):
        await manager.create(project_id=PROJECT, run_id=RUN, protocol=AOM_ONLY, sha256="a" * 64,
                             duration_minutes=5, approved_by="  ")
    for protocol in ({**AOM_ONLY, "devices": []}, {**AOM_ONLY, "devices": "dev01-aom"},
                     {**AOM_ONLY, "devices": [AOM_ONLY["devices"][0]] * 2}):
        with pytest.raises(GrantError):
            await _grant(manager, protocol)
    assert manager.activity() == {}


@pytest.mark.asyncio
async def test_one_live_grant_per_run_and_per_device(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    await _grant(manager, AOM_ONLY)
    with pytest.raises(GrantError, match="already has an active grant"):
        await _grant(manager, AWG_ONLY)
    with pytest.raises(GrantError, match="held by another Automation grant"):
        await _grant(manager, AOM_ONLY, run_id=RUN2)
    await _grant(manager, AWG_ONLY, run_id=RUN2)  # other devices, other run: fine
    await manager.close()


@pytest.mark.asyncio
async def test_two_approvals_at_once_get_one_grant(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.acquire_delay = 0.1
    manager = _manager(tmp_path, lab)
    results = await asyncio.gather(_grant(manager, AOM_ONLY), _grant(manager, AOM_ONLY, run_id=RUN2),
                                   return_exceptions=True)
    assert sum(isinstance(result, GrantError) for result in results) == 1
    assert lab.log.count(("lease", "dev01-aom")) == 1 and lab.held() == {"dev01-aom"}
    await manager.close()


@pytest.mark.asyncio
async def test_the_token_is_handed_out_once_and_kept_only_as_a_hash(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    grant = await manager.create(project_id=PROJECT, run_id=RUN, protocol=AOM_ONLY, sha256="b" * 64,
                                 duration_minutes=5, approved_by="alice")
    stored = (tmp_path / "automation" / "state" / PROJECT / "grants" / f"{grant['id']}.json").read_text("utf-8")
    token, public = manager.take_token(RUN)
    assert public["id"] == grant["id"]
    with pytest.raises(GrantError) as again:
        manager.take_token(RUN)
    assert again.value.status_code == 409
    record = _record(tmp_path, grant["id"])
    assert token not in stored and token not in json.dumps(record)
    assert record["token_sha256"] == hashlib.sha256(token.encode()).hexdigest() and record["token_taken"] is True
    assert record["sha256"] == "b" * 64 and record["run_id"] == RUN
    assert manager.authenticate(token) == grant["id"]
    for wrong in (token[:-1] + ("A" if token[-1] != "A" else "B"), "lnr_" + token[4:], "", None, token + "x" * 300):
        with pytest.raises(GrantError) as failure:
            await manager.call(wrong, _req(value=80.0))
        assert failure.value.status_code == 401
    with pytest.raises(GrantError):
        manager.take_token(RUN2)
    await manager.close()


@pytest.mark.asyncio
async def test_refused_calls_are_audited_and_answer_refused(tmp_path: Path) -> None:
    lab = FakeLab()
    manager = _manager(tmp_path, lab)
    grant, token = await _grant(manager)
    error = await _refused(manager, token, _req(value=95.0))
    assert error.status_code == 422 and error.code == "refused" and "outside" in error.detail
    assert lab.calls == []
    line = [line for line in _audit(tmp_path, grant["id"]) if line["kind"] == "call"][-1]
    assert line["outcome"] == "refused" and "outside" in line["message"] and line["args"] == {"value": 95.0}
    assert manager.grant_for_run(RUN)["refused_calls"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_rates_counts_and_steps_through_the_manager(tmp_path: Path) -> None:
    clock = Clock()
    manager = _manager(tmp_path, clock=clock)
    _grant_info, token = await _grant(manager)
    await manager.call(token, _req(value=80.0))
    clock.now += 0.1
    assert "times per second" in (await _refused(manager, token, _req(value=81.0))).detail
    clock.now += 0.1
    await manager.call(token, _req(value=82.0))  # 0.2 s later: allowed, and 2 from the last value
    clock.now += 0.2
    assert "at most 2 per call" in (await _refused(manager, token, _req(value=84.5))).detail
    for _ in range(10):
        clock.now += 1.0
        await manager.call(token, _req(method="set_rf_enabled", enabled=True))
    clock.now += 1.0
    assert "used all 10" in (await _refused(manager, token, _req(method="set_rf_enabled", enabled=True))).detail
    await manager.close()


@pytest.mark.asyncio
async def test_a_failed_call_widens_the_step_reference_to_both_values(tmp_path: Path) -> None:
    lab, clock = FakeLab(), Clock()
    manager = _manager(tmp_path, lab, clock=clock)
    _grant_info, token = await _grant(manager)
    await manager.call(token, _req(value=80.0))
    lab.failures[("dev01-aom", "set_frequency")] = [DeviceUnavailableError(grpc.StatusCode.UNAVAILABLE, "down")]
    clock.now += 1
    error = await _refused(manager, token, _req(value=82.0))
    assert error.code == "device_error" and error.status_code == 424
    clock.now += 1
    assert "at most 2" in (await _refused(manager, token, _req(value=84.0))).detail  # 4 from 80
    clock.now += 1
    assert "at most 2" in (await _refused(manager, token, _req(value=78.0))).detail  # 4 from 82
    clock.now += 1
    await manager.call(token, _req(value=82.0))
    await manager.close()


@pytest.mark.asyncio
async def test_one_call_at_a_time_per_device(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.delays[("dev01-aom", "read_diffraction_efficiency")] = 0.3
    manager = _manager(tmp_path, lab)
    grant, token = await _grant(manager, PROTOCOL)
    slow = asyncio.create_task(manager.call(token, _req(method="read_diffraction_efficiency")))
    await _until(lambda: lab.calls)
    error = await _refused(manager, token, _req(value=80.0))
    assert error.status_code == 409 and error.code == "busy"
    other = await manager.call(token, _req("dev01-awg", "read_amplitude"))  # another device: fine
    assert other["result"] == 0.5
    assert (await slow)["result"] == 0.5
    await manager.call(token, _req(value=80.0))  # free again
    outcomes = [line["outcome"] for line in _audit(tmp_path, grant["id"]) if line["kind"] == "call"]
    assert sorted(outcomes) == ["busy", "ok", "ok", "ok"]
    await manager.close()


@pytest.mark.asyncio
async def test_expiry_applies_the_safe_state_and_releases_the_leases(tmp_path: Path) -> None:
    lab, runs = FakeLab(), FakeRuns()
    manager = _manager(tmp_path, lab, runs)
    grant, token = await _grant(manager, AOM_ONLY, minutes=0.005)  # 0.3 s
    assert manager.blocked("dev01-aom")
    await _until(lambda: runs.phases)  # the run is told last
    ended = manager.grant_for_run(RUN)
    assert ended["status"] == "ended" and ended["ended_reason"] == "expired" and ended["ended_at"]
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}]
    assert lab.held() == set() and not manager.blocked("dev01-aom") and manager.activity() == {}
    assert runs.grants[-1]["status"] == "ended" and runs.phases == ["analyzing"]
    assert _record(tmp_path, grant["id"])["status"] == "ended"
    error = await _refused(manager, token, _req(value=80.0))
    assert error.status_code == 410 and error.code == "expired"
    lines = _audit(tmp_path, grant["id"])
    assert [line["kind"] for line in lines][-3:] == ["safe_state", "end", "call"]
    assert lines[-2]["outcome"] == "ended" and lines[-2]["released"] is True


@pytest.mark.asyncio
async def test_a_call_after_the_deadline_is_expired_even_before_the_timer(tmp_path: Path) -> None:
    clock = Clock()
    manager = _manager(tmp_path, clock=clock)
    _grant_info, token = await _grant(manager, minutes=5)
    clock.now += 300.0
    error = await _refused(manager, token, _req(value=80.0))
    assert error.code == "expired" and error.status_code == 410
    await _until(lambda: manager.grant_for_run(RUN)["status"] == "ended")


@pytest.mark.asyncio
async def test_each_call_is_bounded_by_the_time_left(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.delays[("dev01-aom", "read_diffraction_efficiency")] = 30.0
    manager = _manager(tmp_path, lab)
    _grant_info, token = await _grant(manager, minutes=0.005)
    started = time.monotonic()
    error = await _refused(manager, token, _req(method="read_diffraction_efficiency"))
    assert error.code == "expired" and time.monotonic() - started < 5
    await _until(lambda: manager.grant_for_run(RUN)["status"] == "ended")
    assert lab.held() == set()


@pytest.mark.asyncio
async def test_stop_cancels_the_call_in_flight_then_safe_state_then_release(tmp_path: Path) -> None:
    lab, runs = FakeLab(), FakeRuns()
    lab.delays[("dev01-aom", "read_diffraction_efficiency")] = 30.0
    manager = _manager(tmp_path, lab, runs)
    grant, token = await _grant(manager)
    slow = asyncio.create_task(manager.call(token, _req(method="read_diffraction_efficiency")))
    await _until(lambda: lab.calls)
    stopped = await manager.stop(grant["id"], reason="Stopped from the device list", by="bob")
    assert stopped["status"] == "ended" and stopped["ended_reason"] == "Stopped from the device list by bob"
    with pytest.raises(GrantError) as failure:
        await slow
    assert failure.value.code == "ended" and failure.value.status_code == 410
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}] and lab.held() == set()
    assert runs.phases == ["analyzing"]
    error = await _refused(manager, token, _req(value=80.0))
    assert error.code == "ended"
    assert (await manager.stop(grant["id"], reason="again", by=""))["status"] == "ended"  # idempotent
    with pytest.raises(GrantError) as unknown:
        await manager.stop("grant-0000000000000000", reason="x", by="")
    assert unknown.value.status_code == 404


@pytest.mark.asyncio
async def test_ten_refused_calls_in_a_row_end_the_grant(tmp_path: Path) -> None:
    lab = FakeLab()
    manager = _manager(tmp_path, lab)
    grant, token = await _grant(manager)
    for _ in range(MAX_REFUSED_IN_A_ROW - 1):
        await _refused(manager, token, _req(value=999.0))
    await manager.call(token, _req(value=80.0))  # a good call resets the count
    for _ in range(MAX_REFUSED_IN_A_ROW - 1):
        await _refused(manager, token, _req(value=999.0))
    assert manager.grant_for_run(RUN)["status"] == "active"
    last = await _refused(manager, token, _req(value=999.0))
    assert last.code == "refused"
    assert manager.grant_for_run(RUN)["status"] in ("ending", "ended")
    assert (await _refused(manager, token, _req(value=80.0))).code == "ended"
    await _until(lambda: manager.grant_for_run(RUN)["status"] == "ended")
    ended = manager.grant_for_run(RUN)
    assert ended["ended_reason"].startswith("violations") and ended["refused_calls"] == 2 * MAX_REFUSED_IN_A_ROW - 1
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}] and lab.held() == set()


@pytest.mark.asyncio
async def test_a_safe_state_that_fails_ends_unsafe(tmp_path: Path) -> None:
    lab, runs = FakeLab(), FakeRuns()
    lab.failures[("dev01-aom", "set_rf_enabled")] = [
        DeviceUnavailableError(grpc.StatusCode.UNAVAILABLE, f"down ({LEASE})") for _ in range(3)
    ]
    manager = _manager(tmp_path, lab, runs)
    grant, _token = await _grant(manager)
    ended = await manager.stop(grant["id"], reason="Stopped", by="")
    assert ended["status"] == "ended_unsafe"
    assert len(lab.calls_to("set_rf_enabled")) == 3 and lab.held() == set()  # released all the same
    assert runs.grants[-1]["status"] == "ended_unsafe"
    tries = [line for line in _audit(tmp_path, grant["id"]) if line["kind"] == "safe_state"]
    assert [line["attempt"] for line in tries] == [1, 2, 3] and all(line["outcome"] == "failed" for line in tries)
    assert all(LEASE not in line["message"] for line in tries)
    assert _record(tmp_path, grant["id"])["status"] == "ended_unsafe"


@pytest.mark.asyncio
async def test_a_safe_state_retry_that_succeeds_is_safe(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.failures[("dev01-aom", "set_rf_enabled")] = [DeviceUnavailableError(grpc.StatusCode.UNAVAILABLE, "blip")]
    manager = _manager(tmp_path, lab)
    grant, _token = await _grant(manager)
    assert (await manager.stop(grant["id"], reason="Stopped", by=""))["status"] == "ended"
    assert len(lab.calls_to("set_rf_enabled")) == 2


@pytest.mark.asyncio
async def test_a_lapsed_lease_is_taken_again_once_for_the_safe_state(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.failures[("dev01-aom", "set_rf_enabled")] = [LeaseAuthorityError(f"Lease {LEASE} expired.")]
    manager = _manager(tmp_path, lab)
    grant, _token = await _grant(manager)
    assert (await manager.stop(grant["id"], reason="Stopped", by=""))["status"] == "ended"
    assert lab.log.count(("lease", "dev01-aom")) == 2 and lab.held() == set()


@pytest.mark.asyncio
async def test_a_lapsed_lease_is_taken_again_and_the_call_retried_if_it_never_ran(tmp_path: Path) -> None:
    lab = FakeLab()
    manager = _manager(tmp_path, lab)
    grant, token = await _grant(manager)
    lab.failures[("dev01-aom", "set_frequency")] = [LeaseAuthorityError(f"Lease {LEASE} expired.")]
    reply = await manager.call(token, _req(value=80.0))
    assert reply["result"] == 80.0
    assert lab.log.count(("lease", "dev01-aom")) == 2 and len(lab.held()) == 1
    line = [line for line in _audit(tmp_path, grant["id"]) if line["kind"] == "call"][-1]
    assert line["outcome"] == "ok" and line["retried_after_lease_lapsed"] is True
    assert manager.grant_for_run(RUN)["status"] == "active"
    await manager.close()
    assert lab.held() == set()


@pytest.mark.asyncio
async def test_a_write_the_lab_pc_refused_is_not_sent_twice(tmp_path: Path) -> None:
    lab, clock = FakeLab(), Clock()
    manager = _manager(tmp_path, lab, clock=clock)
    _grant_info, token = await _grant(manager)
    denied = DevicePermissionDeniedError(grpc.StatusCode.PERMISSION_DENIED, f"lease {LEASE} not valid")
    lab.failures[("dev01-aom", "set_frequency")] = [denied]
    error = await _refused(manager, token, _req(value=80.0))
    assert error.code == "device_error" and LEASE not in error.detail and "lease_…" in error.detail
    assert len(lab.calls_to("set_frequency")) == 1  # not retried
    assert lab.log.count(("lease", "dev01-aom")) == 2  # but the lease was taken again for the next call
    # A read the Lab PC refused is safe to retry.
    lab.failures[("dev01-aom", "read_diffraction_efficiency")] = [
        DevicePermissionDeniedError(grpc.StatusCode.PERMISSION_DENIED, "lease not valid")
    ]
    clock.now += 1
    assert (await manager.call(token, _req(method="read_diffraction_efficiency")))["result"] == 0.5
    assert len(lab.calls_to("read_diffraction_efficiency")) == 2
    clock.now += 1
    assert (await manager.call(token, _req(value=80.0)))["result"] == 80.0
    await manager.close()


@pytest.mark.asyncio
async def test_a_lease_that_cannot_be_taken_again_ends_the_grant(tmp_path: Path) -> None:
    lab = FakeLab()
    manager = _manager(tmp_path, lab)
    _grant_info, token = await _grant(manager)
    lab.failures[("dev01-aom", "set_frequency")] = [LeaseAuthorityError("expired")]
    lab.busy.add("dev01-aom")  # someone else took it meanwhile
    error = await _refused(manager, token, _req(value=80.0))
    assert error.code == "device_error" and "could not be taken again" in error.detail
    await _until(lambda: manager.grant_for_run(RUN)["status"] not in ("active", "ending"))
    ended = manager.grant_for_run(RUN)
    assert ended["status"] == "ended_unsafe"  # the safe state couldn't reach the device
    assert "lease" in ended["ended_reason"]


@pytest.mark.asyncio
async def test_device_errors_are_redacted_in_replies_and_audit(tmp_path: Path) -> None:
    lab = FakeLab()
    manager = _manager(tmp_path, lab)
    grant, token = await _grant(manager)
    lab.failures[("dev01-aom", "read_diffraction_efficiency")] = [
        RuntimeError(f"Lab PC says {LEASE} is unknown; key labnet_{'k' * 30}")
    ]
    error = await _refused(manager, token, _req(method="read_diffraction_efficiency"))
    assert error.code == "device_error" and error.status_code == 424
    assert LEASE not in error.detail and "k" * 30 not in error.detail and "lease_…" in error.detail
    assert LEASE not in (tmp_path / "automation" / "state" / PROJECT / "audit" / RUN /
                         f"grant-{grant['id']}.jsonl").read_text(encoding="utf-8")
    assert manager.grant_for_run(RUN)["status"] == "active"  # a device error is not a violation
    await manager.close()


@pytest.mark.asyncio
async def test_activity_and_blocked_show_what_the_grants_hold(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    grant, _token = await _grant(manager, AOM_ONLY, minutes=10)
    held = manager.activity()
    assert set(held) == {"dev01-aom"}
    automation = held["dev01-aom"]["automation"]
    assert automation["grant_id"] == grant["id"] and automation["label"] == f"{PROJECT}/Task1"
    assert automation["stop_url"] == f"/api/automation/grants/{grant['id']}/stop"
    assert 590 < automation["seconds_left"] <= 600 and automation["status"] == "active"
    assert held["dev01-aom"]["page"] == f"/automation/{PROJECT}"
    assert manager.blocked("dev01-aom") and not manager.blocked("dev01-awg")
    await manager.end_for_run(RUN, reason="the run ended (cancelled)")
    assert manager.activity() == {} and not manager.blocked("dev01-aom")
    assert manager.grant_for_run(RUN)["ended_reason"] == "the run ended (cancelled)"
    assert await manager.end_for_run(RUN, reason="again") is None


@pytest.mark.asyncio
async def test_an_ended_run_is_not_moved_to_analyzing(tmp_path: Path) -> None:
    runs = FakeRuns()
    manager = _manager(tmp_path, runs=runs)
    await _grant(manager)
    runs.status = "cancelled"
    await manager.end_for_run(RUN, reason="the run ended (cancelled)")
    assert runs.phases == [] and runs.grants[-1]["status"] == "ended"


@pytest.mark.asyncio
async def test_close_ends_every_grant_on_shutdown(tmp_path: Path) -> None:
    lab = FakeLab()
    manager = _manager(tmp_path, lab)
    first, _ = await _grant(manager, AOM_ONLY)
    second, _ = await _grant(manager, AWG_ONLY, run_id=RUN2)
    await manager.close()
    assert lab.held() == set() and lab.calls_to("set_rf_enabled") == [{"enabled": False}]
    for grant_id in (first["id"], second["id"]):
        assert _record(tmp_path, grant_id)["status"] == "ended"
        assert _record(tmp_path, grant_id)["ended_reason"] == "the Control Center is shutting down"
    with pytest.raises(GrantError, match="shutting down"):
        await _grant(manager, AOM_ONLY, run_id="run-20261007-120002-ijkm")


def test_mark_interrupted_marks_grants_left_active(tmp_path: Path) -> None:
    runs = FakeRuns()
    folder = tmp_path / "automation" / "state" / PROJECT / "grants"
    folder.mkdir(parents=True)
    base = {"approved_by": "alice", "starts_at": "2026-10-07T12:00:00Z", "ends_at": "2026-10-07T12:20:00Z",
            "devices": ["dev01-aom"], "calls": 3, "refused_calls": 0, "ended_reason": None, "ended_at": None,
            "run_id": RUN, "project_id": PROJECT, "token_sha256": "0" * 64}
    for grant_id, status in (("grant-00000000000000aa", "active"), ("grant-00000000000000bb", "ending"),
                             ("grant-00000000000000cc", "ended")):
        (folder / f"{grant_id}.json").write_text(json.dumps({**base, "id": grant_id, "status": status}), "utf-8")
    (folder / "grant-00000000000000dd.json").write_text("{broken", "utf-8")
    (folder / "notes.json").write_text("{}", "utf-8")

    _manager(tmp_path, runs=runs).mark_interrupted()

    def status(grant_id: str) -> str:
        return json.loads((folder / f"{grant_id}.json").read_text("utf-8"))["status"]

    assert status("grant-00000000000000aa") == status("grant-00000000000000bb") == "interrupted"
    assert status("grant-00000000000000cc") == "ended"
    assert [grant["status"] for grant in runs.grants] == ["interrupted", "interrupted"]
    assert "restarted" in runs.grants[0]["ended_reason"] and runs.phases == ["analyzing", "analyzing"]


def test_the_redaction_hides_grant_tokens() -> None:
    token = "lng_" + "Ab-_9" * 9
    assert redact(f"Bearer {token}.") == "Bearer lng_…."
    assert redact("lng_short") == "lng_short"


# ---- through the Control Center: routes, sessions, the Network page ---------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def cc(tmp_path: Path):
    """A Control Center with a claimed run on a FakeLab, and helpers to grant it devices."""

    lab = FakeLab()
    with TestClient(create_app(_settings(tmp_path), client_factory=lambda _: lab)) as client:
        client.lab = lab
        client.runner = client.app.state.runners.add("lab-laptop")
        reply = client.post("/api/automation/projects", json={"name": "AOM study"})
        assert reply.status_code == 201, reply.text
        client.project = reply.json()["project"]
        reply = client.post(f"/api/automation/projects/{client.project['id']}/runs", json={"task": "Tasks/Task1.md"})
        assert reply.status_code == 201, reply.text
        reply = client.post(f"{RUNNER}/claim", headers=_bearer(client.runner))
        assert reply.status_code == 200, reply.text
        client.run = reply.json()["run"]
        yield client


def _create_grant(client: TestClient, protocol: dict = AOM_ONLY, minutes: float = 10.0) -> dict:
    return client.portal.call(functools.partial(
        client.app.state.grants.create, project_id=client.project["id"], run_id=client.run["id"],
        protocol=protocol, sha256="c" * 64, duration_minutes=minutes, approved_by="alice",
    ))


def _token(client: TestClient) -> str:
    reply = client.post(f"{RUNNER}/runs/{client.run['id']}/grant/token", json={}, headers=_bearer(client.runner))
    assert reply.status_code == 200, reply.text
    assert reply.headers["cache-control"] == "no-store"
    return reply.json()["token"]


def _wait(condition, seconds: float = 10.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("condition never became true")


def test_the_grant_token_is_given_to_the_claiming_runner_once(cc: TestClient) -> None:
    url = f"{RUNNER}/runs/{cc.run['id']}/grant/token"
    assert cc.post(url, json={}, headers=_bearer(cc.runner)).status_code == 409  # no grant yet
    grant = _create_grant(cc)
    other = cc.app.state.runners.add("central-docker")
    assert cc.post(url, json={}, headers=_bearer(other)).status_code == 404  # not its run
    assert cc.post(url, json={}).status_code == 401
    reply = cc.post(url, json={}, headers=_bearer(cc.runner))
    assert reply.status_code == 200 and reply.json()["grant"]["id"] == grant["id"]
    assert reply.json()["token"].startswith("lng_")
    again = cc.post(url, json={}, headers=_bearer(cc.runner))
    assert again.status_code == 409 and again.json()["code"] == "conflict"
    run = cc.get(f"/api/automation/projects/{cc.project['id']}/runs/{cc.run['id']}").json()["run"]
    assert run["grant"]["id"] == grant["id"] and run["grant"]["status"] == "active"
    assert reply.json()["token"] not in json.dumps(run)


def test_the_gateway_runs_allowed_calls_and_refuses_the_rest(cc: TestClient) -> None:
    _create_grant(cc)
    token = _token(cc)
    ok = cc.post(GATEWAY, json=_req(value=80.0), headers=_bearer(token))
    assert ok.status_code == 200, ok.text
    assert set(ok.json()) == {"result", "calls_left", "seconds_left"} and ok.json()["result"] == 80.0
    assert ok.headers["cache-control"] == "no-store"
    stream = cc.post(GATEWAY, json=_stream(rate=10.0, count=4), headers=_bearer(token))
    assert stream.status_code == 200 and stream.json()["result"] == [0.25] * 4
    refused = cc.post(GATEWAY, json=_req(value=99.0), headers=_bearer(token))
    assert refused.status_code == 422 and set(refused.json()) == {"detail", "code"}
    assert refused.json()["code"] == "refused"
    positional = cc.post(GATEWAY, json={"device_id": "dev01-aom", "method": "set_frequency", "args": [80.0]},
                         headers=_bearer(token))
    assert positional.status_code == 422 and positional.json()["code"] == "refused"
    nan = cc.post(GATEWAY, content=b'{"device_id": "dev01-aom", "method": "set_frequency", "args": {"value": NaN}}',
                  headers={**_bearer(token), "Content-Type": "application/json"})
    assert nan.status_code == 422 and nan.json()["code"] == "refused"
    assert cc.lab.calls_to("set_frequency") == [{"value": 80.0}]


def test_the_gateway_needs_a_grant_token_and_refuses_browsers(cc: TestClient) -> None:
    _create_grant(cc)
    token = _token(cc)
    call = _req(value=80.0)
    for headers in ({}, _bearer(cc.runner), _bearer("lng_" + "x" * 43), {"Authorization": f"Basic {token}"}):
        reply = cc.post(GATEWAY, json=call, headers=headers)
        assert reply.status_code == 401 and reply.json()["code"] == "ended", headers
    for site in ("same-origin", "cross-site", "none"):
        reply = cc.post(GATEWAY, json=call, headers={**_bearer(token), "Sec-Fetch-Site": site})
        assert reply.status_code == 403 and reply.json()["code"] == "refused"
    # A runner token can't reach the gateway, and a grant token can't reach the runner API.
    assert cc.get(f"{RUNNER}/whoami", headers=_bearer(token)).status_code == 401
    assert cc.lab.calls == []


def test_the_gateway_caps_a_call_at_16_kib(cc: TestClient) -> None:
    _create_grant(cc)
    token = _token(cc)
    big = json.dumps({**_req(value=80.0), "pad": "x" * (16 * 1024)}).encode()
    reply = cc.post(GATEWAY, content=big, headers={**_bearer(token), "Content-Type": "application/json"})
    assert reply.status_code == 413 and reply.json()["code"] == "refused"

    def chunks():  # no Content-Length: counted as it arrives
        yield big[:8000]
        yield big[8000:]

    reply = cc.post(GATEWAY, content=chunks(), headers={**_bearer(token), "Content-Type": "application/json"})
    assert reply.status_code == 413
    reply = cc.post(GATEWAY, content=b"value=80", headers={**_bearer(token), "Content-Type": "text/plain"})
    assert reply.status_code == 415
    assert cc.lab.calls == []


def test_device_errors_through_the_gateway_never_show_a_lease_id(cc: TestClient) -> None:
    _create_grant(cc)
    token = _token(cc)
    cc.lab.failures[("dev01-aom", "read_diffraction_efficiency")] = [RuntimeError(f"lease {LEASE} rejected")]
    reply = cc.post(GATEWAY, json=_req(method="read_diffraction_efficiency"), headers=_bearer(token))
    assert reply.status_code == 424 and reply.json()["code"] == "device_error"
    assert LEASE not in reply.text and "lease_…" in reply.text


def test_the_runner_ends_its_grant(cc: TestClient) -> None:
    url = f"{RUNNER}/runs/{cc.run['id']}/grant/end"
    assert cc.post(url, json={"reason": "done"}, headers=_bearer(cc.runner)).status_code == 409  # none
    _create_grant(cc)
    token = _token(cc)
    assert cc.post(url, json={"reason": 5}, headers=_bearer(cc.runner)).status_code == 422
    reply = cc.post(url, json={"reason": "measured  the\nwhole sweep"}, headers=_bearer(cc.runner))
    assert reply.status_code == 200, reply.text
    grant = reply.json()["grant"]
    assert grant["status"] == "ended" and grant["ended_reason"] == "ended by the runner: measured the whole sweep"
    assert cc.lab.calls_to("set_rf_enabled") == [{"enabled": False}] and cc.lab.held() == set()
    after = cc.post(GATEWAY, json=_req(value=80.0), headers=_bearer(token))
    assert after.status_code == 410 and after.json()["code"] == "ended"
    again = cc.post(url, json={}, headers=_bearer(cc.runner))
    assert again.status_code == 200 and again.json()["grant"]["status"] == "ended"


def test_people_stop_a_grant_from_the_project_or_the_device_list(cc: TestClient) -> None:
    project_id, run_id = cc.project["id"], cc.run["id"]
    url = f"/api/automation/projects/{project_id}/runs/{run_id}/grant/stop"
    assert cc.post(url, json={}).status_code == 409  # no grant
    grant = _create_grant(cc)
    assert cc.post(url, json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert cc.post(url, content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert cc.post(f"/api/automation/projects/{project_id}/runs/run-20261007-000000-zzzz/grant/stop",
                   json={}).status_code == 404
    reply = cc.post(url, json={"stopped_by": "Rob"})
    assert reply.status_code == 200, reply.text
    assert reply.json()["grant"]["status"] == "ended"
    assert reply.json()["grant"]["ended_reason"] == "Stopped from the project page by Rob"
    assert cc.lab.held() == set()
    assert cc.post(url, json={}).json()["grant"]["id"] == grant["id"]  # already over: how it ended

    # The device-list Stop, on a fresh run of the same task.
    assert cc.post(f"/api/automation/projects/{project_id}/runs/{run_id}/cancel", json={}).status_code == 200
    assert cc.post(f"/api/automation/projects/{project_id}/runs", json={"task": "Tasks/Task1.md"}).status_code == 201
    cc.run = cc.post(f"{RUNNER}/claim", headers=_bearer(cc.runner)).json()["run"]
    second = _create_grant(cc)
    stop = f"/api/automation/grants/{second['id']}/stop"
    assert cc.post(stop, json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert cc.post("/api/automation/grants/grant-0000000000000000/stop", json={}).status_code == 404
    assert cc.post("/api/automation/grants/nonsense/stop", json={}).status_code == 404
    reply = cc.post(stop, json={})
    assert reply.status_code == 200 and reply.json()["grant"]["status"] == "ended"
    assert reply.json()["grant"]["ended_reason"] == "Stopped from the device list"


def test_play_and_scripts_are_refused_on_a_granted_device(cc: TestClient) -> None:
    grant = _create_grant(cc)
    play = {"settings": [{"method": "set_frequency", "value": 80.0}],
            "stream": {"method": "stream_diffraction_efficiency", "sample_rate_hz": 10.0}}
    reply = cc.post("/api/devices/dev01-aom/play", json=play)
    assert reply.status_code == 409 and "Automation" in reply.json()["detail"]
    reply = cc.post("/api/devices/dev01-aom/script", json={"code": "device.read_frequency()"})
    assert reply.status_code == 409 and "Automation" in reply.json()["detail"]
    assert cc.app.state.sessions.blocked("dev01-aom") and not cc.app.state.sessions.blocked("dev01-awg")
    cc.post(f"/api/automation/grants/{grant['id']}/stop", json={})
    assert not cc.app.state.sessions.blocked("dev01-aom")


def test_the_network_page_shows_the_automation_holder(cc: TestClient) -> None:
    def devices() -> dict[str, dict]:
        snapshot = cc.get("/api/status").json()
        return {device["id"]: device for pc in snapshot["lab_pcs"] for device in pc["devices"]}

    assert devices()["dev01-aom"]["control_center"] is None
    grant = _create_grant(cc, minutes=15)
    aom = devices()["dev01-aom"]
    assert aom["state"] == "leased"
    assert aom["detail"] == f"Held by Automation ({cc.project['id']}/Task1)"
    held = aom["control_center"]
    assert held["page"] == f"/automation/{cc.project['id']}"
    assert held["automation"]["grant_id"] == grant["id"] and held["automation"]["status"] == "active"
    assert held["automation"]["stop_url"] == f"/api/automation/grants/{grant['id']}/stop"
    assert held["automation"]["ends_at"] > time.time() + 14 * 60
    assert devices()["dev01-awg"]["control_center"] is None
    assert cc.post(held["automation"]["stop_url"], json={}).status_code == 200
    assert devices()["dev01-aom"]["control_center"] is None


def test_a_run_that_ends_ends_its_grant(cc: TestClient) -> None:
    _create_grant(cc)
    reply = cc.post(f"/api/automation/projects/{cc.project['id']}/runs/{cc.run['id']}/cancel", json={})
    assert reply.status_code == 200, reply.text
    _wait(lambda: cc.app.state.grants.grant_for_run(cc.run["id"])["status"] == "ended")
    grant = cc.app.state.grants.grant_for_run(cc.run["id"])
    assert grant["ended_reason"] == "the run ended (cancelled)"
    assert cc.lab.held() == set() and cc.lab.calls_to("set_rf_enabled") == [{"enabled": False}]


def test_shutdown_ends_grants_before_the_client_closes(tmp_path: Path) -> None:
    lab = FakeLab()
    with TestClient(create_app(_settings(tmp_path), client_factory=lambda _: lab)) as client:
        client.portal.call(functools.partial(
            client.app.state.grants.create, project_id=PROJECT, run_id=RUN, protocol=AOM_ONLY,
            sha256="d" * 64, duration_minutes=10, approved_by="alice",
        ))
        assert lab.held() == {"dev01-aom"}
    assert lab.held() == set() and lab.calls_to("set_rf_enabled") == [{"enabled": False}]
    records = list((tmp_path / "automation" / "state" / PROJECT / "grants").glob("*.json"))
    assert len(records) == 1 and json.loads(records[0].read_text("utf-8"))["status"] == "ended"


def test_startup_marks_grants_a_restart_cut_off(tmp_path: Path) -> None:
    folder = tmp_path / "automation" / "state" / PROJECT / "grants"
    folder.mkdir(parents=True)
    (folder / "grant-00000000000000aa.json").write_text(json.dumps({
        "id": "grant-00000000000000aa", "status": "active", "run_id": RUN, "approved_by": "alice",
        "starts_at": None, "ends_at": None, "devices": ["dev01-aom"], "calls": 0, "refused_calls": 0,
        "ended_reason": None, "ended_at": None,
    }), "utf-8")
    with TestClient(create_app(_settings(tmp_path), client_factory=lambda _: FakeLab())):
        record = json.loads((folder / "grant-00000000000000aa.json").read_text("utf-8"))
    assert record["status"] == "interrupted" and record["ended_at"]


def test_with_logins_the_gateway_needs_only_the_grant_token_and_stop_needs_a_login(tmp_path: Path) -> None:
    secret = "s3cret-" + "x" * 41
    signed_in = {"X-LabNet-Proxy": secret, "X-LabNet-User": "alice"}
    lab = FakeLab()
    with TestClient(create_app(_settings(tmp_path, proxy_secret=secret), client_factory=lambda _: lab)) as client:
        grant = client.portal.call(functools.partial(
            client.app.state.grants.create, project_id=PROJECT, run_id=RUN, protocol=AOM_ONLY,
            sha256="e" * 64, duration_minutes=10, approved_by="alice",
        ))
        token, _ = client.app.state.grants.take_token(RUN)
        reply = client.post(GATEWAY, json=_req(value=80.0), headers=_bearer(token))  # no Caddy headers
        assert reply.status_code == 200, reply.text
        stop = f"/api/automation/grants/{grant['id']}/stop"
        assert client.post(stop, json={}, headers=_bearer(token)).status_code == 403  # a token is not a login
        assert lab.held() == {"dev01-aom"}
        reply = client.post(stop, json={"stopped_by": "mallory"}, headers=signed_in)
        assert reply.status_code == 200
        assert reply.json()["grant"]["ended_reason"] == "Stopped from the device list by alice"
        assert lab.held() == set()


# ---- the security review's findings: each test asserts the safe behaviour (each failed before the fix) ----


AWG_TWO_CHANNELS: dict[str, Any] = {
    **PROTOCOL,
    "devices": [{
        "device_id": "dev01-awg", "device_type": "dummy_awg",
        "methods": {
            "set_amplitude": {
                "args": {"value": {"min": 0.0, "max": 1.0, "max_step": 0.1},
                         "channel": {"choices": ["CH1", "CH2"]}},
                "max_calls": 100.0, "max_rate_hz": 100.0,
            },
        },
    }],
    "safe_state": [],
}
AWG_CH2_ONLY: dict[str, Any] = copy.deepcopy(AWG_TWO_CHANNELS)
AWG_CH2_ONLY["devices"][0]["methods"]["set_amplitude"]["args"]["channel"] = {"choices": ["CH2"]}


def _physical(lab: FakeLab, channel: str) -> list[float]:
    """What a DummyAWG channel was actually driven to (its driver uses CH1 when channel is left out)."""

    return [kwargs["value"] for _device, name, kwargs in lab.calls
            if name == "set_amplitude" and (kwargs.get("channel") or "CH1") == channel]


@pytest.mark.asyncio
async def test_a_bounded_channel_left_out_is_refused_so_the_default_channel_is_never_driven(tmp_path: Path) -> None:
    lab, clock = FakeLab(), Clock()
    manager = _manager(tmp_path, lab, clock=clock)
    _g, token = await _grant(manager, AWG_CH2_ONLY)
    assert (await _refused(manager, token, _req("dev01-awg", "set_amplitude", value=0.5, channel="CH1"))).code == "refused"
    clock.now += 1
    error = await _refused(manager, token, _req("dev01-awg", "set_amplitude", value=0.5))
    assert error.code == "refused" and "must send it" in error.detail
    assert _physical(lab, "CH1") == [] and lab.calls == []  # nothing reached the device, not even a reading
    await manager.close()


@pytest.mark.asyncio
async def test_alternating_channels_cannot_double_max_step(tmp_path: Path) -> None:
    lab, clock = FakeLab(), Clock()
    lab.positions.update({("dev01-awg", "amplitude", "CH1"): 0.0, ("dev01-awg", "amplitude", "CH2"): 0.0})
    manager = _manager(tmp_path, lab, clock=clock)
    _g, token = await _grant(manager, AWG_TWO_CHANNELS)
    refused = []
    for channel, value in [("CH1", 0.0), ("CH2", 0.1), ("CH1", 0.2), ("CH2", 0.3), ("CH1", 0.1), ("CH2", 0.2)]:
        clock.now += 1
        try:
            await manager.call(token, _req("dev01-awg", "set_amplitude", value=value, channel=channel))
        except GrantError as exc:
            refused.append((channel, value, exc.code))
    assert refused == [("CH1", 0.2, "refused"), ("CH2", 0.3, "refused")]
    for channel in ("CH1", "CH2"):
        sequence = [0.0, *_physical(lab, channel)]  # from where the gateway read it
        assert all(abs(b - a) <= 0.1 + 1e-12 for a, b in zip(sequence, sequence[1:])), (channel, sequence)
    # Each channel was read once, with its own channel, before its first change.
    assert lab.calls_to("read_amplitude") == [{"channel": "CH1"}, {"channel": "CH2"}]
    assert step_key("dev01-awg", "set_amplitude", "value", {"value": 0.1, "channel": "CH2"}) == \
        "dev01-awg.set_amplitude.value|channel=CH2"
    await manager.close()


class ApplyThenFail(FakeLab):
    """A write that reaches the device and is applied, but the reply is lost (a gRPC deadline)."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next = False

    async def unary(self, device, name, kwargs):
        result = await super().unary(device, name, kwargs)
        if name == "set_frequency" and self.fail_next:
            self.fail_next = False
            raise DeviceUnavailableError(grpc.StatusCode.DEADLINE_EXCEEDED, "deadline exceeded")
        return result


@pytest.mark.asyncio
async def test_a_write_that_errors_after_reaching_the_device_widens_the_step_reference(tmp_path: Path) -> None:
    lab, clock = ApplyThenFail(), Clock()
    manager = _manager(tmp_path, lab, clock=clock)
    _g, token = await _grant(manager, AOM_ONLY)
    await manager.call(token, _req(value=80.0))
    clock.now += 1
    lab.fail_next = True
    assert (await _refused(manager, token, _req(value=82.0))).code == "device_error"  # but the device IS at 82
    clock.now += 1
    error = await _refused(manager, token, _req(value=78.0))  # 2 from 80, but 4 from 82
    assert error.code == "refused" and "somewhere from 80 to 82" in error.detail
    clock.now += 1
    await manager.call(token, _req(value=81.0))  # within 2 of both
    clock.now += 1
    await manager.call(token, _req(value=83.0))  # a success narrowed it back to 81
    assert [kwargs["value"] for kwargs in lab.calls_to("set_frequency")] == [80.0, 82.0, 81.0, 83.0]
    await manager.close()


@pytest.mark.asyncio
async def test_a_write_the_runner_hung_up_on_widens_the_step_reference(tmp_path: Path) -> None:
    lab, clock = FakeLab(), Clock()
    manager = _manager(tmp_path, lab, clock=clock)
    _g, token = await _grant(manager, AOM_ONLY)
    await manager.call(token, _req(value=80.0))
    clock.now += 1
    lab.delays[("dev01-aom", "set_frequency")] = 10.0
    pending = asyncio.create_task(manager.call(token, _req(value=82.0)))
    await _until(lambda: len(lab.calls_to("set_frequency")) == 2)
    pending.cancel()  # the runner (or script) hangs up after the write reached the device
    with pytest.raises(asyncio.CancelledError):
        await pending
    del lab.delays[("dev01-aom", "set_frequency")]
    clock.now += 1
    assert (await _refused(manager, token, _req(value=78.0))).code == "refused"
    clock.now += 1
    await manager.call(token, _req(value=80.5))
    assert [kwargs["value"] for kwargs in lab.calls_to("set_frequency")] == [80.0, 82.0, 80.5]
    await manager.close()


def test_a_write_that_times_out_widens_the_step_reference() -> None:
    state = GrantCallState(last_values={FREQ: (80.0, 80.0)})
    request = _req(value=82.0)
    state.started(request, 1000.0)
    state.finished(request, succeeded=False)  # what GrantManager.call does after a timeout, too
    assert state.last_values[FREQ] == (80.0, 82.0)


@pytest.mark.asyncio
async def test_the_first_change_is_measured_from_the_devices_own_reading(tmp_path: Path) -> None:
    lab, clock = FakeLab(), Clock()
    lab.positions[("dev01-aom", "frequency", None)] = 70.0  # say, the last person's setting
    manager = _manager(tmp_path, lab, clock=clock)
    grant, token = await _grant(manager, AOM_ONLY)
    error = await _refused(manager, token, _req(value=90.0))
    assert error.code == "refused" and "it is 70, so 90.0 is too far" in error.detail
    assert lab.calls_to("set_frequency") == [] and lab.calls_to("read_frequency") == [{}]
    clock.now += 1
    reply = await manager.call(token, _req(value=72.0))
    assert reply["calls_left"] == 199  # the reading isn't one of the protocol's calls
    assert lab.calls_to("read_frequency") == [{}]  # read once: from then on, measured from what was set
    readings = [line for line in _audit(tmp_path, grant["id"]) if line["kind"] == "reading"]
    assert readings == [{**readings[0], "device_id": "dev01-aom", "method": "read_frequency", "args": {},
                         "for": FREQ, "outcome": "ok", "result": 70.0}]
    assert manager.grant_for_run(RUN)["calls"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_a_reading_that_fails_refuses_the_change(tmp_path: Path) -> None:
    lab, clock = FakeLab(), Clock()
    lab.failures[("dev01-aom", "read_frequency")] = [RuntimeError(f"Lab PC says {LEASE} is unknown")]
    manager = _manager(tmp_path, lab, clock=clock)
    grant, token = await _grant(manager, AOM_ONLY)
    error = await _refused(manager, token, _req(value=80.0))
    assert error.code == "device_error" and error.status_code == 424
    assert "read_frequency() failed" in error.detail and "not sent" in error.detail and LEASE not in error.detail
    assert lab.calls_to("set_frequency") == []
    lines = _audit(tmp_path, grant["id"])
    assert [line["outcome"] for line in lines if line["kind"] == "reading"] == ["failed"]
    assert [line["outcome"] for line in lines if line["kind"] == "call"] == ["device_error"]
    assert manager.grant_for_run(RUN)["refused_calls"] == 1 and manager.grant_for_run(RUN)["calls"] == 0
    # A reading that isn't a number is no reading either.
    lab.failures.clear()
    lab.positions[("dev01-aom", "frequency", None)] = math.nan
    clock.now += 1
    assert (await _refused(manager, token, _req(value=80.0))).code == "device_error"
    lab.positions[("dev01-aom", "frequency", None)] = 80.0
    clock.now += 1
    await manager.call(token, _req(value=80.0))
    await manager.close()


@pytest.mark.asyncio
async def test_a_device_busy_being_read_is_busy(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.delays[("dev01-aom", "read_frequency")] = 0.3
    manager = _manager(tmp_path, lab)
    _g, token = await _grant(manager, AOM_ONLY)
    first = asyncio.create_task(manager.call(token, _req(value=80.0)))
    await _until(lambda: lab.calls_to("read_frequency"))
    assert (await _refused(manager, token, _req(method="set_rf_enabled", enabled=True))).code == "busy"
    assert (await first)["result"] == 80.0
    await manager.close()


@pytest.mark.asyncio
async def test_a_whole_number_safe_state_is_applied(tmp_path: Path) -> None:
    from labnet.control_center.automation_protocol import validate_protocol

    sacher = {"device_id": "dev01-sacher", "device_type": "sacher_laser", "methods": {
        "jog_steps": {"args": {"relative_steps": {"min": -10, "max": 10}}, "max_calls": 5, "max_rate_hz": 1}}}
    raw = {**{key: PROTOCOL[key] for key in ("schema_version", "task", "title", "duration_minutes")},
           "devices": [sacher],
           "safe_state": [{"device_id": "dev01-sacher", "method": "jog_steps", "args": {"relative_steps": 0}}]}
    live = {"dev01-sacher": {"id": "dev01-sacher", "type": "sacher_laser", "state": "available", "hints": {}}}
    normalized, problems = validate_protocol(raw, project={"allowed_devices": ["dev01-sacher"]}, live_devices=live,
                                             canvas_devices=[{"device_id": "dev01-sacher",
                                                              "device_type": "sacher_laser"}],
                                             task="Tasks/Task1.md")
    assert problems == []
    lab = FakeLab()
    manager = _manager(tmp_path, lab)
    grant, _token = await _grant(manager, normalized)
    ended = await manager.stop(grant["id"], reason="Stopped", by="")
    assert ended["status"] == "ended"
    assert lab.calls_to("jog_steps") == [{"relative_steps": 0}]
    assert type(lab.calls_to("jog_steps")[0]["relative_steps"]) is int


@pytest.mark.asyncio
async def test_shutdown_during_create_leaves_no_grant(tmp_path: Path) -> None:
    lab = FakeLab()
    lab.acquire_delay = 0.3
    manager = _manager(tmp_path, lab)
    creating = asyncio.create_task(manager.create(project_id=PROJECT, run_id=RUN, protocol=AOM_ONLY,
                                                  sha256="a" * 64, duration_minutes=5, approved_by="alice"))
    await asyncio.sleep(0.05)
    await manager.close()  # shutdown while the lease is on its way
    with pytest.raises(GrantError, match="shutting down"):
        await creating
    assert lab.held() == set() and not manager.blocked("dev01-aom") and manager.activity() == {}
    assert manager.grant_for_run(RUN) is None


def _raw(client: TestClient, body: bytes, token: str, content_type: str = "application/json"):
    return client.post(GATEWAY, content=body, headers={**_bearer(token), "Content-Type": content_type})


def test_calls_refused_before_they_are_read_are_audited_and_counted(cc: TestClient, tmp_path: Path) -> None:
    grant = _create_grant(cc)
    token = _token(cc)
    bodies = [
        (b"{not json", "application/json", 422),
        (b'{"device_id": "dev01-aom", "method": "set_frequency", "args": {"value": ' + b"9" * 5000 + b"}}",
         "application/json", 422),
        (b"value=80", "text/plain", 415),
        (json.dumps({**_req(value=80.0), "pad": "x" * (16 * 1024)}).encode(), "application/json", 413),
    ]
    for body, media, status in bodies:
        reply = _raw(cc, body, token, media)
        assert reply.status_code == status and reply.json()["code"] == "refused", reply.text
    state = cc.app.state.grants.grant_for_run(cc.run["id"])
    assert state["refused_calls"] == 4 and state["status"] == "active"
    audit = tmp_path / "automation" / "state" / cc.project["id"] / "audit" / cc.run["id"] / f"grant-{grant['id']}.jsonl"
    lines = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in lines if line["kind"] == "call"]
    assert [(line["outcome"], line["http_status"]) for line in lines] == [
        ("refused", 422), ("refused", 422), ("refused", 415), ("refused", 413)]
    # They count toward the ten in a row that end the grant.
    for _ in range(MAX_REFUSED_IN_A_ROW - 4):
        assert _raw(cc, b"[", token).status_code == 422
    _wait(lambda: cc.app.state.grants.grant_for_run(cc.run["id"])["status"] == "ended")
    assert cc.app.state.grants.grant_for_run(cc.run["id"])["ended_reason"].startswith("violations")
    assert cc.lab.calls_to("set_frequency") == [] and cc.lab.held() == set()
    # A body sent without a valid token is refused before it is read, and charged to no grant.
    assert _raw(cc, b"{not json", "lng_" + "x" * 43).status_code == 401


def test_the_token_reply_carries_the_approved_protocol(cc: TestClient) -> None:
    _create_grant(cc, AOM_ONLY)
    reply = cc.post(f"{RUNNER}/runs/{cc.run['id']}/grant/token", json={}, headers=_bearer(cc.runner))
    assert reply.status_code == 200, reply.text
    assert reply.json()["protocol"] == AOM_ONLY
    assert set(reply.json()) == {"token", "grant", "protocol"}
