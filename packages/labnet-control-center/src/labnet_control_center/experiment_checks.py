"""Check an experiment against the device contracts and the live network.

The report has two kinds of problems:

* errors stop "Export script": a method the device's contract doesn't have, a
  read used as a setting, an argument of the wrong type, a device that is now
  a different type. They never stop saving, because people sketch experiments
  before every detail is right;
* warnings are for people to read: a device that is offline or in use, a
  value outside the range its driver publishes, nothing recorded, a long run.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from google.protobuf.descriptor import FieldDescriptor

from labnet.contracts import RpcSpec, registry

from .contracts import facade_class_name, sweep_field
from .experiment_schema import ExperimentContent, Node, total_points


_KIND = {
    "RPC_PATTERN_SCALAR_READ": "read",
    "RPC_PATTERN_SCALAR_WRITE": "write",
    "RPC_PATTERN_UNARY_COMMAND": "command",
    "RPC_PATTERN_POLLED_SCALAR_STREAM": "stream",
}
_INT_RANGES = {
    FieldDescriptor.TYPE_INT32: (-(2**31), 2**31 - 1),
    FieldDescriptor.TYPE_SINT32: (-(2**31), 2**31 - 1),
    FieldDescriptor.TYPE_SFIXED32: (-(2**31), 2**31 - 1),
    FieldDescriptor.TYPE_UINT32: (0, 2**32 - 1),
    FieldDescriptor.TYPE_FIXED32: (0, 2**32 - 1),
    FieldDescriptor.TYPE_INT64: (-(2**63), 2**63 - 1),
    FieldDescriptor.TYPE_SINT64: (-(2**63), 2**63 - 1),
    FieldDescriptor.TYPE_SFIXED64: (-(2**63), 2**63 - 1),
    FieldDescriptor.TYPE_UINT64: (0, 2**64 - 1),
    FieldDescriptor.TYPE_FIXED64: (0, 2**64 - 1),
}
_TYPE_WORDS = {"float": "a number", "int": "a whole number", "bool": "true or false", "str": "text"}
# Rough time for one read over the network, for the run-time estimate.
_READ_SECONDS = 0.02
LONG_RUN_SECONDS = 3600


def kind_of(spec: RpcSpec) -> str:
    return _KIND.get(spec.pattern, "other")


def _base_type(python_type: str) -> str:
    return python_type.removesuffix(" | None")


def value_problem(spec: RpcSpec, name: str, python_type: str, value: Any) -> str | None:
    """Why ``value`` can't be passed as argument ``name``, or None."""

    base = _base_type(python_type)
    if base == "float":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif base == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
        if ok:
            descriptor = spec.request_type.DESCRIPTOR.fields_by_name.get(name)
            low, high = _INT_RANGES.get(descriptor.type if descriptor else -1, (-(2**63), 2**63 - 1))
            if not low <= value <= high:
                return f"{name} must be between {low} and {high}."
    elif base == "bool":
        ok = isinstance(value, bool)
    elif base == "str":
        ok = isinstance(value, str)
    else:
        return f"{name} is a {python_type}, which an experiment can't set."
    return None if ok else f"{name} needs {_TYPE_WORDS[base]}."


class _Report:
    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []

    def error(self, code: str, message: str, node: Node | None = None, item_id: str | None = None, field: str = "") -> None:
        self.errors.append(_item(code, message, node, item_id, field))

    def warn(self, code: str, message: str, node: Node | None = None, item_id: str | None = None, field: str = "") -> None:
        self.warnings.append(_item(code, message, node, item_id, field))


def _item(code: str, message: str, node: Node | None, item_id: str | None, field: str) -> dict[str, Any]:
    return {"code": code, "message": message, "node_id": node.id if node else None, "item_id": item_id, "field": field}


def _name(node: Node) -> str:
    return node.label or node.device_id or node.id


def check_experiment(content: ExperimentContent, devices: Mapping[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The report for one experiment. ``devices``: live devices by id (from
    /api/status, with hints); None when the network picture isn't available."""

    report = _Report()
    seconds = 2.0  # connecting and leasing
    per_point = content.run.settle_seconds  # the script waits this long at every point
    recorded = 0

    for node in content.nodes:
        if node.kind != "instrument" or not node.device_type:
            continue
        live = devices.get(node.device_id) if devices is not None else None
        _check_live(report, node, live, devices is not None)
        if not node.settings and not node.records:
            report.warn("unused", f"{_name(node)} has no settings or records, so the script won't use it.", node)
        contract = registry.find(node.device_type)
        if contract is None:
            report.error("no-contract", f"This Control Center has no contract for {node.device_type!r}.", node)
            continue
        if facade_class_name(contract) is None:
            report.error("no-class", f"labnet has no client class for {node.device_type!r}.", node)
        methods = {spec.python_name: spec for spec in contract.methods}
        hints = (live or {}).get("hints") or {}
        seen: set[tuple[str, Any]] = set()
        for setting in node.settings:
            spec = methods.get(setting.method)
            if spec is None:
                report.error("unknown-method", f"{node.device_type} has no method {setting.method}.", node, setting.id)
                continue
            if kind_of(spec) not in ("write", "command"):
                report.error(
                    "wrong-role", f"{setting.method} reads a value; record it instead of setting it.", node, setting.id
                )
                continue
            swept = None
            if setting.sweep is not None:
                allowed = sweep_field(spec)
                if allowed is None:
                    report.error("sweep", f"{setting.method} can't be swept.", node, setting.id, "sweep")
                elif setting.sweep.field != allowed:
                    report.error("sweep", f"Only {allowed} of {setting.method} can be swept.", node, setting.id, "sweep")
                else:
                    swept = allowed
                    _check_limits(report, node, setting.id, hints, f"{setting.method}.{allowed}",
                                  (setting.sweep.start, setting.sweep.stop), "sweep")
                    if setting.sweep.start == setting.sweep.stop:
                        report.warn("flat-sweep", f"The {setting.method} sweep starts and stops at the same value.",
                                    node, setting.id, "sweep")
            _check_args(report, node, setting.id, spec, setting.args, hints, skip=swept)
            key = (setting.method, setting.args.get("channel"))
            if key in seen:
                report.error("duplicate", f"{setting.method} is set twice on the same channel.", node, setting.id)
            seen.add(key)
        seen = set()
        for record in node.records:
            spec = methods.get(record.method)
            if spec is None:
                report.error("unknown-method", f"{node.device_type} has no method {record.method}.", node, record.id)
                continue
            kind = kind_of(spec)
            if kind not in ("read", "stream"):
                report.error("wrong-role", f"{record.method} doesn't return a reading; it can't be recorded.",
                             node, record.id)
                continue
            _check_args(report, node, record.id, spec, record.args, hints)
            samples = record.samples or content.run.samples
            if kind == "stream":
                per_point += samples / (record.sample_rate_hz or content.run.sample_rate_hz)
            else:
                per_point += samples * _READ_SECONDS
            recorded += 1
            key = (record.method, record.args.get("channel"))
            if key in seen:
                report.error("duplicate", f"{record.method} is recorded twice on the same channel.", node, record.id)
            seen.add(key)

    if not recorded:
        report.warn("nothing-recorded", "Nothing is recorded: the script would only apply the settings.")
    points = total_points(content)
    seconds += points * per_point
    if seconds > LONG_RUN_SECONDS:
        report.warn("long-run", f"The run would take about {seconds / 3600:.1f} h.")
    return {
        "exportable": not report.errors,
        "errors": report.errors,
        "warnings": report.warnings,
        "points": points,
        "estimated_seconds": round(seconds, 1),
    }


def _check_live(report: _Report, node: Node, live: dict[str, Any] | None, network_known: bool) -> None:
    if live is None:
        if network_known:
            report.warn("offline", f"{node.device_id} isn't on the network right now.", node)
        return
    if live.get("type") != node.device_type:
        report.error(
            "type-changed", f"{node.device_id} is now a {live.get('type_label') or live.get('type')}, "
            f"not a {node.device_type}.", node,
        )
    held = live.get("control_center")
    if held:
        report.warn("held", f"{node.device_id} is held by the Control Center page /{held.get('session')}; "
                    "pause it before running.", node)
    elif live.get("state") == "leased":
        report.warn("leased", f"{node.device_id} is leased by another client right now.", node)
    elif live.get("state") == "unavailable":
        report.warn("unavailable", f"{node.device_id} is unavailable: {live.get('detail')}.", node)


def _check_args(
    report: _Report,
    node: Node,
    item_id: str,
    spec: RpcSpec,
    args: Mapping[str, Any],
    hints: Mapping[str, Any],
    *,
    skip: str | None = None,
) -> None:
    fields = {field.name: field for field in spec.forwarded_field_specs}
    for name, value in args.items():
        field = fields.get(name)
        if field is None:
            report.error("argument", f"{spec.python_name} has no argument {name}.", node, item_id, f"args.{name}")
            continue
        if name == skip:
            report.error("argument", f"The sweep sets {name}; remove the fixed value.", node, item_id, f"args.{name}")
            continue
        problem = value_problem(spec, name, field.python_type, value)
        if problem:
            report.error("argument", problem, node, item_id, f"args.{name}")
            continue
        if name == "channel" and hints.get("channels") and value not in hints["channels"]:
            report.warn("channel", f"{node.device_id} has channels {', '.join(hints['channels'])}, not {value}.",
                        node, item_id, "args.channel")
        choices = (hints.get("choices") or {}).get(f"{spec.python_name}.{name}")
        if choices and isinstance(value, str) and value not in choices:
            report.warn("choices", f"{name} should be one of {', '.join(choices)}.", node, item_id, f"args.{name}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            _check_limits(report, node, item_id, hints, f"{spec.python_name}.{name}", (value,), f"args.{name}")
    for name, field in fields.items():
        if not field.optional and name not in args and name != skip:
            report.error("argument", f"{spec.python_name} needs {name}.", node, item_id, f"args.{name}")


def _check_limits(
    report: _Report, node: Node, item_id: str, hints: Mapping[str, Any], key: str, values: tuple[float, ...], field: str
) -> None:
    bounds = (hints.get("limits") or {}).get(key)
    if bounds and any(not bounds[0] <= value <= bounds[1] for value in values):
        report.warn("limits", f"{key.split('.')[0]} accepts {bounds[0]:g} to {bounds[1]:g} on {node.device_id}.",
                    node, item_id, field)


__all__ = ["check_experiment", "kind_of", "value_problem"]
