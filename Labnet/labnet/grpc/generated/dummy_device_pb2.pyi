from . import common_pb2 as _common_pb2
from . import transfer_pb2 as _transfer_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ReadValueRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "channel")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    channel: str
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., channel: _Optional[str] = ...) -> None: ...

class ReadValueResponse(_message.Message):
    __slots__ = ("operation_id", "measurement")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    MEASUREMENT_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    measurement: _common_pb2.ScalarMeasurement
    def __init__(self, operation_id: _Optional[str] = ..., measurement: _Optional[_Union[_common_pb2.ScalarMeasurement, _Mapping]] = ...) -> None: ...

class WriteValueRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "channel", "value", "unit")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    UNIT_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    channel: str
    value: float
    unit: str
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., channel: _Optional[str] = ..., value: _Optional[float] = ..., unit: _Optional[str] = ...) -> None: ...

class WriteValueResponse(_message.Message):
    __slots__ = ("operation_id", "measurement")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    MEASUREMENT_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    measurement: _common_pb2.ScalarMeasurement
    def __init__(self, operation_id: _Optional[str] = ..., measurement: _Optional[_Union[_common_pb2.ScalarMeasurement, _Mapping]] = ...) -> None: ...
