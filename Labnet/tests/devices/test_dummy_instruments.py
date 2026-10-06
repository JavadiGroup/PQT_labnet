"""The simulated optics bench: DummyAOM, DummyEOM, DummyAWG and DummyLaser."""

from __future__ import annotations

import math
import os
from dataclasses import replace

import grpc
import pytest

import labnet
from labnet.devices.DummyAOM.physical import PhysicalDummyAOM
from labnet.devices.DummyAWG.physical import PhysicalDummyAWG
from labnet.devices.DummyEOM.physical import PhysicalDummyEOM, bessel_j
from labnet.devices.DummyLaser.physical import PhysicalDummyLaser
from labnet.grpc import LEASE_METADATA_KEY
from labnet.grpc.generated import (
    dummy_aom_pb2,
    dummy_aom_pb2_grpc,
    dummy_awg_pb2,
    dummy_awg_pb2_grpc,
    dummy_eom_pb2_grpc,
    dummy_laser_pb2,
    dummy_laser_pb2_grpc,
    transfer_pb2,
)
from labnet.grpc.generated.device_contracts import DEVICE_CONTRACT_BY_TYPE, validate_implementation
from labnet.lab_pc.app import create_app
from labnet.lab_pc.config import LabPCSettings


FAMILIES = {
    "dummy_aom": PhysicalDummyAOM,
    "dummy_eom": PhysicalDummyEOM,
    "dummy_awg": PhysicalDummyAWG,
    "dummy_laser": PhysicalDummyLaser,
}


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


def _connected(device):
    device.connect()
    return device


@pytest.mark.parametrize("device_type", sorted(FAMILIES))
def test_each_driver_satisfies_its_contract(device_type: str) -> None:
    driver = FAMILIES[device_type]("bench-device")
    contract = DEVICE_CONTRACT_BY_TYPE[device_type]
    validate_implementation(driver, contract)
    assert driver.device_type == device_type
    assert set(driver.allowed_methods()) == set(contract.physical_methods)
    facade = getattr(labnet, contract.service_name.removesuffix("Service"))
    assert facade.contract is contract and facade.physical_type is FAMILIES[device_type]


# ---- AOM -------------------------------------------------------------------------


def test_aom_efficiency_peaks_at_center_frequency_and_saturation_power() -> None:
    aom = _connected(PhysicalDummyAOM("aom", noise=0.0))

    aom.set_rf_power(1.0)
    assert aom.read_diffraction_efficiency() == pytest.approx(0.85)
    aom.set_frequency(92.0)  # one bandwidth away
    assert aom.read_diffraction_efficiency() == pytest.approx(0.85 * math.exp(-1))
    aom.set_frequency(80.0)
    aom.set_rf_power(0.5)
    assert aom.read_diffraction_efficiency() == pytest.approx(0.85 * math.sin(math.pi / 2 * math.sqrt(0.5)) ** 2)
    aom.set_rf_enabled(False)
    assert aom.read_diffraction_efficiency() == 0.0
    assert aom.read_deflection_angle() == pytest.approx(780e-9 * 80e6 / 4200 * 1e3)  # 14.86 mrad


def test_aom_rejects_out_of_range_settings() -> None:
    aom = _connected(PhysicalDummyAOM("aom"))
    for call in (lambda: aom.set_frequency(59), lambda: aom.set_rf_power(2.5), lambda: aom.set_rf_enabled(1)):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(ValueError, match="one channel"):
        aom.read_diffraction_efficiency(channel="CH2")
    assert aom.read_frequency() == 80.0  # unchanged


# ---- EOM -------------------------------------------------------------------------


def test_eom_sidebands_follow_bessel_functions() -> None:
    assert bessel_j(0, 1.0) == pytest.approx(0.7651976866, abs=1e-10)
    assert bessel_j(1, 1.0) == pytest.approx(0.4400505857, abs=1e-10)

    eom = _connected(PhysicalDummyEOM("eom", noise=0.0))
    eom.set_rf_amplitude(2.5)  # half of V_pi on resonance
    assert eom.read_modulation_depth() == pytest.approx(math.pi / 2)
    depth = math.pi / 2
    assert eom.read_carrier_fraction() == pytest.approx(bessel_j(0, depth) ** 2)
    assert eom.read_sideband_ratio() == pytest.approx((bessel_j(1, depth) / bessel_j(0, depth)) ** 2)
    assert eom.read_carrier_fraction() + 2 * eom.read_sideband_fraction() <= 1.0

    eom.set_modulation_frequency(20.5)  # 2Q(f - fr)/fr = 1: depth falls by sqrt(2)
    assert eom.read_modulation_depth() == pytest.approx(math.pi / 2 / math.sqrt(2))
    eom.set_rf_amplitude(3.5)
    eom.set_modulation_frequency(20.0)
    assert eom.read_modulation_depth() < 2.405  # never the carrier's zero
    eom.set_rf_enabled(False)
    assert eom.read_sideband_ratio() == 0.0


# ---- AWG -------------------------------------------------------------------------


def test_awg_channels_are_independent_and_start_off() -> None:
    awg = _connected(PhysicalDummyAWG("awg", noise=0.0))
    assert awg.read_output_rms() == 0.0 and awg.read_output_voltage("CH2") == 0.0

    awg.set_amplitude(2.0, channel="CH1")
    awg.set_output_enabled(True, channel="ch1")  # any letter case
    assert awg.read_output_rms() == pytest.approx(math.sqrt(0.5))  # sine: (A/2)^2 / 2
    assert -1.0 <= awg.read_output_voltage() <= 1.0

    assert awg.set_waveform("DC", channel="CH2") == "dc"
    awg.set_offset(1.5, channel="CH2")
    awg.set_output_enabled(True, channel="CH2")
    assert awg.read_output_rms("CH2") == pytest.approx(1.5)
    assert awg.read_output_voltage("CH2") == pytest.approx(1.5)
    assert awg.read_frequency("CH1") == awg.read_frequency("CH2") == 1000.0

    awg.set_waveform("square", channel="CH1")
    assert awg.read_output_rms("CH1") == pytest.approx(1.0)
    for call in (
        lambda: awg.set_waveform("noise"),
        lambda: awg.read_offset("CH3"),
        lambda: awg.set_amplitude(11),
        lambda: awg.set_offset(-6, channel="CH2"),
    ):
        with pytest.raises(ValueError):
            call()


def test_awg_publishes_channels_and_choices() -> None:
    hints = PhysicalDummyAWG("awg").metadata()["hints"]
    assert hints["channels"] == ["CH1", "CH2"]
    assert hints["choices"]["set_waveform.waveform"] == ["sine", "square", "triangle", "ramp", "dc"]
    assert hints["limits"]["set_offset.value"] == [-5.0, 5.0]


# ---- Laser -----------------------------------------------------------------------


def test_laser_power_rises_above_threshold_only_with_emission_on() -> None:
    laser = _connected(PhysicalDummyLaser("laser", noise_mw=0.0, noise_nm=0.0))
    laser.set_current(120)
    assert laser.read_power() == 0.0  # emission starts off
    laser.set_emission_enabled(True)
    assert laser.read_power() == pytest.approx(0.8 * 80)
    assert laser.read_wavelength() == pytest.approx(780.241 + 0.003 * 80)
    laser.set_current(30)  # below threshold
    assert laser.read_power() == 0.0
    laser.set_wavelength(770.0)
    assert laser.read_wavelength() == pytest.approx(770.0)
    with pytest.raises(ValueError):
        laser.set_wavelength(800)


# ---- served by a Lab PC ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_bench_is_served_over_grpc_with_typed_command_results() -> None:
    settings = replace(
        LabPCSettings.from_env(search_cwd=False),
        grpc_port=0,
        grpc_advertised_target="127.0.0.1:0",
    )
    app = create_app(
        settings=settings,
        device_factory=lambda: [
            PhysicalDummyAOM("aom", noise=0.0),
            PhysicalDummyEOM("eom", noise=0.0),
            PhysicalDummyAWG("awg", noise=0.0),
            PhysicalDummyLaser("laser", noise_mw=0.0, noise_nm=0.0),
        ],
    )
    async with app.router.lifespan_context(app):
        server = app.state.runtime.grpc_server
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}")
        metadata = ((LEASE_METADATA_KEY, "development-lease"),)
        try:
            aom = dummy_aom_pb2_grpc.DummyAOMServiceStub(channel)
            angle = await aom.ReadDeflectionAngle(
                dummy_aom_pb2.DummyAomRequest(device_id="aom", operation_id="op-1"), metadata=metadata
            )
            assert angle.measurement.unit == "mrad"
            assert angle.measurement.value == pytest.approx(14.857, abs=1e-3)

            awg = dummy_awg_pb2_grpc.DummyAWGServiceStub(channel)
            shape = await awg.SetWaveform(
                dummy_awg_pb2.DummyAwgWaveformRequest(device_id="awg", operation_id="op-2", channel="CH2", waveform="Square"),
                metadata=metadata,
            )
            assert shape.result.text_value == "square"
            output = await awg.SetOutputEnabled(
                dummy_awg_pb2.DummyAwgOutputRequest(device_id="awg", operation_id="op-3", channel="CH2", enabled=True),
                metadata=metadata,
            )
            assert output.result.boolean_value is True
            written = await awg.SetAmplitude(
                dummy_awg_pb2.DummyAwgWriteRequest(device_id="awg", operation_id="op-4", channel="CH2", value=4.0),
                metadata=metadata,
            )
            assert written.measurement.value == 4.0 and written.measurement.channel == "CH2"
            rms = await awg.ReadOutputRms(
                dummy_awg_pb2.DummyAwgChannelRequest(device_id="awg", operation_id="op-5", channel="CH2"),
                metadata=metadata,
            )
            assert rms.measurement.value == pytest.approx(2.0)  # square: A/2

            laser = dummy_laser_pb2_grpc.DummyLaserServiceStub(channel)
            emission = await laser.SetEmissionEnabled(
                dummy_laser_pb2.DummyLaserEmissionRequest(device_id="laser", operation_id="op-6", enabled=True),
                metadata=metadata,
            )
            assert emission.result.boolean_value is True

            eom = dummy_eom_pb2_grpc.DummyEOMServiceStub(channel)
            samples = [
                response
                async for response in eom.StreamSidebandRatio(
                    transfer_pb2.StreamDataRequest(
                        device_id="eom", operation_id="op-7", sample_rate_hz=50.0, sample_count=3
                    ),
                    metadata=metadata,
                )
            ]
            depth = math.pi * 1.0 / 5.0  # default 1 V on resonance
            assert [sample.measurement.value for sample in samples] == pytest.approx(
                [(bessel_j(1, depth) / bessel_j(0, depth)) ** 2] * 3
            )

            with pytest.raises(grpc.aio.AioRpcError):  # the AOM is not reachable as an AWG
                await awg.ReadOutputRms(
                    dummy_awg_pb2.DummyAwgChannelRequest(device_id="aom", operation_id="op-8"), metadata=metadata
                )
        finally:
            await channel.close()
