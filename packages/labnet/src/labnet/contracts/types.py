"""The types every device contract is made of, and the check a driver must pass.

Generated contract modules build :class:`DeviceContract` values from these;
nothing here depends on any particular device family.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from google.protobuf import message as _message


class PhysicalContractError(TypeError):
    """A physical implementation does not satisfy its generated contract."""


@dataclass(frozen=True, slots=True)
class ForwardedField:
    name: str
    python_type: str
    optional: bool


@dataclass(frozen=True, slots=True)
class RpcSpec:
    rpc_full_name: str
    rpc_name: str
    python_name: str
    pattern: str
    pattern_value: int
    physical_method: str
    default_unit: str
    request_type: type[_message.Message]
    response_type: type[_message.Message]
    request_full_name: str
    response_full_name: str
    client_streaming: bool
    server_streaming: bool
    cardinality: str
    forwarded_fields: tuple[str, ...]
    forwarded_field_specs: tuple[ForwardedField, ...]


@dataclass(frozen=True, slots=True)
class DeviceContract:
    device_type: str
    service_full_name: str
    service_name: str
    stub_type: type[Any]
    servicer_type: type[Any]
    add_servicer_to_server: Callable[..., None]
    methods: tuple[RpcSpec, ...]
    physical_methods: tuple[str, ...]
    physical_protocol: type[Any]
    # Who provides it, for matching a Lab PC's driver against a Control Center's:
    distribution: str = ""  # the installed package, e.g. "labnet-sim"
    fingerprint: str = ""  # sha256 of its proto file's descriptor, as built
    facade: str = ""  # "module:Class" a client imports, e.g. "labnet_sim:DummyAOM"
    physical: str = ""  # "module:Class" of the driver

    @property
    def allowed_physical_methods(self) -> tuple[str, ...]:
        return self.physical_methods

    @property
    def add_servicer(self) -> Callable[..., None]:
        return self.add_servicer_to_server


def validate_implementation(
    implementation: object | type[Any],
    contract: DeviceContract,
) -> None:
    """Fail actionably if physical.py does not implement a generated contract."""

    errors: list[str] = []
    specs_by_method: dict[str, list[RpcSpec]] = {}
    for method in contract.methods:
        specs_by_method.setdefault(method.physical_method, []).append(method)
    for method_name, specs in sorted(specs_by_method.items()):
        method = getattr(implementation, method_name, None)
        if not callable(method):
            errors.append(f"missing callable method {method_name!r}")
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            continue
        parameters = signature.parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        forwarded_occurrences: dict[str, list[ForwardedField]] = {}
        for spec in specs:
            for field in spec.forwarded_field_specs:
                forwarded_occurrences.setdefault(field.name, []).append(field)
        forwarded_specs: dict[str, ForwardedField] = {}
        for field_name, occurrences in forwarded_occurrences.items():
            sample = occurrences[0]
            optional = len(occurrences) != len(specs) or any(
                occurrence.optional for occurrence in occurrences
            )
            forwarded_specs[field_name] = ForwardedField(
                name=field_name,
                python_type=sample.python_type.replace(" | None", ""),
                optional=optional,
            )
        forwarded = set(forwarded_specs)
        for field in sorted(forwarded):
            parameter = parameters.get(field)
            if parameter is None and not accepts_kwargs:
                errors.append(
                    f"method {method_name!r} is missing protobuf argument {field!r}"
                )
            elif parameter is not None and parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
                errors.append(
                    f"method {method_name!r} argument {field!r} cannot be positional-only"
                )
            elif (
                parameter is not None
                and forwarded_specs[field].optional
                and parameter.default is inspect.Parameter.empty
                and not accepts_kwargs
            ):
                errors.append(
                    f"method {method_name!r} argument {field!r} must "
                    "have a Python default because it is optional or is "
                    "not supplied by every mapped RPC"
                )
        for parameter in parameters.values():
            if parameter.name in {"self", "cls"} or parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            if (
                parameter.default is inspect.Parameter.empty
                and parameter.name not in forwarded
            ):
                errors.append(
                    f"method {method_name!r} has unmapped required argument "
                    f"{parameter.name!r}"
                )
    if errors:
        target_name = getattr(implementation, "__name__", type(implementation).__name__)
        details = "\n  - ".join(errors)
        raise PhysicalContractError(
            f"{target_name} does not satisfy {contract.device_type!r} physical contract:"
            f"\n  - {details}"
        )


__all__ = [
    "DeviceContract",
    "ForwardedField",
    "PhysicalContractError",
    "RpcSpec",
    "validate_implementation",
]
