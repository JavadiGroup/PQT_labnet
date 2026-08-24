from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
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
