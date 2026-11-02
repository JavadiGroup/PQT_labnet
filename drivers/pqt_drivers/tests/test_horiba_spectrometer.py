"""The Horiba wrapper passes a Lab PC's checks, maps each method onto PQT_devices, and serves spectra."""

from __future__ import annotations

import math
import os
from dataclasses import replace

import grpc
import numpy as np
import pytest

from labnet.grpc import LEASE_METADATA_KEY
from labnet.lab_pc.app import create_app
from labnet.lab_pc.config import LabPCSettings
from labnet.testing import check_driver
from PQT_devices.spectrometer import Spectrum
from pqt_drivers import PhysicalHoribaSpectrometer
from pqt_drivers._generated import horiba_spectrometer_pb2, horiba_spectrometer_pb2_grpc

GRATINGS = ("600 gr/mm", "1200 gr/mm")


class FakeHoriba:
    """The HoribaSpectrometer methods the wrapper uses."""

    server_banner = "AFMServer 0.6.3"

    def __init__(self) -> None:
        self.is_connected = False
        self.exposure_s = 1.0
        self.accumulations = 1
        self.center_nm = 770.0
        self.grating = GRATINGS[0]
        self.acquisitions = 0

    def connect(self) -> None:
        self.is_connected = True

    def close(self) -> None:
        self.is_connected = False

    def get_available_gratings(self) -> tuple[str, ...]:
        return GRATINGS

    def get_detector(self) -> str:
        return "Synapse CCD"

    def acquire_spectrum(self) -> Spectrum:
        self.acquisitions += 1
        wavelengths = self.center_nm + np.linspace(-10.0, 10.0, 1024)
        counts = 100.0 + 5000.0 * np.exp(-((wavelengths - 770.0) / 0.05) ** 2)
        return Spectrum(wavelengths, counts, self.exposure_s, self.accumulations, self.center_nm)

    def get_exposure_s(self) -> float:
        return self.exposure_s

    def set_exposure_s(self, exposure_s: float) -> None:
        if not exposure_s > 0:
            raise ValueError("exposure_s must be positive")
        self.exposure_s = float(exposure_s)

    def get_accumulations(self) -> int:
        return self.accumulations

    def set_accumulations(self, accumulations: int) -> None:
        self.accumulations = accumulations

    def get_center_wavelength_nm(self) -> float:
        return self.center_nm

    def set_center_wavelength_nm(self, wavelength_nm: float) -> None:
        self.center_nm = float(wavelength_nm)

    def get_acquisition_time_s(self) -> float:
        return self.exposure_s * self.accumulations + 0.2

    def get_grating(self) -> str:
        return self.grating

    def set_grating(self, grating: str) -> None:
        self.grating = grating


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


def test_a_lab_pc_would_accept_the_driver_and_its_grating_choices() -> None:
    snapshot = check_driver(PhysicalHoribaSpectrometer("horiba-spec-vis", spectrometer=FakeHoriba()))
    assert snapshot.metadata["hints"]["choices"] == {"set_grating.grating": list(GRATINGS)}
    assert snapshot.metadata["detector"] == "Synapse CCD"


def test_a_center_wavelength_range_is_published_as_a_limit() -> None:
    physical = PhysicalHoribaSpectrometer(
        "horiba-spec-vis", center_wavelength_range_nm=(700.0, 800.0), spectrometer=FakeHoriba()
    )
    assert physical.metadata()["hints"]["limits"] == {"set_center_wavelength.value": [700.0, 800.0]}


def test_each_method_maps_onto_the_driver() -> None:
    physical = PhysicalHoribaSpectrometer("horiba-spec-vis", spectrometer=FakeHoriba())
    physical.connect()
    with pytest.raises(RuntimeError, match="No spectrum"):
        physical.get_spectrum_wavelengths()
    counts = physical.acquire_spectrum()
    wavelengths = physical.get_spectrum_wavelengths()
    assert len(counts) == len(wavelengths) == 1024
    assert all(type(value) is float for value in counts + wavelengths)
    assert wavelengths[int(np.argmax(counts))] == pytest.approx(770.0, abs=0.02)
    assert physical.set_exposure(0.1) == 0.1
    assert physical.read_exposure() == 0.1
    assert physical.set_accumulations(3) == 3.0
    assert physical.read_accumulations() == 3.0
    assert physical.read_acquisition_time() == pytest.approx(0.5)
    assert physical.set_center_wavelength(780.0) == 780.0
    assert physical.read_center_wavelength() == 780.0
    assert physical.set_grating("1200 gr/mm") == "1200 gr/mm"
    assert physical.get_grating() == "1200 gr/mm"
    physical.disconnect()
    assert physical.status() == "disconnected"


@pytest.mark.parametrize("value", [2.5, math.nan])
def test_accumulations_must_be_whole(value: float) -> None:
    physical = PhysicalHoribaSpectrometer("horiba-spec-vis", spectrometer=FakeHoriba())
    with pytest.raises(ValueError, match="whole number"):
        physical.set_accumulations(value)


@pytest.mark.asyncio
async def test_a_lab_pc_serves_a_spectrum_as_float_lists_and_refuses_unknown_gratings() -> None:
    fake = FakeHoriba()
    settings = replace(
        LabPCSettings.from_env(search_cwd=False),
        grpc_port=0,
        grpc_advertised_target="127.0.0.1:0",
    )
    app = create_app(
        settings=settings,
        device_factory=lambda: [PhysicalHoribaSpectrometer("horiba-spec-vis", spectrometer=fake)],
    )
    async with app.router.lifespan_context(app):
        server = app.state.runtime.grpc_server
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}")
        metadata = ((LEASE_METADATA_KEY, "development-lease"),)
        request = horiba_spectrometer_pb2.HoribaSpectrometerRequest
        try:
            stub = horiba_spectrometer_pb2_grpc.HoribaSpectrometerServiceStub(channel)
            counts = await stub.AcquireSpectrum(request(device_id="horiba-spec-vis", operation_id="a-1"), metadata=metadata)
            wavelengths = await stub.GetSpectrumWavelengths(
                request(device_id="horiba-spec-vis", operation_id="w-1"), metadata=metadata
            )
            assert counts.result.WhichOneof("kind") == "floating_point_sequence"
            assert len(counts.result.floating_point_sequence.values) == 1024
            assert len(wavelengths.result.floating_point_sequence.values) == 1024

            with pytest.raises(grpc.aio.AioRpcError) as refused:
                await stub.SetGrating(
                    horiba_spectrometer_pb2.HoribaSpectrometerGratingRequest(
                        device_id="horiba-spec-vis", operation_id="g-1", grating="300 gr/mm"
                    ),
                    metadata=metadata,
                )
            assert refused.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert fake.grating == GRATINGS[0]
        finally:
            await channel.close()
