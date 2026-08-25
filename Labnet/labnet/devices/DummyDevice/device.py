"""Generated-contract-driven public DummyDevice facade."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import DUMMY_DEVICE_CONTRACT

from .physical import PhysicalDummyDevice


class DummyDevice(ContractDeviceFacade):
    """Use local hardware without a client, or a remote lease with one."""

    contract = DUMMY_DEVICE_CONTRACT
    physical_type = PhysicalDummyDevice


__all__ = ["DummyDevice"]
