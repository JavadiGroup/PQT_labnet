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

from . import dummy_aom_pb2 as _dummy_aom_pb2
from . import dummy_awg_pb2 as _dummy_awg_pb2
from . import dummy_device_pb2 as _dummy_device_pb2
from . import dummy_eom_pb2 as _dummy_eom_pb2
from . import dummy_laser_pb2 as _dummy_laser_pb2
from . import simulated_tunable_laser_pb2 as _simulated_tunable_laser_pb2
from . import simulated_wavemeter_pb2 as _simulated_wavemeter_pb2
from . import dummy_aom_pb2_grpc as _dummy_aom_pb2_grpc
from . import dummy_awg_pb2_grpc as _dummy_awg_pb2_grpc
from . import dummy_device_pb2_grpc as _dummy_device_pb2_grpc
from . import dummy_eom_pb2_grpc as _dummy_eom_pb2_grpc
from . import dummy_laser_pb2_grpc as _dummy_laser_pb2_grpc
from . import simulated_tunable_laser_pb2_grpc as _simulated_tunable_laser_pb2_grpc
from . import simulated_wavemeter_pb2_grpc as _simulated_wavemeter_pb2_grpc

_SYMBOLS = _symbol_database.Default()


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
    rpc_full_name='labnet_sim.v1.DummyAOMService.ReadDeflectionAngle',
    rpc_name='ReadDeflectionAngle',
    python_name='read_deflection_angle',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_deflection_angle',
    default_unit='mrad',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAomRequest',
    response_full_name='labnet_sim.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_AOM_READ_DIFFRACTION_EFFICIENCY: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAOMService.ReadDiffractionEfficiency',
    rpc_name='ReadDiffractionEfficiency',
    python_name='read_diffraction_efficiency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_diffraction_efficiency',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAomRequest',
    response_full_name='labnet_sim.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_AOM_READ_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAOMService.ReadFrequency',
    rpc_name='ReadFrequency',
    python_name='read_frequency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_frequency',
    default_unit='MHz',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAomRequest',
    response_full_name='labnet_sim.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_AOM_READ_RF_POWER: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAOMService.ReadRfPower',
    rpc_name='ReadRfPower',
    python_name='read_rf_power',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_rf_power',
    default_unit='W',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAomRequest',
    response_full_name='labnet_sim.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_AOM_SET_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAOMService.SetFrequency',
    rpc_name='SetFrequency',
    python_name='set_frequency',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_frequency',
    default_unit='MHz',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAomWriteRequest',
    response_full_name='labnet_sim.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_AOM_SET_RF_ENABLED: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAOMService.SetRfEnabled',
    rpc_name='SetRfEnabled',
    python_name='set_rf_enabled',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_rf_enabled',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomRfEnabledRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.DummyAomRfEnabledRequest',
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
    rpc_full_name='labnet_sim.v1.DummyAOMService.SetRfPower',
    rpc_name='SetRfPower',
    python_name='set_rf_power',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_rf_power',
    default_unit='W',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAomWriteRequest',
    response_full_name='labnet_sim.v1.DummyAomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_AOM_STREAM_DIFFRACTION_EFFICIENCY: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAOMService.StreamDiffractionEfficiency',
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
    service_full_name='labnet_sim.v1.DummyAOMService',
    service_name='DummyAOMService',
    stub_type=_dummy_aom_pb2_grpc.DummyAOMServiceStub,
    servicer_type=_dummy_aom_pb2_grpc.DummyAOMServiceServicer,
    add_servicer_to_server=_dummy_aom_pb2_grpc.add_DummyAOMServiceServicer_to_server,
    methods=(_DUMMY_AOM_READ_DEFLECTION_ANGLE, _DUMMY_AOM_READ_DIFFRACTION_EFFICIENCY, _DUMMY_AOM_READ_FREQUENCY, _DUMMY_AOM_READ_RF_POWER, _DUMMY_AOM_SET_FREQUENCY, _DUMMY_AOM_SET_RF_ENABLED, _DUMMY_AOM_SET_RF_POWER, _DUMMY_AOM_STREAM_DIFFRACTION_EFFICIENCY),
    physical_methods=('read_deflection_angle', 'read_diffraction_efficiency', 'read_frequency', 'read_rf_power', 'set_frequency', 'set_rf_enabled', 'set_rf_power'),
    physical_protocol=DummyAOMPhysicalProtocol,
    distribution='labnet-sim',
    fingerprint='8c8120dab269ed5de8eff4bf72208f4b295fab042e2cc44a8e53b0f94e65b05b',
    facade='labnet_sim:DummyAOM',
    physical='labnet_sim.dummy_aom.physical:PhysicalDummyAOM',
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
    rpc_full_name='labnet_sim.v1.DummyAWGService.ReadAmplitude',
    rpc_name='ReadAmplitude',
    python_name='read_amplitude',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_amplitude',
    default_unit='Vpp',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAwgChannelRequest',
    response_full_name='labnet_sim.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_READ_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAWGService.ReadFrequency',
    rpc_name='ReadFrequency',
    python_name='read_frequency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_frequency',
    default_unit='Hz',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAwgChannelRequest',
    response_full_name='labnet_sim.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_READ_OFFSET: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAWGService.ReadOffset',
    rpc_name='ReadOffset',
    python_name='read_offset',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_offset',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAwgChannelRequest',
    response_full_name='labnet_sim.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_READ_OUTPUT_RMS: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAWGService.ReadOutputRms',
    rpc_name='ReadOutputRms',
    python_name='read_output_rms',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_output_rms',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAwgChannelRequest',
    response_full_name='labnet_sim.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_READ_OUTPUT_VOLTAGE: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAWGService.ReadOutputVoltage',
    rpc_name='ReadOutputVoltage',
    python_name='read_output_voltage',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_output_voltage',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgChannelRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAwgChannelRequest',
    response_full_name='labnet_sim.v1.DummyAwgMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_AWG_SET_AMPLITUDE: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyAWGService.SetAmplitude',
    rpc_name='SetAmplitude',
    python_name='set_amplitude',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_amplitude',
    default_unit='Vpp',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAwgWriteRequest',
    response_full_name='labnet_sim.v1.DummyAwgMeasurementResponse',
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
    rpc_full_name='labnet_sim.v1.DummyAWGService.SetFrequency',
    rpc_name='SetFrequency',
    python_name='set_frequency',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_frequency',
    default_unit='Hz',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAwgWriteRequest',
    response_full_name='labnet_sim.v1.DummyAwgMeasurementResponse',
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
    rpc_full_name='labnet_sim.v1.DummyAWGService.SetOffset',
    rpc_name='SetOffset',
    python_name='set_offset',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_offset',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyAwgWriteRequest',
    response_full_name='labnet_sim.v1.DummyAwgMeasurementResponse',
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
    rpc_full_name='labnet_sim.v1.DummyAWGService.SetOutputEnabled',
    rpc_name='SetOutputEnabled',
    python_name='set_output_enabled',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_output_enabled',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgOutputRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.DummyAwgOutputRequest',
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
    rpc_full_name='labnet_sim.v1.DummyAWGService.SetWaveform',
    rpc_name='SetWaveform',
    python_name='set_waveform',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_waveform',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyAwgWaveformRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.DummyAwgWaveformRequest',
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
    rpc_full_name='labnet_sim.v1.DummyAWGService.StreamOutputVoltage',
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
    service_full_name='labnet_sim.v1.DummyAWGService',
    service_name='DummyAWGService',
    stub_type=_dummy_awg_pb2_grpc.DummyAWGServiceStub,
    servicer_type=_dummy_awg_pb2_grpc.DummyAWGServiceServicer,
    add_servicer_to_server=_dummy_awg_pb2_grpc.add_DummyAWGServiceServicer_to_server,
    methods=(_DUMMY_AWG_READ_AMPLITUDE, _DUMMY_AWG_READ_FREQUENCY, _DUMMY_AWG_READ_OFFSET, _DUMMY_AWG_READ_OUTPUT_RMS, _DUMMY_AWG_READ_OUTPUT_VOLTAGE, _DUMMY_AWG_SET_AMPLITUDE, _DUMMY_AWG_SET_FREQUENCY, _DUMMY_AWG_SET_OFFSET, _DUMMY_AWG_SET_OUTPUT_ENABLED, _DUMMY_AWG_SET_WAVEFORM, _DUMMY_AWG_STREAM_OUTPUT_VOLTAGE),
    physical_methods=('read_amplitude', 'read_frequency', 'read_offset', 'read_output_rms', 'read_output_voltage', 'set_amplitude', 'set_frequency', 'set_offset', 'set_output_enabled', 'set_waveform'),
    physical_protocol=DummyAWGPhysicalProtocol,
    distribution='labnet-sim',
    fingerprint='5c3dff2e2580d4fac97906d5f688b28a9ecf6c75f288a2be346bf0bd41596d50',
    facade='labnet_sim:DummyAWG',
    physical='labnet_sim.dummy_awg.physical:PhysicalDummyAWG',
)


@runtime_checkable
class DummyDevicePhysicalProtocol(Protocol):
    def read_value(self, channel: str | None = None) -> Any: ...
    def write_value(self, value: float, channel: str | None = None) -> Any: ...


_DUMMY_DEVICE_READ_VALUE: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyDeviceService.ReadValue',
    rpc_name='ReadValue',
    python_name='read_value',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_value',
    default_unit='Hz',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.ReadValueRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.ReadValueResponse'),
    request_full_name='labnet_sim.v1.ReadValueRequest',
    response_full_name='labnet_sim.v1.ReadValueResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_DUMMY_DEVICE_STREAM_DATA: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyDeviceService.StreamData',
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
    rpc_full_name='labnet_sim.v1.DummyDeviceService.WriteValue',
    rpc_name='WriteValue',
    python_name='write_value',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='write_value',
    default_unit='Hz',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.WriteValueRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.WriteValueResponse'),
    request_full_name='labnet_sim.v1.WriteValueRequest',
    response_full_name='labnet_sim.v1.WriteValueResponse',
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
    service_full_name='labnet_sim.v1.DummyDeviceService',
    service_name='DummyDeviceService',
    stub_type=_dummy_device_pb2_grpc.DummyDeviceServiceStub,
    servicer_type=_dummy_device_pb2_grpc.DummyDeviceServiceServicer,
    add_servicer_to_server=_dummy_device_pb2_grpc.add_DummyDeviceServiceServicer_to_server,
    methods=(_DUMMY_DEVICE_READ_VALUE, _DUMMY_DEVICE_STREAM_DATA, _DUMMY_DEVICE_WRITE_VALUE),
    physical_methods=('read_value', 'write_value'),
    physical_protocol=DummyDevicePhysicalProtocol,
    distribution='labnet-sim',
    fingerprint='bbfce64c239a600b4120f6643a2ce728eb6e8d6982dd523ca22275e6f5ab8a9d',
    facade='labnet_sim:DummyDevice',
    physical='labnet_sim.dummy_device.physical:PhysicalDummyDevice',
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
    rpc_full_name='labnet_sim.v1.DummyEOMService.ReadCarrierFraction',
    rpc_name='ReadCarrierFraction',
    python_name='read_carrier_fraction',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_carrier_fraction',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyEomRequest',
    response_full_name='labnet_sim.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_MODULATION_DEPTH: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyEOMService.ReadModulationDepth',
    rpc_name='ReadModulationDepth',
    python_name='read_modulation_depth',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_modulation_depth',
    default_unit='rad',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyEomRequest',
    response_full_name='labnet_sim.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_MODULATION_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyEOMService.ReadModulationFrequency',
    rpc_name='ReadModulationFrequency',
    python_name='read_modulation_frequency',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_modulation_frequency',
    default_unit='MHz',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyEomRequest',
    response_full_name='labnet_sim.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_RF_AMPLITUDE: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyEOMService.ReadRfAmplitude',
    rpc_name='ReadRfAmplitude',
    python_name='read_rf_amplitude',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_rf_amplitude',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyEomRequest',
    response_full_name='labnet_sim.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_SIDEBAND_FRACTION: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyEOMService.ReadSidebandFraction',
    rpc_name='ReadSidebandFraction',
    python_name='read_sideband_fraction',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_sideband_fraction',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyEomRequest',
    response_full_name='labnet_sim.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_READ_SIDEBAND_RATIO: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyEOMService.ReadSidebandRatio',
    rpc_name='ReadSidebandRatio',
    python_name='read_sideband_ratio',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_sideband_ratio',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyEomRequest',
    response_full_name='labnet_sim.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_EOM_SET_MODULATION_FREQUENCY: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyEOMService.SetModulationFrequency',
    rpc_name='SetModulationFrequency',
    python_name='set_modulation_frequency',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_modulation_frequency',
    default_unit='MHz',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyEomWriteRequest',
    response_full_name='labnet_sim.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_EOM_SET_RF_AMPLITUDE: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyEOMService.SetRfAmplitude',
    rpc_name='SetRfAmplitude',
    python_name='set_rf_amplitude',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_rf_amplitude',
    default_unit='V',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyEomWriteRequest',
    response_full_name='labnet_sim.v1.DummyEomMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_EOM_SET_RF_ENABLED: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyEOMService.SetRfEnabled',
    rpc_name='SetRfEnabled',
    python_name='set_rf_enabled',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_rf_enabled',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyEomRfEnabledRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.DummyEomRfEnabledRequest',
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
    rpc_full_name='labnet_sim.v1.DummyEOMService.StreamSidebandRatio',
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
    service_full_name='labnet_sim.v1.DummyEOMService',
    service_name='DummyEOMService',
    stub_type=_dummy_eom_pb2_grpc.DummyEOMServiceStub,
    servicer_type=_dummy_eom_pb2_grpc.DummyEOMServiceServicer,
    add_servicer_to_server=_dummy_eom_pb2_grpc.add_DummyEOMServiceServicer_to_server,
    methods=(_DUMMY_EOM_READ_CARRIER_FRACTION, _DUMMY_EOM_READ_MODULATION_DEPTH, _DUMMY_EOM_READ_MODULATION_FREQUENCY, _DUMMY_EOM_READ_RF_AMPLITUDE, _DUMMY_EOM_READ_SIDEBAND_FRACTION, _DUMMY_EOM_READ_SIDEBAND_RATIO, _DUMMY_EOM_SET_MODULATION_FREQUENCY, _DUMMY_EOM_SET_RF_AMPLITUDE, _DUMMY_EOM_SET_RF_ENABLED, _DUMMY_EOM_STREAM_SIDEBAND_RATIO),
    physical_methods=('read_carrier_fraction', 'read_modulation_depth', 'read_modulation_frequency', 'read_rf_amplitude', 'read_sideband_fraction', 'read_sideband_ratio', 'set_modulation_frequency', 'set_rf_amplitude', 'set_rf_enabled'),
    physical_protocol=DummyEOMPhysicalProtocol,
    distribution='labnet-sim',
    fingerprint='9258f685eea6d6596f2a25c2dfcedc57ba364378f6703734a744b2f241158069',
    facade='labnet_sim:DummyEOM',
    physical='labnet_sim.dummy_eom.physical:PhysicalDummyEOM',
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
    rpc_full_name='labnet_sim.v1.DummyLaserService.ReadCurrent',
    rpc_name='ReadCurrent',
    python_name='read_current',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_current',
    default_unit='mA',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyLaserRequest',
    response_full_name='labnet_sim.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_LASER_READ_POWER: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyLaserService.ReadPower',
    rpc_name='ReadPower',
    python_name='read_power',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_power',
    default_unit='mW',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyLaserRequest',
    response_full_name='labnet_sim.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_LASER_READ_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyLaserService.ReadWavelength',
    rpc_name='ReadWavelength',
    python_name='read_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyLaserRequest',
    response_full_name='labnet_sim.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_DUMMY_LASER_SET_CURRENT: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyLaserService.SetCurrent',
    rpc_name='SetCurrent',
    python_name='set_current',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_current',
    default_unit='mA',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyLaserWriteRequest',
    response_full_name='labnet_sim.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_LASER_SET_EMISSION_ENABLED: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyLaserService.SetEmissionEnabled',
    rpc_name='SetEmissionEnabled',
    python_name='set_emission_enabled',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_emission_enabled',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserEmissionRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.DummyLaserEmissionRequest',
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
    rpc_full_name='labnet_sim.v1.DummyLaserService.SetWavelength',
    rpc_name='SetWavelength',
    python_name='set_wavelength',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserWriteRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.DummyLaserMeasurementResponse'),
    request_full_name='labnet_sim.v1.DummyLaserWriteRequest',
    response_full_name='labnet_sim.v1.DummyLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('value',),
    forwarded_field_specs=(
        ForwardedField(name='value', python_type='float', optional=False),
    ),
)

_DUMMY_LASER_STREAM_POWER: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.DummyLaserService.StreamPower',
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
    service_full_name='labnet_sim.v1.DummyLaserService',
    service_name='DummyLaserService',
    stub_type=_dummy_laser_pb2_grpc.DummyLaserServiceStub,
    servicer_type=_dummy_laser_pb2_grpc.DummyLaserServiceServicer,
    add_servicer_to_server=_dummy_laser_pb2_grpc.add_DummyLaserServiceServicer_to_server,
    methods=(_DUMMY_LASER_READ_CURRENT, _DUMMY_LASER_READ_POWER, _DUMMY_LASER_READ_WAVELENGTH, _DUMMY_LASER_SET_CURRENT, _DUMMY_LASER_SET_EMISSION_ENABLED, _DUMMY_LASER_SET_WAVELENGTH, _DUMMY_LASER_STREAM_POWER),
    physical_methods=('read_current', 'read_power', 'read_wavelength', 'set_current', 'set_emission_enabled', 'set_wavelength'),
    physical_protocol=DummyLaserPhysicalProtocol,
    distribution='labnet-sim',
    fingerprint='846cc33e42902ed73f31a447f1a0dd20d5aec2d5b3c7c6dfb968f1e1849f8c55',
    facade='labnet_sim:DummyLaser',
    physical='labnet_sim.dummy_laser.physical:PhysicalDummyLaser',
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
    rpc_full_name='labnet_sim.v1.SimulatedTunableLaserService.GoToWavelengthNm',
    rpc_name='GoToWavelengthNm',
    python_name='go_to_wavelength_nm',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='go_to_wavelength_nm',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SimulatedTunableLaserGoToWavelengthRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.SimulatedTunableLaserGoToWavelengthRequest',
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
    rpc_full_name='labnet_sim.v1.SimulatedTunableLaserService.MoveSteps',
    rpc_name='MoveSteps',
    python_name='move_steps',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='move_steps',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SimulatedTunableLaserMoveStepsRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.SimulatedTunableLaserMoveStepsRequest',
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
    rpc_full_name='labnet_sim.v1.SimulatedTunableLaserService.ReadPositionSteps',
    rpc_name='ReadPositionSteps',
    python_name='read_position_steps',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_position_steps',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SimulatedTunableLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SimulatedTunableLaserMeasurementResponse'),
    request_full_name='labnet_sim.v1.SimulatedTunableLaserRequest',
    response_full_name='labnet_sim.v1.SimulatedTunableLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SIMULATED_TUNABLE_LASER_READ_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.SimulatedTunableLaserService.ReadWavelength',
    rpc_name='ReadWavelength',
    python_name='read_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SimulatedTunableLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SimulatedTunableLaserMeasurementResponse'),
    request_full_name='labnet_sim.v1.SimulatedTunableLaserRequest',
    response_full_name='labnet_sim.v1.SimulatedTunableLaserMeasurementResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=(),
    forwarded_field_specs=(
    ),
)

_SIMULATED_TUNABLE_LASER_SET_VELOCITY_NM_S: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.SimulatedTunableLaserService.SetVelocityNmS',
    rpc_name='SetVelocityNmS',
    python_name='set_velocity_nm_s',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='set_velocity_nm_s',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SimulatedTunableLaserVelocityRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.SimulatedTunableLaserVelocityRequest',
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
    rpc_full_name='labnet_sim.v1.SimulatedTunableLaserService.Stop',
    rpc_name='Stop',
    python_name='stop',
    pattern='RPC_PATTERN_UNARY_COMMAND',
    pattern_value=3,
    physical_method='stop',
    default_unit='1',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SimulatedTunableLaserRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet.devices.v1.CommandResponse'),
    request_full_name='labnet_sim.v1.SimulatedTunableLaserRequest',
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
    service_full_name='labnet_sim.v1.SimulatedTunableLaserService',
    service_name='SimulatedTunableLaserService',
    stub_type=_simulated_tunable_laser_pb2_grpc.SimulatedTunableLaserServiceStub,
    servicer_type=_simulated_tunable_laser_pb2_grpc.SimulatedTunableLaserServiceServicer,
    add_servicer_to_server=_simulated_tunable_laser_pb2_grpc.add_SimulatedTunableLaserServiceServicer_to_server,
    methods=(_SIMULATED_TUNABLE_LASER_GO_TO_WAVELENGTH_NM, _SIMULATED_TUNABLE_LASER_MOVE_STEPS, _SIMULATED_TUNABLE_LASER_READ_POSITION_STEPS, _SIMULATED_TUNABLE_LASER_READ_WAVELENGTH, _SIMULATED_TUNABLE_LASER_SET_VELOCITY_NM_S, _SIMULATED_TUNABLE_LASER_STOP),
    physical_methods=('go_to_wavelength_nm', 'move_steps', 'read_position_steps', 'read_wavelength', 'set_velocity_nm_s', 'stop'),
    physical_protocol=SimulatedTunableLaserPhysicalProtocol,
    distribution='labnet-sim',
    fingerprint='540701984f2ed4eea85518f94af5ca9b8c49553bc10c3a98deb250b7fa5d7004',
    facade='labnet_sim:SimulatedTunableLaser',
    physical='labnet_sim.simulated_tunable_laser.physical:PhysicalSimulatedTunableLaser',
)


@runtime_checkable
class SimulatedWavemeterPhysicalProtocol(Protocol):
    def read_wavelength(self, channel: str | None = None) -> Any: ...
    def set_exposure(self, value: float, channel: str | None = None) -> Any: ...


_SIMULATED_WAVEMETER_READ_WAVELENGTH: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.SimulatedWavemeterService.ReadWavelength',
    rpc_name='ReadWavelength',
    python_name='read_wavelength',
    pattern='RPC_PATTERN_SCALAR_READ',
    pattern_value=1,
    physical_method='read_wavelength',
    default_unit='nm',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.ReadWavelengthRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.ReadWavelengthResponse'),
    request_full_name='labnet_sim.v1.ReadWavelengthRequest',
    response_full_name='labnet_sim.v1.ReadWavelengthResponse',
    client_streaming=False,
    server_streaming=False,
    cardinality='unary',
    forwarded_fields=('channel',),
    forwarded_field_specs=(
        ForwardedField(name='channel', python_type='str | None', optional=True),
    ),
)

_SIMULATED_WAVEMETER_SET_EXPOSURE: Final = RpcSpec(
    rpc_full_name='labnet_sim.v1.SimulatedWavemeterService.SetExposure',
    rpc_name='SetExposure',
    python_name='set_exposure',
    pattern='RPC_PATTERN_SCALAR_WRITE',
    pattern_value=2,
    physical_method='set_exposure',
    default_unit='ms',
    request_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SetExposureRequest'),
    response_type=_SYMBOLS.GetSymbol('labnet_sim.v1.SetExposureResponse'),
    request_full_name='labnet_sim.v1.SetExposureRequest',
    response_full_name='labnet_sim.v1.SetExposureResponse',
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
    rpc_full_name='labnet_sim.v1.SimulatedWavemeterService.StreamWavelength',
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
    service_full_name='labnet_sim.v1.SimulatedWavemeterService',
    service_name='SimulatedWavemeterService',
    stub_type=_simulated_wavemeter_pb2_grpc.SimulatedWavemeterServiceStub,
    servicer_type=_simulated_wavemeter_pb2_grpc.SimulatedWavemeterServiceServicer,
    add_servicer_to_server=_simulated_wavemeter_pb2_grpc.add_SimulatedWavemeterServiceServicer_to_server,
    methods=(_SIMULATED_WAVEMETER_READ_WAVELENGTH, _SIMULATED_WAVEMETER_SET_EXPOSURE, _SIMULATED_WAVEMETER_STREAM_WAVELENGTH),
    physical_methods=('read_wavelength', 'set_exposure'),
    physical_protocol=SimulatedWavemeterPhysicalProtocol,
    distribution='labnet-sim',
    fingerprint='e10fe8fa5f76c31064229872d3664aa258e4b156e7bb61c669ca1d7d0fecb6b1',
    facade='labnet_sim:SimulatedWavemeter',
    physical='labnet_sim.simulated_wavemeter.physical:PhysicalSimulatedWavemeter',
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
