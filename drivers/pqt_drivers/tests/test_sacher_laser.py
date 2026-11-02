"""The Sacher wrapper passes a Lab PC's checks, maps each method onto PQT_devices, and keeps site limits."""

from __future__ import annotations

import os
from dataclasses import replace

import grpc
import pytest

from labnet.devices.core import site_limits
from labnet.grpc import LEASE_METADATA_KEY
from labnet.lab_pc.app import create_app
from labnet.lab_pc.config import LabPCSettings
from labnet.testing import check_driver
from PQT_devices.laser.sacher_laser import VIS_CALIBRATION, SacherLaser, SacherLaserError
from pqt_drivers import PhysicalSacherLaser
from pqt_drivers._generated import sacher_laser_pb2, sacher_laser_pb2_grpc

# The atto-pc site limits: the VIS calibration less 1 nm at each end, rounded inward.
SITE_RANGE_NM = (757.0, 782.0)


class FakeSacher:
    """The SacherLaser methods the wrapper uses, on the real VIS calibration."""

    calibration = VIS_CALIBRATION

    def __init__(self) -> None:
        self.is_connected = False
        self.position = VIS_CALIBRATION.position_for_wavelength(770.0)
        self.commanded: float | None = None
        self.velocity = 2.0
        self.moves: list[float] = []

    def connect(self) -> None:
        self.is_connected = True

    def close(self) -> None:
        self.is_connected = False

    def get_state(self) -> str:
        return "ST_DISABLED"

    def move_to_wavelength_nm(self, wavelength_nm: float) -> None:
        wavelength_nm = self.calibration.validate_wavelength(wavelength_nm)
        self.commanded = wavelength_nm
        self.position = self.calibration.position_for_wavelength(wavelength_nm)
        self.moves.append(wavelength_nm)

    def rebase_position_axis(self, measured_wavelength_nm: float) -> int:
        self.position = self.calibration.position_for_wavelength(measured_wavelength_nm)
        return self.position

    def get_live_wavelength_nm(self) -> float:
        return self.calibration.wavelength_for_position(self.position)

    def get_commanded_wavelength_nm(self) -> float:
        if self.commanded is None:
            raise SacherLaserError("no wavelength has been commanded yet; call move_to_wavelength_nm() first")
        return self.commanded

    def get_velocity_nm_s(self) -> float:
        return self.velocity

    def set_velocity_nm_s(self, velocity_nm_s: float) -> None:
        self.velocity = velocity_nm_s


@pytest.fixture(autouse=True)
def isolated_labnet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


def test_a_lab_pc_would_accept_the_driver_with_the_atto_pc_site_limits() -> None:
    device = site_limits(
        PhysicalSacherLaser("sacher-vis", laser=FakeSacher()),
        limits={
            "move_to_wavelength_nm.wavelength_nm": SITE_RANGE_NM,
            "rebase_position_axis.measured_wavelength_nm": SITE_RANGE_NM,
        },
    )
    snapshot = check_driver(device)
    limits = snapshot.metadata["hints"]["limits"]
    assert limits["move_to_wavelength_nm.wavelength_nm"] == list(SITE_RANGE_NM)
    assert limits["set_velocity.value"] == [SacherLaser.MIN_VELOCITY_NM_S, SacherLaser.MAX_VELOCITY_NM_S]


def test_driver_limits_are_the_calibration_range() -> None:
    limits = PhysicalSacherLaser("sacher-vis", laser=FakeSacher()).metadata()["hints"]["limits"]
    calibration_range = [VIS_CALIBRATION.wavelength_min_nm, VIS_CALIBRATION.wavelength_max_nm]
    assert limits["move_to_wavelength_nm.wavelength_nm"] == calibration_range
    assert limits["rebase_position_axis.measured_wavelength_nm"] == calibration_range
    # The site limits lie inside it, as the Lab PC requires.
    assert calibration_range[0] < SITE_RANGE_NM[0] < SITE_RANGE_NM[1] < calibration_range[1]


def test_each_method_maps_onto_the_driver() -> None:
    fake = FakeSacher()
    physical = PhysicalSacherLaser("sacher-vis", laser=fake)
    physical.connect()
    assert physical.status() == "st_disabled"
    with pytest.raises(SacherLaserError, match="no wavelength has been commanded"):
        physical.read_commanded_wavelength()
    assert physical.move_to_wavelength_nm(775.0) is None
    assert physical.read_commanded_wavelength() == 775.0
    assert physical.read_live_wavelength() == pytest.approx(775.0, abs=1e-3)
    assert physical.rebase_position_axis(775.2) == pytest.approx(775.2, abs=1e-3)
    assert physical.read_commanded_wavelength() == 775.0  # a rebase never changes the commanded value
    assert physical.set_velocity(0.5) == 0.5
    assert physical.read_velocity() == 0.5
    assert physical.get_state() == "ST_DISABLED"
    with pytest.raises(ValueError, match="no channels"):
        physical.read_live_wavelength("1")
    physical.disconnect()
    assert physical.status() == "disconnected"


@pytest.mark.asyncio
async def test_a_lab_pc_refuses_moves_and_rebases_outside_the_site_limits() -> None:
    fake = FakeSacher()
    settings = replace(
        LabPCSettings.from_env(search_cwd=False),
        grpc_port=0,
        grpc_advertised_target="127.0.0.1:0",
    )
    app = create_app(
        settings=settings,
        device_factory=lambda: [
            site_limits(
                PhysicalSacherLaser("sacher-vis", laser=fake),
                limits={
                    "move_to_wavelength_nm.wavelength_nm": SITE_RANGE_NM,
                    "rebase_position_axis.measured_wavelength_nm": SITE_RANGE_NM,
                },
            )
        ],
    )
    async with app.router.lifespan_context(app):
        server = app.state.runtime.grpc_server
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}")
        metadata = ((LEASE_METADATA_KEY, "development-lease"),)
        try:
            stub = sacher_laser_pb2_grpc.SacherLaserServiceStub(channel)
            await stub.MoveToWavelengthNm(
                sacher_laser_pb2.SacherLaserMoveRequest(device_id="sacher-vis", operation_id="m-1", wavelength_nm=770.5),
                metadata=metadata,
            )
            assert fake.moves == [770.5]

            # Inside the calibration but outside the site limits: refused before the driver runs.
            for operation_id, wavelength in [("m-2", 756.5), ("m-3", 782.5)]:
                with pytest.raises(grpc.aio.AioRpcError) as refused:
                    await stub.MoveToWavelengthNm(
                        sacher_laser_pb2.SacherLaserMoveRequest(
                            device_id="sacher-vis", operation_id=operation_id, wavelength_nm=wavelength
                        ),
                        metadata=metadata,
                    )
                assert refused.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            with pytest.raises(grpc.aio.AioRpcError) as refused:
                await stub.RebasePositionAxis(
                    sacher_laser_pb2.SacherLaserRebaseRequest(
                        device_id="sacher-vis", operation_id="r-1", measured_wavelength_nm=790.0
                    ),
                    metadata=metadata,
                )
            assert refused.value.code() == grpc.StatusCode.INVALID_ARGUMENT
            assert fake.moves == [770.5]

            rebased = await stub.RebasePositionAxis(
                sacher_laser_pb2.SacherLaserRebaseRequest(
                    device_id="sacher-vis", operation_id="r-2", measured_wavelength_nm=770.6
                ),
                metadata=metadata,
            )
            assert rebased.result.floating_point_value == pytest.approx(770.6, abs=1e-3)
        finally:
            await channel.close()
