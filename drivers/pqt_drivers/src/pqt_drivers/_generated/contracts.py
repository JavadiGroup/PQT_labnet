"""Generated LabNet device RPC contracts. Do not edit by hand."""

from __future__ import annotations

from typing import Any, Final, Protocol, runtime_checkable

from google.protobuf import symbol_database as _symbol_database

# The types live in labnet.contracts; re-exported for code that imported them from here.
from labnet.contracts.types import (  # noqa: F401
    DeviceContract,
    ForwardedField,
    PhysicalContractError,
    RpcSpec,
    validate_implementation,
)

from . import horiba_spectrometer_pb2 as _horiba_spectrometer_pb2
from . import sacher_laser_pb2 as _sacher_laser_pb2
from . import ws7_wavemeter_pb2 as _ws7_wavemeter_pb2
from . import horiba_spectrometer_pb2_grpc as _horiba_spectrometer_pb2_grpc
from . import sacher_laser_pb2_grpc as _sacher_laser_pb2_grpc
from . import ws7_wavemeter_pb2_grpc as _ws7_wavemeter_pb2_grpc

_SYMBOLS = _symbol_database.Default()


@runtime_checkable
class HoribaSpectrometerPhysicalProtocol(Protocol):
    def acquire_spectrum(self) -> Any: ...
    def get_grating(self) -> Any: ...
    def get_spectrum_wavelengths(self) -> Any: ...
    def read_accumulations(self) -> Any: ...
    def read_acquisition_time(self) -> Any: ...
    def read_center_wavelength(self) -> Any: ...
    def read_exposure(self) -> Any: ...
    def set_accumulations(self, value: float) -> Any: ...
    def set_center_wavelength(self, value: float) -> Any: ...
    def set_exposure(self, value: float) -> Any: ...
    def set_grating(self, grating: str) -> Any: ...


_HORIBA_SPECTROMETER_ACQUIRE_SPECTRUM: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.AcquireSpectrum',
    rpc_name='AcquireSpectrum',
    python_name='acquire_spectrum',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='acquire_spectrum',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_HORIBA_SPECTROMETER_GET_GRATING: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.GetGrating',
    rpc_name='GetGrating',
    python_name='get_grating',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_grating',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_HORIBA_SPECTROMETER_GET_SPECTRUM_WAVELENGTHS: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.GetSpectrumWavelengths',
    rpc_name='GetSpectrumWavelengths',
    python_name='get_spectrum_wavelengths',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_spectrum_wavelengths',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_HORIBA_SPECTROMETER_READ_ACCUMULATIONS: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.ReadAccumulations',
    rpc_name='ReadAccumulations',
    python_name='read_accumulations',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_accumulations',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerMeasurementResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerRequest',
    response_full_name='pqt_drivers.v1.HoribaSpectrometerMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_HORIBA_SPECTROMETER_READ_ACQUISITION_TIME: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.ReadAcquisitionTime',
    rpc_name='ReadAcquisitionTime',
    python_name='read_acquisition_time',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_acquisition_time',
    default_unit='s',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerMeasurementResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerRequest',
    response_full_name='pqt_drivers.v1.HoribaSpectrometerMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_HORIBA_SPECTROMETER_READ_CENTER_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.ReadCenterWavelength',
    rpc_name='ReadCenterWavelength',
    python_name='read_center_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_center_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerMeasurementResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerRequest',
    response_full_name='pqt_drivers.v1.HoribaSpectrometerMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_HORIBA_SPECTROMETER_READ_EXPOSURE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.ReadExposure',
    rpc_name='ReadExposure',
    python_name='read_exposure',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_exposure',
    default_unit='s',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerMeasurementResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerRequest',
    response_full_name='pqt_drivers.v1.HoribaSpectrometerMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_HORIBA_SPECTROMETER_SET_ACCUMULATIONS: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.SetAccumulations',
    rpc_name='SetAccumulations',
    python_name='set_accumulations',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_accumulations',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerSetValueRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerMeasurementResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerSetValueRequest',
    response_full_name='pqt_drivers.v1.HoribaSpectrometerMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_HORIBA_SPECTROMETER_SET_CENTER_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.SetCenterWavelength',
    rpc_name='SetCenterWavelength',
    python_name='set_center_wavelength',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_center_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerSetValueRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerMeasurementResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerSetValueRequest',
    response_full_name='pqt_drivers.v1.HoribaSpectrometerMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_HORIBA_SPECTROMETER_SET_EXPOSURE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.SetExposure',
    rpc_name='SetExposure',
    python_name='set_exposure',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_exposure',
    default_unit='s',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerSetValueRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerMeasurementResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerSetValueRequest',
    response_full_name='pqt_drivers.v1.HoribaSpectrometerMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_HORIBA_SPECTROMETER_SET_GRATING: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.HoribaSpectrometerService.SetGrating',
    rpc_name='SetGrating',
    python_name='set_grating',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_grating',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.HoribaSpectrometerGratingRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.HoribaSpectrometerGratingRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('grating',),
    forwarded_field_specs=(
        ForwardedField(name='grating', python_type='str', optional=False),
    ),
)

HORIBA_SPECTROMETER_CONTRACT: Final = DeviceContract(
    device_type='horiba_spectrometer',
    service_full_name='pqt_drivers.v1.HoribaSpectrometerService',
    service_name='HoribaSpectrometerService',
    stub_type=_horiba_spectrometer_pb2_grpc.HoribaSpectrometerServiceStub,
    servicer_type=_horiba_spectrometer_pb2_grpc.HoribaSpectrometerServiceServicer,
    add_servicer_to_server=_horiba_spectrometer_pb2_grpc.add_HoribaSpectrometerServiceServicer_to_server,
    methods=(_HORIBA_SPECTROMETER_ACQUIRE_SPECTRUM, _HORIBA_SPECTROMETER_GET_GRATING, _HORIBA_SPECTROMETER_GET_SPECTRUM_WAVELENGTHS, _HORIBA_SPECTROMETER_READ_ACCUMULATIONS, _HORIBA_SPECTROMETER_READ_ACQUISITION_TIME, _HORIBA_SPECTROMETER_READ_CENTER_WAVELENGTH, _HORIBA_SPECTROMETER_READ_EXPOSURE, _HORIBA_SPECTROMETER_SET_ACCUMULATIONS, _HORIBA_SPECTROMETER_SET_CENTER_WAVELENGTH, _HORIBA_SPECTROMETER_SET_EXPOSURE, _HORIBA_SPECTROMETER_SET_GRATING),
    physical_methods=('acquire_spectrum', 'get_grating', 'get_spectrum_wavelengths', 'read_accumulations', 'read_acquisition_time', 'read_center_wavelength', 'read_exposure', 'set_accumulations', 'set_center_wavelength', 'set_exposure', 'set_grating'),
    physical_protocol=HoribaSpectrometerPhysicalProtocol,
    distribution='pqt-drivers',
    fingerprint='545023c2254ba5197717651f39646ceecca9ff5774836fed9771805ddb9ed5a2',
    facade='pqt_drivers:HoribaSpectrometer',
    physical='pqt_drivers.horiba_spectrometer.physical:PhysicalHoribaSpectrometer',
)


@runtime_checkable
class SacherLaserPhysicalProtocol(Protocol):
    def get_state(self) -> Any: ...
    def move_to_wavelength_nm(self, wavelength_nm: float) -> Any: ...
    def read_commanded_wavelength(self) -> Any: ...
    def read_live_wavelength(self, channel: str | None = None) -> Any: ...
    def read_velocity(self) -> Any: ...
    def rebase_position_axis(self, measured_wavelength_nm: float) -> Any: ...
    def set_velocity(self, value: float) -> Any: ...


_SACHER_LASER_GET_STATE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.SacherLaserService.GetState',
    rpc_name='GetState',
    python_name='get_state',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_state',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.SacherLaserRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_MOVE_TO_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.SacherLaserService.MoveToWavelengthNm',
    rpc_name='MoveToWavelengthNm',
    python_name='move_to_wavelength_nm',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='move_to_wavelength_nm',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserMoveRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.SacherLaserMoveRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('wavelength_nm',),
    forwarded_field_specs=(
        ForwardedField(name='wavelength_nm', python_type='float', optional=False),
    ),
)

_SACHER_LASER_READ_COMMANDED_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.SacherLaserService.ReadCommandedWavelength',
    rpc_name='ReadCommandedWavelength',
    python_name='read_commanded_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_commanded_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserMeasurementResponse'),
    request_full_name='pqt_drivers.v1.SacherLaserRequest',
    response_full_name='pqt_drivers.v1.SacherLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_READ_LIVE_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.SacherLaserService.ReadLiveWavelength',
    rpc_name='ReadLiveWavelength',
    python_name='read_live_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_live_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserMeasurementResponse'),
    request_full_name='pqt_drivers.v1.SacherLaserRequest',
    response_full_name='pqt_drivers.v1.SacherLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_READ_VELOCITY: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.SacherLaserService.ReadVelocity',
    rpc_name='ReadVelocity',
    python_name='read_velocity',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_velocity',
    default_unit='nm/s',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserMeasurementResponse'),
    request_full_name='pqt_drivers.v1.SacherLaserRequest',
    response_full_name='pqt_drivers.v1.SacherLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_REBASE_POSITION_AXIS: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.SacherLaserService.RebasePositionAxis',
    rpc_name='RebasePositionAxis',
    python_name='rebase_position_axis',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='rebase_position_axis',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserRebaseRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.SacherLaserRebaseRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('measured_wavelength_nm',),
    forwarded_field_specs=(
        ForwardedField(name='measured_wavelength_nm', python_type='float', optional=False),
    ),
)

_SACHER_LASER_SET_VELOCITY: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.SacherLaserService.SetVelocity',
    rpc_name='SetVelocity',
    python_name='set_velocity',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_velocity',
    default_unit='nm/s',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserSetVelocityRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.SacherLaserMeasurementResponse'),
    request_full_name='pqt_drivers.v1.SacherLaserSetVelocityRequest',
    response_full_name='pqt_drivers.v1.SacherLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_SACHER_LASER_STREAM_LIVE_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.SacherLaserService.StreamLiveWavelength',
    rpc_name='StreamLiveWavelength',
    python_name='stream_live_wavelength',
    pattern='RPC_PATTERN_POLLED_SCALAR_STREAM',
    pattern_value=6,
    physical_method='read_live_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.StreamDataRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.StreamDataResponse'),
    request_full_name='labnet.devices.v1.StreamDataRequest',
    response_full_name='labnet.devices.v1.StreamDataResponse',
    client_streaming=False,
    server_streaming=True,
    cardinality='server_streaming',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

SACHER_LASER_CONTRACT: Final = DeviceContract(
    device_type='sacher_laser',
    service_full_name='pqt_drivers.v1.SacherLaserService',
    service_name='SacherLaserService',
    stub_type=_sacher_laser_pb2_grpc.SacherLaserServiceStub,
    servicer_type=_sacher_laser_pb2_grpc.SacherLaserServiceServicer,
    add_servicer_to_server=_sacher_laser_pb2_grpc.add_SacherLaserServiceServicer_to_server,
    methods=(_SACHER_LASER_GET_STATE, _SACHER_LASER_MOVE_TO_WAVELENGTH_NM, _SACHER_LASER_READ_COMMANDED_WAVELENGTH, _SACHER_LASER_READ_LIVE_WAVELENGTH, _SACHER_LASER_READ_VELOCITY, _SACHER_LASER_REBASE_POSITION_AXIS, _SACHER_LASER_SET_VELOCITY, _SACHER_LASER_STREAM_LIVE_WAVELENGTH),
    physical_methods=('get_state', 'move_to_wavelength_nm', 'read_commanded_wavelength', 'read_live_wavelength', 'read_velocity', 'rebase_position_axis', 'set_velocity'),
    physical_protocol=SacherLaserPhysicalProtocol,
    distribution='pqt-drivers',
    fingerprint='d641e179f528da896ccca55734ab98ba32f2a603e05c1c8fced0c9ea5c7f31e4',
    facade='pqt_drivers:SacherLaser',
    physical='pqt_drivers.sacher_laser.physical:PhysicalSacherLaser',
)


@runtime_checkable
class Ws7WavemeterPhysicalProtocol(Protocol):
    def get_auto_exposure(self) -> Any: ...
    def get_measurement_state(self) -> Any: ...
    def read_exposure(self) -> Any: ...
    def read_frequency(self) -> Any: ...
    def read_temperature(self) -> Any: ...
    def read_wavelength(self) -> Any: ...
    def read_wavelength_sample(self, channel: str | None = None) -> Any: ...
    def set_auto_exposure(self, enabled: bool) -> Any: ...
    def set_exposure(self, value: float) -> Any: ...
    def start_measurement(self) -> Any: ...
    def stop_measurement(self) -> Any: ...


_WS7_WAVEMETER_GET_AUTO_EXPOSURE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.GetAutoExposure',
    rpc_name='GetAutoExposure',
    python_name='get_auto_exposure',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_auto_exposure',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_WS7_WAVEMETER_GET_MEASUREMENT_STATE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.GetMeasurementState',
    rpc_name='GetMeasurementState',
    python_name='get_measurement_state',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_measurement_state',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_WS7_WAVEMETER_READ_EXPOSURE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.ReadExposure',
    rpc_name='ReadExposure',
    python_name='read_exposure',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_exposure',
    default_unit='ms',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterMeasurementResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterRequest',
    response_full_name='pqt_drivers.v1.Ws7WavemeterMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_WS7_WAVEMETER_READ_FREQUENCY: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.ReadFrequency',
    rpc_name='ReadFrequency',
    python_name='read_frequency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_frequency',
    default_unit='THz',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterMeasurementResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterRequest',
    response_full_name='pqt_drivers.v1.Ws7WavemeterMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_WS7_WAVEMETER_READ_TEMPERATURE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.ReadTemperature',
    rpc_name='ReadTemperature',
    python_name='read_temperature',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_temperature',
    default_unit='degC',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterMeasurementResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterRequest',
    response_full_name='pqt_drivers.v1.Ws7WavemeterMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_WS7_WAVEMETER_READ_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.ReadWavelength',
    rpc_name='ReadWavelength',
    python_name='read_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterMeasurementResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterRequest',
    response_full_name='pqt_drivers.v1.Ws7WavemeterMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_WS7_WAVEMETER_SET_AUTO_EXPOSURE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.SetAutoExposure',
    rpc_name='SetAutoExposure',
    python_name='set_auto_exposure',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_auto_exposure',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterAutoExposureRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterAutoExposureRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('enabled',),
    forwarded_field_specs=(
        ForwardedField(name='enabled', python_type='bool', optional=False),
    ),
)

_WS7_WAVEMETER_SET_EXPOSURE: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.SetExposure',
    rpc_name='SetExposure',
    python_name='set_exposure',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_exposure',
    default_unit='ms',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterSetExposureRequest'),
    response_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterMeasurementResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterSetExposureRequest',
    response_full_name='pqt_drivers.v1.Ws7WavemeterMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_WS7_WAVEMETER_START_MEASUREMENT: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.StartMeasurement',
    rpc_name='StartMeasurement',
    python_name='start_measurement',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='start_measurement',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_WS7_WAVEMETER_STOP_MEASUREMENT: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.StopMeasurement',
    rpc_name='StopMeasurement',
    python_name='stop_measurement',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='stop_measurement',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('pqt_drivers.v1.Ws7WavemeterRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='pqt_drivers.v1.Ws7WavemeterRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_WS7_WAVEMETER_STREAM_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='pqt_drivers.v1.Ws7WavemeterService.StreamWavelength',
    rpc_name='StreamWavelength',
    python_name='stream_wavelength',
    pattern='RPC_PATTERN_POLLED_SCALAR_STREAM',
    pattern_value=6,
    physical_method='read_wavelength_sample',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.StreamDataRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.StreamDataResponse'),
    request_full_name='labnet.devices.v1.StreamDataRequest',
    response_full_name='labnet.devices.v1.StreamDataResponse',
    client_streaming=False,
    server_streaming=True,
    cardinality='server_streaming',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

WS7_WAVEMETER_CONTRACT: Final = DeviceContract(
    device_type='ws7_wavemeter',
    service_full_name='pqt_drivers.v1.Ws7WavemeterService',
    service_name='Ws7WavemeterService',
    stub_type=_ws7_wavemeter_pb2_grpc.Ws7WavemeterServiceStub,
    servicer_type=_ws7_wavemeter_pb2_grpc.Ws7WavemeterServiceServicer,
    add_servicer_to_server=_ws7_wavemeter_pb2_grpc.add_Ws7WavemeterServiceServicer_to_server,
    methods=(_WS7_WAVEMETER_GET_AUTO_EXPOSURE, _WS7_WAVEMETER_GET_MEASUREMENT_STATE, _WS7_WAVEMETER_READ_EXPOSURE, _WS7_WAVEMETER_READ_FREQUENCY, _WS7_WAVEMETER_READ_TEMPERATURE, _WS7_WAVEMETER_READ_WAVELENGTH, _WS7_WAVEMETER_SET_AUTO_EXPOSURE, _WS7_WAVEMETER_SET_EXPOSURE, _WS7_WAVEMETER_START_MEASUREMENT, _WS7_WAVEMETER_STOP_MEASUREMENT, _WS7_WAVEMETER_STREAM_WAVELENGTH),
    physical_methods=('get_auto_exposure', 'get_measurement_state', 'read_exposure', 'read_frequency', 'read_temperature', 'read_wavelength', 'read_wavelength_sample', 'set_auto_exposure', 'set_exposure', 'start_measurement', 'stop_measurement'),
    physical_protocol=Ws7WavemeterPhysicalProtocol,
    distribution='pqt-drivers',
    fingerprint='83e3a25df286e8d4e02c00cb6966a6a81aad104808d41eef6800e35d777ea56b',
    facade='pqt_drivers:Ws7Wavemeter',
    physical='pqt_drivers.ws7_wavemeter.physical:PhysicalWs7Wavemeter',
)


DEVICE_CONTRACTS: Final[tuple[DeviceContract, ...]] = (HORIBA_SPECTROMETER_CONTRACT, SACHER_LASER_CONTRACT, WS7_WAVEMETER_CONTRACT)
DEVICE_CONTRACT_BY_TYPE: Final[dict[str, DeviceContract]] = {
    contract.device_type: contract for contract in DEVICE_CONTRACTS
}
DEVICE_CONTRACT_BY_SERVICE: Final[dict[str, DeviceContract]] = {
    contract.service_full_name: contract for contract in DEVICE_CONTRACTS
}
RPC_SPEC_BY_FULL_NAME: Final[dict[str, RpcSpec]] = {
    method.rpc_full_name: method
    for contract in DEVICE_CONTRACTS
    for method in contract.methods
}
