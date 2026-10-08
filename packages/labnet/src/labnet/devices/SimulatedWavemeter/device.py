"""Generated-contract-driven public SimulatedWavemeter facade."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import SIMULATED_WAVEMETER_CONTRACT

from .physical import PhysicalSimulatedWavemeter


class SimulatedWavemeter(ContractDeviceFacade):
    """Use the simulated wavemeter locally, or remotely through a lease."""

    contract = SIMULATED_WAVEMETER_CONTRACT
    physical_type = PhysicalSimulatedWavemeter


__all__ = ["SimulatedWavemeter"]
