"""The experiment document: a setup diagram and how to run it.

An experiment is what sits on the optical table (instruments bound to devices
on the network, and passive optics), how it is connected (beams and signal
cables), what each instrument is set to (fixed values or sweeps), and what is
recorded. Anyone using the Control Center, and Photo-Agent, writes these, and
scripts are generated from them, so everything is checked strictly here:
exact types, no extra keys, finite numbers, bounded sizes, safe text.

Checks that need the device contracts or the live network (methods, argument
types, offline devices) are in ``experiment_checks.py``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from labnet.identifiers import LABNET_IDENTIFIER_PATTERN


SCHEMA_VERSION = 1
EXPERIMENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,40}-[a-z0-9]{6}")

# Passive components: on the table, not controlled by LabNet.
OPTIC_KINDS = {
    "laser": "Laser",
    "mirror": "Mirror",
    "beamsplitter": "Beamsplitter",
    "pbs": "Polarizing beamsplitter",
    "lens": "Lens",
    "filter": "Filter",
    "waveplate": "Waveplate",
    "fiber": "Fiber",
    "isolator": "Isolator",
    "iris": "Iris",
    "sample": "Sample / cell",
    "beam_dump": "Beam dump",
    "photodetector": "Photodetector",
}
NODE_KINDS = ("instrument", *OPTIC_KINDS, "note")
LINK_TYPES = ("beam", "signal")

MAX_NODES = 200
MAX_INSTRUMENTS = 16
MAX_LINKS = 400
MAX_RECORDS = 32
MAX_POINTS = 10_000
MAX_FILE_BYTES = 256 * 1024

# Unicode categories never allowed in text: controls, format characters
# (bidirectional overrides, zero-width), separators, private and unassigned.
_FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"}


def _text(multiline: bool):
    def check(value: str) -> str:
        for character in value:
            if multiline and character in "\n\t":
                continue
            if unicodedata.category(character) in _FORBIDDEN_CATEGORIES:
                raise ValueError(f"contains a control or invisible character (U+{ord(character):04X})")
        return value.strip()

    return AfterValidator(check)


def Text(max_length: int, *, multiline: bool = False, min_length: int = 0) -> Any:
    return Annotated[str, Field(min_length=min_length, max_length=max_length), _text(multiline)]


ItemId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")]
MethodName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ArgName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
DeviceId = Annotated[str, Field(pattern=rf"^(?:{LABNET_IDENTIFIER_PATTERN})$")]
DeviceType = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ArgValue = bool | int | float | Text(128)
Coordinate = Annotated[float, Field(ge=-100_000, le=100_000)]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class Sweep(_Strict):
    """Step one argument of a setting through evenly spaced points.

    Sweeps nest by ``axis`` (0 is the outermost loop); sweeps on the same axis
    advance together and need the same number of points.
    """

    field: ArgName = "value"
    start: float
    stop: float
    points: int = Field(ge=2, le=1001)
    axis: int = Field(default=0, ge=0, le=3)


class Setting(_Strict):
    """A write or command applied before recording: fixed ``args``, or a sweep."""

    id: ItemId
    method: MethodName
    args: dict[ArgName, ArgValue] = Field(default_factory=dict, max_length=8)
    sweep: Sweep | None = None


class Record(_Strict):
    """A read or stream recorded at every point."""

    id: ItemId
    method: MethodName
    args: dict[ArgName, ArgValue] = Field(default_factory=dict, max_length=8)
    samples: int | None = Field(default=None, ge=1, le=10_000)  # None: the run's default
    sample_rate_hz: float | None = Field(default=None, ge=0.1, le=1000)  # streams only


class Node(_Strict):
    id: ItemId
    kind: Literal[NODE_KINDS]  # type: ignore[valid-type]
    label: Text(60) = ""
    notes: Text(2000, multiline=True) = ""
    # The center of the block; left out, the canvas lays it out.
    x: Coordinate | None = None
    y: Coordinate | None = None
    # Instruments only:
    device_id: DeviceId | None = None
    device_type: DeviceType | None = None
    settings: list[Setting] = Field(default_factory=list, max_length=32)
    records: list[Record] = Field(default_factory=list, max_length=16)


class Link(_Strict):
    id: ItemId
    source: ItemId
    target: ItemId
    type: Literal[LINK_TYPES] = "beam"  # type: ignore[valid-type]
    label: Text(40) = ""


class RunOptions(_Strict):
    settle_seconds: float = Field(default=0.2, ge=0, le=3600)
    samples: int = Field(default=10, ge=1, le=10_000)
    sample_rate_hz: float = Field(default=20.0, ge=0.1, le=1000)
    output_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")] = "results"


class ExperimentContent(_Strict):
    """What a person (or Photo-Agent) edits; the server adds id, version and times."""

    name: Text(120, min_length=1)
    description: Text(4000, multiline=True) = ""
    run: RunOptions = Field(default_factory=RunOptions)
    nodes: list[Node] = Field(default_factory=list, max_length=MAX_NODES)
    links: list[Link] = Field(default_factory=list, max_length=MAX_LINKS)



def _problem(message: str, node_id: str | None = None, item_id: str | None = None, field: str = "") -> dict[str, Any]:
    return {"code": "structure", "message": message, "node_id": node_id, "item_id": item_id, "field": field}


def structure_problems(content: ExperimentContent) -> list[dict[str, Any]]:
    """Problems no script could be generated around, whatever the network."""

    problems: list[dict[str, Any]] = []
    nodes: dict[str, Node] = {}
    for node in content.nodes:
        if node.id in nodes:
            problems.append(_problem(f"Two components share the id {node.id}.", node.id))
        nodes[node.id] = node
    instruments = [node for node in content.nodes if node.kind == "instrument"]
    if len(instruments) > MAX_INSTRUMENTS:
        problems.append(_problem(f"An experiment can have at most {MAX_INSTRUMENTS} instruments."))
    bound: dict[str, str] = {}
    records = 0
    for node in content.nodes:
        if (node.x is None) != (node.y is None):
            problems.append(_problem("Give both x and y, or neither.", node.id, field="x"))
        if node.kind != "instrument":
            if node.device_id or node.device_type or node.settings or node.records:
                problems.append(_problem("Only instruments have a device, settings or records.", node.id))
            continue
        if not node.device_id or not node.device_type:
            problems.append(_problem("An instrument needs a device_id and device_type.", node.id))
        elif node.device_id in bound:
            problems.append(
                _problem(f"{node.device_id} is on the diagram twice (also as {bound[node.device_id]}).", node.id)
            )
        else:
            bound[node.device_id] = node.id
        seen_items: set[str] = set()
        for item in (*node.settings, *node.records):
            if item.id in seen_items:
                problems.append(_problem(f"Two settings or records share the id {item.id}.", node.id, item.id))
            seen_items.add(item.id)
        records += len(node.records)
    if records > MAX_RECORDS:
        problems.append(_problem(f"An experiment can record at most {MAX_RECORDS} readings."))

    link_ids: set[str] = set()
    seen: set[tuple[str, str, str]] = set()
    for link in content.links:
        if link.id in link_ids:
            problems.append(_problem(f"Two links share the id {link.id}.", field="links"))
        link_ids.add(link.id)
        if link.source not in nodes or link.target not in nodes:
            problems.append(_problem(f"Link {link.id} connects a component that isn't on the diagram.", field="links"))
            continue
        if link.source == link.target:
            problems.append(_problem(f"Link {link.id} connects {link.source} to itself.", link.source, field="links"))
        if "note" in (nodes[link.source].kind, nodes[link.target].kind):
            problems.append(_problem(f"Link {link.id}: notes can't be connected.", field="links"))
        key = (link.source, link.target, link.type)
        if key in seen:
            problems.append(_problem(f"Link {link.id} repeats another {link.type} link.", field="links"))
        seen.add(key)

    axes: dict[int, int] = {}
    for node in instruments:
        for setting in node.settings:
            if setting.sweep is None:
                continue
            points = axes.setdefault(setting.sweep.axis, setting.sweep.points)
            if points != setting.sweep.points:
                problems.append(
                    _problem(
                        f"Sweeps in loop {setting.sweep.axis + 1} advance together, so they need the same "
                        f"number of points ({points}).",
                        node.id,
                        setting.id,
                        "sweep.points",
                    )
                )
    if total_points(content) > MAX_POINTS:
        problems.append(_problem(f"The sweeps make more than {MAX_POINTS:,} points."))
    return problems


def parse_content(data: Any) -> tuple[ExperimentContent | None, list[dict[str, Any]]]:
    """Check an experiment as sent: (content, []) or (None, problems)."""

    try:
        content = ExperimentContent.model_validate(data)
    except ValidationError as error:
        return None, problems_from(error, data)
    problems = structure_problems(content)
    return (None, problems) if problems else (content, [])


def sweep_axes(content: ExperimentContent) -> dict[int, int]:
    """Points per loop, outermost first."""

    axes: dict[int, int] = {}
    for node in content.nodes:
        for setting in node.settings:
            if setting.sweep is not None:
                axes.setdefault(setting.sweep.axis, setting.sweep.points)
    return dict(sorted(axes.items()))


def total_points(content: ExperimentContent) -> int:
    total = 1
    for points in sweep_axes(content).values():
        total *= points
    return total


class Experiment(ExperimentContent):
    """A stored experiment."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: Annotated[str, Field(pattern=rf"^{EXPERIMENT_ID.pattern}$")]
    version: int = Field(ge=1)
    created_at: Annotated[str, Field(max_length=40)]
    updated_at: Annotated[str, Field(max_length=40)]
    created_by: Text(64) = ""
    updated_by: Text(64) = ""


def problems_from(error: ValidationError, data: Any) -> list[dict[str, Any]]:
    """Pydantic's errors as report items that name the component they are about."""

    items = []
    seen: set[tuple[Any, ...]] = set()
    for detail in error.errors(include_url=False, include_input=False):
        location = list(detail.get("loc", ()))
        union_value = "args" in location and location.index("args") + 2 < len(location)
        if union_value:  # bool | int | float | str: one message per argument
            location = location[: location.index("args") + 2]
        node_id = item_id = None
        field = ".".join(str(part) for part in location)
        try:
            if location[:1] == ["nodes"] and isinstance(location[1], int):
                node = data["nodes"][location[1]]
                node_id = node.get("id") if isinstance(node, dict) else None
                field = ".".join(str(part) for part in location[2:])
                if location[2] in ("settings", "records") and isinstance(location[3], int):
                    item = node[location[2]][location[3]]
                    item_id = item.get("id") if isinstance(item, dict) else None
                    field = ".".join(str(part) for part in location[4:])
        except (IndexError, KeyError, TypeError):
            pass
        message = str(detail.get("msg", "invalid")).removeprefix("Value error, ")
        if union_value:
            message = "Must be a finite number, true/false, or text up to 128 characters."
        key = (node_id, item_id, field, message)
        if key in seen:
            continue
        seen.add(key)
        items.append({"code": "schema", "message": message, "node_id": node_id, "item_id": item_id, "field": field})
    return items


class _Author(_Strict):
    name: Text(64) = ""


def clean_author(value: Any) -> str:
    """A "created by" / "updated by" name: optional free text, checked like labels."""

    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("must be text")
    try:
        return _Author(name=value).name
    except ValidationError as exc:
        raise ValueError(str(exc.errors()[0]["msg"]).removeprefix("Value error, ")) from None


def json_schema() -> dict[str, Any]:
    return ExperimentContent.model_json_schema()


__all__ = [
    "EXPERIMENT_ID",
    "Experiment",
    "ExperimentContent",
    "LINK_TYPES",
    "MAX_FILE_BYTES",
    "MAX_POINTS",
    "NODE_KINDS",
    "OPTIC_KINDS",
    "SCHEMA_VERSION",
    "clean_author",
    "json_schema",
    "parse_content",
    "problems_from",
    "structure_problems",
    "sweep_axes",
    "total_points",
]
