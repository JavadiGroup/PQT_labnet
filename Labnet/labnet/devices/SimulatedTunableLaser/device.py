"""Generated-contract-driven public SimulatedTunableLaser facade."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import SIMULATED_TUNABLE_LASER_CONTRACT

from .physical import PhysicalSimulatedTunableLaser


class SimulatedTunableLaser(ContractDeviceFacade):
    """Use the simulated tunable laser locally, or remotely through a lease."""

    contract = SIMULATED_TUNABLE_LASER_CONTRACT
    physical_type = PhysicalSimulatedTunableLaser


__all__ = ["SimulatedTunableLaser"]
