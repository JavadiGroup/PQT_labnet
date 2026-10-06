from __future__ import annotations

import os
from dataclasses import replace

import grpc
import pytest

from labnet import SimulatedWavemeter
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice
from labnet.devices.SimulatedWavemeter.physical import PhysicalSimulatedWavemeter
from labnet.grpc import LEASE_METADATA_KEY
from labnet.grpc.generated import (
    simulated_wavemeter_pb2,
    simulated_wavemeter_pb2_grpc,
    transfer_pb2,
)
from labnet.grpc.generated.device_contracts import (
    DEVICE_CONTRACT_BY_TYPE,
    DEVICE_CONTRACTS,
    validate_implementation,
)
from labnet.lab_pc.app import create_app
from labnet.lab_pc.config import LabPCSettings


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


def test_local_facade_reads_a_noisy_wavelength_near_the_line() -> None:
    wavemeter = SimulatedWavemeter(device_id="local-wavemeter")
    try:
        readings = [wavemeter.read_wavelength() for _ in range(20)]
    finally:
        wavemeter.close()

    assert all(abs(value - 780.241) < 0.01 for value in readings)
    assert len(set(readings)) > 1  # noise, not a constant


def test_driver_satisfies_its_generated_contract() -> None:
    contract = DEVICE_CONTRACT_BY_TYPE["simulated_wavemeter"]

    validate_implementation(PhysicalSimulatedWavemeter, contract)
    assert contract.allowed_physical_methods == ("read_wavelength", "set_exposure")


def test_longer_exposure_is_quieter_and_slower() -> None:
    import statistics
    import time

    meter = PhysicalSimulatedWavemeter("exposure-test", noise_nm=0.01, drift_nm_per_hour=0.0)
    meter.connect()
    try:
        short = [meter.read_wavelength() for _ in range(60)]
        assert meter.set_exposure(100.0) == 100.0
        started = time.monotonic()
        long = [meter.read_wavelength() for _ in range(5)]
        assert time.monotonic() - started >= 0.5  # 5 readings x 100 ms
        long += [meter.read_wavelength() for _ in range(55)]
    finally:
        meter.disconnect()

    # Ten times the exposure gives about sqrt(10) ~ 3.2 times less noise.
    assert statistics.stdev(long) < statistics.stdev(short) / 2
    with pytest.raises(ValueError, match="Exposure must be between"):
        meter.set_exposure(0)


@pytest.mark.asyncio
async def test_lab_pc_serves_generated_families_side_by_side() -> None:
    settings = replace(
        LabPCSettings.from_env(search_cwd=False),
        grpc_port=0,
        grpc_advertised_target="127.0.0.1:0",
    )
    app = create_app(
        settings=settings,
        device_factory=lambda: [
            PhysicalDummyDevice("dummy-a"),
            PhysicalSimulatedWavemeter("wavemeter-a", noise_nm=0.0),
        ],
    )

    async with app.router.lifespan_context(app):
        server = app.state.runtime.grpc_server
        assert set(server.device_services) == {
            contract.device_type for contract in DEVICE_CONTRACTS
        }
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}")
        metadata = ((LEASE_METADATA_KEY, "development-lease"),)
        try:
            stub = simulated_wavemeter_pb2_grpc.SimulatedWavemeterServiceStub(channel)
            read = await stub.ReadWavelength(
                simulated_wavemeter_pb2.ReadWavelengthRequest(
                    device_id="wavemeter-a", operation_id="read-1"
                ),
                metadata=metadata,
            )
            assert read.measurement.unit == "nm"
            assert read.measurement.value == pytest.approx(780.241, abs=1e-3)

            samples = [
                response
                async for response in stub.StreamWavelength(
                    transfer_pb2.StreamDataRequest(
                        device_id="wavemeter-a",
                        operation_id="stream-1",
                        sample_rate_hz=50.0,
                        sample_count=3,
                    ),
                    metadata=metadata,
                )
            ]
            assert [sample.measurement.sequence for sample in samples] == [0, 1, 2]

            # A dummy device must not be reachable through the wavemeter service.
            with pytest.raises(grpc.aio.AioRpcError):
                await stub.ReadWavelength(
                    simulated_wavemeter_pb2.ReadWavelengthRequest(
                        device_id="dummy-a", operation_id="wrong-family"
                    ),
                    metadata=metadata,
                )
        finally:
            await channel.close()
