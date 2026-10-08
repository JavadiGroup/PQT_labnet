"""Generated LabNet device RPC contracts. Do not edit by hand."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Final, Mapping, Protocol, Sequence, runtime_checkable

from google.protobuf import message as _message
from google.protobuf import symbol_database as _symbol_database
from . import common_pb2 as _common_pb2
from . import dummy_aom_pb2 as _dummy_aom_pb2
from . import dummy_awg_pb2 as _dummy_awg_pb2
from . import dummy_device_pb2 as _dummy_device_pb2
from . import dummy_eom_pb2 as _dummy_eom_pb2
from . import dummy_laser_pb2 as _dummy_laser_pb2
from . import simulated_tunable_laser_pb2 as _simulated_tunable_laser_pb2
from . import simulated_wavemeter_pb2 as _simulated_wavemeter_pb2
from . import transfer_pb2 as _transfer_pb2
from . import dummy_aom_pb2_grpc as _dummy_aom_pb2_grpc
from . import dummy_awg_pb2_grpc as _dummy_awg_pb2_grpc
from . import dummy_device_pb2_grpc as _dummy_device_pb2_grpc
from . import dummy_eom_pb2_grpc as _dummy_eom_pb2_grpc
from . import dummy_laser_pb2_grpc as _dummy_laser_pb2_grpc
from . import simulated_tunable_laser_pb2_grpc as _simulated_tunable_laser_pb2_grpc
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
class DummyAOMPhysicalProtocol(Protocol):
    def read_deflection_angle(self) -> Any: ...
    def read_diffraction_efficiency(self, channel: str | None = None) -> Any: ...
    def read_frequency(self) -> Any: ...
    def read_rf_power(self) -> Any: ...
    def set_frequency(self, value: float) -> Any: ...
    def set_rf_enabled(self, enabled: bool) -> Any: ...
    def set_rf_power(self, value: float) -> Any: ...


_DUMMY_AOM_READ_DEFLECTION_ANGLE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAOMService.ReadDeflectionAngle',
    rpc_name='ReadDeflectionAngle',
    python_name='read_deflection_angle',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_deflection_angle',
    default_unit='mrad',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAomRequest',
    response_full_name='labnet.devices.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_AOM_READ_DIFFRACTION_EFFICIENCY: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAOMService.ReadDiffractionEfficiency',
    rpc_name='ReadDiffractionEfficiency',
    python_name='read_diffraction_efficiency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_diffraction_efficiency',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAomRequest',
    response_full_name='labnet.devices.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_AOM_READ_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAOMService.ReadFrequency',
    rpc_name='ReadFrequency',
    python_name='read_frequency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_frequency',
    default_unit='MHz',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAomRequest',
    response_full_name='labnet.devices.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_AOM_READ_RF_POWER: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAOMService.ReadRfPower',
    rpc_name='ReadRfPower',
    python_name='read_rf_power',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_rf_power',
    default_unit='W',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAomRequest',
    response_full_name='labnet.devices.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_AOM_SET_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAOMService.SetFrequency',
    rpc_name='SetFrequency',
    python_name='set_frequency',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_frequency',
    default_unit='MHz',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAomWriteRequest',
    response_full_name='labnet.devices.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_AOM_SET_RF_ENABLED: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAOMService.SetRfEnabled',
    rpc_name='SetRfEnabled',
    python_name='set_rf_enabled',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_rf_enabled',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomRfEnabledRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.DummyAomRfEnabledRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('enabled',),
    forwarded_field_specs=(
        ForwardedField(name='enabled', python_type='bool', optional=False),
    ),
)

_DUMMY_AOM_SET_RF_POWER: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAOMService.SetRfPower',
    rpc_name='SetRfPower',
    python_name='set_rf_power',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_rf_power',
    default_unit='W',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAomWriteRequest',
    response_full_name='labnet.devices.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_AOM_STREAM_DIFFRACTION_EFFICIENCY: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAOMService.StreamDiffractionEfficiency',
    rpc_name='StreamDiffractionEfficiency',
    python_name='stream_diffraction_efficiency',
    pattern='RPC_PATTERN_POLLED_SCALAR_STREAM',
    pattern_value=6,
    physical_method='read_diffraction_efficiency',
    default_unit='1',
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

DUMMY_AOM_CONTRACT: Final = DeviceContract(
    device_type='dummy_aom',
    service_full_name='labnet.devices.v1.DummyAOMService',
    service_name='DummyAOMService',
    stub_type=_dummy_aom_pb2_grpc.DummyAOMServiceStub,
    servicer_type=_dummy_aom_pb2_grpc.DummyAOMServiceServicer,
    add_servicer_to_server=_dummy_aom_pb2_grpc.add_DummyAOMServiceServicer_to_server,
    methods=(_DUMMY_AOM_READ_DEFLECTION_ANGLE, _DUMMY_AOM_READ_DIFFRACTION_EFFICIENCY, _DUMMY_AOM_READ_FREQUENCY, _DUMMY_AOM_READ_RF_POWER, _DUMMY_AOM_SET_FREQUENCY, _DUMMY_AOM_SET_RF_ENABLED, _DUMMY_AOM_SET_RF_POWER, _DUMMY_AOM_STREAM_DIFFRACTION_EFFICIENCY),
    physical_methods=('read_deflection_angle', 'read_diffraction_efficiency', 'read_frequency', 'read_rf_power', 'set_frequency', 'set_rf_enabled', 'set_rf_power'),
    physical_protocol=DummyAOMPhysicalProtocol,
)


@runtime_checkable
class DummyAWGPhysicalProtocol(Protocol):
    def read_amplitude(self, channel: str | None = None) -> Any: ...
    def read_frequency(self, channel: str | None = None) -> Any: ...
    def read_offset(self, channel: str | None = None) -> Any: ...
    def read_output_rms(self, channel: str | None = None) -> Any: ...
    def read_output_voltage(self, channel: str | None = None) -> Any: ...
    def set_amplitude(self, value: float, channel: str | None = None) -> Any: ...
    def set_frequency(self, value: float, channel: str | None = None) -> Any: ...
    def set_offset(self, value: float, channel: str | None = None) -> Any: ...
    def set_output_enabled(self, enabled: bool, channel: str | None = None) -> Any: ...
    def set_waveform(self, waveform: str, channel: str | None = None) -> Any: ...


_DUMMY_AWG_READ_AMPLITUDE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.ReadAmplitude',
    rpc_name='ReadAmplitude',
    python_name='read_amplitude',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_amplitude',
    default_unit='Vpp',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAwgChannelRequest',
    response_full_name='labnet.devices.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_READ_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.ReadFrequency',
    rpc_name='ReadFrequency',
    python_name='read_frequency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_frequency',
    default_unit='Hz',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAwgChannelRequest',
    response_full_name='labnet.devices.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_READ_OFFSET: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.ReadOffset',
    rpc_name='ReadOffset',
    python_name='read_offset',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_offset',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAwgChannelRequest',
    response_full_name='labnet.devices.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_READ_OUTPUT_RMS: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.ReadOutputRms',
    rpc_name='ReadOutputRms',
    python_name='read_output_rms',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_output_rms',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAwgChannelRequest',
    response_full_name='labnet.devices.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_READ_OUTPUT_VOLTAGE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.ReadOutputVoltage',
    rpc_name='ReadOutputVoltage',
    python_name='read_output_voltage',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_output_voltage',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAwgChannelRequest',
    response_full_name='labnet.devices.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_SET_AMPLITUDE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.SetAmplitude',
    rpc_name='SetAmplitude',
    python_name='set_amplitude',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_amplitude',
    default_unit='Vpp',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAwgWriteRequest',
    response_full_name='labnet.devices.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value', 'channel'),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_SET_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.SetFrequency',
    rpc_name='SetFrequency',
    python_name='set_frequency',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_frequency',
    default_unit='Hz',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAwgWriteRequest',
    response_full_name='labnet.devices.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value', 'channel'),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_SET_OFFSET: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.SetOffset',
    rpc_name='SetOffset',
    python_name='set_offset',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_offset',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyAwgWriteRequest',
    response_full_name='labnet.devices.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value', 'channel'),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_SET_OUTPUT_ENABLED: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.SetOutputEnabled',
    rpc_name='SetOutputEnabled',
    python_name='set_output_enabled',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_output_enabled',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgOutputRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.DummyAwgOutputRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('enabled', 'channel'),
    forwarded_field_specs=(
        ForwardedField(name='enabled', python_type='bool', optional=False),
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_SET_WAVEFORM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.SetWaveform',
    rpc_name='SetWaveform',
    python_name='set_waveform',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_waveform',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyAwgWaveformRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.DummyAwgWaveformRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('waveform', 'channel'),
    forwarded_field_specs=(
        ForwardedField(name='waveform', python_type='str', optional=False),
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_STREAM_OUTPUT_VOLTAGE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyAWGService.StreamOutputVoltage',
    rpc_name='StreamOutputVoltage',
    python_name='stream_output_voltage',
    pattern='RPC_PATTERN_POLLED_SCALAR_STREAM',
    pattern_value=6,
    physical_method='read_output_voltage',
    default_unit='V',
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

DUMMY_AWG_CONTRACT: Final = DeviceContract(
    device_type='dummy_awg',
    service_full_name='labnet.devices.v1.DummyAWGService',
    service_name='DummyAWGService',
    stub_type=_dummy_awg_pb2_grpc.DummyAWGServiceStub,
    servicer_type=_dummy_awg_pb2_grpc.DummyAWGServiceServicer,
    add_servicer_to_server=_dummy_awg_pb2_grpc.add_DummyAWGServiceServicer_to_server,
    methods=(_DUMMY_AWG_READ_AMPLITUDE, _DUMMY_AWG_READ_FREQUENCY, _DUMMY_AWG_READ_OFFSET, _DUMMY_AWG_READ_OUTPUT_RMS, _DUMMY_AWG_READ_OUTPUT_VOLTAGE, _DUMMY_AWG_SET_AMPLITUDE, _DUMMY_AWG_SET_FREQUENCY, _DUMMY_AWG_SET_OFFSET, _DUMMY_AWG_SET_OUTPUT_ENABLED, _DUMMY_AWG_SET_WAVEFORM, _DUMMY_AWG_STREAM_OUTPUT_VOLTAGE),
    physical_methods=('read_amplitude', 'read_frequency', 'read_offset', 'read_output_rms', 'read_output_voltage', 'set_amplitude', 'set_frequency', 'set_offset', 'set_output_enabled', 'set_waveform'),
    physical_protocol=DummyAWGPhysicalProtocol,
)


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
class DummyEOMPhysicalProtocol(Protocol):
    def read_carrier_fraction(self) -> Any: ...
    def read_modulation_depth(self) -> Any: ...
    def read_modulation_frequency(self) -> Any: ...
    def read_rf_amplitude(self) -> Any: ...
    def read_sideband_fraction(self) -> Any: ...
    def read_sideband_ratio(self, channel: str | None = None) -> Any: ...
    def set_modulation_frequency(self, value: float) -> Any: ...
    def set_rf_amplitude(self, value: float) -> Any: ...
    def set_rf_enabled(self, enabled: bool) -> Any: ...


_DUMMY_EOM_READ_CARRIER_FRACTION: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.ReadCarrierFraction',
    rpc_name='ReadCarrierFraction',
    python_name='read_carrier_fraction',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_carrier_fraction',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyEomRequest',
    response_full_name='labnet.devices.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_MODULATION_DEPTH: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.ReadModulationDepth',
    rpc_name='ReadModulationDepth',
    python_name='read_modulation_depth',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_modulation_depth',
    default_unit='rad',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyEomRequest',
    response_full_name='labnet.devices.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_MODULATION_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.ReadModulationFrequency',
    rpc_name='ReadModulationFrequency',
    python_name='read_modulation_frequency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_modulation_frequency',
    default_unit='MHz',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyEomRequest',
    response_full_name='labnet.devices.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_RF_AMPLITUDE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.ReadRfAmplitude',
    rpc_name='ReadRfAmplitude',
    python_name='read_rf_amplitude',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_rf_amplitude',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyEomRequest',
    response_full_name='labnet.devices.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_SIDEBAND_FRACTION: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.ReadSidebandFraction',
    rpc_name='ReadSidebandFraction',
    python_name='read_sideband_fraction',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_sideband_fraction',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyEomRequest',
    response_full_name='labnet.devices.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_SIDEBAND_RATIO: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.ReadSidebandRatio',
    rpc_name='ReadSidebandRatio',
    python_name='read_sideband_ratio',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_sideband_ratio',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyEomRequest',
    response_full_name='labnet.devices.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_SET_MODULATION_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.SetModulationFrequency',
    rpc_name='SetModulationFrequency',
    python_name='set_modulation_frequency',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_modulation_frequency',
    default_unit='MHz',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyEomWriteRequest',
    response_full_name='labnet.devices.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_EOM_SET_RF_AMPLITUDE: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.SetRfAmplitude',
    rpc_name='SetRfAmplitude',
    python_name='set_rf_amplitude',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_rf_amplitude',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyEomWriteRequest',
    response_full_name='labnet.devices.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_EOM_SET_RF_ENABLED: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.SetRfEnabled',
    rpc_name='SetRfEnabled',
    python_name='set_rf_enabled',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_rf_enabled',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyEomRfEnabledRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.DummyEomRfEnabledRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('enabled',),
    forwarded_field_specs=(
        ForwardedField(name='enabled', python_type='bool', optional=False),
    ),
)

_DUMMY_EOM_STREAM_SIDEBAND_RATIO: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyEOMService.StreamSidebandRatio',
    rpc_name='StreamSidebandRatio',
    python_name='stream_sideband_ratio',
    pattern='RPC_PATTERN_POLLED_SCALAR_STREAM',
    pattern_value=6,
    physical_method='read_sideband_ratio',
    default_unit='1',
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

DUMMY_EOM_CONTRACT: Final = DeviceContract(
    device_type='dummy_eom',
    service_full_name='labnet.devices.v1.DummyEOMService',
    service_name='DummyEOMService',
    stub_type=_dummy_eom_pb2_grpc.DummyEOMServiceStub,
    servicer_type=_dummy_eom_pb2_grpc.DummyEOMServiceServicer,
    add_servicer_to_server=_dummy_eom_pb2_grpc.add_DummyEOMServiceServicer_to_server,
    methods=(_DUMMY_EOM_READ_CARRIER_FRACTION, _DUMMY_EOM_READ_MODULATION_DEPTH, _DUMMY_EOM_READ_MODULATION_FREQUENCY, _DUMMY_EOM_READ_RF_AMPLITUDE, _DUMMY_EOM_READ_SIDEBAND_FRACTION, _DUMMY_EOM_READ_SIDEBAND_RATIO, _DUMMY_EOM_SET_MODULATION_FREQUENCY, _DUMMY_EOM_SET_RF_AMPLITUDE, _DUMMY_EOM_SET_RF_ENABLED, _DUMMY_EOM_STREAM_SIDEBAND_RATIO),
    physical_methods=('read_carrier_fraction', 'read_modulation_depth', 'read_modulation_frequency', 'read_rf_amplitude', 'read_sideband_fraction', 'read_sideband_ratio', 'set_modulation_frequency', 'set_rf_amplitude', 'set_rf_enabled'),
    physical_protocol=DummyEOMPhysicalProtocol,
)


@runtime_checkable
class DummyLaserPhysicalProtocol(Protocol):
    def read_current(self) -> Any: ...
    def read_power(self, channel: str | None = None) -> Any: ...
    def read_wavelength(self) -> Any: ...
    def set_current(self, value: float) -> Any: ...
    def set_emission_enabled(self, enabled: bool) -> Any: ...
    def set_wavelength(self, value: float) -> Any: ...


_DUMMY_LASER_READ_CURRENT: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyLaserService.ReadCurrent',
    rpc_name='ReadCurrent',
    python_name='read_current',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_current',
    default_unit='mA',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyLaserRequest',
    response_full_name='labnet.devices.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_LASER_READ_POWER: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyLaserService.ReadPower',
    rpc_name='ReadPower',
    python_name='read_power',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_power',
    default_unit='mW',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyLaserRequest',
    response_full_name='labnet.devices.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_LASER_READ_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyLaserService.ReadWavelength',
    rpc_name='ReadWavelength',
    python_name='read_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyLaserRequest',
    response_full_name='labnet.devices.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_LASER_SET_CURRENT: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyLaserService.SetCurrent',
    rpc_name='SetCurrent',
    python_name='set_current',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_current',
    default_unit='mA',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyLaserWriteRequest',
    response_full_name='labnet.devices.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_LASER_SET_EMISSION_ENABLED: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyLaserService.SetEmissionEnabled',
    rpc_name='SetEmissionEnabled',
    python_name='set_emission_enabled',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_emission_enabled',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserEmissionRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.DummyLaserEmissionRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('enabled',),
    forwarded_field_specs=(
        ForwardedField(name='enabled', python_type='bool', optional=False),
    ),
)

_DUMMY_LASER_SET_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyLaserService.SetWavelength',
    rpc_name='SetWavelength',
    python_name='set_wavelength',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet.devices.v1.DummyLaserWriteRequest',
    response_full_name='labnet.devices.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_LASER_STREAM_POWER: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.DummyLaserService.StreamPower',
    rpc_name='StreamPower',
    python_name='stream_power',
    pattern='RPC_PATTERN_POLLED_SCALAR_STREAM',
    pattern_value=6,
    physical_method='read_power',
    default_unit='mW',
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

DUMMY_LASER_CONTRACT: Final = DeviceContract(
    device_type='dummy_laser',
    service_full_name='labnet.devices.v1.DummyLaserService',
    service_name='DummyLaserService',
    stub_type=_dummy_laser_pb2_grpc.DummyLaserServiceStub,
    servicer_type=_dummy_laser_pb2_grpc.DummyLaserServiceServicer,
    add_servicer_to_server=_dummy_laser_pb2_grpc.add_DummyLaserServiceServicer_to_server,
    methods=(_DUMMY_LASER_READ_CURRENT, _DUMMY_LASER_READ_POWER, _DUMMY_LASER_READ_WAVELENGTH, _DUMMY_LASER_SET_CURRENT, _DUMMY_LASER_SET_EMISSION_ENABLED, _DUMMY_LASER_SET_WAVELENGTH, _DUMMY_LASER_STREAM_POWER),
    physical_methods=('read_current', 'read_power', 'read_wavelength', 'set_current', 'set_emission_enabled', 'set_wavelength'),
    physical_protocol=DummyLaserPhysicalProtocol,
)


@runtime_checkable
class SimulatedTunableLaserPhysicalProtocol(Protocol):
    def go_to_wavelength_nm(self, wavelength_nm: float) -> Any: ...
    def move_steps(self, relative_steps: int, velocity_mode: str | None = None) -> Any: ...
    def read_position_steps(self) -> Any: ...
    def read_wavelength(self) -> Any: ...
    def set_velocity_nm_s(self, velocity_nm_s: float) -> Any: ...
    def stop(self) -> Any: ...


_SIMULATED_TUNABLE_LASER_GO_TO_WAVELENGTH_NM: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedTunableLaserService.GoToWavelengthNm',
    rpc_name='GoToWavelengthNm',
    python_name='go_to_wavelength_nm',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='go_to_wavelength_nm',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SimulatedTunableLaserGoToWavelengthRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SimulatedTunableLaserGoToWavelengthRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('wavelength_nm',),
    forwarded_field_specs=(
        ForwardedField(name='wavelength_nm', python_type='float', optional=False),
    ),
)

_SIMULATED_TUNABLE_LASER_MOVE_STEPS: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedTunableLaserService.MoveSteps',
    rpc_name='MoveSteps',
    python_name='move_steps',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='move_steps',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SimulatedTunableLaserMoveStepsRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SimulatedTunableLaserMoveStepsRequest',
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

_SIMULATED_TUNABLE_LASER_READ_POSITION_STEPS: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedTunableLaserService.ReadPositionSteps',
    rpc_name='ReadPositionSteps',
    python_name='read_position_steps',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_position_steps',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SimulatedTunableLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SimulatedTunableLaserMeasurementResponse'),
    request_full_name='labnet.devices.v1.SimulatedTunableLaserRequest',
    response_full_name='labnet.devices.v1.SimulatedTunableLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SIMULATED_TUNABLE_LASER_READ_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedTunableLaserService.ReadWavelength',
    rpc_name='ReadWavelength',
    python_name='read_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SimulatedTunableLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SimulatedTunableLaserMeasurementResponse'),
    request_full_name='labnet.devices.v1.SimulatedTunableLaserRequest',
    response_full_name='labnet.devices.v1.SimulatedTunableLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SIMULATED_TUNABLE_LASER_SET_VELOCITY_NM_S: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedTunableLaserService.SetVelocityNmS',
    rpc_name='SetVelocityNmS',
    python_name='set_velocity_nm_s',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_velocity_nm_s',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SimulatedTunableLaserVelocityRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SimulatedTunableLaserVelocityRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('velocity_nm_s',),
    forwarded_field_specs=(
        ForwardedField(name='velocity_nm_s', python_type='float', optional=False),
    ),
)

_SIMULATED_TUNABLE_LASER_STOP: Final = RpcSpec(
    rpc_full_name='labnet.devices.v1.SimulatedTunableLaserService.Stop',
    rpc_name='Stop',
    python_name='stop',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='stop',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet.devices.v1.SimulatedTunableLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet.devices.v1.SimulatedTunableLaserRequest',
    response_full_name='labnet.devices.v1.CommandResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

SIMULATED_TUNABLE_LASER_CONTRACT: Final = DeviceContract(
    device_type='simulated_tunable_laser',
    service_full_name='labnet.devices.v1.SimulatedTunableLaserService',
    service_name='SimulatedTunableLaserService',
    stub_type=_simulated_tunable_laser_pb2_grpc.SimulatedTunableLaserServiceStub,
    servicer_type=_simulated_tunable_laser_pb2_grpc.SimulatedTunableLaserServiceServicer,
    add_servicer_to_server=_simulated_tunable_laser_pb2_grpc.add_SimulatedTunableLaserServiceServicer_to_server,
    methods=(_SIMULATED_TUNABLE_LASER_GO_TO_WAVELENGTH_NM, _SIMULATED_TUNABLE_LASER_MOVE_STEPS, _SIMULATED_TUNABLE_LASER_READ_POSITION_STEPS, _SIMULATED_TUNABLE_LASER_READ_WAVELENGTH, _SIMULATED_TUNABLE_LASER_SET_VELOCITY_NM_S, _SIMULATED_TUNABLE_LASER_STOP),
    physical_methods=('go_to_wavelength_nm', 'move_steps', 'read_position_steps', 'read_wavelength', 'set_velocity_nm_s', 'stop'),
    physical_protocol=SimulatedTunableLaserPhysicalProtocol,
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


DEVICE_CONTRACTS: Final[tuple[DeviceContract, ...]] = (DUMMY_AOM_CONTRACT, DUMMY_AWG_CONTRACT, DUMMY_DEVICE_CONTRACT, DUMMY_EOM_CONTRACT, DUMMY_LASER_CONTRACT, SIMULATED_TUNABLE_LASER_CONTRACT, SIMULATED_WAVEMETER_CONTRACT)
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
