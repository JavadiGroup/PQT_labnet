import datetime

from . import common_pb2 as _common_pb2
from google.protobuf import duration_pb2 as _duration_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class StreamDataRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "channel", "sample_rate_hz", "sample_count", "duration")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_RATE_HZ_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_COUNT_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    channel: str
    sample_rate_hz: float
    sample_count: int
    duration: _duration_pb2.Duration
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., channel: _Optional[str] = ..., sample_rate_hz: _Optional[float] = ..., sample_count: _Optional[int] = ..., duration: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ...) -> None: ...

class StreamDataResponse(_message.Message):
    __slots__ = ("operation_id", "measurement")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    MEASUREMENT_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    measurement: _common_pb2.ScalarMeasurement
    def __init__(self, operation_id: _Optional[str] = ..., measurement: _Optional[_Union[_common_pb2.ScalarMeasurement, _Mapping]] = ...) -> None: ...
