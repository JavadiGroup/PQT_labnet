"""Generated-contract-driven public DummyAWG facade."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import DUMMY_AWG_CONTRACT

from .physical import PhysicalDummyAWG


class DummyAWG(ContractDeviceFacade):
    """Use the simulated arbitrary waveform generator locally, or remotely through a lease."""

    contract = DUMMY_AWG_CONTRACT
    physical_type = PhysicalDummyAWG


__all__ = ["DummyAWG"]
