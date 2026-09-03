from __future__ import annotations

import argparse
import importlib.util
import keyword
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Sequence

import grpc_tools
from google.protobuf import descriptor_pb2
from grpc_tools import protoc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTO_ROOT = PROJECT_ROOT / "protos" / "labnet" / "devices" / "v1"
OUTPUT_ROOT = PROJECT_ROOT / "labnet" / "grpc" / "generated"
RPC_OPTIONS_FILE = "rpc_options.proto"
RPC_OPTION_FULL_NAME = "labnet.devices.v1.rpc"
LOCAL_IMPORT = re.compile(
    r"^import ([A-Za-z][A-Za-z0-9_]*_pb2) as ",
    flags=re.MULTILINE,
)
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


def _proto_files(proto_root: Path, proto_files: Sequence[str] | None) -> tuple[str, ...]:
    names = (
        tuple(proto_files)
        if proto_files is not None
        else tuple(path.name for path in sorted(proto_root.glob("*.proto")))
    )
    if not names:
        raise ContractValidationError(f"No .proto files found in {proto_root}.")
    if RPC_OPTIONS_FILE not in names:
        raise ContractValidationError(
            f"{RPC_OPTIONS_FILE} must be included so device RPC contracts can be validated."
        )
    missing = [name for name in names if not (proto_root / name).is_file()]
    if missing:
        raise ContractValidationError(
            "Proto file(s) not found: " + ", ".join(sorted(missing))
        )
    return tuple(sorted(dict.fromkeys(names)))


def _run_protoc(
    *,
    proto_root: Path,
    output_root: Path,
    descriptor_path: Path,
    proto_files: Sequence[str],
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    well_known_types = Path(grpc_tools.__file__).resolve().parent / "_proto"
    arguments = [
        "grpc_tools.protoc",
        f"--proto_path={proto_root}",
        f"--proto_path={well_known_types}",
        f"--python_out={output_root}",
        f"--pyi_out={output_root}",
        f"--grpc_python_out={output_root}",
        f"--descriptor_set_out={descriptor_path}",
        "--include_imports",
        *(str(proto_root / name) for name in proto_files),
    ]
    result = protoc.main(arguments)
    if result != 0:
        raise SystemExit(f"grpc_tools.protoc failed with exit code {result}.")


def _make_imports_package_relative(output_root: Path) -> None:
    generated_paths = {
        *output_root.glob("*_pb2.py"),
        *output_root.glob("*_pb2.pyi"),
        *output_root.glob("*_pb2_grpc.py"),
    }
    for path in sorted(generated_paths):
        generated = path.read_text(encoding="utf-8")
        package_relative = LOCAL_IMPORT.sub(r"from . import \1 as ", generated)
        path.write_text(package_relative, encoding="utf-8", newline="\n")


def _load_rpc_options_module(output_root: Path) -> ModuleType:
    module_path = output_root / "rpc_options_pb2.py"
    spec = importlib.util.spec_from_file_location(
        "_labnet_contract_generation_rpc_options_pb2",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load generated options module at {module_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _physical_methods_for_service(
    contracts: Sequence[RpcContractSpec],
) -> dict[str, tuple[ForwardedFieldSpec, ...]]:
    contracts_by_method: dict[str, list[RpcContractSpec]] = {}
    for contract in contracts:
        contracts_by_method.setdefault(contract.physical_method, []).append(contract)

    result: dict[str, tuple[ForwardedFieldSpec, ...]] = {}
    for method, method_contracts in contracts_by_method.items():
        default_units = {contract.default_unit for contract in method_contracts}
        if len(default_units) != 1:
            raise ContractValidationError(
                f"{method_contracts[0].service_full_name}: RPCs mapped to physical "
                f"method {method!r} declare incompatible default_unit values: "
                f"{', '.join(sorted(default_units))}. Use one physical method per "
                "unit or add an explicit conversion-aware pattern."
            )
        field_order: list[str] = []
        occurrences: dict[str, list[ForwardedFieldSpec]] = {}
        for contract in method_contracts:
            for field in contract.forwarded_field_specs:
                if field.name not in occurrences:
                    field_order.append(field.name)
                occurrences.setdefault(field.name, []).append(field)

        merged: list[ForwardedFieldSpec] = []
        for name in field_order:
            fields = occurrences[name]
            base_annotation = fields[0].annotation.replace(" | None", "")
            if any(
                field.annotation.replace(" | None", "") != base_annotation
                for field in fields[1:]
            ):
                raise ContractValidationError(
                    f"{method_contracts[0].service_full_name}: physical method "
                    f"{method!r} maps incompatible protobuf field types for "
                    f"{name!r}."
                )
            optional = len(fields) != len(method_contracts) or any(
                field.optional for field in fields
            )
            merged.append(
                ForwardedFieldSpec(
                    name=name,
                    annotation=(
                        f"{base_annotation} | None" if optional else base_annotation
                    ),
                    optional=optional,
                )
            )
        result[method] = tuple(
            [field for field in merged if not field.optional]
            + [field for field in merged if field.optional]
        )
    return result


def _render_protocol_method(
    method_name: str,
    fields: Sequence[ForwardedFieldSpec],
) -> str:
    arguments = ["self"]
    for field in fields:
        default = " = None" if field.optional else ""
        arguments.append(f"{field.name}: {field.annotation}{default}")
    signature = ", ".join(arguments)
    return f"    def {method_name}({signature}) -> Any: ..."


def _render_contracts_module(
    contracts: Sequence[RpcContractSpec],
    *,
    proto_files: Sequence[str],
) -> str:
    service_groups: dict[str, list[RpcContractSpec]] = {}
    for contract in contracts:
        service_groups.setdefault(contract.service_full_name, []).append(contract)
    grpc_modules = sorted({contract.service_file_stem for contract in contracts})
    pb2_modules = sorted(
        {
            Path(name).stem
            for name in proto_files
            if name != RPC_OPTIONS_FILE
        }
    )
    lines = [
        '"""Generated LabNet device RPC contracts. Do not edit by hand."""',
        "",
        "from __future__ import annotations",
        "",
        "import inspect",
        "from dataclasses import dataclass",
        "from typing import Any, Callable, Final, Mapping, Protocol, Sequence, runtime_checkable",
        "",
        "from google.protobuf import message as _message",
        "from google.protobuf import symbol_database as _symbol_database",
    ]
    for module in pb2_modules:
        lines.append(f"from . import {module}_pb2 as _{module}_pb2")
    for module in grpc_modules:
        lines.append(f"from . import {module}_pb2_grpc as _{module}_pb2_grpc")
    lines.extend(
        [
            "",
            "_SYMBOLS = _symbol_database.Default()",
            "",
            "",
            "class PhysicalContractError(TypeError):",
            '    """A physical implementation does not satisfy its generated contract."""',
            "",
            "",
            "@dataclass(frozen=True, slots=True)",
            "class ForwardedField:",
            "    name: str",
            "    python_type: str",
            "    optional: bool",
            "",
            "",
            "@dataclass(frozen=True, slots=True)",
            "class RpcSpec:",
            "    rpc_full_name: str",
            "    rpc_name: str",
            "    python_name: str",
            "    pattern: str",
            "    pattern_value: int",
            "    physical_method: str",
            "    default_unit: str",
            "    request_type: type[_message.Message]",
            "    response_type: type[_message.Message]",
            "    request_full_name: str",
            "    response_full_name: str",
            "    client_streaming: bool",
            "    server_streaming: bool",
            "    cardinality: str",
            "    forwarded_fields: tuple[str, ...]",
            "    forwarded_field_specs: tuple[ForwardedField, ...]",
            "",
            "",
            "@dataclass(frozen=True, slots=True)",
            "class DeviceContract:",
            "    device_type: str",
            "    service_full_name: str",
            "    service_name: str",
            "    stub_type: type[Any]",
            "    servicer_type: type[Any]",
            "    add_servicer_to_server: Callable[..., None]",
            "    methods: tuple[RpcSpec, ...]",
            "    physical_methods: tuple[str, ...]",
            "    physical_protocol: type[Any]",
            "",
            "    @property",
            "    def allowed_physical_methods(self) -> tuple[str, ...]:",
            "        return self.physical_methods",
            "",
            "    @property",
            "    def add_servicer(self) -> Callable[..., None]:",
            "        return self.add_servicer_to_server",
            "",
            "",
        ]
    )

    service_variable_names: dict[str, str] = {}
    for service_full_name, service_contracts in sorted(service_groups.items()):
        sample = service_contracts[0]
        base_name = sample.service_name.removesuffix("Service")
        protocol_name = f"{base_name}PhysicalProtocol"
        variable_name = f"{_snake_case(base_name).upper()}_CONTRACT"
        service_variable_names[service_full_name] = variable_name
        physical_methods = _physical_methods_for_service(service_contracts)
        lines.extend(
            [
                "@runtime_checkable",
                f"class {protocol_name}(Protocol):",
            ]
        )
        for method_name, fields in sorted(physical_methods.items()):
            lines.append(_render_protocol_method(method_name, fields))
        lines.extend(["", ""])

        method_variable_names: list[str] = []
        for contract in service_contracts:
            method_variable = (
                f"_{_snake_case(base_name).upper()}_{_snake_case(contract.rpc_name).upper()}"
            )
            method_variable_names.append(method_variable)
            lines.extend(
                [
                    f"{method_variable}: Final = RpcSpec(",
                    f"    rpc_full_name={contract.rpc_full_name!r},",
                    f"    rpc_name={contract.rpc_name!r},",
                    f"    python_name={contract.python_name!r},",
                    f"    pattern={contract.pattern!r},",
                    f"    pattern_value={contract.pattern_value!r},",
                    f"    physical_method={contract.physical_method!r},",
                    f"    default_unit={contract.default_unit!r},",
                    f"    request_type=_SYMBOLS.GetSymbol({contract.request_full_name!r}),",
                    f"    response_type=_SYMBOLS.GetSymbol({contract.response_full_name!r}),",
                    f"    request_full_name={contract.request_full_name!r},",
                    f"    response_full_name={contract.response_full_name!r},",
                    f"    client_streaming={contract.client_streaming!r},",
                    f"    server_streaming={contract.server_streaming!r},",
                    f"    cardinality={contract.cardinality!r},",
                    f"    forwarded_fields={contract.forwarded_fields!r},",
                    "    forwarded_field_specs=(",
                    *(
                        "        ForwardedField("
                        f"name={field.name!r}, python_type={field.annotation!r}, "
                        f"optional={field.optional!r}),"
                        for field in contract.forwarded_field_specs
                    ),
                    "    ),",
                    ")",
                    "",
                ]
            )
        methods_expression = ", ".join(method_variable_names)
        if len(method_variable_names) == 1:
            methods_expression += ","
        physical_expression = ", ".join(repr(name) for name in sorted(physical_methods))
        if len(physical_methods) == 1:
            physical_expression += ","
        grpc_alias = f"_{sample.service_file_stem}_pb2_grpc"
        lines.extend(
            [
                f"{variable_name}: Final = DeviceContract(",
                f"    device_type={_snake_case(base_name)!r},",
                f"    service_full_name={service_full_name!r},",
                f"    service_name={sample.service_name!r},",
                f"    stub_type={grpc_alias}.{sample.service_name}Stub,",
                f"    servicer_type={grpc_alias}.{sample.service_name}Servicer,",
                f"    add_servicer_to_server={grpc_alias}.add_{sample.service_name}Servicer_to_server,",
                f"    methods=({methods_expression}),",
                f"    physical_methods=({physical_expression}),",
                f"    physical_protocol={protocol_name},",
                ")",
                "",
                "",
            ]
        )

    variables = [service_variable_names[name] for name in sorted(service_variable_names)]
    variables_expression = ", ".join(variables)
    if len(variables) == 1:
        variables_expression += ","
    lines.extend(
        [
            f"DEVICE_CONTRACTS: Final[tuple[DeviceContract, ...]] = ({variables_expression})",
            "DEVICE_CONTRACT_BY_TYPE: Final[dict[str, DeviceContract]] = {",
            "    contract.device_type: contract for contract in DEVICE_CONTRACTS",
            "}",
            "DEVICE_CONTRACT_BY_SERVICE: Final[dict[str, DeviceContract]] = {",
            "    contract.service_full_name: contract for contract in DEVICE_CONTRACTS",
            "}",
            "RPC_SPEC_BY_FULL_NAME: Final[dict[str, RpcSpec]] = {",
            "    method.rpc_full_name: method",
            "    for contract in DEVICE_CONTRACTS",
            "    for method in contract.methods",
            "}",
            "",
            "",
            "def validate_implementation(",
            "    implementation: object | type[Any],",
            "    contract: DeviceContract,",
            ") -> None:",
            '    """Fail actionably if physical.py does not implement a generated contract."""',
            "",
            "    errors: list[str] = []",
            "    specs_by_method: dict[str, list[RpcSpec]] = {}",
            "    for method in contract.methods:",
            "        specs_by_method.setdefault(method.physical_method, []).append(method)",
            "    for method_name, specs in sorted(specs_by_method.items()):",
            "        method = getattr(implementation, method_name, None)",
            "        if not callable(method):",
            "            errors.append(f\"missing callable method {method_name!r}\")",
            "            continue",
            "        try:",
            "            signature = inspect.signature(method)",
            "        except (TypeError, ValueError):",
            "            continue",
            "        parameters = signature.parameters",
            "        accepts_kwargs = any(",
            "            parameter.kind is inspect.Parameter.VAR_KEYWORD",
            "            for parameter in parameters.values()",
            "        )",
            "        forwarded_occurrences: dict[str, list[ForwardedField]] = {}",
            "        for spec in specs:",
            "            for field in spec.forwarded_field_specs:",
            "                forwarded_occurrences.setdefault(field.name, []).append(field)",
            "        forwarded_specs: dict[str, ForwardedField] = {}",
            "        for field_name, occurrences in forwarded_occurrences.items():",
            "            sample = occurrences[0]",
            "            optional = len(occurrences) != len(specs) or any(",
            "                occurrence.optional for occurrence in occurrences",
            "            )",
            "            forwarded_specs[field_name] = ForwardedField(",
            "                name=field_name,",
            "                python_type=sample.python_type.replace(\" | None\", \"\"),",
            "                optional=optional,",
            "            )",
            "        forwarded = set(forwarded_specs)",
            "        for field in sorted(forwarded):",
            "            parameter = parameters.get(field)",
            "            if parameter is None and not accepts_kwargs:",
            "                errors.append(",
            "                    f\"method {method_name!r} is missing protobuf argument {field!r}\"",
            "                )",
            "            elif parameter is not None and parameter.kind is inspect.Parameter.POSITIONAL_ONLY:",
            "                errors.append(",
            "                    f\"method {method_name!r} argument {field!r} cannot be positional-only\"",
            "                )",
            "            elif (",
            "                parameter is not None",
            "                and forwarded_specs[field].optional",
            "                and parameter.default is inspect.Parameter.empty",
            "                and not accepts_kwargs",
            "            ):",
            "                errors.append(",
            "                    f\"method {method_name!r} argument {field!r} must \"",
            "                    \"have a Python default because it is optional or is \"",
            "                    \"not supplied by every mapped RPC\"",
            "                )",
            "        for parameter in parameters.values():",
            "            if parameter.name in {\"self\", \"cls\"} or parameter.kind in {",
            "                inspect.Parameter.VAR_POSITIONAL,",
            "                inspect.Parameter.VAR_KEYWORD,",
            "            }:",
            "                continue",
            "            if (",
            "                parameter.default is inspect.Parameter.empty",
            "                and parameter.name not in forwarded",
            "            ):",
            "                errors.append(",
            "                    f\"method {method_name!r} has unmapped required argument \"",
            "                    f\"{parameter.name!r}\"",
            "                )",
            "    if errors:",
            "        target_name = getattr(implementation, \"__name__\", type(implementation).__name__)",
            "        details = \"\\n  - \".join(errors)",
            "        raise PhysicalContractError(",
            "            f\"{target_name} does not satisfy {contract.device_type!r} physical contract:\"",
            "            f\"\\n  - {details}\"",
            "        )",
            "",
        ]
    )
    return "\n".join(lines)


def _generated_files(output_root: Path) -> set[Path]:
    return {
        *output_root.glob("*_pb2.py"),
        *output_root.glob("*_pb2.pyi"),
        *output_root.glob("*_pb2_grpc.py"),
        output_root / "device_contracts.py",
    }


def _copy_generated_files(staging_root: Path, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    staged_files = {path.name: path for path in _generated_files(staging_root) if path.is_file()}
    for name, source in sorted(staged_files.items()):
        destination = output_root / name
        temporary_destination = destination.with_name(f".{destination.name}.tmp")
        shutil.copyfile(source, temporary_destination)
        temporary_destination.replace(destination)

    for existing in sorted(_generated_files(output_root)):
        if existing.is_file() and existing.name not in staged_files:
            existing.unlink()


def _assert_generated_files_current(staging_root: Path, output_root: Path) -> None:
    staged = {path.name: path.read_bytes() for path in _generated_files(staging_root) if path.is_file()}
    current = {path.name: path.read_bytes() for path in _generated_files(output_root) if path.is_file()}
    stale = sorted(name for name in set(staged) | set(current) if staged.get(name) != current.get(name))
    if stale:
        raise ContractValidationError(
            "Generated protobuf artifacts are stale: "
            f"{', '.join(stale)}. Run `python tools/generate_protos.py`."
        )


def generate_protos(
    *,
    proto_root: Path = PROTO_ROOT,
    output_root: Path = OUTPUT_ROOT,
    proto_files: Sequence[str] | None = None,
    check: bool = False,
) -> tuple[RpcContractSpec, ...]:
    """Compile, validate, and optionally install all generated artifacts."""

    proto_root = Path(proto_root).resolve()
    output_root = Path(output_root).resolve()
    names = _proto_files(proto_root, proto_files)
    with tempfile.TemporaryDirectory(prefix="labnet-protos-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        staging_root = temporary_root / "generated"
        descriptor_path = temporary_root / "device_contracts.pb"
        _run_protoc(
            proto_root=proto_root,
            output_root=staging_root,
            descriptor_path=descriptor_path,
            proto_files=names,
        )
        _make_imports_package_relative(staging_root)
        rpc_options_module = _load_rpc_options_module(staging_root)
        contracts = validate_descriptor_set(
            descriptor_path,
            rpc_options_module,
            local_proto_files=names,
        )
        rendered_contracts = _render_contracts_module(
            contracts,
            proto_files=names,
        )
        try:
            compile(rendered_contracts, "device_contracts.py", "exec")
        except SyntaxError as exc:
            raise ContractValidationError(
                "Generated device contract source is not valid Python: "
                f"{exc.msg} (line {exc.lineno})."
            ) from exc
        (staging_root / "device_contracts.py").write_text(
            rendered_contracts,
            encoding="utf-8",
            newline="\n",
        )
        if check:
            _assert_generated_files_current(staging_root, output_root)
        else:
            _copy_generated_files(staging_root, output_root)
    return contracts


def _verify_repository_imports() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from labnet.grpc.generated import "
                "common_pb2, transfer_pb2, rpc_options_pb2, "
                "dummy_device_pb2, dummy_device_pb2_grpc, "
                "sacher_laser_pb2, sacher_laser_pb2_grpc, device_contracts"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate and validate LabNet protobuf device contracts."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if committed generated artifacts differ; do not write files.",
    )
    arguments = parser.parse_args(argv)
    contracts = generate_protos(check=arguments.check)
    _verify_repository_imports()
    action = "Checked" if arguments.check else "Generated"
    print(
        f"{action} {len(_proto_files(PROTO_ROOT, None))} protobuf schemas and "
        f"validated {len(contracts)} explicit device RPC contracts."
    )


if __name__ == "__main__":
    main()
