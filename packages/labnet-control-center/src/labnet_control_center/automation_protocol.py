"""Protocols: what an agent asks to do to lab hardware, checked before a person sees it.

The agent writes ``Protocols/<task stem>.protocol.json`` (and free prose in
``Protocols/<task stem>.protocol.md``); the runner uploads both and asks the
Control Center to check the JSON. Everything in it is untrusted, so the checks
here are strict and every refusal says what to change:

* the file: UTF-8 JSON of at most 64 KiB; no repeated keys, no NaN or
  Infinity, no unknown keys anywhere;
* the devices: each one on a canvas linked to the project, allowed for
  automation there, on the network now, and of the type it says;
* the methods: each one in that type's contract, with ``max_calls`` and
  ``max_rate_hz``; streams also with sample caps, and nothing else with them;
* the arguments: bounds for every argument a method sends. An optional one may
  be left out, and then may never be sent; one that is bounded must be sent in
  every call (else the driver's default, e.g. channel CH1, would be driven
  although only CH2 was approved). Numbers as ``min``/``max`` (``max_step``
  optional), booleans and strings as ``choices``; never a write's ``unit``. The
  limits, choices and channels a device publishes (its driver's, narrowed by the
  Lab PC inventory's ``site_limits``) are hard limits: bounds must lie inside
  them. No limit, no automation: a number the device publishes no limit for
  can't be bounded at all, so a person must set a site limit first;
* ``max_step`` only where the gateway can read where the field is now (a
  ``set_<x>`` with a matching ``read_<x>`` that takes the same choice
  arguments, see :func:`step_reader`): steps are measured from that reading;
* the safe state: up to 16 calls the protocol itself allows, each giving every
  bounded argument, applied when the grant ends.

A protocol that passes is *normalized* (defaults filled in, bounds as floats,
counts as integers) and serialized canonically; the sha256 of that text is what
a person approves. The review a person reads is built here from the normalized
copy and the device contracts, never from the agent's prose, which is shown
separately as the agent's own words.

    normalized, problems = validate_protocol(raw, project=..., live_devices=...,
                                             canvas_devices=..., max_minutes=120, task="Tasks/Task1.md")
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any

from labnet.contracts import DeviceContract, RpcSpec, registry

from .contracts import describe_method, type_label
from .device_calls import find_method


SCHEMA_VERSION = 1
MAX_PROTOCOL_BYTES = 64 * 1024
MAX_PROSE_BYTES = 64 * 1024
DEFAULT_MAX_MINUTES = 120.0
MIN_MINUTES = 1.0
MAX_TITLE_CHARACTERS = 120
MAX_DEVICES = 16
MAX_METHODS = 64  # per device
MAX_CHOICES = 32
MAX_CHOICE_CHARACTERS = 64
MAX_SAFE_STATE = 16
MAX_CALLS = 100_000
MAX_RATE_HZ = 100.0
MAX_SAMPLE_RATE_HZ = 200.0
MAX_SAMPLES = 100_000
MAX_MAGNITUDE = 1e15  # larger numbers are refused (no instrument setting is this large)
MAX_PROBLEMS = 100
MAX_NOTE_CHARACTERS = 2000
PROTOCOLS_FOLDER = "Protocols"
# A stream's own controls: limited by "stream", never bounded as arguments.
STREAM_CONTROLS = ("sample_rate_hz", "sample_count", "duration_seconds")

_TOP_KEYS = ("schema_version", "task", "title", "duration_minutes", "devices", "safe_state")
_TOP_REQUIRED = ("schema_version", "task", "title", "duration_minutes", "devices")
_DEVICE_KEYS = ("device_id", "device_type", "methods")
_METHOD_KEYS = ("args", "max_calls", "max_rate_hz", "stream")
_STREAM_KEYS = ("max_sample_rate_hz", "max_samples")
_NUMBER_KEYS = ("min", "max", "max_step")
_CHOICE_KEYS = ("choices",)
_SAFE_KEYS = ("device_id", "method", "args")
_NUMBER_TYPES = ("float", "int")
# Unicode categories never allowed in text: controls, format characters
# (bidirectional overrides, zero-width), separators, private and unassigned.
_FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"}
# Combining marks (Mn, Me) on one base character: more than this can stack a
# title or a choice over the rest of the review ("Zalgo" text).
MAX_COMBINING_MARKS = 2
_COMBINING_CATEGORIES = {"Mn", "Me"}
SCALAR_READ = "RPC_PATTERN_SCALAR_READ"

NetworkSource = Callable[[], Awaitable[dict[str, Any]]]


# ---- the files ------------------------------------------------------------------------------


def protocol_files(task: str) -> tuple[PurePosixPath, PurePosixPath]:
    """``Tasks/Task1.md`` → (``Protocols/Task1.protocol.json``, ``Protocols/Task1.protocol.md``)."""

    stem = PurePosixPath(task).stem
    return (PurePosixPath(PROTOCOLS_FOLDER, f"{stem}.protocol.json"),
            PurePosixPath(PROTOCOLS_FOLDER, f"{stem}.protocol.md"))


class _Object(dict):
    """A JSON object that remembers the keys it had more than once."""

    duplicates: tuple[str, ...] = ()


class _NonFinite:
    """NaN, Infinity or -Infinity in the file (JSON has no such numbers)."""

    def __init__(self, text: str) -> None:
        self.text = text


def _pairs(pairs: list[tuple[str, Any]]) -> _Object:
    found = _Object()
    repeated = []
    for key, value in pairs:
        if key in found:
            repeated.append(key)
        else:
            found[key] = value
    if repeated:
        found.duplicates = tuple(dict.fromkeys(repeated))
    return found


def parse_protocol(data: bytes) -> tuple[Any, list[dict[str, str]]]:
    """(the parsed JSON, problems): problems if it is too large or not JSON at all."""

    if len(data) > MAX_PROTOCOL_BYTES:
        return None, [_problem("", f"The protocol is {len(data)} bytes; it may be at most "
                                   f"{MAX_PROTOCOL_BYTES // 1024} KiB. Shorten it.")]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, [_problem("", f"The protocol isn't UTF-8 text (byte {exc.start}). Save it as UTF-8 JSON.")]
    try:
        parsed = json.loads(text, object_pairs_hook=_pairs, parse_constant=_NonFinite)
    except json.JSONDecodeError as exc:
        return None, [_problem("", f"The protocol isn't valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}. "
                                   "Comments and trailing commas aren't allowed in JSON.")]
    except (ValueError, RecursionError) as exc:
        return None, [_problem("", f"The protocol isn't valid JSON ({type(exc).__name__}: it is nested too deeply "
                                   "or has a number too long to read).")]
    return parsed, []


def canonical(protocol: dict[str, Any]) -> str:
    """The text a person signs: sorted keys, no spaces, UTF-8."""

    return json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def protocol_sha256(protocol: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(protocol).encode("utf-8")).hexdigest()


async def network_devices(network: NetworkSource) -> dict[str, dict[str, Any]] | None:
    """The devices on the network now (id → device as /api/status shows it), or None
    when Central can't be reached: a protocol is never checked against a stale picture."""

    try:
        snapshot = await network()
    except Exception:  # noqa: BLE001 - any failure means "can't check now"
        return None
    if not snapshot.get("central", {}).get("ok"):
        return None
    return {device["id"]: device for pc in snapshot.get("lab_pcs", []) for device in pc.get("devices", [])}


# ---- problems -----------------------------------------------------------------------------------


def _problem(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


class _Problems:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, path: str, message: str) -> None:
        if len(self.items) < MAX_PROBLEMS:
            self.items.append(_problem(path, message))
        elif len(self.items) == MAX_PROBLEMS:
            self.items.append(_problem("", "More problems were found; fix these and submit again."))

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)


def _key(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _describe(value: Any) -> str:
    if isinstance(value, _NonFinite):
        return value.text
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, str):
        return "text"
    return "a number"


def _object(value: Any, path: str, allowed: tuple[str, ...], required: tuple[str, ...], problems: _Problems,
            what: str) -> bool:
    """Check ``value`` is a JSON object with only ``allowed`` keys and every ``required`` one."""

    if not isinstance(value, dict):
        problems.add(path, f"{what} must be a JSON object {{...}}, not {_describe(value)}.")
        return False
    for key in getattr(value, "duplicates", ()):
        problems.add(_key(path, key), f"The key {key!r} appears more than once in this object; give it once.")
    for key in value:
        if key not in allowed:
            problems.add(_key(path, str(key)), f"Unknown key {key!r}. Allowed here: {', '.join(allowed)}.")
    for key in required:
        if key not in value:
            problems.add(_key(path, key), f"{what} needs {key!r}.")
    return True


def _number(value: Any, path: str, problems: _Problems, name: str) -> float | None:
    """A finite JSON number (booleans aren't numbers), or None after adding a problem."""

    if isinstance(value, _NonFinite):
        problems.add(path, f"{name} is {value.text}; write a finite number.")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.add(path, f"{name} must be a number, not {_describe(value)}.")
        return None
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number) or abs(number) > MAX_MAGNITUDE:
        problems.add(path, f"{name} must be a finite number no larger than {MAX_MAGNITUDE:g} in size.")
        return None
    return number


def _whole(value: Any, path: str, problems: _Problems, name: str, low: int, high: int) -> int | None:
    number = _number(value, path, problems, name)
    if number is None:
        return None
    if not number.is_integer() or not low <= number <= high:
        problems.add(path, f"{name} must be a whole number from {low} to {high}, not {value!r}.")
        return None
    return int(number)


def _rate(value: Any, path: str, problems: _Problems, name: str, high: float) -> float | None:
    number = _number(value, path, problems, name)
    if number is None:
        return None
    if not 0 < number <= high:
        problems.add(path, f"{name} must be more than 0 and at most {high:g} (per second), not {value!r}.")
        return None
    return number


def _text(value: Any, path: str, problems: _Problems, name: str, limit: int) -> str | None:
    if not isinstance(value, str):
        problems.add(path, f"{name} must be text, not {_describe(value)}.")
        return None
    marks = 0
    for character in value:
        category = unicodedata.category(character)
        if category in _FORBIDDEN_CATEGORIES:
            problems.add(path, f"{name} contains a control or invisible character (U+{ord(character):04X}); remove it.")
            return None
        marks = marks + 1 if category in _COMBINING_CATEGORIES else 0
        if marks > MAX_COMBINING_MARKS:
            problems.add(path, f"{name} stacks more than {MAX_COMBINING_MARKS} combining marks on one character "
                               f"(U+{ord(character):04X}); remove them.")
            return None
    text = value.strip()
    if not text:
        problems.add(path, f"{name} must not be empty.")
        return None
    if len(text) > limit:
        problems.add(path, f"{name} is {len(text)} characters; it may be at most {limit}.")
        return None
    return text


def _list(value: Any, path: str, problems: _Problems, name: str, low: int, high: int) -> list[Any] | None:
    if not isinstance(value, list):
        problems.add(path, f"{name} must be a list [...], not {_describe(value)}.")
        return None
    if not low <= len(value) <= high:
        problems.add(path, f"{name} must have {low} to {high} items; it has {len(value)}.")
        return None
    return value


def _field_type(field: dict[str, Any]) -> str:
    return field["type"].replace(" | None", "").strip()


def _number_type(python_type: str) -> bool:
    return python_type.replace("| None", "").strip() in _NUMBER_TYPES


def step_reader(contract: DeviceContract, method: str, field: str) -> tuple[RpcSpec | None, str]:
    """The method that reads where ``method``'s ``field`` is now, or (None, why not).

    ``set_<x>`` is read by ``read_<x>`` (a scalar read). The setter must have no
    other number argument (the reading is one value), the reader may take only
    the setter's choice arguments (same names and types), and must take every
    one of them (so the reading is of what the call would change). The gateway
    reads with the call's own choice arguments, e.g. ``read_amplitude(channel="CH2")``.
    """

    setter = find_method(contract, method)
    reader_name = "read_" + method.removeprefix("set_") if method.startswith("set_") else None
    reader = find_method(contract, reader_name) if reader_name else None
    if reader_name is None:
        return None, "only a set_<x> method with a matching read_<x> can be read back"
    if setter is None or reader is None or reader.pattern != SCALAR_READ:
        return None, f"it has no {reader_name}() to read it with"
    setter_fields = {item.name: item for item in setter.forwarded_field_specs}
    numbers = [name for name, item in setter_fields.items() if _number_type(item.python_type)]
    if numbers != [field]:
        return None, f"{method} sets more than one number, and {reader_name}() reads one"
    reader_fields = {item.name: item for item in reader.forwarded_field_specs}
    for name, item in reader_fields.items():
        mine = setter_fields.get(name)
        if mine is None or _number_type(item.python_type) or \
                mine.python_type.replace(" | None", "") != item.python_type.replace(" | None", ""):
            return None, f"{reader_name}() takes {name}, which {method} doesn't send the same way"
    for name in setter_fields:
        if name != field and name not in reader_fields:
            return None, f"{reader_name}() doesn't take {name}, so its reading may not be what {method} changes"
    return reader, ""


# ---- the protocol ---------------------------------------------------------------------------------


def validate_protocol(
    raw: Any,
    *,
    project: dict[str, Any],
    live_devices: dict[str, dict[str, Any]],
    canvas_devices: list[dict[str, Any]],
    max_minutes: float = DEFAULT_MAX_MINUTES,
    task: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """(the normalized protocol, []) or (None, problems), each ``{"path", "message"}``.

    ``project`` is the project record (its ``allowed_devices``), ``live_devices`` the
    network now (see :func:`network_devices`), ``canvas_devices`` the instruments on
    the project's linked canvases (``AutomationStore.snapshot_devices``), ``task``
    the run's task, which the protocol must name.
    """

    problems = _Problems()
    if not _object(raw, "", _TOP_KEYS, _TOP_REQUIRED, problems, "The protocol"):
        return None, problems.items

    version = raw.get("schema_version")
    if "schema_version" in raw and (isinstance(version, bool) or version != SCHEMA_VERSION
                                    or not isinstance(version, int)):
        shown = repr(version) if isinstance(version, (int, float)) and not isinstance(version, bool) else _describe(version)
        problems.add("schema_version", f"schema_version must be {SCHEMA_VERSION}, not {shown}.")

    named_task = None
    if "task" in raw:
        named_task = _text(raw["task"], "task", problems, "task", 240)
        if named_task is not None and task is not None and named_task != task:
            problems.add("task", f"task must be {task!r}, the task this run works on.")

    title = _text(raw["title"], "title", problems, "title", MAX_TITLE_CHARACTERS) if "title" in raw else None

    duration = None
    if "duration_minutes" in raw:
        duration = _number(raw["duration_minutes"], "duration_minutes", problems, "duration_minutes")
        if duration is not None and not MIN_MINUTES <= duration <= max_minutes:
            problems.add("duration_minutes", f"duration_minutes must be from {MIN_MINUTES:g} to {max_minutes:g} "
                                             "(this Control Center's longest grant).")
            duration = None

    devices: list[dict[str, Any]] = []
    declared: dict[str, tuple[int, dict[str, Any] | None]] = {}  # device id → (index, normalized or None)
    if "devices" in raw:
        listed = _list(raw["devices"], "devices", problems, "devices", 1, MAX_DEVICES)
        allowed = set(project.get("allowed_devices", []))
        canvas = {item["device_id"]: item for item in canvas_devices}
        for index, item in enumerate(listed or []):
            path = f"devices[{index}]"
            device = _device(item, path, problems, allowed=allowed, canvas=canvas, live=live_devices,
                             declared=declared, index=index)
            if device is not None:
                devices.append(device)

    safe_state: list[dict[str, Any]] = []
    raw_safe = raw.get("safe_state", [])
    listed_safe = _list(raw_safe, "safe_state", problems, "safe_state", 0, MAX_SAFE_STATE)
    for index, item in enumerate(listed_safe or []):
        call = _safe_call(item, f"safe_state[{index}]", problems, declared)
        if call is not None:
            safe_state.append(call)

    if problems:
        return None, problems.items
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "task": named_task,
        "title": title,
        "duration_minutes": duration,
        "devices": devices,
        "safe_state": safe_state,
    }
    return normalized, []


def _device(
    raw: Any, path: str, problems: _Problems, *, allowed: set[str], canvas: dict[str, dict[str, Any]],
    live: dict[str, dict[str, Any]], declared: dict[str, tuple[int, dict[str, Any] | None]], index: int,
) -> dict[str, Any] | None:
    before = len(problems)
    if not _object(raw, path, _DEVICE_KEYS, _DEVICE_KEYS, problems, "A device"):
        return None
    device_id = _text(raw.get("device_id"), _key(path, "device_id"), problems, "device_id", 64) \
        if "device_id" in raw else None
    if device_id is not None:
        if device_id in declared:
            problems.add(_key(path, "device_id"), f"{device_id} is already listed as devices[{declared[device_id][0]}]; "
                                                  "list each device once, with all its methods under it.")
            device_id = None
        else:
            declared[device_id] = (index, None)
    found = live.get(device_id) if device_id else None
    if device_id is not None:
        if device_id not in canvas:
            problems.add(_key(path, "device_id"), f"{device_id} isn't on any experiment linked to this project; use a "
                                                  "device listed in Experiment_layout/DEVICES.md.")
        elif device_id not in allowed:
            problems.add(_key(path, "device_id"), f"{device_id} isn't allowed for automation in this project (DEVICES.md "
                                                  "says which are; a person allows devices in the project's settings).")
        if found is None:
            problems.add(_key(path, "device_id"), f"{device_id} isn't on the network now, so it can't be checked or "
                                                  "used; ask a person to bring it online.")
    device_type = _text(raw.get("device_type"), _key(path, "device_type"), problems, "device_type", 64) \
        if "device_type" in raw else None
    live_type = found.get("type") if found else None
    if device_type is not None and live_type is not None and device_type != live_type:
        problems.add(_key(path, "device_type"), f"{device_id} is a {live_type} on the network, not {device_type}; "
                                                f"write \"device_type\": \"{live_type}\".")
    canvas_type = canvas.get(device_id, {}).get("device_type") if device_id else None
    if canvas_type and live_type and canvas_type != live_type:
        problems.add(_key(path, "device_type"), f"The linked experiment says {device_id} is a {canvas_type}, but the "
                                                f"network says {live_type}; a person must fix the experiment first.")
    contract_type = live_type or device_type
    contract = registry.find(contract_type) if contract_type else None
    if contract_type and contract is None:
        problems.add(_key(path, "device_type"), f"This Control Center has no contract for {contract_type}, so it "
                                                "can't be automated.")
    methods: dict[str, dict[str, Any]] = {}
    if "methods" in raw and _object(raw["methods"], _key(path, "methods"), tuple(raw["methods"]) if isinstance(
            raw["methods"], dict) else (), (), problems, "methods"):
        if not 1 <= len(raw["methods"]) <= MAX_METHODS:
            problems.add(_key(path, "methods"), f"methods must list 1 to {MAX_METHODS} methods; it has "
                                                f"{len(raw['methods'])}.")
        elif contract is not None:
            specs = {spec.python_name: describe_method(spec) for spec in contract.methods}
            hints = (found or {}).get("hints") or {}
            for name, item in raw["methods"].items():
                method_path = _key(_key(path, "methods"), name)
                described = specs.get(name)
                if described is None:
                    problems.add(method_path, f"{contract_type} has no method {name!r}. Its methods: "
                                              f"{', '.join(sorted(specs))}.")
                    continue
                # Limits are checked only against a device on the network now (one that
                # isn't is already a problem).
                method = _method(item, method_path, problems, described, hints, contract,
                                 device_id if found is not None else None)
                if method is not None:
                    methods[name] = method
    if len(problems) > before or device_id is None:
        return None
    device = {"device_id": device_id, "device_type": device_type, "methods": methods}
    declared[device_id] = (index, device)
    return device


def _method(raw: Any, path: str, problems: _Problems, described: dict[str, Any], hints: dict[str, Any],
            contract: DeviceContract, device_id: str | None) -> dict[str, Any] | None:
    before = len(problems)
    name, kind = described["name"], described["kind"]
    required = ("max_calls", "max_rate_hz", "stream") if kind == "stream" else ("max_calls", "max_rate_hz")
    if not _object(raw, path, _METHOD_KEYS, required, problems, f"The limits for {name}"):
        return None
    max_calls = _whole(raw["max_calls"], _key(path, "max_calls"), problems, "max_calls", 1, MAX_CALLS) \
        if "max_calls" in raw else None
    max_rate = _rate(raw["max_rate_hz"], _key(path, "max_rate_hz"), problems, "max_rate_hz", MAX_RATE_HZ) \
        if "max_rate_hz" in raw else None
    stream = None
    if kind == "stream":
        if "stream" in raw and _object(raw["stream"], _key(path, "stream"), _STREAM_KEYS, _STREAM_KEYS, problems,
                                       "stream"):
            stream_path = _key(path, "stream")
            rate = _rate(raw["stream"]["max_sample_rate_hz"], _key(stream_path, "max_sample_rate_hz"), problems,
                         "max_sample_rate_hz", MAX_SAMPLE_RATE_HZ) if "max_sample_rate_hz" in raw["stream"] else None
            samples = _whole(raw["stream"]["max_samples"], _key(stream_path, "max_samples"), problems, "max_samples",
                             1, MAX_SAMPLES) if "max_samples" in raw["stream"] else None
            stream = {"max_sample_rate_hz": rate, "max_samples": samples}
    elif "stream" in raw:
        problems.add(_key(path, "stream"), f"Only streams take stream limits, and {name} is a {kind}; remove \"stream\".")

    args: dict[str, dict[str, Any]] = {}
    fields = {field["name"]: field for field in described["fields"]}
    raw_args = raw.get("args", {})
    args_path = _key(path, "args")
    if _object(raw_args, args_path, tuple(raw_args) if isinstance(raw_args, dict) else (), (), problems, "args"):
        for field_name, bound in raw_args.items():
            field_path = _key(args_path, field_name)
            field = fields.get(field_name)
            if field is None:
                if field_name == "unit" and kind == "write":
                    problems.add(field_path, f"unit can't be bounded or sent: {name} always uses its own unit "
                                             f"({described['unit']}). Remove it.")
                elif field_name in STREAM_CONTROLS and kind == "stream":
                    problems.add(field_path, f"{field_name} is a stream control, limited by \"stream\" "
                                             "(max_sample_rate_hz, max_samples); remove it from args.")
                elif fields:
                    problems.add(field_path, f"{name} has no argument {field_name!r}. Its arguments: "
                                             f"{', '.join(fields)}.")
                else:
                    problems.add(field_path, f"{name} takes no arguments; remove {field_name!r}.")
                continue
            normalized = _bound(bound, field_path, problems, name, field, described["unit"], hints, device_id)
            if normalized is not None:
                args[field_name] = normalized
        for field_name, field in fields.items():
            if not field["optional"] and field_name not in raw_args:
                example = ('{"min": ..., "max": ..., "max_step": ...}' if _field_type(field) in _NUMBER_TYPES
                           else '{"choices": [...]}')
                problems.add(_key(args_path, field_name), f"{name} always sends {field_name}, so it needs bounds: "
                                                          f"{example}.")
        for field_name, bound in args.items():
            if bound.get("max_step") is None:
                continue
            reader, why = step_reader(contract, name, field_name)
            if reader is None:
                problems.add(_key(_key(args_path, field_name), "max_step"),
                             f"{contract.device_type} can't report where {field_name} of {name} is now ({why}), so "
                             "a step limit can't be enforced; drop max_step (any value in range is then allowed) or "
                             "narrow min and max to what you'd accept in one jump.")
                continue
            for item in reader.forwarded_field_specs:
                if not item.optional and item.name not in args:
                    problems.add(_key(_key(args_path, field_name), "max_step"),
                                 f"{reader.python_name}() needs {item.name} to read where {field_name} is now, and "
                                 f"{name} doesn't bound it here; bound {item.name} with choices, or drop max_step "
                                 "(any value in range is then allowed).")
    if len(problems) > before:
        return None
    method: dict[str, Any] = {"args": args, "max_calls": max_calls, "max_rate_hz": max_rate}
    if stream is not None:
        method["stream"] = stream
    return method


def _limit_source(hints: dict[str, Any], key: str) -> str:
    """Where a published limit comes from: "site" (the Lab PC inventory) or "driver"."""

    return "site" if (hints.get("sources") or {}).get(key) == "site" else "driver"


def _bound(raw: Any, path: str, problems: _Problems, method: str, field: dict[str, Any], unit: str,
           hints: dict[str, Any], device_id: str | None = None) -> dict[str, Any] | None:
    before = len(problems)
    kind = _field_type(field)
    key = f"{method}.{field['name']}"
    if kind in _NUMBER_TYPES:
        if not _object(raw, path, _NUMBER_KEYS, ("min", "max"), problems, f"The bounds of {field['name']} (a number)"):
            return None
        low = _number(raw["min"], _key(path, "min"), problems, "min") if "min" in raw else None
        high = _number(raw["max"], _key(path, "max"), problems, "max") if "max" in raw else None
        step = None
        if raw.get("max_step") is not None:
            step = _number(raw["max_step"], _key(path, "max_step"), problems, "max_step")
            if step is not None and step <= 0:
                problems.add(_key(path, "max_step"), "max_step must be more than 0 (or left out for any step).")
        if kind == "int":
            for which, value in (("min", low), ("max", high)):
                if value is not None and not value.is_integer():
                    problems.add(_key(path, which), f"{field['name']} is a whole number, so {which} must be one too.")
        if low is not None and high is not None and low > high:
            problems.add(_key(path, "min"), f"min ({low:g}) is larger than max ({high:g}).")
        limit = (hints.get("limits") or {}).get(key)
        if limit is None:
            # No limit, no automation: the Lab PC enforces published limits on
            # every call, so a number without one has no hard bound at all.
            if device_id is not None:
                problems.add(path, f"{device_id} publishes no limit for {key}; set one in the Lab PC "
                                   f"inventory with site_limits(...) before automating it.")
        else:
            shown = f"{limit[0]:g} to {limit[1]:g}" + (f" {unit}" if unit and unit != "1" else "")
            whose = "site limit" if _limit_source(hints, key) == "site" else "driver's limit"
            if low is not None and low < limit[0]:
                problems.add(_key(path, "min"), f"min ({low:g}) is below the {whose} for {key} ({shown}).")
            if high is not None and high > limit[1]:
                problems.add(_key(path, "max"), f"max ({high:g}) is above the {whose} for {key} ({shown}).")
        if len(problems) > before:
            return None
        return {"min": low, "max": high, "max_step": step}
    if kind not in ("bool", "str"):
        problems.add(path, f"{field['name']} is of type {field['type']}, which automation can't bound or send.")
        return None
    if not _object(raw, path, _CHOICE_KEYS, _CHOICE_KEYS, problems, f"The bounds of {field['name']} ({kind})"):
        return None
    choices_path = _key(path, "choices")
    choices = _list(raw["choices"], choices_path, problems, "choices", 1, 2 if kind == "bool" else MAX_CHOICES) \
        if "choices" in raw else None
    if choices is None:
        return None
    clean: list[Any] = []
    for index, choice in enumerate(choices):
        choice_path = f"{choices_path}[{index}]"
        if kind == "bool":
            if not isinstance(choice, bool):
                problems.add(choice_path, f"{field['name']} is true or false; {_describe(choice)} isn't.")
                continue
            value: Any = choice
        else:
            if not isinstance(choice, str):
                problems.add(choice_path, f"{field['name']} is text; {_describe(choice)} isn't.")
                continue
            value = _text(choice, choice_path, problems, "A choice", MAX_CHOICE_CHARACTERS)
            if value is None:
                continue
            if value != choice:
                problems.add(choice_path, "A choice must not start or end with spaces.")
                continue
        if value in clean:
            problems.add(choice_path, f"{_describe(value) if kind == 'bool' else repr(value)} is listed twice.")
            continue
        clean.append(value)
    offered = (hints.get("choices") or {}).get(key)
    whose = "site choices" if _limit_source(hints, key) == "site" else "driver's choices"
    if kind == "bool" and offered is not None:
        for index, value in enumerate(clean):
            if not any(isinstance(item, bool) and item == value for item in offered):
                problems.add(f"{choices_path}[{index}]", f"{_describe(value)} isn't one of the {whose} for {key}: "
                                                         f"{', '.join(_describe(item) for item in offered)}.")
    if kind == "str":
        if offered is not None:
            for index, value in enumerate(clean):
                if value not in offered:
                    problems.add(f"{choices_path}[{index}]", f"{value!r} isn't one of the {whose} for {key}: "
                                                             f"{', '.join(map(str, offered))}.")
        channels = hints.get("channels")
        if field["name"] == "channel" and channels:
            for index, value in enumerate(clean):
                if value not in channels:
                    problems.add(f"{choices_path}[{index}]", f"{value!r} isn't one of this device's channels: "
                                                             f"{', '.join(channels)}.")
    if len(problems) > before:
        return None
    return {"choices": clean}


def _safe_call(raw: Any, path: str, problems: _Problems,
               declared: dict[str, tuple[int, dict[str, Any] | None]]) -> dict[str, Any] | None:
    before = len(problems)
    if not _object(raw, path, _SAFE_KEYS, ("device_id", "method"), problems, "A safe-state call"):
        return None
    device_id = raw.get("device_id")
    method_name = raw.get("method")
    if "device_id" in raw and not isinstance(device_id, str):
        problems.add(_key(path, "device_id"), f"device_id must be text, not {_describe(device_id)}.")
        return None
    if "method" in raw and not isinstance(method_name, str):
        problems.add(_key(path, "method"), f"method must be text, not {_describe(method_name)}.")
        return None
    if device_id is None or method_name is None:
        return None
    if device_id not in declared:
        problems.add(_key(path, "device_id"), f"{device_id} isn't in devices; a safe state may call only devices "
                                              "the protocol lists.")
        return None
    index, device = declared[device_id]
    if device is None:
        return None  # the device's own problems are already listed
    method = device["methods"].get(method_name)
    if method is None:
        problems.add(_key(path, "method"), f"{method_name} isn't listed under devices[{index}].methods; list it "
                                           "there, with bounds, to use it in the safe state.")
        return None
    if "stream" in method:
        problems.add(_key(path, "method"), f"{method_name} is a stream; a safe state is a write or command, "
                                           "such as switching an output off.")
        return None
    contract = registry.get(device["device_type"])
    described = next(describe_method(spec) for spec in contract.methods if spec.python_name == method_name)
    fields = {field["name"]: field for field in described["fields"]}
    raw_args = raw.get("args", {})
    args_path = _key(path, "args")
    args: dict[str, Any] = {}
    if _object(raw_args, args_path, tuple(raw_args) if isinstance(raw_args, dict) else (), (), problems, "args"):
        for name, value in raw_args.items():
            value_path = _key(args_path, name)
            bound = method["args"].get(name)
            if bound is None:
                problems.add(value_path, f"{name} isn't bounded in devices[{index}].methods.{method_name}.args, "
                                         "so it can't be sent.")
                continue
            if "choices" in bound:
                if isinstance(value, bool) != isinstance(bound["choices"][0], bool) or value not in bound["choices"]:
                    problems.add(value_path, f"{_describe(value) if not isinstance(value, str) else repr(value)} isn't "
                                             f"one of the protocol's choices for {name}: "
                                             f"{', '.join(json.dumps(item) for item in bound['choices'])}.")
                    continue
                args[name] = value
                continue
            number = _number(value, value_path, problems, name)
            if number is None:
                continue
            whole = _field_type(fields[name]) == "int"
            if whole and not number.is_integer():
                problems.add(value_path, f"{name} is a whole number; {value!r} isn't.")
                continue
            if not bound["min"] <= number <= bound["max"]:
                problems.add(value_path, f"{number:g} is outside the protocol's bounds for {name} "
                                         f"({bound['min']:g} to {bound['max']:g}).")
                continue
            args[name] = int(number) if whole else number  # a whole-number field is sent as a JSON integer
        for name, field in fields.items():
            if not field["optional"] and name not in raw_args:
                problems.add(_key(args_path, name), f"{method_name} always sends {name}; give its value.")
            elif name in method["args"] and name not in raw_args:
                problems.add(_key(args_path, name), f"{name} is bounded for {method_name}, so every call sends it, "
                                                    "the safe state's too; give its value (leaving it out would "
                                                    "use the driver's default).")
    if len(problems) > before:
        return None
    return {"device_id": device_id, "method": method_name, "args": args}


# ---- what a person reads --------------------------------------------------------------------------


def _call_text(call: dict[str, Any]) -> str:
    args = ", ".join(f"{name}={json.dumps(value, ensure_ascii=False)}" for name, value in call["args"].items())
    return f"{call['device_id']}.{call['method']}({args})"


def build_review(protocol: dict[str, Any], live_devices: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """What the approval card shows, from the normalized protocol and the device contracts only.

    One row per device method: its kind and unit, each argument's range or
    choices and largest step (and the device's published limit with its
    source, ``"site"`` or ``"driver"``), that every bounded
    argument is always sent, which optional arguments may never be sent, the
    call and rate caps, the stream caps; then the safe state, the duration, and
    warnings worth a second look. Steps are measured from the device's own
    reading, taken by the gateway before the first change.
    """

    warnings: list[str] = []
    devices = []
    for device in protocol["devices"]:
        contract = registry.find(device["device_type"])
        specs = {spec.python_name: describe_method(spec) for spec in contract.methods} if contract else {}
        live = (live_devices or {}).get(device["device_id"])
        hints = (live or {}).get("hints") or {}
        methods = []
        for name in sorted(device["methods"]):
            limits = device["methods"][name]
            described = specs.get(name, {"kind": "unknown", "unit": "", "fields": []})
            unit = described["unit"] if described["unit"] not in ("", "1") else ""
            arguments = []
            never = []
            for field in described["fields"]:
                bound = limits["args"].get(field["name"])
                if bound is None:
                    never.append(field["name"])
                    continue
                row: dict[str, Any] = {"name": field["name"], "type": _field_type(field), "optional": field["optional"],
                                       "always_sent": True}
                key = f"{name}.{field['name']}"
                if "choices" in bound:
                    row["choices"] = bound["choices"]
                    offered = (hints.get("choices") or {}).get(key)
                    if offered is None and field["name"] == "channel" and hints.get("channels"):
                        row["offered"], row["limit_source"] = hints["channels"], "driver"
                    elif offered is not None:
                        row["offered"], row["limit_source"] = offered, _limit_source(hints, key)
                else:
                    row.update(min=bound["min"], max=bound["max"], max_step=bound["max_step"],
                               unit=unit if field["name"] == "value" else "")
                    if bound["max_step"] is not None:
                        row["step_from"] = "the device's reading before the first change"
                    published = (hints.get("limits") or {}).get(key)
                    source = _limit_source(hints, key) if published is not None else None
                    # The device's published limit (the Lab PC enforces it on every call) and
                    # where it comes from: "site" (the Lab PC inventory) or "driver".
                    row["limit"] = published
                    row["limit_source"] = source
                    row["driver_limit"] = published if source == "driver" else None
                    row["site_limit"] = published if source == "site" else None
                    if bound["max_step"] is None:
                        warnings.append(f"{device['device_id']}.{name}: {field['name']} may jump anywhere within "
                                        "its range in one call (no max_step).")
                    if published is None:
                        warnings.append(f"{device['device_id']}.{name}: {device['device_id']} publishes no limit for "
                                        f"{key} now (offline, or its limits changed); approving re-checks it.")
                arguments.append(row)
            methods.append({
                "name": name,
                "kind": described["kind"],
                "unit": unit,
                "args": arguments,
                "never_sent": never,
                "max_calls": limits["max_calls"],
                "max_rate_hz": limits["max_rate_hz"],
                "stream": limits.get("stream"),
            })
        devices.append({
            "device_id": device["device_id"],
            "device_type": device["device_type"],
            "type_label": type_label(device["device_type"]),
            "state": live["state"] if live else "not on the network",
            "methods": methods,
        })
    if not protocol["safe_state"]:
        warnings.append("No safe state: nothing is set when the grant ends; devices stay as the agent left them.")
    return {
        "title": protocol["title"],
        "task": protocol["task"],
        "duration_minutes": protocol["duration_minutes"],
        "devices": devices,
        "safe_state": [{**call, "text": _call_text(call)} for call in protocol["safe_state"]],
        "warnings": warnings,
    }


__all__ = [
    "DEFAULT_MAX_MINUTES",
    "MAX_NOTE_CHARACTERS",
    "MAX_PROSE_BYTES",
    "MAX_PROTOCOL_BYTES",
    "build_review",
    "canonical",
    "network_devices",
    "parse_protocol",
    "protocol_files",
    "protocol_sha256",
    "step_reader",
    "validate_protocol",
]
