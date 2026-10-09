"""Render the Python modules a build writes next to the generated protobuf code."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Sequence

from .validate import RPC_OPTIONS_FILE, ContractValidationError, ForwardedFieldSpec, RpcContractSpec, _snake_case


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


def family_names(service_name: str, package: str) -> tuple[str, str, str]:
    """(facade class, ``module:Class`` a client imports, ``module:Class`` of the driver).

    ``DummyAOMService`` in ``labnet_sim``: ``DummyAOM``, ``labnet_sim:DummyAOM``,
    ``labnet_sim.dummy_aom.physical:PhysicalDummyAOM``. Without a package (labnet's
    own and test builds) the import strings are empty.
    """

    base = service_name.removesuffix("Service")
    if not package:
        return base, "", ""
    return base, f"{package}:{base}", f"{package}.{_snake_case(base)}.physical:Physical{base}"


def render_contracts_module(
    contracts: Sequence[RpcContractSpec],
    *,
    proto_files: Sequence[str],
    distribution: str = "",
    package: str = "",
    fingerprints: Mapping[str, str] | None = None,
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
        "from typing import Any, Final, Protocol, runtime_checkable",
        "",
        "from google.protobuf import symbol_database as _symbol_database",
        "",
        "# The types live in labnet.contracts; re-exported for code that imported them from here.",
        "from labnet.contracts.types import (  # noqa: F401",
        "    DeviceContract,",
        "    ForwardedField,",
        "    PhysicalContractError,",
        "    RpcSpec,",
        "    validate_implementation,",
        ")",
        "",
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
        _, facade, physical = family_names(sample.service_name, package)
        fingerprint = (fingerprints or {}).get(service_full_name, "")
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
                f"    distribution={distribution!r},",
                f"    fingerprint={fingerprint!r},",
                f"    facade={facade!r},",
                f"    physical={physical!r},",
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
        ]
    )
    return "\n".join(lines)


def _variable(service_name: str) -> str:
    return f"{_snake_case(service_name.removesuffix('Service')).upper()}_CONTRACT"


def _services(contracts: Sequence[RpcContractSpec]) -> dict[str, list[RpcContractSpec]]:
    services: dict[str, list[RpcContractSpec]] = {}
    for contract in contracts:
        services.setdefault(contract.service_name, []).append(contract)
    return dict(sorted(services.items()))


def render_facades_module(contracts: Sequence[RpcContractSpec], *, package: str, contracts_module: str) -> str:
    """One class per device family, used locally (its driver) or remotely (a lease).

    The driver is named by an import string, so importing a facade never imports
    the driver's vendor SDK; only using it locally does.
    """

    services = _services(contracts)
    lines = [
        '"""Generated LabNet device facades. Do not edit by hand."""',
        "",
        "from __future__ import annotations",
        "",
        "from labnet.devices.core.facade import ContractDeviceFacade",
        "",
        f"from .{contracts_module} import {', '.join(_variable(name) for name in services)}",
        "",
    ]
    names = []
    for service_name in services:
        base, _, physical = family_names(service_name, package)
        names.append(base)
        lines += [
            "",
            f"class {base}(ContractDeviceFacade):",
            f'    """{base}: local through its driver, or remote through a lease."""',
            "",
            f"    contract = {_variable(service_name)}",
            f"    physical_type = {physical!r}",
            "",
        ]
    lines += ["", f"__all__ = {names!r}", ""]
    return "\n".join(lines)


def render_facades_stub(contracts: Sequence[RpcContractSpec]) -> str:
    """Typed signatures of each facade's device methods, for editors and type checkers."""

    lines = [
        "# Generated LabNet device facade signatures. Do not edit by hand.",
        "from collections.abc import Iterator, Mapping, Sequence",
        "from typing import Any",
        "",
        "from labnet.devices.core.facade import ContractDeviceFacade",
        "",
    ]
    for service_name, rpcs in _services(contracts).items():
        lines += ["", f"class {service_name.removesuffix('Service')}(ContractDeviceFacade):"]
        for rpc in sorted(rpcs, key=lambda spec: spec.python_name):
            arguments = ["self"] + [
                f"{field.name}: {field.annotation}{' = None' if field.optional else ''}"
                for field in rpc.forwarded_field_specs
            ]
            if rpc.server_streaming:
                arguments.append("*")
                arguments.append("sample_rate_hz: float")
                arguments.append("sample_count: int | None = None")
                arguments.append("duration_seconds: float | None = None")
            returns = "Iterator[Any]" if rpc.server_streaming else "Any"
            lines.append(f"    def {rpc.python_name}({', '.join(arguments)}) -> {returns}: ...")
    lines.append("")
    return "\n".join(lines)


__all__ = ["family_names", "render_contracts_module", "render_facades_module", "render_facades_stub"]
