"""Generated-contract-driven public DummyLaser facade."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import DUMMY_LASER_CONTRACT

from .physical import PhysicalDummyLaser


class DummyLaser(ContractDeviceFacade):
    """Use the simulated tunable diode laser locally, or remotely through a lease."""

    contract = DUMMY_LASER_CONTRACT
    physical_type = PhysicalDummyLaser


__all__ = ["DummyLaser"]
