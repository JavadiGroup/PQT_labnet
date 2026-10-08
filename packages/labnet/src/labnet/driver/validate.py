"""Validate compiled device protos against the LabNet RPC contract rules.

``validate_descriptor_set`` checks every service method of the target's own
proto files (patterns, options, request and response shapes, argument names)
and returns one :class:`RpcContractSpec` per RPC, in a stable order.
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Sequence

from google.protobuf import descriptor_pb2


CORE_PROTO_DIR = "labnet/devices/v1"
RPC_OPTIONS_FILE = f"{CORE_PROTO_DIR}/rpc_options.proto"
RPC_OPTION_FULL_NAME = "labnet.devices.v1.rpc"
PHYSICAL_METHOD = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_PUBLIC_METHODS = frozenset(
    {
        "aclose",
        "allowed_methods",
        "client",
        "close",
        "closed",
        "connect",
        "contract",
        "default_timeout_seconds",
        "device_id",
        "device_type",
        "disconnect",
        "grant",
        "grpc",
        "is_remote",
        "lease",
        "lease_expired",
        "lease_id",
        "metadata",
        "open_stream",
        "physical_type",
        "read_measurement",
        "status",
        "write_measurement",
    }
)


class ContractValidationError(ValueError):
    """Raised when a device RPC lacks an explicit, valid LabNet contract."""


@dataclass(frozen=True, slots=True)
class ForwardedFieldSpec:
    name: str
    annotation: str
    optional: bool


@dataclass(frozen=True, slots=True)
class RpcContractSpec:
    rpc_full_name: str
    service_full_name: str
    service_name: str
    service_file_stem: str
    rpc_name: str
    python_name: str
    pattern: str
    pattern_value: int
    physical_method: str
    default_unit: str
    request_full_name: str
    response_full_name: str
    client_streaming: bool
    server_streaming: bool
    cardinality: str
    forwarded_fields: tuple[str, ...]
    forwarded_field_specs: tuple[ForwardedFieldSpec, ...]


_UNARY_PATTERNS = frozenset(
    {
        "RPC_PATTERN_SCALAR_READ",
        "RPC_PATTERN_SCALAR_WRITE",
        "RPC_PATTERN_UNARY_COMMAND",
        "RPC_PATTERN_QUERY_METADATA",
        "RPC_PATTERN_ACQUIRE_SINGLE",
    }
)
_SERVER_STREAMING_PATTERNS = frozenset(
    {
        "RPC_PATTERN_POLLED_SCALAR_STREAM",
        "RPC_PATTERN_NATIVE_STREAM",
        "RPC_PATTERN_CHUNKED_BINARY_STREAM",
        "RPC_PATTERN_EVENT_STREAM",
    }
)
_CLIENT_STREAMING_PATTERNS = frozenset(
    {
        "RPC_PATTERN_CHUNKED_BINARY_UPLOAD",
        "RPC_PATTERN_SEQUENCE_UPLOAD",
    }
)
_BIDIRECTIONAL_PATTERNS = frozenset({"RPC_PATTERN_BIDIRECTIONAL_SESSION"})
# The enum intentionally reserves the planned operation vocabulary, but the
# generator must never promise runtime support that has not been implemented.
_IMPLEMENTED_PATTERNS = frozenset(
    {
        "RPC_PATTERN_SCALAR_READ",
        "RPC_PATTERN_SCALAR_WRITE",
        "RPC_PATTERN_UNARY_COMMAND",
        "RPC_PATTERN_POLLED_SCALAR_STREAM",
    }
)
_FRAMEWORK_FIELDS = frozenset({"device_id", "operation_id"})
_CLIENT_TRANSPORT_FIELDS = frozenset({"timeout_seconds"})
_PATTERN_CONTROL_FIELDS = {
    "RPC_PATTERN_SCALAR_WRITE": frozenset({"unit"}),
    "RPC_PATTERN_POLLED_SCALAR_STREAM": frozenset(
        {"sample_rate_hz", "sample_count", "duration"}
    ),
}
_PYTHON_SCALAR_TYPES = {
    descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE: "float",
    descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT: "float",
    descriptor_pb2.FieldDescriptorProto.TYPE_INT64: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_UINT64: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_INT32: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_FIXED64: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_FIXED32: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_BOOL: "bool",
    descriptor_pb2.FieldDescriptorProto.TYPE_STRING: "str",
    descriptor_pb2.FieldDescriptorProto.TYPE_BYTES: "bytes",
    descriptor_pb2.FieldDescriptorProto.TYPE_UINT32: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED32: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED64: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_SINT32: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_SINT64: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_ENUM: "int",
    descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE: "Any",
    descriptor_pb2.FieldDescriptorProto.TYPE_GROUP: "Any",
}


def _snake_case(name: str) -> str:
    first_pass = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first_pass).lower()


def _cardinality(client_streaming: bool, server_streaming: bool) -> str:
    if client_streaming and server_streaming:
        return "bidirectional_streaming"
    if client_streaming:
        return "client_streaming"
    if server_streaming:
        return "server_streaming"
    return "unary"


def _validate_cardinality(
    *,
    rpc_full_name: str,
    pattern: str,
    client_streaming: bool,
    server_streaming: bool,
) -> None:
    cardinality = _cardinality(client_streaming, server_streaming)
    allowed_patterns = {
        "unary": _UNARY_PATTERNS,
        "server_streaming": _SERVER_STREAMING_PATTERNS,
        "client_streaming": _CLIENT_STREAMING_PATTERNS,
        "bidirectional_streaming": _BIDIRECTIONAL_PATTERNS,
    }[cardinality]
    if pattern not in allowed_patterns:
        allowed = ", ".join(sorted(allowed_patterns))
        raise ContractValidationError(
            f"{rpc_full_name}: pattern {pattern} is incompatible with {cardinality} "
            f"RPC cardinality. Choose one of: {allowed}."
        )


def _validate_implemented_pattern(*, rpc_full_name: str, pattern: str) -> None:
    if pattern in _IMPLEMENTED_PATTERNS:
        return
    supported = ", ".join(sorted(_IMPLEMENTED_PATTERNS))
    raise ContractValidationError(
        f"{rpc_full_name}: pattern {pattern} is declared but not implemented by "
        f"the current LabNet runtime. Supported patterns: {supported}."
    )


def _message_descriptors(
    file_descriptor_set: descriptor_pb2.FileDescriptorSet,
) -> dict[str, descriptor_pb2.DescriptorProto]:
    messages: dict[str, descriptor_pb2.DescriptorProto] = {}

    def add_messages(
        descriptors: Sequence[descriptor_pb2.DescriptorProto],
        prefix: str,
    ) -> None:
        for message in descriptors:
            full_name = f"{prefix}.{message.name}" if prefix else message.name
            messages[full_name] = message
            add_messages(message.nested_type, full_name)

    for file_descriptor in file_descriptor_set.file:
        add_messages(file_descriptor.message_type, file_descriptor.package)
    return messages


def _forwarded_field_specs(
    *,
    rpc_full_name: str,
    pattern: str,
    request_full_name: str,
    messages: dict[str, descriptor_pb2.DescriptorProto],
) -> tuple[ForwardedFieldSpec, ...]:
    try:
        request = messages[request_full_name]
    except KeyError as exc:
        raise ContractValidationError(
            f"{rpc_full_name}: request message {request_full_name!r} was not found "
            "in the compiled descriptor set."
        ) from exc
    excluded = _FRAMEWORK_FIELDS | _PATTERN_CONTROL_FIELDS.get(pattern, frozenset())
    forwarded: list[ForwardedFieldSpec] = []
    for field in request.field:
        if field.name in excluded:
            continue
        if field.name in _CLIENT_TRANSPORT_FIELDS:
            raise ContractValidationError(
                f"{rpc_full_name}: request field {field.name!r} conflicts with "
                "the shared client transport API. Rename the protobuf field."
            )
        if (
            keyword.iskeyword(field.name)
            or field.name in {"self", "cls"}
            or field.name.startswith("_")
        ):
            raise ContractValidationError(
                f"{rpc_full_name}: request field {field.name!r} cannot be used "
                "as a generated Python physical-method argument. Rename it."
            )
        if field.HasField("oneof_index") and not field.proto3_optional:
            raise ContractValidationError(
                f"{rpc_full_name}: request oneof field {field.name!r} is not "
                "supported by the shared argument binder. Use independent "
                "optional fields or add a reusable oneof-aware pattern."
            )
        annotation = _PYTHON_SCALAR_TYPES.get(field.type, "Any")
        if field.label == descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED:
            map_entry = (
                messages.get(field.type_name.removeprefix("."))
                if field.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
                else None
            )
            if map_entry is not None and map_entry.options.map_entry:
                key_field, value_field = map_entry.field
                key_type = _PYTHON_SCALAR_TYPES.get(key_field.type, "Any")
                value_type = _PYTHON_SCALAR_TYPES.get(value_field.type, "Any")
                annotation = f"Mapping[{key_type}, {value_type}]"
            else:
                annotation = f"Sequence[{annotation}]"
            optional = False
        else:
            optional = bool(field.proto3_optional) or field.type in {
                descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
                descriptor_pb2.FieldDescriptorProto.TYPE_GROUP,
            }
            if optional:
                annotation = f"{annotation} | None"
        forwarded.append(
            ForwardedFieldSpec(
                name=field.name,
                annotation=annotation,
                optional=optional,
            )
        )
    # Keyword forwarding is deterministic, and required arguments precede
    # optional arguments so the same order can form a valid Python signature.
    return tuple(
        [field for field in forwarded if not field.optional]
        + [field for field in forwarded if field.optional]
    )


def _require_message_field(
    *,
    rpc_full_name: str,
    message_name: str,
    message: descriptor_pb2.DescriptorProto,
    field_name: str,
    field_type: int,
    type_name: str | None = None,
    proto3_optional: bool | None = None,
) -> descriptor_pb2.FieldDescriptorProto:
    field = next((candidate for candidate in message.field if candidate.name == field_name), None)
    if field is None:
        raise ContractValidationError(
            f"{rpc_full_name}: {message_name} must define field {field_name!r} for "
            "the selected LabNet pattern."
        )
    if field.label == descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED:
        raise ContractValidationError(
            f"{rpc_full_name}: {message_name}.{field_name} must be singular."
        )
    if field.type != field_type:
        expected = descriptor_pb2.FieldDescriptorProto.Type.Name(field_type)
        actual = descriptor_pb2.FieldDescriptorProto.Type.Name(field.type)
        raise ContractValidationError(
            f"{rpc_full_name}: {message_name}.{field_name} must use {expected}; "
            f"received {actual}."
        )
    if type_name is not None and field.type_name != type_name:
        raise ContractValidationError(
            f"{rpc_full_name}: {message_name}.{field_name} must use message type "
            f"{type_name.removeprefix('.')}; received "
            f"{field.type_name.removeprefix('.') or '<none>'}."
        )
    if proto3_optional is not None and bool(field.proto3_optional) is not proto3_optional:
        qualifier = "must be declared optional" if proto3_optional else "must not be optional"
        raise ContractValidationError(
            f"{rpc_full_name}: {message_name}.{field_name} {qualifier}."
        )
    return field


def _validate_pattern_shape(
    *,
    rpc_full_name: str,
    pattern: str,
    request_full_name: str,
    response_full_name: str,
    messages: dict[str, descriptor_pb2.DescriptorProto],
) -> None:
    try:
        request = messages[request_full_name]
        response = messages[response_full_name]
    except KeyError as exc:
        raise ContractValidationError(
            f"{rpc_full_name}: request and response messages must be available in "
            "the compiled descriptor set."
        ) from exc

    for field_name in ("device_id", "operation_id"):
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=request_full_name,
            message=request,
            field_name=field_name,
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
            proto3_optional=False,
        )
    _require_message_field(
        rpc_full_name=rpc_full_name,
        message_name=response_full_name,
        message=response,
        field_name="operation_id",
        field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
        proto3_optional=False,
    )

    def require_measurement_response() -> None:
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=response_full_name,
            message=response,
            field_name="measurement",
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
            type_name=".labnet.devices.v1.ScalarMeasurement",
            proto3_optional=False,
        )

    def validate_optional_channel(*, required: bool) -> None:
        channel = next(
            (field for field in request.field if field.name == "channel"),
            None,
        )
        if channel is None and not required:
            return
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=request_full_name,
            message=request,
            field_name="channel",
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
            proto3_optional=True,
        )

    if pattern == "RPC_PATTERN_SCALAR_READ":
        validate_optional_channel(required=False)
        require_measurement_response()
        return

    if pattern == "RPC_PATTERN_SCALAR_WRITE":
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=request_full_name,
            message=request,
            field_name="value",
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE,
            proto3_optional=False,
        )
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=request_full_name,
            message=request,
            field_name="unit",
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
            proto3_optional=True,
        )
        validate_optional_channel(required=False)
        require_measurement_response()
        return

    if pattern == "RPC_PATTERN_POLLED_SCALAR_STREAM":
        validate_optional_channel(required=True)
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=request_full_name,
            message=request,
            field_name="sample_rate_hz",
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE,
            proto3_optional=False,
        )
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=request_full_name,
            message=request,
            field_name="sample_count",
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
            proto3_optional=True,
        )
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=request_full_name,
            message=request,
            field_name="duration",
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
            type_name=".google.protobuf.Duration",
            proto3_optional=False,
        )
        require_measurement_response()
        return

    if pattern == "RPC_PATTERN_UNARY_COMMAND":
        _require_message_field(
            rpc_full_name=rpc_full_name,
            message_name=response_full_name,
            message=response,
            field_name="result",
            field_type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
            type_name=".labnet.devices.v1.CommandResult",
            proto3_optional=False,
        )
        return

    # _validate_implemented_pattern prevents reaching this branch.
    raise AssertionError(f"Unhandled implemented pattern: {pattern}")


def validate_descriptor_set(
    descriptor_path: Path,
    rpc_options_module: ModuleType,
    *,
    local_proto_files: Sequence[str],
) -> tuple[RpcContractSpec, ...]:
    """Validate all local service methods and return deterministic contracts."""

    file_descriptor_set = descriptor_pb2.FileDescriptorSet()
    file_descriptor_set.ParseFromString(Path(descriptor_path).read_bytes())
    messages = _message_descriptors(file_descriptor_set)
    local_names = set(local_proto_files)
    extension = rpc_options_module.rpc
    enum_descriptor = rpc_options_module.RpcPattern.DESCRIPTOR
    enum_names_by_number = {
        value.number: value.name for value in enum_descriptor.values
    }
    contracts: list[RpcContractSpec] = []

    for file_descriptor in sorted(
        file_descriptor_set.file,
        key=lambda descriptor: descriptor.name,
    ):
        if file_descriptor.name not in local_names:
            continue
        package_prefix = f"{file_descriptor.package}." if file_descriptor.package else ""
        for service in file_descriptor.service:
            service_full_name = f"{package_prefix}{service.name}"
            for method in service.method:
                rpc_full_name = f"{service_full_name}.{method.name}"
                if not method.options.HasExtension(extension):
                    raise ContractValidationError(
                        f"{rpc_full_name}: missing required option "
                        f"'({RPC_OPTION_FULL_NAME})'. Explicitly set pattern, "
                        "physical_method, and default_unit."
                    )

                rpc_options = method.options.Extensions[extension]
                missing_fields = [
                    field_name
                    for field_name in ("pattern", "physical_method", "default_unit")
                    if not rpc_options.HasField(field_name)
                ]
                if missing_fields:
                    raise ContractValidationError(
                        f"{rpc_full_name}: option '({RPC_OPTION_FULL_NAME})' must "
                        "explicitly set field(s): "
                        f"{', '.join(missing_fields)}. Use default_unit: \"1\" for "
                        "dimensionless operations."
                    )

                pattern_value = int(rpc_options.pattern)
                if pattern_value == int(rpc_options_module.RPC_PATTERN_UNSPECIFIED):
                    raise ContractValidationError(
                        f"{rpc_full_name}: pattern must not be "
                        "RPC_PATTERN_UNSPECIFIED."
                    )
                if pattern_value not in enum_names_by_number:
                    raise ContractValidationError(
                        f"{rpc_full_name}: unknown RpcPattern numeric value "
                        f"{pattern_value}."
                    )
                pattern = enum_names_by_number[pattern_value]

                physical_method = rpc_options.physical_method.strip()
                if not physical_method:
                    raise ContractValidationError(
                        f"{rpc_full_name}: physical_method must be non-empty."
                    )
                if physical_method != rpc_options.physical_method or not PHYSICAL_METHOD.fullmatch(
                    physical_method
                ):
                    raise ContractValidationError(
                        f"{rpc_full_name}: physical_method must be a snake_case Python "
                        f"identifier; received {rpc_options.physical_method!r}."
                    )
                if keyword.iskeyword(physical_method):
                    raise ContractValidationError(
                        f"{rpc_full_name}: physical_method {physical_method!r} is "
                        "a Python keyword. Choose another snake_case name."
                    )

                default_unit = rpc_options.default_unit.strip()
                if not default_unit:
                    raise ContractValidationError(
                        f"{rpc_full_name}: default_unit must be non-empty. Use \"1\" "
                        "for dimensionless operations."
                    )
                if default_unit != rpc_options.default_unit:
                    raise ContractValidationError(
                        f"{rpc_full_name}: default_unit must not contain leading or "
                        "trailing whitespace."
                    )
                if len(default_unit) > 32 or any(
                    ord(character) < 0x20 for character in default_unit
                ):
                    raise ContractValidationError(
                        f"{rpc_full_name}: default_unit must be at most 32 printable "
                        "characters."
                    )

                _validate_implemented_pattern(
                    rpc_full_name=rpc_full_name,
                    pattern=pattern,
                )
                _validate_cardinality(
                    rpc_full_name=rpc_full_name,
                    pattern=pattern,
                    client_streaming=method.client_streaming,
                    server_streaming=method.server_streaming,
                )
                request_full_name = method.input_type.removeprefix(".")
                response_full_name = method.output_type.removeprefix(".")
                _validate_pattern_shape(
                    rpc_full_name=rpc_full_name,
                    pattern=pattern,
                    request_full_name=request_full_name,
                    response_full_name=response_full_name,
                    messages=messages,
                )
                forwarded_field_specs = _forwarded_field_specs(
                    rpc_full_name=rpc_full_name,
                    pattern=pattern,
                    request_full_name=request_full_name,
                    messages=messages,
                )
                contracts.append(
                    RpcContractSpec(
                        rpc_full_name=rpc_full_name,
                        service_full_name=service_full_name,
                        service_name=service.name,
                        service_file_stem=Path(file_descriptor.name).stem,
                        rpc_name=method.name,
                        python_name=_snake_case(method.name),
                        pattern=pattern,
                        pattern_value=pattern_value,
                        physical_method=physical_method,
                        default_unit=default_unit,
                        request_full_name=request_full_name,
                        response_full_name=response_full_name,
                        client_streaming=method.client_streaming,
                        server_streaming=method.server_streaming,
                        cardinality=_cardinality(
                            method.client_streaming,
                            method.server_streaming,
                        ),
                        forwarded_fields=tuple(
                            field.name for field in forwarded_field_specs
                        ),
                        forwarded_field_specs=forwarded_field_specs,
                    )
                )

    if not contracts:
        raise ContractValidationError(
            "No device RPCs were found in the selected local proto files."
        )
    ordered_contracts = tuple(
        sorted(contracts, key=lambda contract: contract.rpc_full_name)
    )
    services_by_device_type: dict[str, str] = {}
    rpc_by_python_name: dict[tuple[str, str], str] = {}
    for contract in ordered_contracts:
        device_type = _snake_case(contract.service_name.removesuffix("Service"))
        previous_service = services_by_device_type.setdefault(
            device_type,
            contract.service_full_name,
        )
        if previous_service != contract.service_full_name:
            raise ContractValidationError(
                "Device service names must derive unique device_type values; "
                f"{previous_service!r} and {contract.service_full_name!r} both "
                f"derive {device_type!r}. Rename one service."
            )
        python_key = (contract.service_full_name, contract.python_name)
        if (
            contract.python_name.startswith("_")
            or keyword.iskeyword(contract.python_name)
            or contract.python_name in _RESERVED_PUBLIC_METHODS
        ):
            raise ContractValidationError(
                f"{contract.rpc_full_name}: generated Python method name "
                f"{contract.python_name!r} conflicts with the shared device "
                "lifecycle/client API. Rename the RPC."
            )
        previous_rpc = rpc_by_python_name.setdefault(
            python_key,
            contract.rpc_full_name,
        )
        if previous_rpc != contract.rpc_full_name:
            raise ContractValidationError(
                f"{previous_rpc!r} and {contract.rpc_full_name!r} both map to "
                f"Python method {contract.python_name!r}. Rename one RPC so the "
                "generated client surface is unambiguous."
            )
    return ordered_contracts


__all__ = [
    "ContractValidationError",
    "ForwardedFieldSpec",
    "RpcContractSpec",
    "validate_descriptor_set",
]
