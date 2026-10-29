from labnet.grpc.generated import common_pb2 as _common_pb2
from labnet.grpc.generated import rpc_options_pb2 as _rpc_options_pb2
from labnet.grpc.generated import transfer_pb2 as _transfer_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SacherLaserRequest(_message.Message):
    __slots__ = ("device_id", "operation_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ...) -> None: ...

class SacherLaserMeasurementResponse(_message.Message):
    __slots__ = ("operation_id", "measurement")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    MEASUREMENT_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    measurement: _common_pb2.ScalarMeasurement
    def __init__(self, operation_id: _Optional[str] = ..., measurement: _Optional[_Union[_common_pb2.ScalarMeasurement, _Mapping]] = ...) -> None: ...

class SacherLaserMoveRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "wavelength_nm")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    WAVELENGTH_NM_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    wavelength_nm: float
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., wavelength_nm: _Optional[float] = ...) -> None: ...

class SacherLaserRebaseRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "measured_wavelength_nm")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    MEASURED_WAVELENGTH_NM_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    measured_wavelength_nm: float
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., measured_wavelength_nm: _Optional[float] = ...) -> None: ...

class SacherLaserSetVelocityRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "value", "unit")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    UNIT_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    value: float
    unit: str
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., value: _Optional[float] = ..., unit: _Optional[str] = ...) -> None: ...
