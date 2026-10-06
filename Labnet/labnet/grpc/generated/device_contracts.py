"""Generated LabNet device RPC contracts. Do not edit by hand."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Final, Mapping, Protocol, Sequence, runtime_checkable

from google.protobuf import message as _message
from google.protobuf import symbol_database as _symbol_database
from . import common_pb2 as _common_pb2
from . import dummy_device_pb2 as _dummy_device_pb2
from . import sacher_laser_pb2 as _sacher_laser_pb2
from . import simulated_wavemeter_pb2 as _simulated_wavemeter_pb2
from . import transfer_pb2 as _transfer_pb2
from . import dummy_device_pb2_grpc as _dummy_device_pb2_grpc
from . import sacher_laser_pb2_grpc as _sacher_laser_pb2_grpc
from . import simulated_wavemeter_pb2_grpc as _simulated_wavemeter_pb2_grpc

_SYMBOLS = _symbol_database.Default()


class PhysicalContractError(TypeError):
    """A physical implementation does not satisfy its generated contract."""


@dataclass(frozen=True, slots=True)
class ForwardedField:
    name: str
    python_type: str
    optional: bool


@dataclass(frozen=True, slots=True)
class RpcSpec:
    rpc_full_name: str
    rpc_name: str
    python_name: str
    pattern: str
    pattern_value: int
    physical_method: str
    default_unit: str
    request_type: type[_message.Message]
    response_type: type[_message.Message]
    request_full_name: str
    response_full_name: str
    client_streaming: bool
    server_streaming: bool
    cardinality: str
    forwarded_fields: tuple[str, ...]
    forwarded_field_specs: tuple[ForwardedField, ...]


@dataclass(frozen=True, slots=True)
class DeviceContract:
    device_type: str
    service_full_name: str
    service_name: str
    stub_type: type[Any]
    servicer_type: type[Any]
    add_servicer_to_server: Callable[..., None]
    methods: tuple[RpcSpec, ...]
    physical_methods: tuple[str, ...]
    physical_protocol: type[Any]

    @property
    def allowed_physical_methods(self) -> tuple[str, ...]:
        return self.physical_methods

    @property
    def add_servicer(self) -> Callable[..., None]:
        return self.add_servicer_to_server


@runtime_checkable
class DummyDevicePhysicalProtocol(Protocol):
    def read_value(self, channel: str | None = None) -> Any: ...
    def write_value(self, value: float, channel: str | None = None) -> Any: ...


_DUMMY_DEVICE_READ_VALUE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyDeviceService.ReadValue',
    rpc_name='ReadValue',
    python_name='read_value',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_value',
    default_unit='Hz',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.ReadValueRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.ReadValueResponse'),
    request_full_name='labnet.devices.v1.ReadValueRequest',
    response_full_name='labnet.devices.v1.ReadValueResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_DEVICE_STREAM_DATA: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyDeviceService.StreamData',
    rpc_name='StreamData',
    python_name='stream_data',
    pattern='RPC_PATTERN_POLLED_SCALAR_STREAM',
    pattern_value=6,
    physical_method='read_value',
    default_unit='Hz',
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

_DUMMY_DEVICE_WRITE_VALUE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyDeviceService.WriteValue',
    rpc_name='WriteValue',
    python_name='write_value',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='write_value',
    default_unit='Hz',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.WriteValueRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.WriteValueResponse'),
    request_full_name='labnet.devices.v1.WriteValueRequest',
    response_full_name='labnet.devices.v1.WriteValueResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value', 'channel'),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

DUMMY_DEVICE_CONTRACT: Final = DeviceContract(
    device_type='dummy_device',
    service_full_name='labnet.devices.v1.DummyDeviceService',
    service_name='DummyDeviceService',
    stub_type=_dummy_device_pb2_grpc.DummyDeviceServiceStub,
    servicer_type=_dummy_device_pb2_grpc.DummyDeviceServiceServicer,
    add_servicer_to_server=_dummy_device_pb2_grpc.add_DummyDeviceServiceServicer_to_server,
    methods=(_DUMMY_DEVICE_READ_VALUE, _DUMMY_DEVICE_STREAM_DATA, _DUMMY_DEVICE_WRITE_VALUE),
    physical_methods=('read_value', 'write_value'),
    physical_protocol=DummyDevicePhysicalProtocol,
)


@runtime_checkable
class SacherLaserPhysicalProtocol(Protocol):
    def get_acceleration_nm_s2(self) -> Any: ...
    def get_deceleration_nm_s2(self) -> Any: ...
    def get_device_error_codes(self) -> Any: ...
    def get_live_wavelength_nm(self) -> Any: ...
    def get_position_offset_steps(self) -> Any: ...
    def get_position_steps(self) -> Any: ...
    def get_start_wavelength_nm(self) -> Any: ...
    def get_state(self) -> Any: ...
    def get_stored_position_steps(self) -> Any: ...
    def get_stored_wavelength_nm(self) -> Any: ...
    def get_target_wavelength_nm(self) -> Any: ...
    def get_velocity_nm_s(self) -> Any: ...
    def go_to_wavelength_nm(self, wavelength_nm: float, velocity_mode: str | None = None) -> Any: ...
    def jog_steps(self, relative_steps: int, velocity_mode: str | None = None) -> Any: ...
    def recalibrate_position(self, measured_wavelength_nm: float) -> Any: ...
    def reset_position(self) -> Any: ...
    def set_acceleration_nm_s2(self, acceleration_nm_s2: float) -> Any: ...
    def set_deceleration_nm_s2(self, deceleration_nm_s2: float) -> Any: ...
    def set_start_wavelength_nm(self, wavelength_nm: float) -> Any: ...
    def set_target_wavelength_nm(self, wavelength_nm: float) -> Any: ...
    def set_velocity_nm_s(self, velocity_nm_s: float) -> Any: ...
    def start_scan(self, start_wavelength_nm: float, target_wavelength_nm: float) -> Any: ...
    def stop(self) -> Any: ...


_SACHER_LASER_GET_ACCELERATION_NM_S2: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetAccelerationNmS2',
    rpc_name='GetAccelerationNmS2',
    python_name='get_acceleration_nm_s2',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='get_acceleration_nm_s2',
    default_unit='nm/s2',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherMeasurementResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.SacherMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_DECELERATION_NM_S2: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetDecelerationNmS2',
    rpc_name='GetDecelerationNmS2',
    python_name='get_deceleration_nm_s2',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='get_deceleration_nm_s2',
    default_unit='nm/s2',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherMeasurementResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.SacherMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_DEVICE_ERROR_CODES: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetDeviceErrorCodes',
    rpc_name='GetDeviceErrorCodes',
    python_name='get_device_error_codes',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_device_error_codes',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_LIVE_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetLiveWavelengthNm',
    rpc_name='GetLiveWavelengthNm',
    python_name='get_live_wavelength_nm',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='get_live_wavelength_nm',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherMeasurementResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.SacherMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_POSITION_OFFSET_STEPS: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetPositionOffsetSteps',
    rpc_name='GetPositionOffsetSteps',
    python_name='get_position_offset_steps',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_position_offset_steps',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_POSITION_STEPS: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetPositionSteps',
    rpc_name='GetPositionSteps',
    python_name='get_position_steps',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_position_steps',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_START_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetStartWavelengthNm',
    rpc_name='GetStartWavelengthNm',
    python_name='get_start_wavelength_nm',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='get_start_wavelength_nm',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherMeasurementResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.SacherMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_STATE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetState',
    rpc_name='GetState',
    python_name='get_state',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_state',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_STORED_POSITION_STEPS: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetStoredPositionSteps',
    rpc_name='GetStoredPositionSteps',
    python_name='get_stored_position_steps',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='get_stored_position_steps',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_STORED_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetStoredWavelengthNm',
    rpc_name='GetStoredWavelengthNm',
    python_name='get_stored_wavelength_nm',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='get_stored_wavelength_nm',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherMeasurementResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.SacherMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_TARGET_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetTargetWavelengthNm',
    rpc_name='GetTargetWavelengthNm',
    python_name='get_target_wavelength_nm',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='get_target_wavelength_nm',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherMeasurementResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.SacherMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GET_VELOCITY_NM_S: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GetVelocityNmS',
    rpc_name='GetVelocityNmS',
    python_name='get_velocity_nm_s',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='get_velocity_nm_s',
    default_unit='nm/s',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherMeasurementResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.SacherMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_GO_TO_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.GoToWavelengthNm',
    rpc_name='GoToWavelengthNm',
    python_name='go_to_wavelength_nm',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='go_to_wavelength_nm',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherGoToWavelengthRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherGoToWavelengthRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('wavelength_nm', 'velocity_mode'),
    forwarded_field_specs=(
        ForwardedField(name='wavelength_nm', python_type='float', optional=False),
        ForwardedField(name='velocity_mode', python_type='str | None', optional=True),
    ),
)

_SACHER_LASER_JOG_STEPS: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.JogSteps',
    rpc_name='JogSteps',
    python_name='jog_steps',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='jog_steps',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherJogRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherJogRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('relative_steps', 'velocity_mode'),
    forwarded_field_specs=(
        ForwardedField(name='relative_steps', python_type='int', optional=False),
        ForwardedField(name='velocity_mode', python_type='str | None', optional=True),
    ),
)

_SACHER_LASER_RECALIBRATE_POSITION: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.RecalibratePosition',
    rpc_name='RecalibratePosition',
    python_name='recalibrate_position',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='recalibrate_position',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRecalibrateRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherRecalibrateRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('measured_wavelength_nm',),
    forwarded_field_specs=(
        ForwardedField(name='measured_wavelength_nm', python_type='float', optional=False),
    ),
)

_SACHER_LASER_RESET_POSITION: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.ResetPosition',
    rpc_name='ResetPosition',
    python_name='reset_position',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='reset_position',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SACHER_LASER_SET_ACCELERATION_NM_S2: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.SetAccelerationNmS2',
    rpc_name='SetAccelerationNmS2',
    python_name='set_acceleration_nm_s2',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_acceleration_nm_s2',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherAccelerationRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherAccelerationRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('acceleration_nm_s2',),
    forwarded_field_specs=(
        ForwardedField(name='acceleration_nm_s2', python_type='float', optional=False),
    ),
)

_SACHER_LASER_SET_DECELERATION_NM_S2: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.SetDecelerationNmS2',
    rpc_name='SetDecelerationNmS2',
    python_name='set_deceleration_nm_s2',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_deceleration_nm_s2',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherDecelerationRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherDecelerationRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('deceleration_nm_s2',),
    forwarded_field_specs=(
        ForwardedField(name='deceleration_nm_s2', python_type='float', optional=False),
    ),
)

_SACHER_LASER_SET_START_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.SetStartWavelengthNm',
    rpc_name='SetStartWavelengthNm',
    python_name='set_start_wavelength_nm',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_start_wavelength_nm',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherWavelengthRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherWavelengthRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('wavelength_nm',),
    forwarded_field_specs=(
        ForwardedField(name='wavelength_nm', python_type='float', optional=False),
    ),
)

_SACHER_LASER_SET_TARGET_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.SetTargetWavelengthNm',
    rpc_name='SetTargetWavelengthNm',
    python_name='set_target_wavelength_nm',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_target_wavelength_nm',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherWavelengthRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherWavelengthRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('wavelength_nm',),
    forwarded_field_specs=(
        ForwardedField(name='wavelength_nm', python_type='float', optional=False),
    ),
)

_SACHER_LASER_SET_VELOCITY_NM_S: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.SetVelocityNmS',
    rpc_name='SetVelocityNmS',
    python_name='set_velocity_nm_s',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_velocity_nm_s',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherVelocityRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherVelocityRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('velocity_nm_s',),
    forwarded_field_specs=(
        ForwardedField(name='velocity_nm_s', python_type='float', optional=False),
    ),
)

_SACHER_LASER_START_SCAN: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.StartScan',
    rpc_name='StartScan',
    python_name='start_scan',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='start_scan',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherScanRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherScanRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('start_wavelength_nm', 'target_wavelength_nm'),
    forwarded_field_specs=(
        ForwardedField(name='start_wavelength_nm', python_type='float', optional=False),
        ForwardedField(name='target_wavelength_nm', python_type='float', optional=False),
    ),
)

_SACHER_LASER_STOP: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SacherLaserService.Stop',
    rpc_name='Stop',
    python_name='stop',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='stop',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SacherRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SacherRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

SACHER_LASER_CONTRACT: Final = DeviceContract(
    device_type='sacher_laser',
    service_full_name='labnet.devices.v1.SacherLaserService',
    service_name='SacherLaserService',
    stub_type=_sacher_laser_pb2_grpc.SacherLaserServiceStub,
    servicer_type=_sacher_laser_pb2_grpc.SacherLaserServiceServicer,
    add_servicer_to_server=_sacher_laser_pb2_grpc.add_SacherLaserServiceServicer_to_server,
    methods=(_SACHER_LASER_GET_ACCELERATION_NM_S2, _SACHER_LASER_GET_DECELERATION_NM_S2, _SACHER_LASER_GET_DEVICE_ERROR_CODES, _SACHER_LASER_GET_LIVE_WAVELENGTH_NM, _SACHER_LASER_GET_POSITION_OFFSET_STEPS, _SACHER_LASER_GET_POSITION_STEPS, _SACHER_LASER_GET_START_WAVELENGTH_NM, _SACHER_LASER_GET_STATE, _SACHER_LASER_GET_STORED_POSITION_STEPS, _SACHER_LASER_GET_STORED_WAVELENGTH_NM, _SACHER_LASER_GET_TARGET_WAVELENGTH_NM, _SACHER_LASER_GET_VELOCITY_NM_S, _SACHER_LASER_GO_TO_WAVELENGTH_NM, _SACHER_LASER_JOG_STEPS, _SACHER_LASER_RECALIBRATE_POSITION, _SACHER_LASER_RESET_POSITION, _SACHER_LASER_SET_ACCELERATION_NM_S2, _SACHER_LASER_SET_DECELERATION_NM_S2, _SACHER_LASER_SET_START_WAVELENGTH_NM, _SACHER_LASER_SET_TARGET_WAVELENGTH_NM, _SACHER_LASER_SET_VELOCITY_NM_S, _SACHER_LASER_START_SCAN, _SACHER_LASER_STOP),
    physical_methods=('get_acceleration_nm_s2', 'get_deceleration_nm_s2', 'get_device_error_codes', 'get_live_wavelength_nm', 'get_position_offset_steps', 'get_position_steps', 'get_start_wavelength_nm', 'get_state', 'get_stored_position_steps', 'get_stored_wavelength_nm', 'get_target_wavelength_nm', 'get_velocity_nm_s', 'go_to_wavelength_nm', 'jog_steps', 'recalibrate_position', 'reset_position', 'set_acceleration_nm_s2', 'set_deceleration_nm_s2', 'set_start_wavelength_nm', 'set_target_wavelength_nm', 'set_velocity_nm_s', 'start_scan', 'stop'),
    physical_protocol=SacherLaserPhysicalProtocol,
)


@runtime_checkable
class SimulatedWavemeterPhysicalProtocol(Protocol):
    def read_wavelength(self, channel: str | None = None) -> Any: ...
    def set_exposure(self, value: float, channel: str | None = None) -> Any: ...


_SIMULATED_WAVEMETER_READ_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedWavemeterService.ReadWavelength',
    rpc_name='ReadWavelength',
    python_name='read_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.ReadWavelengthRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.ReadWavelengthResponse'),
    request_full_name='labnet.devices.v1.ReadWavelengthRequest',
    response_full_name='labnet.devices.v1.ReadWavelengthResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_SIMULATED_WAVEMETER_SET_EXPOSURE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedWavemeterService.SetExposure',
    rpc_name='SetExposure',
    python_name='set_exposure',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_exposure',
    default_unit='ms',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SetExposureRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SetExposureResponse'),
    request_full_name='labnet.devices.v1.SetExposureRequest',
    response_full_name='labnet.devices.v1.SetExposureResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value', 'channel'),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_SIMULATED_WAVEMETER_STREAM_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedWavemeterService.StreamWavelength',
    rpc_name='StreamWavelength',
    python_name='stream_wavelength',
    pattern='RPC_PATTERN_POLLED_SCALAR_STREAM',
    pattern_value=6,
    physical_method='read_wavelength',
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

SIMULATED_WAVEMETER_CONTRACT: Final = DeviceContract(
    device_type='simulated_wavemeter',
    service_full_name='labnet.devices.v1.SimulatedWavemeterService',
    service_name='SimulatedWavemeterService',
    stub_type=_simulated_wavemeter_pb2_grpc.SimulatedWavemeterServiceStub,
    servicer_type=_simulated_wavemeter_pb2_grpc.SimulatedWavemeterServiceServicer,
    add_servicer_to_server=_simulated_wavemeter_pb2_grpc.add_SimulatedWavemeterServiceServicer_to_server,
    methods=(_SIMULATED_WAVEMETER_READ_WAVELENGTH, _SIMULATED_WAVEMETER_SET_EXPOSURE, _SIMULATED_WAVEMETER_STREAM_WAVELENGTH),
    physical_methods=('read_wavelength', 'set_exposure'),
    physical_protocol=SimulatedWavemeterPhysicalProtocol,
)


DEVICE_CONTRACTS: Final[tuple[DeviceContract, ...]] = (DUMMY_DEVICE_CONTRACT, SACHER_LASER_CONTRACT, SIMULATED_WAVEMETER_CONTRACT)
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


def validate_implementation(
    implementation: object | type[Any],
    contract: DeviceContract,
) -> None:
    """Fail actionably if physical.py does not implement a generated contract."""

    errors: list[str] = []
    specs_by_method: dict[str, list[RpcSpec]] = {}
    for method in contract.methods:
        specs_by_method.setdefault(method.physical_method, []).append(method)
    for method_name, specs in sorted(specs_by_method.items()):
        method = getattr(implementation, method_name, None)
        if not callable(method):
            errors.append(f"missing callable method {method_name!r}")
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            continue
        parameters = signature.parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        forwarded_occurrences: dict[str, list[ForwardedField]] = {}
        for spec in specs:
            for field in spec.forwarded_field_specs:
                forwarded_occurrences.setdefault(field.name, []).append(field)
        forwarded_specs: dict[str, ForwardedField] = {}
        for field_name, occurrences in forwarded_occurrences.items():
            sample = occurrences[0]
            optional = len(occurrences) != len(specs) or any(
                occurrence.optional for occurrence in occurrences
            )
            forwarded_specs[field_name] = ForwardedField(
                name=field_name,
                python_type=sample.python_type.replace(" | None", ""),
                optional=optional,
            )
        forwarded = set(forwarded_specs)
        for field in sorted(forwarded):
            parameter = parameters.get(field)
            if parameter is None and not accepts_kwargs:
                errors.append(
                    f"method {method_name!r} is missing protobuf argument {field!r}"
                )
            elif parameter is not None and parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                errors.append(
                    f"method {method_name!r} argument {field!r} cannot be positional-only"
                )
            elif (
                parameter is not None
                and forwarded_specs[field].optional
                and parameter.default is inspect.Parameter.empty
                and not accepts_kwargs
            ):
                errors.append(
                    f"method {method_name!r} argument {field!r} must "
                    "have a Python default because it is optional or is "
                    "not supplied by every mapped RPC"
                )
        for parameter in parameters.values():
            if parameter.name in {"self", "cls"} or parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            if (
                parameter.default is inspect.Parameter.empty
                and parameter.name not in forwarded
            ):
                errors.append(
                    f"method {method_name!r} has unmapped required argument "
                    f"{parameter.name!r}"
                )
    if errors:
        target_name = getattr(implementation, "__name__", type(implementation).__name__)
        details = "\n  - ".join(errors)
        raise PhysicalContractError(
            f"{target_name} does not satisfy {contract.device_type!r} physical contract:"
            f"\n  - {details}"
        )
