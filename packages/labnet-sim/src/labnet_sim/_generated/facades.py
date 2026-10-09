"""Generated LabNet device facades. Do not edit by hand."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade

from .contracts import DUMMY_AOM_CONTRACT, DUMMY_AWG_CONTRACT, DUMMY_DEVICE_CONTRACT, DUMMY_EOM_CONTRACT, DUMMY_LASER_CONTRACT, SIMULATED_TUNABLE_LASER_CONTRACT, SIMULATED_WAVEMETER_CONTRACT


class DummyAOM(ContractDeviceFacade):
    """DummyAOM: local through its driver, or remote through a lease."""

    contract = DUMMY_AOM_CONTRACT
    physical_type = 'labnet_sim.dummy_aom.physical:PhysicalDummyAOM'


class DummyAWG(ContractDeviceFacade):
    """DummyAWG: local through its driver, or remote through a lease."""

    contract = DUMMY_AWG_CONTRACT
    physical_type = 'labnet_sim.dummy_awg.physical:PhysicalDummyAWG'


class DummyDevice(ContractDeviceFacade):
    """DummyDevice: local through its driver, or remote through a lease."""

    contract = DUMMY_DEVICE_CONTRACT
    physical_type = 'labnet_sim.dummy_device.physical:PhysicalDummyDevice'


class DummyEOM(ContractDeviceFacade):
    """DummyEOM: local through its driver, or remote through a lease."""

    contract = DUMMY_EOM_CONTRACT
    physical_type = 'labnet_sim.dummy_eom.physical:PhysicalDummyEOM'


class DummyLaser(ContractDeviceFacade):
    """DummyLaser: local through its driver, or remote through a lease."""

    contract = DUMMY_LASER_CONTRACT
    physical_type = 'labnet_sim.dummy_laser.physical:PhysicalDummyLaser'


class SimulatedTunableLaser(ContractDeviceFacade):
    """SimulatedTunableLaser: local through its driver, or remote through a lease."""

    contract = SIMULATED_TUNABLE_LASER_CONTRACT
    physical_type = 'labnet_sim.simulated_tunable_laser.physical:PhysicalSimulatedTunableLaser'


class SimulatedWavemeter(ContractDeviceFacade):
    """SimulatedWavemeter: local through its driver, or remote through a lease."""

    contract = SIMULATED_WAVEMETER_CONTRACT
    physical_type = 'labnet_sim.simulated_wavemeter.physical:PhysicalSimulatedWavemeter'


__all__ = ['DummyAOM', 'DummyAWG', 'DummyDevice', 'DummyEOM', 'DummyLaser', 'SimulatedTunableLaser', 'SimulatedWavemeter']
