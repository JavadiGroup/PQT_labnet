"""Generated LabNet device facades. Do not edit by hand."""

from __future__ import annotations

from labnet.devices.core.facade import ContractDeviceFacade

from .contracts import HORIBA_SPECTROMETER_CONTRACT, SACHER_LASER_CONTRACT, WS7_WAVEMETER_CONTRACT


class HoribaSpectrometer(ContractDeviceFacade):
    """HoribaSpectrometer: local through its driver, or remote through a lease."""

    contract = HORIBA_SPECTROMETER_CONTRACT
    physical_type = 'pqt_drivers.horiba_spectrometer.physical:PhysicalHoribaSpectrometer'


class SacherLaser(ContractDeviceFacade):
    """SacherLaser: local through its driver, or remote through a lease."""

    contract = SACHER_LASER_CONTRACT
    physical_type = 'pqt_drivers.sacher_laser.physical:PhysicalSacherLaser'


class Ws7Wavemeter(ContractDeviceFacade):
    """Ws7Wavemeter: local through its driver, or remote through a lease."""

    contract = WS7_WAVEMETER_CONTRACT
    physical_type = 'pqt_drivers.ws7_wavemeter.physical:PhysicalWs7Wavemeter'


__all__ = ['HoribaSpectrometer', 'SacherLaser', 'Ws7Wavemeter']
