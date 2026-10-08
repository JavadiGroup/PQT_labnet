from . import common_pb2 as _common_pb2
from . import rpc_options_pb2 as _rpc_options_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SimulatedTunableLaserRequest(_message.Message):
    __slots__ = ("device_id", "operation_id")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ...) -> None: ...

class SimulatedTunableLaserMeasurementResponse(_message.Message):
    __slots__ = ("operation_id", "measurement")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    MEASUREMENT_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    measurement: _common_pb2.ScalarMeasurement
    def __init__(self, operation_id: _Optional[str] = ..., measurement: _Optional[_Union[_common_pb2.ScalarMeasurement, _Mapping]] = ...) -> None: ...

class SimulatedTunableLaserMoveStepsRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "relative_steps", "velocity_mode")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    RELATIVE_STEPS_FIELD_NUMBER: _ClassVar[int]
    VELOCITY_MODE_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    relative_steps: int
    velocity_mode: str
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., relative_steps: _Optional[int] = ..., velocity_mode: _Optional[str] = ...) -> None: ...

class SimulatedTunableLaserGoToWavelengthRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "wavelength_nm")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    WAVELENGTH_NM_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    wavelength_nm: float
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., wavelength_nm: _Optional[float] = ...) -> None: ...

class SimulatedTunableLaserVelocityRequest(_message.Message):
    __slots__ = ("device_id", "operation_id", "velocity_nm_s")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    VELOCITY_NM_S_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    operation_id: str
    velocity_nm_s: float
    def __init__(self, device_id: _Optional[str] = ..., operation_id: _Optional[str] = ..., velocity_nm_s: _Optional[float] = ...) -> None: ...
