"""Turn an experiment into a Python script that runs it ("Export script").

The script follows examples/04_scan_two_devices.py: connect with
``LabClient.from_env()``, lease every device it uses (each by its device id),
apply the fixed settings upstream first (following the beams and cables),
step through the sweeps, record the readings at every point into a CSV, and
release everything in reverse order in ``finally``.

The output is deterministic (the same experiment version always gives the same
script) and safe to generate from untrusted experiments, which anyone using
the Control Center, or Photo-Agent, can write:

* method and argument names come from the device contracts, never the JSON;
* values are Python literals (``repr``, ``json.dumps``) checked with
  ``ast.literal_eval``;
* names, labels and descriptions appear only in comments and the docstring,
  stripped of control, bidirectional and zero-width characters;
* variable names are made from labels but reduced to plain identifiers.
"""

from __future__ import annotations

import ast
import builtins
import json
import keyword
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from labnet.contracts import DeviceContract, RpcSpec, registry

from .contracts import facade_class_name, facade_import
from .experiment_checks import check_experiment, kind_of
from .experiment_schema import ExperimentContent, Node, sweep_axes


class ScriptError(Exception):
    """The experiment can't be exported; ``errors`` are report items."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(errors[0]["message"] if errors else "The experiment can't be exported.")
        self.errors = errors


_SCRIPT_NAMES = {
    "main", "parser", "args", "argv", "central", "output", "writer", "point", "linspace", "summarize",
    "error", "sample", "readings", "row", "SETTLE_SECONDS", "TOTAL_POINTS", "argparse", "csv", "statistics",
    "time", "datetime", "timezone", "LabClient", "LabClientError", "annotations", "labnet",
}
_FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"}
_NUMBER_TYPES = {"float", "int"}


# ---- text and names --------------------------------------------------------------


def clean_text(text: str, limit: int = 200) -> str:
    """One line of free text, safe in a comment or the docstring."""

    kept = "".join(
        " " if character in "\r\n\t" else character
        for character in str(text)
        if character in "\r\n\t" or unicodedata.category(character) not in _FORBIDDEN_CATEGORIES
    )
    kept = re.sub(r"\s+", " ", kept).strip()
    kept = kept.replace("\\", "/").replace('"', "'")  # can't end the docstring or start an escape
    return kept[: limit - 1] + "…" if len(kept) > limit else kept


def _identifier(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    name = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")[:32].strip("_")
    if name and name[0].isdigit():
        name = "dev_" + name
    return name


class _Names:
    """Unique, safe Python identifiers."""

    def __init__(self, reserved: set[str]) -> None:
        self.reserved = set(reserved) | set(keyword.kwlist) | set(keyword.softkwlist) | set(dir(builtins))
        self.used: set[str] = set()

    def take(self, *candidates: str, constant: bool = False) -> str:
        base = next((name for name in map(_identifier, candidates) if name), "device")
        if constant:
            base = base.upper()
        if base in self.reserved:
            base += "_device"
        name = base
        number = 2
        while name in self.used or name in self.reserved:
            name = f"{base}_{number}"
            number += 1
        assert name.isidentifier()
        self.used.add(name)
        return name


def _literal(value: Any, python_type: str) -> str:
    """A Python literal for ``value``, of the argument's type."""

    base = python_type.replace(" | None", "")
    if base == "bool":
        text = "True" if value else "False"
    elif base == "int":
        text = repr(int(value))
    elif base == "float":
        text = repr(float(value))
    elif base == "str":
        value = str(value)
        text = json.dumps(value) if value.isascii() and value.isprintable() else repr(value)
    else:
        raise ValueError(f"{python_type} arguments can't be written into a script")
    if ast.literal_eval(text) != value and not (base == "float" and ast.literal_eval(text) == float(value)):
        raise ValueError(f"{value!r} can't be written as a literal")
    return text


def _unit_suffix(unit: str) -> str:
    if not unit or unit == "1":
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", unit.replace("%", "pct").replace("/", "_per_"))
    return "_" + cleaned if cleaned else ""


def _stem(method: str) -> str:
    for prefix in ("read_", "stream_", "get_", "set_"):
        if method.startswith(prefix):
            return method[len(prefix):]
    return method


# ---- order ------------------------------------------------------------------------


def _order(content: ExperimentContent) -> list[Node]:
    """Every block, upstream first along beams and cables; ties by position.

    Loops (a double-pass AOM) are broken at the block that comes first by
    position among those already reached.
    """

    nodes = {node.id: node for node in content.nodes}
    key = {node.id: (node.x or 0.0, node.y or 0.0, node.id) for node in content.nodes}
    incoming: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    outgoing: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for link in content.links:
        if link.source in nodes and link.target in nodes and link.source != link.target:
            incoming[link.target].add(link.source)
            outgoing[link.source].add(link.target)
    ordered: list[str] = []
    placed: set[str] = set()
    while len(ordered) < len(nodes):
        ready = [node_id for node_id in nodes if node_id not in placed and incoming[node_id] <= placed]
        if not ready:  # only loops remain
            remaining = [node_id for node_id in nodes if node_id not in placed]
            reached = [node_id for node_id in remaining if incoming[node_id] & placed]
            ready = reached or remaining
        choice = min(ready, key=lambda node_id: key[node_id])
        ordered.append(choice)
        placed.add(choice)
    return [nodes[node_id] for node_id in ordered]


def _paths(content: ExperimentContent, kind: str, names: Mapping[str, str]) -> list[str]:
    """Readable chains of beams (or cables), e.g. "Laser -> AOM 1 -> Mirror"."""

    links = [link for link in content.links if link.type == kind]
    if not links:
        return []
    outgoing: dict[str, list[str]] = {}
    targets = set()
    for link in links:
        outgoing.setdefault(link.source, []).append(link.target)
        targets.add(link.target)
    starts = [node.id for node in content.nodes if node.id in outgoing and node.id not in targets]
    starts = starts or [links[0].source]
    chains: list[str] = []

    def walk(node_id: str, chain: list[str]) -> None:
        if len(chains) >= 12:
            return
        if node_id in chain:
            chains.append(" -> ".join(names[item] for item in chain) + f" -> (back to {names[node_id]})")
            return
        chain = [*chain, node_id]
        nexts = outgoing.get(node_id, [])
        if not nexts:
            chains.append(" -> ".join(names[item] for item in chain))
        for target in nexts:
            walk(target, chain)

    for start in starts:
        walk(start, [])
    return chains


# ---- generation ----------------------------------------------------------------------


class _Device:
    def __init__(self, node: Node, contract: DeviceContract, class_name: str, variable: str) -> None:
        self.node = node
        self.contract = contract
        self.class_name = class_name
        self.variable = variable
        self.methods = {spec.python_name: spec for spec in contract.methods}


def _call(device: _Device, spec: RpcSpec, args: Mapping[str, Any], swept: tuple[str, str] | None = None) -> str:
    """``aom.set_frequency(80.0, channel="CH1")``; ``swept`` = (field, loop variable)."""

    fields = {field.name: field for field in spec.forwarded_field_specs}
    values: dict[str, str] = {name: _literal(value, fields[name].python_type) for name, value in args.items()}
    if swept:
        values[swept[0]] = swept[1]
    parts: list[str] = []
    first = spec.forwarded_fields[0] if spec.forwarded_fields else None
    if first == "value" and "value" in values:
        parts.append(values.pop("value"))
    for field in spec.forwarded_field_specs:  # contract order
        if field.name in values:
            parts.append(f"{field.name}={values[field.name]}")
    return f"{device.variable}.{spec.python_name}({', '.join(parts)})"


def generate_script(experiment: Mapping[str, Any]) -> str:
    content = ExperimentContent.model_validate(
        {key: experiment[key] for key in ("name", "description", "run", "nodes", "links") if key in experiment}
    )
    report = check_experiment(content, None)
    if report["errors"]:
        raise ScriptError(report["errors"])

    names = _Names(_SCRIPT_NAMES)
    order = _order(content)
    labels = {node.id: clean_text(node.label or node.device_id or node.kind.replace("_", " ").title(), 60) for node in content.nodes}
    devices: list[_Device] = []
    unused: list[Node] = []
    class_names: set[str] = set()
    for node in order:
        if node.kind != "instrument":
            continue
        if not node.settings and not node.records:
            unused.append(node)
            continue
        contract = registry.get(node.device_type)
        class_name = facade_class_name(contract)
        assert class_name is not None  # checked above
        class_names.add(class_name)
        devices.append(_Device(node, contract, class_name, ""))
    names.reserved |= class_names
    for device in devices:
        device.variable = names.take(device.node.label, device.node.device_id, device.node.id)
    for device in devices:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", device.node.device_id)

    # Sweeps, by loop (axis), and the columns they fill.
    sweeps_by_axis: dict[int, list[tuple[_Device, Any, RpcSpec, str, str]]] = {}
    columns: list[str] = ["point", "time_utc"]
    taken_columns = set(columns)

    def column(name: str) -> str:
        unique = name
        number = 2
        while unique in taken_columns:
            unique = f"{name}_{number}"
            number += 1
        taken_columns.add(unique)
        return unique

    column_bases: dict[str, str] = {}  # loop variable -> column name, before uniquifying
    for device in devices:
        for setting in device.node.settings:
            if setting.sweep is None:
                continue
            spec = device.methods[setting.method]
            channel = setting.args.get("channel")
            base = f"{device.variable}_{_stem(setting.method)}" + (f"_{_identifier(str(channel))}" if channel else "")
            loop_variable = names.take(base)
            constant = names.take(base, constant=True)
            unit = spec.default_unit if setting.sweep.field == "value" else ""
            sweeps_by_axis.setdefault(setting.sweep.axis, []).append((device, setting, spec, loop_variable, constant))
            column_bases[loop_variable] = base + _unit_suffix(unit)
    # Setpoint columns in loop order (outermost first), as the rows are written.
    for axis in sorted(sweeps_by_axis):
        for _device, _setting, _spec, loop_variable, _constant in sweeps_by_axis[axis]:
            columns.append(column(column_bases[loop_variable]))

    records: list[tuple[_Device, Any, RpcSpec, str, int]] = []
    record_columns: list[str] = []
    uses_summary = False
    for device in devices:
        for record in device.node.records:
            spec = device.methods[record.method]
            channel = record.args.get("channel")
            base = f"{device.variable}_{_stem(record.method)}" + (f"_{_identifier(str(channel))}" if channel else "")
            variable = names.take(base)
            samples = record.samples or content.run.samples
            records.append((device, record, spec, variable, samples))
            suffix = _unit_suffix(spec.default_unit)
            if samples > 1:
                uses_summary = True
                record_columns += [column(base + suffix + "_mean"), column(base + suffix + "_std")]
            else:
                record_columns.append(column(base + suffix))
    columns += record_columns

    axes = sweep_axes(content)
    total = 1
    for points in axes.values():
        total *= points
    output_name = content.run.output_name
    settle = content.run.settle_seconds

    # ---- header ----
    out: list[str] = []
    write = out.append
    title = clean_text(content.name, 120) or "Experiment"
    write(f'"""{title}')
    write("")
    write(
        f"Experiment {experiment.get('id', 'unsaved')}, version {experiment.get('version', 0)}, "
        f"saved {clean_text(str(experiment.get('updated_at', 'never')), 40)}."
    )
    write("Generated by the PQT Lab Control Center from that version; edit freely.")
    write(f"Run:    python {output_name}.py [--output {output_name}.csv]")
    write(f"Writes: one CSV row per point ({total:,} {'point' if total == 1 else 'points'}): "
          + ("setpoints, then readings." if records else "setpoints only (nothing is recorded)."))
    write('"""')
    write("")
    description = [clean_text(line, 100) for line in content.description.splitlines() if line.strip()]
    for line in description[:20]:
        write(f"# {line}")
    authors = [
        f"Created by {clean_text(experiment['created_by'], 64)}" if experiment.get("created_by") else "",
        f"last saved by {clean_text(experiment['updated_by'], 64)}" if experiment.get("updated_by") else "",
    ]
    if any(authors):
        write("# " + "; ".join(part for part in authors if part) + ".")
    for kind, title_text in (("beam", "Beam path"), ("signal", "Signal cables")):
        chains = _paths(content, kind, labels)
        if chains:
            write(f"# {title_text}{'s' if kind == 'beam' and len(chains) > 1 else ''}:")
            for chain in chains:
                write(f"#   {chain}")
    for link in content.links:
        if link.type == "signal" and link.label:
            write(f"#   ({labels[link.source]} -> {labels[link.target]}: {clean_text(link.label, 40)})")
    if devices:
        write("# Devices, leased in this order:")
        for device in devices:
            write(f"#   {device.variable} = {device.node.device_id} ({device.class_name})")
    if unused:
        write("# On the diagram but not used (nothing set or recorded): "
              + ", ".join(clean_text(node.device_id or node.id, 64) for node in unused))
    write("")
    write("from __future__ import annotations")
    write("")
    write("import argparse")
    write("import csv")
    if uses_summary:
        write("import statistics")
    if settle:
        write("import time")
    write("from datetime import datetime, timezone")
    write("")
    write("from labnet import LabClient, LabClientError")
    facade_modules: dict[str, set[str]] = {}
    for device in devices:
        module, name = facade_import(device.contract)  # type: ignore[misc]  # checked above
        facade_modules.setdefault(module, set()).add(name)
    for module, names_in_module in sorted(facade_modules.items()):
        write(f"from {module} import {', '.join(sorted(names_in_module))}")
    write("")
    write("")
    if axes:
        write("def linspace(start: float, stop: float, points: int) -> list[float]:")
        write('    """`points` evenly spaced values from start to stop; both ends are exact."""')
        write("    step = (stop - start) / (points - 1)")
        write("    return [start + step * index for index in range(points - 1)] + [stop]")
        write("")
        write("")
    if uses_summary:
        write("def summarize(values: list[float]) -> tuple[float, float]:")
        write('    """Mean and sample standard deviation of repeated readings."""')
        write("    return statistics.fmean(values), statistics.stdev(values)")
        write("")
        write("")
    if settle:
        write(f"SETTLE_SECONDS = {settle!r}  # wait after each new setpoint")
    for axis in sorted(sweeps_by_axis):
        for device, setting, spec, _loop_variable, constant in sweeps_by_axis[axis]:
            unit = spec.default_unit if setting.sweep.field == "value" and spec.default_unit != "1" else ""
            sweep = setting.sweep
            write(
                f"{constant} = linspace({float(sweep.start)!r}, {float(sweep.stop)!r}, {int(sweep.points)!r})"
                f"  # {device.variable}.{setting.method}{', ' + unit if unit else ''} (loop {list(sorted(sweeps_by_axis)).index(axis) + 1})"
            )
    write(f"TOTAL_POINTS = {total!r}")
    write("")
    write("")

    # ---- main ----
    write("def main(argv: list[str] | None = None) -> None:")
    write('    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0].replace("%", "%%"))')
    write(f"    parser.add_argument(\"--output\", default={json.dumps(output_name + '.csv')})")
    write("    args = parser.parse_args(argv)")
    write("")
    write("    central = None")
    for device in devices:
        write(f"    {device.variable} = None")
    write("    output = None")
    write("    try:")
    write("        central = LabClient.from_env()")
    if devices:
        write("        # Lease every device first: a busy one stops the run before anything changes.")
        for device in devices:
            write(
                f"        {device.variable} = {device.class_name}(device_id={json.dumps(device.node.device_id)}, client=central)"
            )
        write(f"        print({json.dumps('Leased ' + ', '.join(device.node.device_id for device in devices))})")
    fixed: list[str] = []
    for device in devices:
        for setting in device.node.settings:
            if setting.sweep is not None:
                continue
            spec = device.methods[setting.method]
            unit = spec.default_unit if kind_of(spec) == "write" and spec.default_unit != "1" else ""
            fixed.append(f"        {_call(device, spec, setting.args)}" + (f"  # {unit}" if unit else ""))
    if fixed:
        write("")
        write("        # Fixed settings, upstream first.")
        out.extend(fixed)
    write("")
    write(f"        output = open(args.output, \"w\", newline=\"\", encoding=\"utf-8\")")
    write("        writer = csv.writer(output)")
    write(f"        writer.writerow({json.dumps(columns)})")
    write("        output.flush()")
    write("")
    write("        point = 0")

    indent = "        "
    loop_values: list[str] = []
    for axis in sorted(sweeps_by_axis):
        group = sweeps_by_axis[axis]
        if len(group) == 1:
            _device, _setting, _spec, loop_variable, constant = group[0]
            write(f"{indent}for {loop_variable} in {constant}:")
        else:
            variables = ", ".join(item[3] for item in group)
            constants = ", ".join(item[4] for item in group)
            write(f"{indent}for {variables} in zip({constants}):")
        indent += "    "
        for device, setting, spec, loop_variable, _constant in group:
            args = {name: value for name, value in setting.args.items() if name != setting.sweep.field}
            write(f"{indent}{_call(device, spec, args, swept=(setting.sweep.field, loop_variable))}")
            loop_values.append(loop_variable)
    if settle:
        write(f"{indent}time.sleep(SETTLE_SECONDS)")
    row_values: list[str] = []
    for device, record, spec, variable, samples in records:
        if kind_of(spec) == "stream":
            rate = record.sample_rate_hz or content.run.sample_rate_hz
            call = _call(device, spec, record.args)
            call = call[:-1] + (", " if call[-2] != "(" else "") + f"sample_rate_hz={float(rate)!r}, sample_count={samples!r})"
            write(f"{indent}{variable} = [sample.value for sample in {call}]")
        elif samples == 1:
            write(f"{indent}{variable} = [{_call(device, spec, record.args)}]")
        else:
            write(f"{indent}{variable} = [{_call(device, spec, record.args)} for _ in range({samples!r})]")
        row_values.append(f"*summarize({variable})" if samples > 1 else f"{variable}[0]")
    write(f"{indent}point += 1")
    write(f"{indent}writer.writerow([")
    write(f"{indent}    point,")
    write(f"{indent}    datetime.now(timezone.utc).isoformat(timespec=\"milliseconds\"),")
    for value in [*loop_values, *row_values]:
        write(f"{indent}    {value},")
    write(f"{indent}])")
    write(f"{indent}output.flush()")
    write(f"{indent}print(f\"point {{point}}/{{TOTAL_POINTS}}\")")
    write("")
    write("        print(f\"Wrote {args.output}\")")
    write("    except LabClientError as error:")
    write("        raise SystemExit(f\"LabNet error: {error}\")")
    write("    finally:")
    write("        # Reverse order of creation: rows so far are kept and every lease is released.")
    write("        if output is not None:")
    write("            output.close()")
    for device in reversed(devices):
        write(f"        if {device.variable} is not None:")
        write(f"            {device.variable}.close()")
    write("        if central is not None:")
    write("            central.close()")
    write("")
    write("")
    write('if __name__ == "__main__":')
    write("    main()")
    script = "\n".join(out) + "\n"
    compile(script, f"{output_name}.py", "exec")  # never hand out a script that doesn't parse
    return script


__all__ = ["ScriptError", "clean_text", "generate_script"]
