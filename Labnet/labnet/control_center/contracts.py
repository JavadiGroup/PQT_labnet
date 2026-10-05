"""Describe each device's callable methods from its generated contract.

The Control Center shows the same method names and arguments a Python client
uses (``device.read_wavelength(channel=...)``), so nothing here is written per
device family.
"""

from __future__ import annotations

from typing import Any

from labnet.grpc.generated.device_contracts import DEVICE_CONTRACT_BY_TYPE, RpcSpec


_STREAM_CONTROLS = (
    "sample_rate_hz: float",
    "sample_count: int | None = None",
    "duration_seconds: float | None = None",
)
_KIND_BY_PATTERN = {
    "RPC_PATTERN_SCALAR_READ": "read",
    "RPC_PATTERN_SCALAR_WRITE": "write",
    "RPC_PATTERN_UNARY_COMMAND": "command",
    "RPC_PATTERN_POLLED_SCALAR_STREAM": "stream",
}
_RETURNS_BY_KIND = {
    "read": "Measurement",
    "write": "Measurement",
    "command": "result",
    "stream": "stream of Measurement",
}


def describe_method(spec: RpcSpec) -> dict[str, Any]:
    kind = _KIND_BY_PATTERN.get(spec.pattern, "other")
    parameters: list[str] = list(_STREAM_CONTROLS) if kind == "stream" else []
    for field in spec.forwarded_field_specs:
        default = " = None" if field.optional else ""
        parameters.append(f"{field.name}: {field.python_type}{default}")
    if kind == "write":
        parameters.append("unit: str | None = None")
    return {
        "name": spec.python_name,
        "kind": kind,
        "parameters": parameters,
        "returns": _RETURNS_BY_KIND.get(kind, "result"),
        "unit": spec.default_unit,
    }


def describe_device_type(device_type: str) -> list[dict[str, Any]] | None:
    """Methods for ``device_type``, or None if this build has no contract for it."""

    contract = DEVICE_CONTRACT_BY_TYPE.get(device_type)
    if contract is None:
        return None
    return [describe_method(spec) for spec in contract.methods]


def type_label(device_type: str) -> str:
    """``simulated_wavemeter`` -> ``Simulated Wavemeter``."""

    return " ".join(part.capitalize() for part in device_type.split("_") if part) or "Unknown"


__all__ = ["describe_device_type", "describe_method", "type_label"]
