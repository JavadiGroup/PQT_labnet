"""Site limits on a running Lab PC: refused at startup when wrong, enforced on every remote call."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import grpc
import httpx
import pytest

import labnet
from labnet.client import LabClient
from labnet.client.core.exceptions import translate_grpc_error
from labnet.device_manager import ArgumentOutsideLimitsError, InvalidDeviceLimitsError
from labnet.devices.core import DeviceLimitsError, site_limits
from labnet.devices.DummyAOM.physical import PhysicalDummyAOM
from labnet.devices.DummyAWG.physical import PhysicalDummyAWG
from labnet.devices.DummyDevice import DummyDevice
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.grpc import LEASE_METADATA_KEY
from labnet.grpc.generated import (
    dummy_aom_pb2,
    dummy_aom_pb2_grpc,
    dummy_awg_pb2,
    dummy_awg_pb2_grpc,
    dummy_device_pb2,
    dummy_device_pb2_grpc,
    transfer_pb2,
)
from labnet.lab_pc.app import create_app, load_device_factory
from labnet.lab_pc.config import LabPCSettings


METADATA = ((LEASE_METADATA_KEY, "development-lease"),)
REPO = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


def _settings() -> LabPCSettings:
    return replace(LabPCSettings.from_env(search_cwd=False), grpc_port=0, grpc_advertised_target="127.0.0.1:0")


def _bench() -> list[Any]:
    return [
        site_limits(PhysicalDummyAOM("aom", noise=0.0), limits={"set_frequency.value": (65.0, 95.0)}),
        PhysicalDummyAWG("awg", noise=0.0),
        site_limits(PhysicalDummyDevice("dd"), limits={"write_value.value": (-10.0, 10.0)},
                    choices={"read_value.channel": ["a"]}),
    ]


async def _refused(call: Any) -> grpc.aio.AioRpcError:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await call
    error = caught.value
    assert error.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert dict(error.trailing_metadata())["labnet-error-code"] == "ARGUMENT_OUTSIDE_LIMITS"
    return error


@pytest.mark.asyncio
async def test_a_lab_pc_refuses_calls_outside_site_and_driver_limits_over_grpc() -> None:
    app = create_app(settings=_settings(), device_factory=_bench)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{runtime.grpc_server.bound_port}")
        try:
            aom = dummy_aom_pb2_grpc.DummyAOMServiceStub(channel)

            def frequency(value: float, op: str) -> Any:
                return aom.SetFrequency(dummy_aom_pb2.DummyAomWriteRequest(device_id="aom", operation_id=op,
                                                                           value=value), metadata=METADATA)

            assert (await frequency(70.0, "op-1")).measurement.value == 70.0
            assert (await frequency(95.0, "op-2")).measurement.value == 95.0  # the edge is inside
            error = await _refused(frequency(96.0, "op-3"))  # inside the driver's 60-100, outside the site's 65-95
            assert error.details() == "aom.set_frequency: value=96 is outside the site limit 65 to 95."
            # A remote client gets a typed, final error.
            translated = translate_grpc_error(error)
            assert isinstance(translated, labnet.InvalidDeviceArgumentError)
            assert translated.error_code == "ARGUMENT_OUTSIDE_LIMITS" and translated.retryable is False
            # The driver was never called: the AOM is where the last allowed call left it.
            read = await aom.ReadFrequency(dummy_aom_pb2.DummyAomRequest(device_id="aom", operation_id="op-4"),
                                           metadata=METADATA)
            assert read.measurement.value == 95.0
            # The driver's own published limit is enforced too (0-2 W).
            error = await _refused(aom.SetRfPower(dummy_aom_pb2.DummyAomWriteRequest(
                device_id="aom", operation_id="op-5", value=2.5), metadata=METADATA))
            assert "driver's limit 0 to 2" in error.details()

            awg = dummy_awg_pb2_grpc.DummyAWGServiceStub(channel)
            await _refused(awg.SetWaveform(dummy_awg_pb2.DummyAwgWaveformRequest(
                device_id="awg", operation_id="op-6", channel="CH1", waveform="saw"), metadata=METADATA))
            await _refused(awg.SetAmplitude(dummy_awg_pb2.DummyAwgWriteRequest(
                device_id="awg", operation_id="op-7", channel="CH3", value=1.0), metadata=METADATA))
            done = await awg.SetWaveform(dummy_awg_pb2.DummyAwgWaveformRequest(
                device_id="awg", operation_id="op-8", channel="CH2", waveform="square"), metadata=METADATA)
            assert done.result.text_value == "square"

            dummy = dummy_device_pb2_grpc.DummyDeviceServiceStub(channel)
            await _refused(dummy.WriteValue(dummy_device_pb2.WriteValueRequest(
                device_id="dd", operation_id="op-9", channel="a", value=10.5), metadata=METADATA))
            # A site-limited channel must be sent: the driver's default may be outside it.
            error = await _refused(dummy.ReadValue(dummy_device_pb2.ReadValueRequest(
                device_id="dd", operation_id="op-10"), metadata=METADATA))
            assert "must be sent explicitly" in error.details()
            ok = await dummy.ReadValue(dummy_device_pb2.ReadValueRequest(device_id="dd", operation_id="op-11",
                                                                         channel="a"), metadata=METADATA)
            assert ok.measurement.channel == "a"
            # Streams reach the same check (their sample controls are not limited here).
            stream = dummy.StreamData(transfer_pb2.StreamDataRequest(
                device_id="dd", operation_id="op-12", channel="b", sample_rate_hz=10.0, sample_count=2),
                metadata=METADATA)
            with pytest.raises(grpc.aio.AioRpcError) as caught:
                [item async for item in stream]
            assert caught.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            samples = [item async for item in dummy.StreamData(transfer_pb2.StreamDataRequest(
                device_id="dd", operation_id="op-13", channel="a", sample_rate_hz=50.0, sample_count=2),
                metadata=METADATA)]
            assert len(samples) == 2
        finally:
            await channel.close()

        # A bool is never a number, at the dispatch point every transport uses.
        executor = runtime.manager.executor
        with pytest.raises(ArgumentOutsideLimitsError, match="true is not one"):
            await executor.execute("development-lease", "aom", "set_frequency", kwargs={"value": True})
        with pytest.raises(ArgumentOutsideLimitsError, match="true is not one"):
            await executor.execute("development-lease", "dd", "write_value", kwargs={"value": True, "channel": "a"})
        # Positional arguments are checked by name too.
        with pytest.raises(ArgumentOutsideLimitsError, match="outside the site limit"):
            await executor.execute("development-lease", "aom", "set_frequency", args=(99.0,))
        assert await executor.execute("development-lease", "aom", "set_frequency", args=(80.0,)) == 80.0

        # What the Lab PC publishes (to Central, and so to the Control Center): the effective limits and sources.
        hints = runtime.manager.get_snapshot("aom").metadata["hints"]
        assert hints["limits"] == {"set_frequency.value": [65.0, 95.0], "set_rf_power.value": [0.0, 2.0]}
        assert hints["sources"] == {"set_frequency.value": "site", "set_rf_power.value": "driver"}
        registered = runtime.manager.get_snapshot("dd").to_registration_dict()["metadata"]["hints"]
        assert registered["choices"] == {"read_value.channel": ["a"], "stream_data.channel": ["a"]}


@pytest.mark.asyncio
async def test_a_lab_pc_refuses_to_start_when_a_site_limit_is_wider_than_the_driver() -> None:
    def inventory() -> list[Any]:
        return [PhysicalDummyAWG("awg"),
                site_limits(PhysicalDummyAOM("aom"), limits={"set_frequency.value": (50.0, 95.0)})]

    app = create_app(settings=_settings(), device_factory=inventory)
    with pytest.raises(InvalidDeviceLimitsError) as caught:
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - never reached
    message = str(caught.value)
    assert "fix the Lab PC inventory" in message
    assert "aom: site limit set_frequency.value (50 to 95) is not inside the driver's own limit (60 to 100)" in message
    assert caught.value.details["device_ids"] == ["aom"]


@pytest.mark.asyncio
async def test_a_lab_pc_refuses_to_start_on_an_inventory_with_a_bad_site_limit(tmp_path: Path) -> None:
    inventory = tmp_path / "lab_pc_09.py"
    inventory.write_text(
        "from labnet.devices.DummyAOM.physical import PhysicalDummyAOM\n"
        "from labnet.devices.core import site_limits\n"
        "def create_devices():\n"
        "    return [site_limits(PhysicalDummyAOM('aom'), limits={'set_frequncy.value': (65, 95)})]\n",
        encoding="utf-8",
    )
    settings = replace(_settings(), device_inventory=f"{inventory}:create_devices")
    app = create_app(settings=settings)
    with pytest.raises(DeviceLimitsError, match="dummy_aom has no method 'set_frequncy'"):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - never reached


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["dev_lab_pc_01.py", "dev_lab_pc_02.py"])
async def test_the_dev_inventories_start_with_their_site_limits(name: str) -> None:
    factory = load_device_factory(f"{REPO / 'scripts' / 'dev' / 'inventories' / name}:create_devices")
    app = create_app(settings=_settings(), device_factory=factory)
    async with app.router.lifespan_context(app):
        snapshots = {item.device_id: item for item in app.state.runtime.manager.snapshots()}
        assert all(item.available for item in snapshots.values()), snapshots
        if name == "dev_lab_pc_01.py":
            hints = snapshots["dev01-aom"].metadata["hints"]
            assert hints["limits"]["set_frequency.value"] == [65.0, 95.0]
            assert hints["sources"]["set_frequency.value"] == "site"
            assert snapshots["dev01-dummy-a"].metadata["hints"]["sources"] == {"write_value.value": "site"}
        else:
            assert snapshots["dev02-dummy-a"].metadata["hints"]["limits"] == {"write_value.value": [-100.0, 100.0]}


# ---- a remote Python client --------------------------------------------------------------------


class _Central:
    """Just enough of Central to lease one device at a Lab PC's endpoint."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/leases":
            device_id = str(json.loads(request.content)["device_id"])
            now = datetime.now(timezone.utc)
            return httpx.Response(201, json={
                "lease": {"lease_id": f"lease-{device_id}", "device_id": device_id, "user": "developer",
                          "created_at": now.isoformat(), "expires_at": (now + timedelta(seconds=60)).isoformat(),
                          "state": "active"},
                "device": {"device_id": device_id, "manager_id": "lab-pc-1", "device_type": "dummy_device",
                           "runtime_state": "ready", "availability": "available", "effective_available": False,
                           "endpoint": {"transport": "grpc", "target": self.endpoint,
                                        "api_version": "labnet.devices.v1", "secure": False}},
            })
        if request.method == "DELETE" and request.url.path.startswith("/api/v1/leases/"):
            lease_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"lease_id": lease_id, "state": "released", "released": True})
        raise AssertionError(f"Unexpected Central request: {request.method} {request.url}")


@contextmanager
def _lab_pc_in_a_thread(factory: Any) -> Iterator[int]:
    """A real Lab PC on its own event loop, for synchronous clients."""

    ready = threading.Event()
    stop = threading.Event()
    port: list[int] = []
    failure: list[BaseException] = []

    async def serve() -> None:
        app = create_app(settings=_settings(), device_factory=factory)
        async with app.router.lifespan_context(app):
            port.append(app.state.runtime.grpc_server.bound_port)
            ready.set()
            await asyncio.get_running_loop().run_in_executor(None, stop.wait)

    def run() -> None:
        try:
            asyncio.run(serve())
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            failure.append(exc)
            ready.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(30), "the Lab PC did not start"
        if failure:
            raise failure[0]
        yield port[0]
    finally:
        stop.set()
        thread.join(30)


def test_a_remote_python_client_gets_a_typed_error_for_a_call_outside_the_limits() -> None:
    with _lab_pc_in_a_thread(_bench) as port:
        client = LabClient("https://central.example", "secret", authenticate_on_start=False,
                           transport=httpx.MockTransport(_Central(f"127.0.0.1:{port}")))
        device = None
        try:
            device = DummyDevice(client=client, device_id="dd", auto_renew=False)
            device.write_value(7.5, channel="a")
            assert device.read_value(channel="a") == 7.5
            with pytest.raises(labnet.InvalidDeviceArgumentError) as caught:
                device.write_value(50.0, channel="a")
            assert caught.value.error_code == "ARGUMENT_OUTSIDE_LIMITS"
            assert "dd.write_value: value=50 is outside the site limit -10 to 10." in str(caught.value)
            assert device.read_value(channel="a") == 7.5  # never reached the driver
        finally:
            if device is not None:
                device.close()
            client.close()
