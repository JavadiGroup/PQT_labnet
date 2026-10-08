from google.protobuf import descriptor_pb2 as _descriptor_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RpcPattern(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RPC_PATTERN_UNSPECIFIED: _ClassVar[RpcPattern]
    RPC_PATTERN_SCALAR_READ: _ClassVar[RpcPattern]
    RPC_PATTERN_SCALAR_WRITE: _ClassVar[RpcPattern]
    RPC_PATTERN_UNARY_COMMAND: _ClassVar[RpcPattern]
    RPC_PATTERN_QUERY_METADATA: _ClassVar[RpcPattern]
    RPC_PATTERN_ACQUIRE_SINGLE: _ClassVar[RpcPattern]
    RPC_PATTERN_POLLED_SCALAR_STREAM: _ClassVar[RpcPattern]
    RPC_PATTERN_NATIVE_STREAM: _ClassVar[RpcPattern]
    RPC_PATTERN_CHUNKED_BINARY_STREAM: _ClassVar[RpcPattern]
    RPC_PATTERN_EVENT_STREAM: _ClassVar[RpcPattern]
    RPC_PATTERN_CHUNKED_BINARY_UPLOAD: _ClassVar[RpcPattern]
    RPC_PATTERN_SEQUENCE_UPLOAD: _ClassVar[RpcPattern]
    RPC_PATTERN_BIDIRECTIONAL_SESSION: _ClassVar[RpcPattern]
RPC_PATTERN_UNSPECIFIED: RpcPattern
RPC_PATTERN_SCALAR_READ: RpcPattern
RPC_PATTERN_SCALAR_WRITE: RpcPattern
RPC_PATTERN_UNARY_COMMAND: RpcPattern
RPC_PATTERN_QUERY_METADATA: RpcPattern
RPC_PATTERN_ACQUIRE_SINGLE: RpcPattern
RPC_PATTERN_POLLED_SCALAR_STREAM: RpcPattern
RPC_PATTERN_NATIVE_STREAM: RpcPattern
RPC_PATTERN_CHUNKED_BINARY_STREAM: RpcPattern
RPC_PATTERN_EVENT_STREAM: RpcPattern
RPC_PATTERN_CHUNKED_BINARY_UPLOAD: RpcPattern
RPC_PATTERN_SEQUENCE_UPLOAD: RpcPattern
RPC_PATTERN_BIDIRECTIONAL_SESSION: RpcPattern
RPC_FIELD_NUMBER: _ClassVar[int]
rpc: _descriptor.FieldDescriptor

class RpcOptions(_message.Message):
    __slots__ = ("pattern", "physical_method", "default_unit")
    PATTERN_FIELD_NUMBER: _ClassVar[int]
    PHYSICAL_METHOD_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_UNIT_FIELD_NUMBER: _ClassVar[int]
    pattern: RpcPattern
    physical_method: str
    default_unit: str
    def __init__(self, pattern: _Optional[_Union[RpcPattern, str]] = ..., physical_method: _Optional[str] = ..., default_unit: _Optional[str] = ...) -> None: ...
