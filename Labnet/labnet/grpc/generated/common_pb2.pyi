from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class MeasurementQuality(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MEASUREMENT_QUALITY_UNSPECIFIED: _ClassVar[MeasurementQuality]
    MEASUREMENT_QUALITY_GOOD: _ClassVar[MeasurementQuality]
    MEASUREMENT_QUALITY_SUSPECT: _ClassVar[MeasurementQuality]
    MEASUREMENT_QUALITY_BAD: _ClassVar[MeasurementQuality]
MEASUREMENT_QUALITY_UNSPECIFIED: MeasurementQuality
MEASUREMENT_QUALITY_GOOD: MeasurementQuality
MEASUREMENT_QUALITY_SUSPECT: MeasurementQuality
MEASUREMENT_QUALITY_BAD: MeasurementQuality

class ScalarMeasurement(_message.Message):
    __slots__ = ("sequence", "timestamp_ns", "value", "unit", "quality", "status", "channel")
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_NS_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    UNIT_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    timestamp_ns: int
    value: float
    unit: str
    quality: MeasurementQuality
    status: str
    channel: str
    def __init__(self, sequence: _Optional[int] = ..., timestamp_ns: _Optional[int] = ..., value: _Optional[float] = ..., unit: _Optional[str] = ..., quality: _Optional[_Union[MeasurementQuality, str]] = ..., status: _Optional[str] = ..., channel: _Optional[str] = ...) -> None: ...

class IntegerSequence(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, values: _Optional[_Iterable[int]] = ...) -> None: ...

class CommandResult(_message.Message):
    __slots__ = ("none_value", "boolean_value", "integer_value", "floating_point_value", "text_value", "integer_sequence")
    NONE_VALUE_FIELD_NUMBER: _ClassVar[int]
    BOOLEAN_VALUE_FIELD_NUMBER: _ClassVar[int]
    INTEGER_VALUE_FIELD_NUMBER: _ClassVar[int]
    FLOATING_POINT_VALUE_FIELD_NUMBER: _ClassVar[int]
    TEXT_VALUE_FIELD_NUMBER: _ClassVar[int]
    INTEGER_SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    none_value: _empty_pb2.Empty
    boolean_value: bool
    integer_value: int
    floating_point_value: float
    text_value: str
    integer_sequence: IntegerSequence
    def __init__(self, none_value: _Optional[_Union[_empty_pb2.Empty, _Mapping]] = ..., boolean_value: _Optional[bool] = ..., integer_value: _Optional[int] = ..., floating_point_value: _Optional[float] = ..., text_value: _Optional[str] = ..., integer_sequence: _Optional[_Union[IntegerSequence, _Mapping]] = ...) -> None: ...

class CommandResponse(_message.Message):
    __slots__ = ("operation_id", "result")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    result: CommandResult
    def __init__(self, operation_id: _Optional[str] = ..., result: _Optional[_Union[CommandResult, _Mapping]] = ...) -> None: ...
