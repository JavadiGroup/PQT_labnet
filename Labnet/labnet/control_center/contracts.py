"""Describe each device's callable methods from its generated contract.

The Control Center shows the same method names and arguments a Python client
uses (``device.read_wavelength(channel=...)``), so nothing here is written per
device family.
"""

from __future__ import annotations

import re
from typing import Any

import labnet
from labnet.grpc.generated.device_contracts import DEVICE_CONTRACT_BY_TYPE, DeviceContract, RpcSpec


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
# What a call returns through the synchronous facades (DummyAOM(...).read_x()),
# the restricted script box and generated scripts. AsyncLabClient's devices
# return a Measurement from reads and writes instead.
_RETURNS_BY_KIND = {
    "read": "float",
    "write": "None",
    "command": "result",
    "stream": "stream of Measurement (.value)",
}
# How a method can be used in an experiment (see experiment_checks.py).
_ROLE_BY_KIND = {"read": "record", "stream": "record", "write": "setting", "command": "command"}
_NUMBER_TYPES = {"float", "int"}
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def sweep_field(spec: RpcSpec) -> str | None:
    """The argument a sweep varies: a write's ``value``, or a command's only
    required number (e.g. ``go_to_wavelength_nm(wavelength_nm)``)."""

    kind = _KIND_BY_PATTERN.get(spec.pattern)
    if kind == "write":
        return "value"
    if kind == "command":
        required = [field for field in spec.forwarded_field_specs if not field.optional]
        if len(required) == 1 and required[0].python_type in _NUMBER_TYPES:
            return required[0].name
    return None


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
        # The same arguments, structured for forms and checks.
        "fields": [
            {"name": field.name, "type": field.python_type, "optional": field.optional}
            for field in spec.forwarded_field_specs
        ],
        "returns": _RETURNS_BY_KIND.get(kind, "result"),
        "unit": spec.default_unit,
        "role": _ROLE_BY_KIND.get(kind, "other"),
        "sweep_field": sweep_field(spec),
    }


def describe_device_type(device_type: str) -> list[dict[str, Any]] | None:
    """Methods for ``device_type``, or None if this build has no contract for it."""

    contract = DEVICE_CONTRACT_BY_TYPE.get(device_type)
    if contract is None:
        return None
    return [describe_method(spec) for spec in contract.methods]


def facade_class_name(contract: DeviceContract) -> str | None:
    """The class a client script uses for this family: ``DummyAOMService`` ->
    ``DummyAOM``. None if ``labnet`` exports no class by that name."""

    name = contract.service_name.removesuffix("Service")
    return name if name in labnet.__all__ else None


_HINT_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\.[a-z][a-z0-9_]{0,63}")
_HINT_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.:+/-]{0,31}")


def clean_hints(metadata: Any) -> dict[str, Any]:
    """The ``hints`` a driver publishes in ``metadata()``, checked and size-limited:

    ``{"channels": ["CH1", "CH2"],
       "limits": {"set_frequency.value": [60.0, 100.0]},
       "choices": {"set_waveform.waveform": ["sine", "square"]}}``

    Keys are ``<method>.<argument>``. Anything malformed is dropped.
    """

    raw = metadata.get("hints") if isinstance(metadata, dict) else None
    if not isinstance(raw, dict):
        return {}
    hints: dict[str, Any] = {}
    channels = raw.get("channels")
    if isinstance(channels, list):
        clean = [item for item in channels[:16] if isinstance(item, str) and _HINT_TEXT.fullmatch(item)]
        if clean:
            hints["channels"] = clean
    limits = raw.get("limits")
    if isinstance(limits, dict):
        clean_limits = {}
        for key, value in list(limits.items())[:64]:
            if (
                isinstance(key, str) and _HINT_KEY.fullmatch(key)
                and isinstance(value, list) and len(value) == 2
                and all(isinstance(bound, (int, float)) and not isinstance(bound, bool) for bound in value)
                and all(abs(bound) < 1e300 for bound in value) and value[0] <= value[1]
            ):
                clean_limits[key] = [float(value[0]), float(value[1])]
        if clean_limits:
            hints["limits"] = clean_limits
    choices = raw.get("choices")
    if isinstance(choices, dict):
        clean_choices = {}
        for key, value in list(choices.items())[:64]:
            if isinstance(key, str) and _HINT_KEY.fullmatch(key) and isinstance(value, list):
                options = [item for item in value[:32] if isinstance(item, str) and _HINT_TEXT.fullmatch(item)]
                if options:
                    clean_choices[key] = options
        if clean_choices:
            hints["choices"] = clean_choices
    return hints


def method_line(method: dict[str, Any]) -> str:
    """One method for an agent to read: ``name(params)`` → returns [role, unit]."""

    role = method.get("role", method["kind"])
    if method.get("sweep_field"):
        role += f", sweepable ({method['sweep_field']})"
    unit = method.get("unit")
    if unit and unit != "1":
        role += f", {unit}"
    return f"`{method['name']}({', '.join(method['parameters'])})` → {method['returns']} [{role}]"


def hint_lines(hints: dict[str, Any]) -> list[str]:
    """A driver's channels, ranges and choices as Markdown list items."""

    lines = []
    if hints.get("channels"):
        lines.append("- Channels: " + ", ".join(hints["channels"]))
    for key, (low, high) in (hints.get("limits") or {}).items():
        lines.append(f"- Range of {key}: {low:g} to {high:g}")
    for key, options in (hints.get("choices") or {}).items():
        lines.append(f"- Choices for {key}: " + ", ".join(options))
    return lines


def type_label(device_type: str) -> str:
    """``simulated_wavemeter`` -> ``Simulated Wavemeter``; ``dummy_aom`` -> ``Dummy AOM``.

    Known types take their words from the service name, which keeps acronyms.
    """

    contract = DEVICE_CONTRACT_BY_TYPE.get(device_type)
    if contract is not None:
        words = _WORDS.findall(contract.service_name.removesuffix("Service"))
        if words:
            return " ".join(words)
    return " ".join(part.capitalize() for part in device_type.split("_") if part) or "Unknown"


__all__ = [
    "clean_hints",
    "describe_device_type",
    "describe_method",
    "facade_class_name",
    "hint_lines",
    "method_line",
    "sweep_field",
    "type_label",
]
