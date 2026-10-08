"""Generated-contract-driven public DummyEOM facade."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import DUMMY_EOM_CONTRACT

from .physical import PhysicalDummyEOM


class DummyEOM(ContractDeviceFacade):
    """Use the simulated electro-optic modulator locally, or remotely through a lease."""

    contract = DUMMY_EOM_CONTRACT
    physical_type = PhysicalDummyEOM


__all__ = ["DummyEOM"]
