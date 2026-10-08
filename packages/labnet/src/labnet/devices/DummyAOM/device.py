"""Generated-contract-driven public DummyAOM facade."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import DUMMY_AOM_CONTRACT

from .physical import PhysicalDummyAOM


class DummyAOM(ContractDeviceFacade):
    """Use the simulated acousto-optic modulator locally, or remotely through a lease."""

    contract = DUMMY_AOM_CONTRACT
    physical_type = PhysicalDummyAOM


__all__ = ["DummyAOM"]
