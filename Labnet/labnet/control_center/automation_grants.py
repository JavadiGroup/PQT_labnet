"""Device grants: what an approved protocol lets an Automation run do to lab hardware.

A person approves a protocol (see ``automation_protocol.py``); :meth:`GrantManager.create`
then takes every lease the protocol names, up front and all-or-nothing, and
issues a grant: a time limit, the protocol's bounds, and a token
(``lng_…``) that the runner's device relay sends with each call. Only the
token's sha256 is stored; the plaintext is handed out once
(:meth:`GrantManager.take_token`).

Every call a script makes goes through :meth:`GrantManager.call`, which
refuses anything :func:`check_call` does not allow. The rules fail closed:
anything unexpected, including a malformed protocol, is a refusal.

* the grant is active and has time left;
* the device is in the grant, the method is listed for it;
* ``args`` is a JSON object of exactly the method's arguments: every required
  one, and every one the protocol bounds (an optional argument left out would
  drive the driver's default, e.g. channel CH1 when only CH2 was approved);
  optional ones the protocol doesn't bound never; never ``unit``;
* numbers are JSON numbers (not booleans), finite, within ``[min, max]``, and
  within ``max_step`` of where that field may be now. Where it is is kept per
  device, method, field *and* the call's choice arguments
  (``dev02-awg.set_amplitude.value|channel=CH2``, see :func:`step_key`), so
  alternating channels can't double a step. Before the first change the
  gateway reads it from the device (``set_amplitude`` → ``read_amplitude`` with
  the same channel; see ``automation_protocol.step_reader``); a call that
  succeeded moves it to the value sent; a call whose outcome is uncertain (a
  device error, a timeout, a cancelled call) widens it to the interval
  between the old position and the value sent, and the next value must be
  within ``max_step`` of both ends;
  booleans and text are one of the approved ``choices``;
* streams send ``sample_rate_hz`` (at most the approved rate) and a whole
  ``sample_count`` (1 … max_samples) that fits in the time left; other methods
  send neither;
* each method keeps to its approved number of calls and rate;
* one call at a time per device (``busy``).

Ten refused calls in a row end the grant. Every call, allowed or not, is one
line in ``state/<project>/audit/<run>/grant-<id>.jsonl``, with its outcome and
duration. Device errors are redacted (no lease ids, no tokens).

Ending (expiry, Stop, violations, the run ending, the agent, shutdown) always
goes the same way: refuse new calls → cancel calls in flight → apply the
protocol's safe state (each call up to three tries, re-taking a lost lease once)
→ release the leases → audit → ``ended`` (or ``ended_unsafe`` if the safe
state could not be applied). After a restart, grants left active on disk are
marked ``interrupted`` (:meth:`GrantManager.mark_interrupted`).

**The safe-state watchdog** (``labnet.automation_watchdog``) is a separate
process that applies a grant's safe state when this Control Center dies or
hangs mid-grant. It needs nothing from this process:

* While a grant holds its leases (``active``/``ending``), a task on this event
  loop rewrites ``state/<project>/grants/<id>.json`` every
  ``HEARTBEAT_SECONDS`` with a fresh ``alive_at`` (wall-clock seconds), so a
  hung loop stops the heartbeat. The record holds the approved normalized
  ``protocol`` (devices, types, safe-state calls) and ``ends_at``. If the
  heartbeat can't be written ``HEARTBEAT_FAILURES_TO_END`` times in a row, the
  grant is ended here (safe state) rather than left for the watchdog to guess.
* The watchdog never writes this process's record. It writes its own
  ``<id>.watchdog.json`` (a *claim*: ``claimed`` → ``safing`` → ``ended`` |
  ``ended_unsafe``, or ``stood_down``) and lines of kind ``watchdog`` in the
  grant's audit file. One writer per file, so no update is ever lost.
* ``claimed`` (no lease taken yet) binds nobody: a heartbeat from here makes
  the watchdog stand down. ``safing`` and the final states bind: the watchdog
  holds a lease, so this process *hands the grant over* (refuses calls, cancels
  calls in flight, releases its leases without a safe state of its own, unless
  the claim says ``ended_unsafe``: then it tries its own) and marks it
  ``interrupted`` with ``handed_to_watchdog``; when the claim is
  final, its outcome is copied into the record and the run
  (``safe_state_by: "watchdog"``). :meth:`GrantManager.mark_interrupted` does
  the same at startup, so a grant the watchdog handled is never overwritten.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from labnet.client import ResourceConflictError, ResourceNotFoundError
from labnet.contracts import DeviceContract, RpcSpec, registry

from .automation_protocol import step_reader
from .automation_store import PROJECT_ID, AutomationError, write_json_file
from .device_calls import (
    READ_PATTERNS,
    STREAM_PATTERN,
    call_device,
    device_error,
    find_method,
    json_safe,
    lease_lost_before_call,
    lease_rejected,
    plain,
)
from ..redaction import redact


LOGGER = logging.getLogger(__name__)

ACTIVE, ENDING, ENDED, ENDED_UNSAFE, INTERRUPTED = "active", "ending", "ended", "ended_unsafe", "interrupted"
LIVE = frozenset({ACTIVE, ENDING})  # the grant still holds its leases
GRANT_ID = re.compile(r"grant-[0-9a-f]{16}")
TOKEN_PREFIX = "lng_"
MAX_TOKEN_CHARACTERS = 200
REQUEST_KEYS = frozenset({"device_id", "method", "args", "sample_rate_hz", "sample_count"})
MAX_REFUSED_IN_A_ROW = 10
SAFE_STATE_TRIES = 3
SAFE_STATE_CALL_SECONDS = 15.0  # one try of one safe-state call
LEASE_SECONDS = 20.0  # taking one lease
MAX_AUDIT_BYTES = 32 * 1024 * 1024
MAX_RECORD_BYTES = 256 * 1024
MAX_REASON_CHARACTERS = 300
# A step may exceed max_step by this fraction (float rounding of e.g. 70.1 → 72.1), no more.
STEP_TOLERANCE = 1e-12
# The run record's ``grant`` field has exactly these keys (see automation_runs.py).
PUBLIC_KEYS = (
    "id", "status", "approved_by", "starts_at", "ends_at", "devices", "calls", "refused_calls",
    "ended_reason", "ended_at",
)

# ---- the watchdog's side of the record (see the module docstring) ----
HEARTBEAT_SECONDS = 5.0  # how often a live grant's record gets a fresh alive_at
HEARTBEAT_FAILURES_TO_END = 3  # heartbeats that couldn't be written in a row: end the grant here
WATCHDOG_SUFFIX = ".watchdog.json"  # state/<project>/grants/<id>.watchdog.json: written only by the watchdog
CLAIMED, SAFING, STOOD_DOWN = "claimed", "safing", "stood_down"
WATCHDOG_STATUSES = frozenset({CLAIMED, SAFING, STOOD_DOWN, ENDED, ENDED_UNSAFE})
WATCHDOG_FINAL = frozenset({ENDED, ENDED_UNSAFE})
# The watchdog took (or decided about) the grant: a Control Center that comes back ends it, without resuming.
WATCHDOG_BINDING = frozenset({SAFING}) | WATCHDOG_FINAL
# ...and leaves the safe state to the watchdog, which holds the leases or has applied it. After
# ended_unsafe the Control Center tries its own safe state, in case it still holds a lease.
WATCHDOG_APPLIES = frozenset({SAFING, ENDED})
HANDED_REASON = ("the Control Center stopped answering, so the safe-state watchdog took the grant over "
                 "and applies its safe state")
HANDED_FOLLOW_SECONDS = 3600.0  # how long a handed-over grant's claim is watched for its outcome


class GrantError(AutomationError):
    """A refused grant request. The app answers ``{"detail", "code"}`` with ``status_code``.

    Codes: ``refused`` (outside the approved bounds), ``busy`` (a call to that
    device is running), ``expired`` (time is up), ``ended`` (stopped, or no such
    grant), ``device_error`` (the device failed), ``conflict`` (creating a grant).
    """

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        detail = redact(detail)
        super().__init__(status_code, detail, code=code)
        self.code = code
        self.detail = detail


# ---- the per-call rules -----------------------------------------------------------------


_PLAIN_CHOICE = re.compile(r"[A-Za-z0-9_.+-]+")


def step_key(device_id: str, method: str, name: str, args: dict[str, Any]) -> str:
    """Where max_step is measured from: per device, method, field and the call's choice arguments.

    ``dev02-awg.set_amplitude.value|channel=CH2``. A choice that isn't plain
    text is written as JSON (``"…"``), so two different choices never share a key.
    """

    key = f"{device_id}.{method}.{name}"
    for other in sorted(args):
        value = args[other]
        if other == name or not isinstance(value, (bool, str)):
            continue
        shown = value if isinstance(value, str) and _PLAIN_CHOICE.fullmatch(value) else json.dumps(value)
        key += f"|{other}={shown}"
    return key


def _interval(position: Any) -> tuple[float, float] | None:
    """A stored position as (low, high): a single number is (n, n); anything else is no position."""

    if _is_number(position):
        return position, position
    if isinstance(position, tuple) and len(position) == 2 and all(_is_number(end) for end in position):
        return min(position), max(position)
    return None


@dataclass
class GrantCallState:
    """What :func:`check_call` needs to remember between calls of one grant."""

    calls: dict[str, int] = field(default_factory=dict)  # "device.method" → calls made
    last_call_at: dict[str, float] = field(default_factory=dict)  # "device.method" → when (clock)
    # step_key(...) → (low, high): where that field may be now. One value after a
    # call that succeeded (or the gateway's reading); an interval after a call
    # whose outcome is uncertain. No entry: unknown, so read it first.
    last_values: dict[str, tuple[float, float]] = field(default_factory=dict)
    busy: set[str] = field(default_factory=set)  # devices with a call running
    refused_in_a_row: int = 0

    def started(self, request: dict[str, Any], now: float) -> None:
        """An allowed call is about to run: count it, and mark its device busy."""

        key = f"{request['device_id']}.{request['method']}"
        self.calls[key] = self.calls.get(key, 0) + 1
        self.last_call_at[key] = now
        self.busy.add(request["device_id"])
        self.refused_in_a_row = 0

    def finished(self, request: dict[str, Any], *, succeeded: bool) -> None:
        """The call is over. One that succeeded sets where its numbers are; one that failed may still
        have reached the device, so where they may be widens to include the value sent."""

        self.busy.discard(request["device_id"])
        args = request["args"]
        for name, value in args.items():
            if not _is_number(value):
                continue
            key = step_key(request["device_id"], request["method"], name, args)
            if succeeded:
                self.last_values[key] = (value, value)
            elif (known := _interval(self.last_values.get(key))) is not None:
                self.last_values[key] = (min(known[0], value), max(known[1], value))
            # Unknown before and uncertain now: still unknown (the next change reads it first).


def _is_number(value: Any) -> bool:
    """A real, finite JSON number (a bool is not a number here)."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


def _json_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def _protocol_device(protocol: dict[str, Any], device_id: str) -> dict[str, Any] | None:
    for device in protocol["devices"]:
        if isinstance(device, dict) and device.get("device_id") == device_id:
            return device
    return None


def _check_value(
    key: str, name: str, python_type: str, bound: Any, value: Any, state: GrantCallState, *, steps: bool = True
) -> str | None:
    base = python_type.replace("| None", "").strip()
    if not isinstance(bound, dict):
        return f"The approved protocol has no usable bounds for {name}."
    if base in ("float", "int"):
        low, high = bound.get("min"), bound.get("max")
        if "choices" in bound or not _is_number(low) or not _is_number(high) or low > high:
            return f"The approved protocol has no usable range for {name}."
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{name} must be a number, not {_json_kind(value)}."
        if base == "int" and not isinstance(value, int):
            return f"{name} must be a whole number."
        if not _is_number(value):
            return f"{name} must be a finite number."
        if not low <= value <= high:
            return f"{name}={value!r} is outside the approved range [{low:g}, {high:g}]."
        step = bound.get("max_step")
        if step is not None and steps:
            if not _is_number(step) or step <= 0:
                return f"The approved protocol has no usable max_step for {name}."
            position = _interval(state.last_values.get(key))
            if position is None:
                return (f"Where {name} is now isn't known (the device hasn't been read), so a step of at most "
                        f"{step:g} can't be checked.")
            low, high = position
            limit = step * (1 + STEP_TOLERANCE)
            if abs(value - low) > limit or abs(value - high) > limit:
                where = f"{low:g}" if low == high else f"somewhere from {low:g} to {high:g} (a call failed after it was sent)"
                return f"{name} may change by at most {step:g} per call: it is {where}, so {value!r} is too far."
        return None
    if base in ("bool", "str"):
        choices = bound.get("choices")
        if not isinstance(choices, list) or "min" in bound or "max" in bound:
            return f"The approved protocol has no usable choices for {name}."
        wanted = bool if base == "bool" else str
        if type(value) is not wanted:
            return f"{name} must be {'true or false' if base == 'bool' else 'text'}, not {_json_kind(value)}."
        if not any(type(choice) is wanted and choice == value for choice in choices):
            shown = ", ".join(json.dumps(choice) for choice in choices[:32])
            return f"{name}={json.dumps(value)} is not one of the approved choices ({shown})."
        return None
    return f"{name} ({python_type}) cannot be sent through the device gateway."


def _check(
    protocol: dict[str, Any], state: GrantCallState, request: Any, *, now: float, seconds_left: float,
    steps: bool = True,
) -> str | None:
    if not _is_number(seconds_left) or seconds_left <= 0:
        return "The grant's time is up."
    if not isinstance(request, dict):
        return "Send the call as a JSON object: {\"device_id\", \"method\", \"args\": {…}}."
    unknown = sorted(str(key) for key in request if key not in REQUEST_KEYS)
    if unknown:
        return f"Unknown field(s) in the call: {', '.join(unknown)}."
    device_id, method = request.get("device_id"), request.get("method")
    if not isinstance(device_id, str) or not isinstance(method, str):
        return "Send device_id and method as text."
    device = _protocol_device(protocol, device_id)
    if device is None:
        return f"{device_id} is not one of this grant's devices."
    methods = device["methods"]
    if not isinstance(methods, dict) or method not in methods:
        return f"{method} is not allowed on {device_id} by the approved protocol."
    bounds = methods[method]
    contract = registry.find(device["device_type"])
    spec = find_method(contract, method) if contract is not None else None
    if spec is None or not isinstance(bounds, dict):
        return f"{method} is not a method of {device['device_type']}."
    if device_id in state.busy:
        return f"{device_id} is busy with another call (one call at a time per device)."

    args = request.get("args")
    if "args" not in request or not isinstance(args, dict):
        return "args must be a JSON object of keyword arguments (positional arguments are not accepted)."
    fields = {item.name: item for item in spec.forwarded_field_specs}
    arg_bounds = bounds.get("args") or {}
    if not isinstance(arg_bounds, dict):
        return f"The approved protocol has no usable bounds for {method}."
    for name in args:
        if name == "unit":
            return "unit may not be sent: values are always in the method's own unit."
        if name not in fields:
            return f"{method}() takes no argument {name!r}."
        if name not in arg_bounds:
            return f"{name} is not bounded by the approved protocol, so it may not be sent."
    for item in fields.values():
        if not item.optional and item.name not in args:
            return f"{method}() needs {item.name}."
    for name in arg_bounds:
        if name not in args:
            return (f"{name} is bounded by the approved protocol, so every {method} call must send it "
                    "(leaving it out would use the driver's default).")
    key = f"{device_id}.{method}"
    for name, value in args.items():
        problem = _check_value(step_key(device_id, method, name, args), name, fields[name].python_type,
                               arg_bounds[name], value, state, steps=steps)
        if problem:
            return problem

    if spec.pattern == STREAM_PATTERN:
        limits = bounds.get("stream")
        if not isinstance(limits, dict):
            return f"The approved protocol sets no stream limits for {method}."
        max_rate, max_samples = limits.get("max_sample_rate_hz"), limits.get("max_samples")
        if not _is_number(max_rate) or max_rate <= 0 or not _is_number(max_samples) or max_samples < 1:
            return f"The approved protocol's stream limits for {method} are not usable."
        rate, count = request.get("sample_rate_hz"), request.get("sample_count")
        if not _is_number(rate) or rate <= 0:
            return f"{method} needs sample_rate_hz, a number above 0."
        if rate > max_rate:
            return f"sample_rate_hz={rate!r} is above the approved {max_rate:g} Hz."
        if isinstance(count, bool) or not isinstance(count, int):
            return f"{method} needs sample_count, a whole number."
        if not 1 <= count <= max_samples:
            return f"sample_count={count} is outside the approved 1 … {max_samples:g}."
        if count / rate > seconds_left:
            return (
                f"{count} samples at {rate:g} Hz take {count / rate:.1f} s, "
                f"but the grant has only {seconds_left:.1f} s left."
            )
    elif "sample_rate_hz" in request or "sample_count" in request:
        return f"{method} is not a stream: send no sample_rate_hz or sample_count."

    max_calls, max_rate_hz = bounds.get("max_calls"), bounds.get("max_rate_hz")
    if not _is_number(max_calls) or not _is_number(max_rate_hz) or max_rate_hz <= 0:
        return f"The approved protocol's call limits for {method} are not usable."
    if state.calls.get(key, 0) >= max_calls:
        return f"{method} on {device_id} has used all {max_calls:g} approved calls."
    last = state.last_call_at.get(key)
    if last is not None and now - last < 1 / max_rate_hz:
        return (
            f"{method} on {device_id} may be called at most {max_rate_hz:g} times per second; "
            f"wait {1 / max_rate_hz - (now - last):.3f} s."
        )
    return None


def check_call(
    protocol: dict[str, Any], state: GrantCallState, request: dict[str, Any], *, now: float, seconds_left: float,
    steps: bool = True,
) -> str | None:
    """None if the approved ``protocol`` allows ``request`` now, else why not. Pure: changes nothing.

    ``now`` is the clock the call rates are measured with; ``seconds_left`` the grant's time left.
    ``steps=False`` leaves out max_step (only for the safe state, to which it doesn't apply).
    A change whose step reference is unknown is refused: :meth:`GrantManager.call` reads it first.
    """

    try:
        return _check(protocol, state, request, now=now, seconds_left=seconds_left, steps=steps)
    except Exception as exc:  # a malformed protocol or an odd value: refuse, never allow
        return f"The call could not be checked ({type(exc).__name__}), so it is refused."


def readings_needed(protocol: dict[str, Any], state: GrantCallState, request: Any) -> list[tuple[str, str, RpcSpec, dict[str, Any]]]:
    """The readings to take before ``request`` can be checked: (step key, field, reader, reader's args)
    for each stepped number whose position isn't known yet. [] when there are none, or the request is
    malformed (check_call then refuses it, or refuses for the unknown position if no reader exists)."""

    try:
        device = _protocol_device(protocol, request["device_id"])
        method = request["method"]
        bounds = device["methods"][method]["args"]
        contract = registry.get(device["device_type"])
        args = request["args"]
        needed = []
        for name, value in args.items():
            bound = bounds.get(name)
            if not isinstance(bound, dict) or bound.get("max_step") is None or not _is_number(value):
                continue
            key = step_key(request["device_id"], method, name, args)
            if _interval(state.last_values.get(key)) is not None:
                continue
            reader, _why = step_reader(contract, method, name)
            if reader is None:
                continue
            taken = {item.name for item in reader.forwarded_field_specs}
            needed.append((key, name, reader, {other: args[other] for other in args if other in taken}))
        return needed
    except Exception:  # noqa: BLE001 - malformed: nothing to read; check_call refuses it
        return []


# ---- grants -------------------------------------------------------------------------------


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clip(text: str, limit: int = MAX_REASON_CHARACTERS) -> str:
    text = redact(" ".join(str(text).split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_iso(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _read_small_json(path: Path) -> Any:
    """A JSON file of at most MAX_RECORD_BYTES. A replace by the other process can make Windows
    refuse an open for a moment, so that is retried briefly; OSError/ValueError otherwise."""

    for attempt in range(3):
        try:
            raw = path.read_bytes()
            break
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.02)
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("too large")
    return json.loads(raw.decode("utf-8"))


def read_grant_record(path: Path) -> dict[str, Any]:
    """``state/<project>/grants/<id>.json``; ValueError if it isn't that grant's record, OSError if unreadable."""

    record = _read_small_json(path)
    if not isinstance(record, dict) or record.get("id") != path.name[:-5] or GRANT_ID.fullmatch(path.name[:-5]) is None:
        raise ValueError("not a grant record")
    return record


def watchdog_claim_path(grants_folder: Path, grant_id: str) -> Path:
    """Where the watchdog keeps what it did with ``grant_id`` (it is the only writer)."""

    return Path(grants_folder) / f"{grant_id}{WATCHDOG_SUFFIX}"


def read_watchdog_claim(grants_folder: Path, grant_id: str) -> dict[str, Any] | None:
    """The watchdog's claim on ``grant_id``, or None (none, or not readable as a claim: logged)."""

    path = watchdog_claim_path(grants_folder, grant_id)
    try:
        claim = _read_small_json(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        LOGGER.warning("Could not read the watchdog's claim %s: %s", path, exc)
        return None
    if not isinstance(claim, dict) or claim.get("grant_id") != grant_id or claim.get("status") not in WATCHDOG_STATUSES:
        LOGGER.warning("Ignoring %s: not a watchdog claim on %s", path, grant_id)
        return None
    return claim


class _LeaseGone(Exception):
    """A lease lapsed and could not be taken again."""


@dataclass(eq=False)
class _Grant:
    id: str
    project_id: str
    run_id: str
    task: str
    protocol: dict[str, Any]
    sha256: str
    approved_by: str
    duration_minutes: float
    starts_at: float  # wall clock
    ends_at: float  # wall clock
    deadline: float  # the manager's clock
    token_sha256: str
    token: str | None  # the plaintext until the runner takes it
    contracts: dict[str, DeviceContract]
    devices: dict[str, Any]  # device id → leased device client (None while re-taking a lapsed lease)
    status: str = ACTIVE
    state: GrantCallState = field(default_factory=GrantCallState)
    calls: int = 0
    refused_calls: int = 0
    reason_code: str | None = None
    ended_reason: str | None = None
    ended_at: float | None = None
    in_flight: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    timer: asyncio.Task[None] | None = None
    end_task: asyncio.Task[dict[str, Any]] | None = None
    audit_path: Path | None = None
    audit_full: bool = False
    alive_at: float | None = None  # wall clock of the last heartbeat written (see HEARTBEAT_SECONDS)
    heartbeat_seconds: float = HEARTBEAT_SECONDS
    heartbeat_failures: int = 0
    claim_seen: str | None = None  # the watchdog claim status last noticed (logged once per change)
    handed_to_watchdog: bool = False
    safe_state_by: str | None = None  # "watchdog" once its outcome is copied in

    @property
    def device_ids(self) -> list[str]:
        return list(self.contracts)

    def public(self) -> dict[str, Any]:
        """The run record's ``grant`` field."""

        return {
            "id": self.id,
            "status": self.status,
            "approved_by": self.approved_by,
            "starts_at": _iso(self.starts_at),
            "ends_at": _iso(self.ends_at),
            "devices": self.device_ids,
            "calls": self.calls,
            "refused_calls": self.refused_calls,
            "ended_reason": self.ended_reason,
            "ended_at": _iso(self.ended_at) if self.ended_at is not None else None,
        }

    def record(self) -> dict[str, Any]:
        """What ``state/<project>/grants/<id>.json`` holds (the token only as its sha256)."""

        return {
            "schema_version": 1,
            **self.public(),
            "project_id": self.project_id,
            "run_id": self.run_id,
            "task": self.task,
            "sha256": self.sha256,
            "duration_minutes": self.duration_minutes,
            "token_sha256": self.token_sha256,
            "token_taken": self.token is None,
            # What the watchdog needs to apply the safe state without this process.
            "alive_at": self.alive_at,
            "heartbeat_seconds": self.heartbeat_seconds,
            "protocol": self.protocol,
            "handed_to_watchdog": self.handed_to_watchdog,
            "safe_state_by": self.safe_state_by,
        }


class GrantManager:
    """Every grant of this Control Center: its leases, its calls, its ending.

    ``client`` is the Control Center's ``AsyncLabClient`` (shared with the
    device sessions), or a function of no arguments returning it. ``store`` is
    the ``AutomationStore`` (grant records and audit lines go under its
    ``state/``), ``runs`` the ``RunStore`` (each grant's state is copied into
    its run with ``set_grant``, and an ended grant moves the run on to
    ``set_phase(..., "analyzing")``).
    """

    def __init__(
        self,
        client: Any,
        store: Any,
        runs: Any,
        settings: Any = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        if callable(client) and not hasattr(client, "acquire_device"):
            client = client()
        self._client = client
        self._store = store
        self._runs = runs
        self._settings = settings
        self._clock = clock
        self._wall = wall
        self._heartbeat_seconds = heartbeat_seconds
        self._grants: dict[str, _Grant] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._closed = False
        # Runs and devices a create() is taking leases for right now (two approvals at once).
        self._creating: set[str] = set()
        # Grants handed to the watchdog (here or by a restart) whose outcome is still to be copied in:
        # grant id → (project id, run id, the in-memory grant or None, since when).
        self._handed: dict[str, tuple[str, str, _Grant | None, float]] = {}
        self._monitor_task: asyncio.Task[None] | None = None

    # ---- where things are kept --------------------------------------------------------

    def _project_state(self, project_id: str) -> Path:
        return Path(self._store.runs_folder(project_id)).parent  # state/<project>

    def _grants_folder(self, project_id: str) -> Path:
        return self._project_state(project_id) / "grants"

    def _save(self, grant: _Grant) -> bool:
        """Write the grant's record; while it holds its leases this is also its heartbeat (alive_at)."""

        if grant.status in LIVE:
            grant.alive_at = self._wall()
        try:
            write_json_file(self._grants_folder(grant.project_id), f"{grant.id}.json", grant.record())
            return True
        except Exception:  # the grant goes on; its audit lines still say what happened
            LOGGER.exception("Could not save grant %s", grant.id)
            return False

    def _audit(self, grant: _Grant, entry: dict[str, Any]) -> None:
        if grant.audit_path is None:
            grant.audit_path = self._project_state(grant.project_id) / "audit" / grant.run_id / f"grant-{grant.id}.jsonl"
        line = (json.dumps({"at": _iso(self._wall()), "grant": grant.id, **entry}, ensure_ascii=False, default=str)
                + "\n").encode("utf-8", "replace")
        try:
            grant.audit_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                size = grant.audit_path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size + len(line) > MAX_AUDIT_BYTES and entry.get("kind") == "call":
                if not grant.audit_full:
                    grant.audit_full = True
                    LOGGER.warning("The audit log of grant %s is full: ending it", grant.id)
                    self._start_ending(grant, "audit_full", "the audit log is full")
                return
            with open(grant.audit_path, "ab") as file:
                file.write(line)
        except OSError:
            LOGGER.exception("Could not write the audit line of grant %s", grant.id)
            if grant.status == ACTIVE:  # calls that can't be audited are not allowed
                self._start_ending(grant, "audit_full", "the audit log could not be written")

    async def _update_run(self, grant: _Grant, *, analyzing: bool) -> None:
        """Copy the grant into its run; when it has ended, move a still-active run on to analyzing."""

        public = grant.public()
        try:
            await asyncio.to_thread(self._runs.set_grant, grant.project_id, grant.run_id, public)
        except Exception as exc:
            LOGGER.warning("Could not record grant %s in run %s: %s", grant.id, grant.run_id, exc)
        if not analyzing:
            return
        try:
            run = await asyncio.to_thread(self._runs.get, grant.project_id, grant.run_id)
            if run.get("status") == "active":
                await asyncio.to_thread(self._runs.set_phase, grant.project_id, grant.run_id, "analyzing")
        except Exception as exc:
            LOGGER.warning("Could not move run %s on to analyzing: %s", grant.run_id, exc)

    # ---- leases -------------------------------------------------------------------------

    async def _lease(self, device_id: str, device_type: str) -> tuple[DeviceContract, Any]:
        contract = registry.find(device_type)
        if contract is None:
            raise GrantError(409, "conflict", f"This Control Center has no contract for {device_type!r}.")
        try:
            info = await asyncio.wait_for(self._client.get_device(device_id), LEASE_SECONDS)
        except ResourceNotFoundError:
            raise GrantError(409, "conflict", f"No device {device_id!r} is registered.") from None
        if info.device_type != device_type:
            raise GrantError(
                409, "conflict", f"{device_id} is a {info.device_type}, not the {device_type} the protocol names."
            )
        try:
            device = await asyncio.wait_for(self._client.acquire_device(contract, device_id), LEASE_SECONDS)
        except ResourceConflictError:
            raise GrantError(409, "conflict", f"{device_id} is in use by another client or unavailable.") from None
        return contract, device

    async def _lease_into(self, held: dict[str, Any], device_id: str, device_type: str) -> DeviceContract:
        """Lease ``device_id`` into ``held[device_id]``.

        If the caller is cancelled meanwhile, the lease is still waited for and put in
        ``held``, so whoever cleans up (the ending, or create's rollback) releases it
        instead of leaving it renewing itself in the background.
        """

        task = asyncio.ensure_future(self._lease(device_id, device_type))
        try:
            contract, device = await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                _contract, held[device_id] = await task
            except Exception:  # noqa: BLE001 - nothing was leased
                pass
            raise
        held[device_id] = device
        return contract

    async def _release(self, device_id: str, device: Any) -> bool:
        if device is None:
            return True
        try:
            await device.aclose()
            return True
        except Exception:
            LOGGER.exception("Releasing the lease on %s failed.", device_id)
            return False

    async def _reacquire(self, grant: _Grant, device_id: str) -> Any:
        """Take a lapsed lease again (once); _LeaseGone if that fails."""

        old = grant.devices.get(device_id)
        grant.devices[device_id] = None
        await self._release(device_id, old)
        device_type = grant.contracts[device_id].device_type
        try:
            await self._lease_into(grant.devices, device_id, device_type)
        except Exception as exc:
            self._audit(grant, {"kind": "lease", "device_id": device_id, "outcome": "lost",
                                "message": device_error(exc)})
            raise _LeaseGone(f"The lease on {device_id} lapsed and could not be taken again.") from exc
        self._audit(grant, {"kind": "lease", "device_id": device_id, "outcome": "retaken"})
        return grant.devices[device_id]

    # ---- creating a grant ------------------------------------------------------------------

    async def create(
        self,
        *,
        project_id: str,
        run_id: str,
        protocol: dict[str, Any],
        sha256: str,
        duration_minutes: float,
        approved_by: str,
    ) -> dict[str, Any]:
        """Take every lease the protocol names (each device's type checked) or none; returns the grant."""

        if self._closed:
            raise GrantError(409, "conflict", "The Control Center is shutting down.")
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise GrantError(422, "conflict", "A grant needs the name of the person who approved it.")
        approved_by = _clip(approved_by, 64)
        if (not _is_number(duration_minutes) or duration_minutes <= 0
                or (_is_number(protocol.get("duration_minutes")) and duration_minutes > protocol["duration_minutes"])):
            raise GrantError(422, "conflict", "The duration must be above 0 and at most what the protocol asks for.")
        devices = protocol.get("devices")
        if not isinstance(devices, list) or not devices or not all(
            isinstance(item, dict) and isinstance(item.get("device_id"), str)
            and isinstance(item.get("device_type"), str) and isinstance(item.get("methods"), dict)
            for item in devices
        ):
            raise GrantError(422, "conflict", "The protocol names no devices.")
        device_types = {item["device_id"]: item["device_type"] for item in devices}
        if len(device_types) != len(devices):
            raise GrantError(422, "conflict", "The protocol names a device twice.")
        for grant in self._grants.values():
            if grant.status in LIVE and grant.run_id == run_id:
                raise GrantError(409, "conflict", "This run already has an active grant.")
            if grant.status in LIVE and (taken := set(grant.contracts) & set(device_types)):
                raise GrantError(409, "conflict", f"{sorted(taken)[0]} is held by another Automation grant.")
        reserved = {f"run:{run_id}", *(f"device:{device_id}" for device_id in device_types)}
        if reserved & self._creating:
            raise GrantError(409, "conflict", "Another approval is taking these devices right now.")
        protocol = copy.deepcopy(protocol)

        contracts: dict[str, DeviceContract] = {}
        leases: dict[str, Any] = {}
        self._creating |= reserved
        try:
            for device_id, device_type in device_types.items():
                contracts[device_id] = await self._lease_into(leases, device_id, device_type)
        except BaseException as exc:
            for device_id, device in leases.items():
                await self._release(device_id, device)
            if isinstance(exc, GrantError):
                raise
            if isinstance(exc, Exception):
                raise GrantError(409, "conflict", f"The devices could not be leased: {device_error(exc)}") from exc
            raise
        finally:
            self._creating -= reserved
        if self._closed:  # shutdown began while the leases were being taken: no grant may outlive it
            for device_id, device in leases.items():
                await self._release(device_id, device)
            raise GrantError(409, "conflict", "The Control Center is shutting down.")

        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now, wall = self._clock(), self._wall()
        grant = _Grant(
            id="grant-" + secrets.token_hex(8), project_id=project_id, run_id=run_id,
            task=str(protocol.get("task") or ""), protocol=protocol, sha256=str(sha256), approved_by=approved_by,
            duration_minutes=float(duration_minutes), starts_at=wall, ends_at=wall + duration_minutes * 60,
            deadline=now + duration_minutes * 60, token_sha256=_token_hash(token), token=token,
            contracts=contracts, devices=leases, heartbeat_seconds=self._heartbeat_seconds,
        )
        self._grants[grant.id] = grant
        self._save(grant)
        self._ensure_monitor()  # the heartbeat the watchdog watches
        self._audit(grant, {"kind": "start", "approved_by": approved_by, "sha256": grant.sha256,
                            "devices": grant.device_ids, "duration_minutes": grant.duration_minutes})
        grant.timer = asyncio.create_task(self._expire_later(grant), name=f"grant-expiry-{grant.id}")
        await self._update_run(grant, analyzing=False)
        LOGGER.info("Grant %s for %s/%s: %s for %g min, approved by %s", grant.id, project_id, run_id,
                    ", ".join(grant.device_ids), duration_minutes, approved_by)
        return grant.public()

    async def _expire_later(self, grant: _Grant) -> None:
        await asyncio.sleep(max(0.0, grant.deadline - self._clock()))
        if grant.status == ACTIVE:
            await asyncio.shield(self._start_ending(grant, "expired", "expired"))

    # ---- the token -------------------------------------------------------------------------

    def _live_for_run(self, run_id: str) -> _Grant | None:
        return next((grant for grant in self._grants.values() if grant.run_id == run_id and grant.status in LIVE), None)

    def take_token(self, run_id: str) -> tuple[str, dict[str, Any]]:
        """The active grant's token, once; 409 if it was taken or the run has no active grant."""

        grant = self._live_for_run(run_id)
        if grant is None or grant.status != ACTIVE:
            raise GrantError(409, "ended", "This run has no active grant.")
        if grant.token is None:
            raise GrantError(409, "conflict", "This grant's token was already taken.")
        token, grant.token = grant.token, None
        self._save(grant)
        self._audit(grant, {"kind": "token", "outcome": "taken"})
        return token, grant.public()

    def protocol_of(self, grant_id: str) -> dict[str, Any]:
        """The normalized protocol a grant was approved with (a copy): what the runner tells the agent."""

        return copy.deepcopy(self._find(grant_id).protocol)

    def refused_before_call(self, token: Any, error: GrantError) -> GrantError:
        """A call refused before it could be read (not JSON, too large, not JSON's media type).

        Counted like any refused call of the grant ``token`` belongs to (an audit line,
        refused_calls, and the refusals in a row that end a grant); ``error`` is returned
        to raise. Nothing is attributed to a token no grant has."""

        try:
            grant = self._authenticate(token)
        except GrantError:
            return error
        self._audit(grant, {"kind": "call", "outcome": error.code, "http_status": error.status_code,
                            "message": redact(error.detail)})
        if grant.status == ACTIVE:
            grant.refused_calls += 1
            grant.state.refused_in_a_row += 1
            if grant.state.refused_in_a_row >= MAX_REFUSED_IN_A_ROW:
                self._start_ending(grant, "violations", f"violations: {MAX_REFUSED_IN_A_ROW} refused calls in a row")
        return error

    def authenticate(self, token: Any) -> str:
        """The id of the grant ``token`` belongs to (live or ended); GrantError 401 if none."""

        return self._authenticate(token).id

    def _authenticate(self, token: Any) -> _Grant:
        if isinstance(token, str) and token.startswith(TOKEN_PREFIX) and len(token) <= MAX_TOKEN_CHARACTERS:
            digest = _token_hash(token)
            for grant in list(self._grants.values()):
                if hmac.compare_digest(grant.token_sha256, digest):
                    return grant
        raise GrantError(401, "ended", "This grant token is not valid, or its grant is over.")

    # ---- calls -----------------------------------------------------------------------------

    def seconds_left(self, grant: _Grant) -> float:
        return max(0.0, grant.deadline - self._clock())

    async def call(self, token: str, request: dict[str, Any]) -> dict[str, Any]:
        """Check one device call against the grant and run it; GrantError (with a code) if refused."""

        grant = self._authenticate(token)
        started = self._clock()
        entry: dict[str, Any] = {"kind": "call"}
        if isinstance(request, dict):
            entry.update({key: request[key] for key in REQUEST_KEYS if key in request})

        def refuse(status: int, code: str, message: str) -> GrantError:
            self._audit(grant, {**entry, "outcome": code, "message": redact(message),
                                "duration_ms": round((self._clock() - started) * 1000, 3)})
            return GrantError(status, code, message)

        if grant.status != ACTIVE:
            if grant.reason_code == "expired":
                raise refuse(410, "expired", "The grant's time is up.")
            raise refuse(410, "ended", f"The grant has ended ({grant.ended_reason or grant.status}).")
        seconds_left = grant.deadline - started
        if seconds_left <= 0:
            self._start_ending(grant, "expired", "expired")
            raise refuse(410, "expired", "The grant's time is up.")
        if isinstance(request, dict) and isinstance(request.get("device_id"), str) \
                and request["device_id"] in grant.state.busy:
            grant.refused_calls += 1
            raise refuse(409, "busy", f"{request['device_id']} is busy with another call (one at a time per device).")

        def violation(status: int, code: str, message: str) -> GrantError:
            grant.refused_calls += 1
            grant.state.refused_in_a_row += 1
            error = refuse(status, code, message)
            if grant.state.refused_in_a_row >= MAX_REFUSED_IN_A_ROW:
                self._start_ending(grant, "violations", f"violations: {MAX_REFUSED_IN_A_ROW} refused calls in a row")
            return error

        readings = readings_needed(grant.protocol, grant.state, request)
        if readings:
            # Everything else first, as if each stepped number were where it is asked to go;
            # only a call that passes is worth reading the device for.
            probe = GrantCallState(calls=grant.state.calls, last_call_at=grant.state.last_call_at,
                                   last_values={**grant.state.last_values,
                                                **{key: (request["args"][name], request["args"][name])
                                                   for key, name, _reader, _args in readings}},
                                   busy=grant.state.busy, refused_in_a_row=grant.state.refused_in_a_row)
            problem = check_call(grant.protocol, probe, request, now=started, seconds_left=seconds_left)
            if problem is not None:
                raise violation(422, "refused", problem)
            await self._take_readings(grant, request["device_id"], readings, refuse=refuse, violation=violation)
            if grant.status != ACTIVE:
                code = "expired" if grant.reason_code == "expired" else "ended"
                raise refuse(410, code, "The grant ended while the device was being read.")
            seconds_left = grant.deadline - self._clock()
            if seconds_left <= 0:
                self._start_ending(grant, "expired", "expired")
                raise refuse(410, "expired", "The grant's time is up.")
        problem = check_call(grant.protocol, grant.state, request, now=started, seconds_left=seconds_left)
        if problem is not None:
            raise violation(422, "refused", problem)

        device_id, method = request["device_id"], request["method"]
        spec = find_method(grant.contracts[device_id], method)
        assert spec is not None  # check_call found it
        kwargs = dict(request["args"])
        if spec.pattern == STREAM_PATTERN:
            kwargs["sample_rate_hz"] = float(request["sample_rate_hz"])
            kwargs["sample_count"] = request["sample_count"]
        grant.state.started(request, started)
        grant.calls += 1
        retried: list[bool] = []
        task = asyncio.create_task(self._invoke(grant, device_id, spec, kwargs, retried))
        grant.in_flight[device_id] = task
        succeeded = False
        try:
            try:
                result = await asyncio.wait_for(task, timeout=seconds_left)
            except TimeoutError:
                raise refuse(410, "expired", "The grant's time ran out while the call was running.") from None
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():  # the runner hung up
                    self._audit(grant, {**entry, "outcome": "cancelled",
                                        "duration_ms": round((self._clock() - started) * 1000, 3)})
                    raise
                code = "expired" if grant.reason_code == "expired" else "ended"
                raise refuse(410, code, "The grant ended while the call was running.") from None
            except _LeaseGone as exc:
                self._start_ending(grant, "lease_lost", f"lost the lease on {device_id}")
                raise refuse(424, "device_error", str(exc)) from None
            except Exception as exc:
                raise refuse(424, "device_error", device_error(exc)) from None
            succeeded = True
        finally:
            grant.in_flight.pop(device_id, None)
            grant.state.finished(request, succeeded=succeeded)
        limits = _protocol_device(grant.protocol, device_id)["methods"][method]  # type: ignore[index]
        calls_left = max(0, int(limits["max_calls"]) - grant.state.calls.get(f"{device_id}.{method}", 0))
        reply = {"result": json_safe(result), "calls_left": calls_left,
                 "seconds_left": round(max(0.0, grant.deadline - self._clock()), 3)}
        outcome: dict[str, Any] = {**entry, "outcome": "ok", "duration_ms": round((self._clock() - started) * 1000, 3)}
        if spec.pattern == STREAM_PATTERN:
            outcome["samples"] = len(result) if isinstance(result, list) else None
        else:
            outcome["result"] = reply["result"]
        if retried:
            outcome["retried_after_lease_lapsed"] = True
        self._audit(grant, outcome)
        return reply

    async def _take_readings(self, grant: _Grant, device_id: str,
                             readings: list[tuple[str, str, RpcSpec, dict[str, Any]]],
                             *, refuse: Callable[[int, str, str], GrantError],
                             violation: Callable[[int, str, str], GrantError]) -> None:
        """Read where each stepped number is now, before its first change (max_step is measured from it).

        With the grant's lease, one call at a time on the device as usual, not counted against the
        protocol's calls or rates, each reading audited. If a reading fails the change is refused:
        the step can't be checked, so it isn't sent (counted as a refused call: ``violation``)."""

        grant.state.busy.add(device_id)
        try:
            for key, name, reader, kwargs in readings:
                started = self._clock()
                entry = {"kind": "reading", "device_id": device_id, "method": reader.python_name, "args": kwargs,
                         "for": key}

                def failed(message: str) -> None:
                    self._audit(grant, {**entry, "outcome": "failed", "message": redact(message),
                                        "duration_ms": round((self._clock() - started) * 1000, 3)})

                task = asyncio.create_task(self._invoke(grant, device_id, reader, dict(kwargs), []))
                grant.in_flight[device_id] = task
                try:
                    result = await asyncio.wait_for(task, timeout=max(0.001, grant.deadline - self._clock()))
                except TimeoutError:
                    failed("the grant's time ran out")
                    raise refuse(410, "expired", "The grant's time ran out while the device was being read.") from None
                except asyncio.CancelledError:
                    failed("cancelled")
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():  # the runner hung up
                        raise
                    code = "expired" if grant.reason_code == "expired" else "ended"
                    raise refuse(410, code, "The grant ended while the device was being read.") from None
                except _LeaseGone as exc:
                    failed(str(exc))
                    self._start_ending(grant, "lease_lost", f"lost the lease on {device_id}")
                    raise violation(424, "device_error", str(exc)) from None
                except Exception as exc:
                    failed(device_error(exc))
                    raise violation(424, "device_error",
                                 f"{reader.python_name}() failed ({device_error(exc)}), so where {name} is now isn't "
                                 "known and a step limit can't be checked; the call was not sent.") from None
                finally:
                    grant.in_flight.pop(device_id, None)
                value = plain(result)
                if not _is_number(value):
                    failed(f"not a number: {_json_kind(value)}")
                    raise violation(424, "device_error",
                                 f"{reader.python_name}() didn't return a number, so where {name} is now isn't known "
                                 "and a step limit can't be checked; the call was not sent.")
                grant.state.last_values[key] = (value, value)
                self._audit(grant, {**entry, "outcome": "ok", "result": json_safe(value),
                                    "duration_ms": round((self._clock() - started) * 1000, 3)})
        finally:
            grant.state.busy.discard(device_id)

    async def _invoke(self, grant: _Grant, device_id: str, spec: RpcSpec, kwargs: dict[str, Any],
                      retried: list[bool]) -> Any:
        """One device call; a lapsed lease is taken again once, and the call retried only if it never ran."""

        device = grant.devices.get(device_id)
        if device is None:
            device = await self._reacquire(grant, device_id)
        try:
            return await call_device(device, spec, kwargs)
        except Exception as exc:
            before = lease_lost_before_call(exc)
            if not before and not lease_rejected(exc):
                raise
            device = await self._reacquire(grant, device_id)
            # A write or command the Lab PC refused may or may not have run: don't send it twice.
            if not before and spec.pattern not in READ_PATTERNS:
                raise
            retried.append(True)
            return await call_device(device, spec, kwargs)

    # ---- ending ----------------------------------------------------------------------------

    def _start_ending(self, grant: _Grant, code: str, reason: str) -> asyncio.Task[dict[str, Any]]:
        """Refuse new calls at once, and end the grant in the background (once)."""

        if grant.end_task is None:
            grant.status = ENDING
            grant.reason_code = code
            grant.ended_reason = _clip(reason)
            grant.end_task = asyncio.create_task(self._ending(grant), name=f"grant-end-{grant.id}")
            self._background.add(grant.end_task)
            grant.end_task.add_done_callback(self._background.discard)
        return grant.end_task

    async def _ending(self, grant: _Grant) -> dict[str, Any]:
        LOGGER.info("Ending grant %s: %s", grant.id, grant.ended_reason)
        self._save(grant)
        if grant.timer is not None and grant.timer is not asyncio.current_task() and not grant.timer.done():
            grant.timer.cancel()
        # Calls in flight are cancelled (the gRPC call with them).
        running = list(grant.in_flight.values())
        for task in running:
            task.cancel()
        for task in running:
            try:
                await task
            except BaseException:  # noqa: BLE001 - cancelled or failed: either way it is over
                pass
        # The watchdog holds a lease of this grant (this loop was hung or slow): the safe state is its.
        handed = self._watchdog_applies(grant)
        unsafe = False
        if not handed:
            unsafe = not await self._apply_safe_state(grant)
            if unsafe and self._watchdog_applies(grant):  # it took a lease while this one failed
                handed = True
        released = True
        for device_id in grant.device_ids:
            device = grant.devices.get(device_id)
            grant.devices[device_id] = None
            released = await self._release(device_id, device) and released
        grant.ended_at = self._wall()
        if handed:
            grant.status = INTERRUPTED
            grant.handed_to_watchdog = True
            grant.ended_reason = _clip(HANDED_REASON)
            self._handed[grant.id] = (grant.project_id, grant.run_id, grant, self._wall())
            self._ensure_monitor()  # copies the watchdog's outcome in when it is final
            outcome = "handed_to_watchdog"
            LOGGER.warning("Grant %s was handed to the safe-state watchdog (no safe state applied here)", grant.id)
        else:
            grant.status = ENDED_UNSAFE if unsafe else ENDED
            outcome = grant.status
        self._audit(grant, {"kind": "end", "outcome": outcome, "reason": grant.ended_reason,
                            "calls": grant.calls, "refused_calls": grant.refused_calls, "released": released})
        self._save(grant)
        await self._update_run(grant, analyzing=True)
        if unsafe:
            LOGGER.error("Grant %s ended UNSAFE: its safe state could not be applied", grant.id)
        return grant.public()

    async def _apply_safe_state(self, grant: _Grant) -> bool:
        """Every safe-state call, each up to SAFE_STATE_TRIES times; False if any never succeeded."""

        calls = grant.protocol.get("safe_state") or []
        if not isinstance(calls, list):
            return False
        ok = True
        for index, item in enumerate(calls):
            if not await self._safe_call(grant, index, item):
                ok = False
        return ok

    async def _safe_call(self, grant: _Grant, index: int, item: Any) -> bool:
        entry: dict[str, Any] = {"kind": "safe_state", "index": index}
        try:
            device_id, method, args = item["device_id"], item["method"], dict(item.get("args") or {})
            spec = find_method(grant.contracts[device_id], method)
            if spec is None or spec.pattern == STREAM_PATTERN:
                raise ValueError(f"{method} is not a method that sets {device_id}")
            # Approved with the protocol, so within its bounds; checked again all the same
            # (max_step, call counts and rates don't apply to the safe state).
            problem = check_call(grant.protocol, GrantCallState(), {"device_id": device_id, "method": method,
                                 "args": args}, now=0.0, seconds_left=1e9, steps=False)
            if problem is not None:
                raise ValueError(problem)
        except Exception as exc:
            self._audit(grant, {**entry, "outcome": "failed", "message": device_error(exc)})
            return False
        entry.update({"device_id": device_id, "method": method, "args": args})
        retaken = False
        for attempt in range(1, SAFE_STATE_TRIES + 1):
            started = self._clock()
            try:
                device = grant.devices.get(device_id)
                if device is None:
                    if retaken:
                        raise _LeaseGone(f"The lease on {device_id} could not be taken again.")
                    retaken = True
                    device = await self._reacquire(grant, device_id)
                await asyncio.wait_for(call_device(device, spec, args), SAFE_STATE_CALL_SECONDS)
            except Exception as exc:
                self._audit(grant, {**entry, "attempt": attempt, "outcome": "failed", "message": device_error(exc),
                                    "duration_ms": round((self._clock() - started) * 1000, 3)})
                if (lease_lost_before_call(exc) or lease_rejected(exc)) and not retaken:
                    retaken = True
                    try:
                        await self._reacquire(grant, device_id)
                    except _LeaseGone:
                        return False
                continue
            self._audit(grant, {**entry, "attempt": attempt, "outcome": "ok",
                                "duration_ms": round((self._clock() - started) * 1000, 3)})
            return True
        return False

    def _find(self, grant_id: str) -> _Grant:
        grant = self._grants.get(grant_id) if isinstance(grant_id, str) else None
        if grant is None:
            raise GrantError(404, "ended", "No such grant.")
        return grant

    async def stop(self, grant_id: str, *, reason: str, by: str) -> dict[str, Any]:
        """End the grant now (safe state first); its state once it has ended."""

        grant = self._find(grant_id)
        if grant.status in LIVE:
            text = f"{reason} by {by}" if by else reason
            await asyncio.shield(self._start_ending(grant, "stopped", text))
        return grant.public()

    async def end_for_run(self, run_id: str, *, reason: str) -> dict[str, Any] | None:
        """End the run's grant, if it has a live one; its state once ended, else None."""

        grant = self._live_for_run(run_id)
        if grant is None:
            return None
        await asyncio.shield(self._start_ending(grant, "run", reason))
        return grant.public()

    def grant_for_run(self, run_id: str) -> dict[str, Any] | None:
        """The run's latest grant in this process (live or ended), or None."""

        found = [grant for grant in self._grants.values() if grant.run_id == run_id]
        return max(found, key=lambda grant: grant.starts_at).public() if found else None

    def grant_id_for_run(self, run_id: str) -> str | None:
        grant = self._live_for_run(run_id)
        return grant.id if grant is not None else None

    # ---- what the Network page and the device sessions see ---------------------------------

    def blocked(self, device_id: str) -> bool:
        """True while a grant holds ``device_id`` (Play and scripts are refused on it)."""

        return any(grant.status in LIVE and device_id in grant.contracts for grant in self._grants.values())

    def activity(self) -> dict[str, dict[str, Any]]:
        """Devices the grants hold, as the Network and Devices pages show holders."""

        held: dict[str, dict[str, Any]] = {}
        for grant in self._grants.values():
            if grant.status not in LIVE:
                continue
            task = PurePosixPath(grant.task).stem if grant.task else grant.run_id
            for device_id in grant.device_ids:
                held[device_id] = {
                    "session": None,
                    "since": grant.starts_at,
                    "streaming": False,
                    "script_running": False,
                    "viewers": 0,
                    "page": f"/automation/{grant.project_id}",
                    "automation": {
                        "grant_id": grant.id,
                        "project_id": grant.project_id,
                        "run_id": grant.run_id,
                        "task": task,
                        "label": f"{grant.project_id}/{task}",
                        "status": grant.status,
                        "ends_at": grant.ends_at,
                        "seconds_left": round(self.seconds_left(grant), 1),
                        "stop_url": f"/api/automation/grants/{grant.id}/stop",
                    },
                }
        return held

    # ---- the watchdog's heartbeat and hand-over (see the module docstring) -------------------

    def _watchdog_claim(self, grant: _Grant) -> dict[str, Any] | None:
        return read_watchdog_claim(self._grants_folder(grant.project_id), grant.id)

    def _watchdog_applies(self, grant: _Grant) -> bool:
        claim = self._watchdog_claim(grant)
        return claim is not None and claim["status"] in WATCHDOG_APPLIES

    def start_monitor(self) -> None:
        """Start the heartbeat task if there is anything to watch (the app calls this at startup,
        after :meth:`mark_interrupted`; :meth:`create` starts it for each new grant)."""

        self._ensure_monitor()

    def _ensure_monitor(self) -> None:
        if self._monitor_task is not None and not self._monitor_task.done():
            return
        if not self._handed and not any(grant.status in LIVE for grant in self._grants.values()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (a test calling mark_interrupted alone): nothing to run it on
            return
        self._monitor_task = loop.create_task(self._monitor(), name="grant-heartbeat")

    async def _monitor(self) -> None:
        """Runs on this event loop while a grant holds leases or a hand-over awaits its outcome."""

        while True:
            try:
                self._beat()
                if self._handed:
                    await self._follow_handed()
            except Exception:  # noqa: BLE001 - the next beat tries again
                LOGGER.exception("The grant heartbeat failed")
            if not self._handed and not any(grant.status in LIVE for grant in self._grants.values()):
                return
            await asyncio.sleep(self._heartbeat_seconds)

    def _beat(self) -> None:
        """One heartbeat: each live grant's record gets a fresh alive_at, unless the watchdog holds it."""

        for grant in list(self._grants.values()):
            if grant.status not in LIVE:
                continue
            claim = self._watchdog_claim(grant)
            status = claim["status"] if claim is not None else None
            if status != grant.claim_seen:
                grant.claim_seen = status
                if status == CLAIMED:
                    LOGGER.warning("The watchdog found grant %s's heartbeat stale and is about to take it over; "
                                   "this heartbeat makes it stand down", grant.id)
                elif status == STOOD_DOWN:
                    LOGGER.info("The watchdog stood down from grant %s", grant.id)
            if status in WATCHDOG_BINDING:
                if grant.end_task is None:  # an ending already under way notices it itself
                    LOGGER.warning("The watchdog took grant %s over while this loop was not answering (claim %s): "
                                   "ending it here", grant.id, status)
                    self._start_ending(grant, "watchdog", HANDED_REASON if status in WATCHDOG_APPLIES else
                                       "the Control Center stopped answering and the watchdog could not apply the "
                                       "safe state")
                continue
            if self._save(grant):
                grant.heartbeat_failures = 0
                continue
            grant.heartbeat_failures += 1
            if grant.heartbeat_failures >= HEARTBEAT_FAILURES_TO_END and grant.status == ACTIVE:
                # The watchdog would think this process is gone: end the grant here, with its safe state.
                self._start_ending(grant, "heartbeat", "the grant's heartbeat for the watchdog could not be written")

    async def _follow_handed(self) -> None:
        """Copy each handed-over grant's outcome in once the watchdog's claim is final."""

        for grant_id, (project_id, run_id, grant, since) in list(self._handed.items()):
            claim = read_watchdog_claim(self._grants_folder(project_id), grant_id)
            if claim is None or claim["status"] not in WATCHDOG_FINAL:
                if self._wall() - since > HANDED_FOLLOW_SECONDS:
                    LOGGER.warning("No outcome from the watchdog for grant %s after %.0f min; the next start of "
                                   "the Control Center copies it in", grant_id, HANDED_FOLLOW_SECONDS / 60)
                    self._handed.pop(grant_id, None)
                continue
            self._handed.pop(grant_id, None)
            if grant is not None:
                grant.status = claim["status"]
                grant.ended_reason = _clip(claim.get("ended_reason") or claim["status"])
                grant.ended_at = _parse_iso(claim.get("ended_at")) or grant.ended_at
                grant.safe_state_by = "watchdog"
                self._save(grant)
                await self._update_run(grant, analyzing=False)
            else:
                await asyncio.to_thread(self._merge_claim, self._grants_folder(project_id), grant_id, claim)
            self._log_outcome(grant_id, claim)

    @staticmethod
    def _log_outcome(grant_id: str, claim: dict[str, Any]) -> None:
        if claim["status"] == ENDED_UNSAFE:
            LOGGER.error("The watchdog could NOT put grant %s's devices in their safe state: %s", grant_id,
                         claim.get("ended_reason"))
        else:
            LOGGER.warning("The watchdog applied grant %s's safe state: %s", grant_id, claim.get("ended_reason"))

    def _merge_claim(self, folder: Path, grant_id: str, claim: dict[str, Any]) -> dict[str, Any] | None:
        """Copy a final watchdog claim into the grant's record on disk, and into its run; the merged record."""

        try:
            record = read_grant_record(folder / f"{grant_id}.json")
        except (OSError, ValueError) as exc:
            LOGGER.warning("Could not read grant %s to record the watchdog's outcome: %s", grant_id, exc)
            return None
        record.update({
            "status": claim["status"],
            "ended_reason": _clip(claim.get("ended_reason") or claim["status"]),
            "ended_at": claim.get("ended_at") if _parse_iso(claim.get("ended_at")) else _iso(self._wall()),
            "safe_state_by": "watchdog",
        })
        try:
            write_json_file(folder, f"{grant_id}.json", record)
        except Exception:
            LOGGER.exception("Could not record the watchdog's outcome in grant %s", grant_id)
            return None
        self._record_in_run(folder.parent.name, record, analyzing=False)
        return record

    def _record_in_run(self, project_id: str, record: dict[str, Any], *, analyzing: bool) -> None:
        public = {key: record.get(key) for key in PUBLIC_KEYS}
        run_id = record.get("run_id")
        try:
            self._runs.set_grant(project_id, run_id, public)
            if analyzing and self._runs.get(project_id, run_id).get("status") == "active":
                self._runs.set_phase(project_id, run_id, "analyzing")
        except Exception as exc:
            LOGGER.warning("Could not record grant %s in its run: %s", record.get("id"), exc)

    # ---- restart and shutdown ----------------------------------------------------------------

    def mark_interrupted(self) -> None:
        """At startup: grants left active (or ending) on disk were cut off by a restart.

        Each is marked ``interrupted`` (no safe state was applied by this process), unless the
        watchdog has already finished with it: then its outcome (``ended``/``ended_unsafe``) is
        kept, never overwritten. A grant the watchdog may still be handling is followed (see
        :meth:`start_monitor`), and an ``interrupted`` grant whose watchdog outcome came in while
        this process was down gets that outcome now.
        """

        root = Path(self._store.state)
        try:
            projects = [entry for entry in os.scandir(root)
                        if entry.is_dir(follow_symlinks=False) and PROJECT_ID.fullmatch(entry.name)]
        except FileNotFoundError:
            return
        for project in projects:
            folder = Path(project.path) / "grants"
            try:
                entries = list(os.scandir(folder))
            except (FileNotFoundError, NotADirectoryError):
                continue
            for entry in entries:
                if not entry.name.endswith(".json") or GRANT_ID.fullmatch(entry.name[:-5]) is None:
                    continue
                try:
                    record = read_grant_record(Path(entry.path))
                except (OSError, ValueError) as exc:
                    LOGGER.warning("Skipping the unreadable grant record %s: %s", entry.path, exc)
                    continue
                grant_id, status = record["id"], record.get("status")
                if status not in LIVE and not (status == INTERRUPTED and record.get("safe_state_by") is None):
                    continue
                claim = read_watchdog_claim(folder, grant_id)
                if claim is not None and claim["status"] in WATCHDOG_FINAL:
                    # The watchdog already finished with it: its outcome stands.
                    merged = self._merge_claim(folder, grant_id, claim)
                    if merged is not None:
                        self._log_outcome(grant_id, claim)
                        if status in LIVE:  # the run may still be executing: move it on (with the outcome)
                            self._record_in_run(project.name, merged, analyzing=True)
                    continue
                if status not in LIVE:
                    continue  # interrupted earlier, and no outcome from the watchdog yet
                watching = claim is not None and claim["status"] in (CLAIMED, SAFING)
                record.update({
                    "status": INTERRUPTED,
                    "ended_reason": (HANDED_REASON if watching else
                                     "the Control Center restarted during the grant (no safe state was applied)"),
                    "ended_at": _iso(self._wall()),
                    "handed_to_watchdog": watching or bool(record.get("handed_to_watchdog")),
                })
                try:
                    write_json_file(folder, entry.name, record)
                except Exception:
                    LOGGER.exception("Could not mark grant %s interrupted", grant_id)
                    continue
                LOGGER.warning("Grant %s was interrupted by a restart%s", grant_id,
                               " (the watchdog is handling it)" if watching else "")
                self._record_in_run(project.name, record, analyzing=True)
                if isinstance(record.get("protocol"), dict):  # a watchdog can act on it: follow its outcome
                    self._handed[grant_id] = (project.name, str(record.get("run_id")), None, self._wall())

    async def close(self) -> None:
        """Shutdown: end every grant (safe state, release) before the leases' client closes."""

        self._closed = True
        endings = [self._start_ending(grant, "shutdown", "the Control Center is shutting down")
                   for grant in list(self._grants.values()) if grant.status in LIVE]
        endings += [grant.end_task for grant in self._grants.values()
                    if grant.end_task is not None and grant.end_task not in endings]
        if endings:
            await asyncio.gather(*endings, return_exceptions=True)
        for grant in self._grants.values():
            if grant.timer is not None and not grant.timer.done():
                grant.timer.cancel()
        # The heartbeat ran until every grant ended; outcomes still pending are copied in at the next start.
        if self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except BaseException:  # noqa: BLE001 - cancelled
                pass


__all__ = [
    "ACTIVE", "CLAIMED", "ENDED", "ENDED_UNSAFE", "ENDING", "GRANT_ID", "HANDED_REASON", "HEARTBEAT_SECONDS",
    "INTERRUPTED", "LIVE", "MAX_REFUSED_IN_A_ROW", "PUBLIC_KEYS", "SAFE_STATE_CALL_SECONDS", "SAFE_STATE_TRIES",
    "SAFING", "STOOD_DOWN", "WATCHDOG_APPLIES", "WATCHDOG_BINDING", "WATCHDOG_FINAL", "WATCHDOG_STATUSES",
    "WATCHDOG_SUFFIX",
    "GrantCallState", "GrantError", "GrantManager", "check_call", "read_grant_record", "read_watchdog_claim",
    "readings_needed", "step_key", "watchdog_claim_path",
]
