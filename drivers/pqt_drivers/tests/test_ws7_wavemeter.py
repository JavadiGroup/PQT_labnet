"""The WS7 wrapper passes a Lab PC's checks, maps each method onto PQT_devices, and serves a stream."""

from __future__ import annotations

import math
import os
from dataclasses import replace

import grpc
import pytest

from labnet.grpc import LEASE_METADATA_KEY
from labnet.grpc.generated import common_pb2, transfer_pb2
from labnet.lab_pc.app import create_app
from labnet.lab_pc.config import LabPCSettings
from labnet.testing import check_driver
from PQT_devices.wavemeter.ws7 import MeasurementState, WlmReadError, Ws7DeviceInfo, Ws7WavemeterError
from pqt_drivers import PhysicalWs7Wavemeter
from pqt_drivers._generated import ws7_wavemeter_pb2, ws7_wavemeter_pb2_grpc


class FakeWs7:
    """The WS7Wavemeter methods the wrapper uses, with the driver's error behaviour."""

    def __init__(self) -> None:
        self.channel = 1
        self.is_connected = False
        self.wavelength_nm: float | None = 770.1234
        self.exposure_ms = 20
        self.auto_exposure = False
        self.state = MeasurementState.MEASURING

    def connect(self) -> None:
        self.is_connected = True

    def close(self) -> None:
        self.is_connected = False

    def get_exposure_range_ms(self) -> tuple[int, int]:
        return 1, 2000

    def get_device_info(self) -> Ws7DeviceInfo:
        return Ws7DeviceInfo(7, 4321, 1, 2, 8)

    def get_wavelength_nm(self) -> float:
        if self.wavelength_nm is None:
            raise WlmReadError("GetWavelengthNum", -3, channel=self.channel)  # underexposed
        return self.wavelength_nm

    def get_frequency_thz(self) -> float:
        return 299_792.458 / self.get_wavelength_nm()

    def get_temperature_c(self) -> float:
        return 24.5

    def get_exposure_ms(self) -> int:
        return self.exposure_ms

    def set_exposure_ms(self, exposure_ms: int) -> None:
        if self.auto_exposure:
            raise Ws7WavemeterError("disable auto exposure before setting exposure")
        self.exposure_ms = exposure_ms

    def get_auto_exposure(self) -> bool:
        return self.auto_exposure

    def set_auto_exposure(self, enabled: bool) -> None:
        self.auto_exposure = enabled

    def get_measurement_state(self) -> MeasurementState:
        return self.state

    def start_measurement(self) -> None:
        self.state = MeasurementState.MEASURING

    def stop_measurement(self) -> None:
        self.state = MeasurementState.STOPPED


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


def test_a_lab_pc_would_accept_the_driver_and_its_exposure_limits() -> None:
    snapshot = check_driver(PhysicalWs7Wavemeter("ws7", wavemeter=FakeWs7()))
    assert snapshot.metadata["hints"]["limits"] == {"set_exposure.value": [1, 2000]}
    assert snapshot.metadata["serial_number"] == 4321


def test_each_method_maps_onto_the_driver() -> None:
    physical = PhysicalWs7Wavemeter("ws7", wavemeter=FakeWs7())
    physical.connect()
    assert physical.read_wavelength() == 770.1234
    assert physical.read_frequency() == pytest.approx(299_792.458 / 770.1234)
    assert physical.read_temperature() == 24.5
    assert physical.status() == "measuring"
    assert physical.stop_measurement() == "stopped"
    assert physical.start_measurement() == "measuring"
    assert physical.set_exposure(150) == 150.0
    assert physical.read_exposure() == 150.0
    assert physical.set_auto_exposure(True) is True
    with pytest.raises(Ws7WavemeterError, match="disable auto exposure"):
        physical.set_exposure(100)
    physical.disconnect()
    assert physical.status() == "disconnected"


@pytest.mark.parametrize("value", [10.5, math.nan, math.inf])
def test_exposure_must_be_whole_milliseconds(value: float) -> None:
    physical = PhysicalWs7Wavemeter("ws7", wavemeter=FakeWs7())
    with pytest.raises(ValueError, match="whole number"):
        physical.set_exposure(value)


def test_a_sample_without_signal_is_bad_instead_of_an_error() -> None:
    fake = FakeWs7()
    physical = PhysicalWs7Wavemeter("ws7", wavemeter=fake)
    assert physical.read_wavelength_sample() == {"value": 770.1234, "quality": "GOOD"}
    fake.wavelength_nm = None
    sample = physical.read_wavelength_sample("1")
    assert math.isnan(sample["value"]) and sample["quality"] == "BAD"
    with pytest.raises(WlmReadError):
        physical.read_wavelength()  # a single reading still fails loudly
    with pytest.raises(ValueError, match="channel 1 only"):
        physical.read_wavelength_sample("2")


@pytest.mark.asyncio
async def test_a_lab_pc_serves_reads_and_a_stream_that_survives_lost_signal() -> None:
    fake = FakeWs7()
    settings = replace(
        LabPCSettings.from_env(search_cwd=False),
        grpc_port=0,
        grpc_advertised_target="127.0.0.1:0",
    )
    app = create_app(settings=settings, device_factory=lambda: [PhysicalWs7Wavemeter("ws7", wavemeter=fake)])
    async with app.router.lifespan_context(app):
        server = app.state.runtime.grpc_server
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}")
        metadata = ((LEASE_METADATA_KEY, "development-lease"),)
        try:
            stub = ws7_wavemeter_pb2_grpc.Ws7WavemeterServiceStub(channel)
            read = await stub.ReadWavelength(
                ws7_wavemeter_pb2.Ws7WavemeterRequest(device_id="ws7", operation_id="read-1"),
                metadata=metadata,
            )
            assert (read.measurement.value, read.measurement.unit) == (770.1234, "nm")

            # Outside the WS7's own exposure range: refused by the Lab PC before the driver.
            with pytest.raises(grpc.aio.AioRpcError) as refused:
                await stub.SetExposure(
                    ws7_wavemeter_pb2.Ws7WavemeterSetExposureRequest(
                        device_id="ws7", operation_id="exp-1", value=5000
                    ),
                    metadata=metadata,
                )
            assert refused.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert fake.exposure_ms == 20

            fake.wavelength_nm = None
            samples = [
                response.measurement
                async for response in stub.StreamWavelength(
                    transfer_pb2.StreamDataRequest(
                        device_id="ws7", operation_id="stream-1", sample_rate_hz=50.0, sample_count=3
                    ),
                    metadata=metadata,
                )
            ]
            assert [sample.sequence for sample in samples] == [0, 1, 2]
            assert all(math.isnan(sample.value) for sample in samples)
            assert {sample.quality for sample in samples} == {common_pb2.MEASUREMENT_QUALITY_BAD}
        finally:
            await channel.close()
