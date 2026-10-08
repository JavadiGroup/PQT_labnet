"""Automation runs: one task of one project, worked through by a runner.

    state/<project>/runs/<run_id>.json           the record: status, who, when, bytes uploaded
    state/<project>/runs/<run_id>.events.jsonl   progress and transcript lines from the runner

A person queues a task (``Tasks/Task1.md``). A runner claims the oldest queued
run of any project, downloads the project's inputs, uploads into the agent's
folders, reports events, and finishes the run; a person can cancel it at any
time, and the runner notices on its next request.

Everything a runner sends is untrusted. Only the runner that claimed a run may
touch it, and only while it is active. Uploads go through ``check_agent_write``
and count against the run's quota, including uploads still streaming, so two
at once can't pass it together. Events and summaries are size-capped and
redacted. Records live in ``state/``, which no runner can reach; one lock
serializes changes to them, so a claim, a cancel and an upload's commit never
interleave.

A queued run carries a copy of its project's run settings (model, effort, max
turns, budget), so changing the project later changes only later runs. Every
runner request about an active run, including a bare heartbeat, records that
the runner was heard from (``last_seen_at``). An active run not heard from for
``stale_minutes`` is marked failed: whenever runs are read, and by
``sweep_forever`` once a minute. The clock starts no earlier than this process,
so runners get a full window to reconnect after the Control Center restarts.

Phase 3 (protocols and grants). While a run is active its ``phase`` says what
it is doing: ``planning`` from the claim; ``awaiting_approval`` once the
Control Center accepted its protocol (``accept_protocol``; at most
MAX_SUBMISSIONS per run); back to ``planning`` when a person rejects it, with
the note in ``protocol.note``; ``executing`` once a person approved it and the
grant took its leases; ``analyzing`` once the grant ended. A protocol not
approved or rejected within ``approval_hours`` fails the run. The normalized
protocol a person reviews is kept as ``state/<project>/protocols/<run>-<n>.json``
(its canonical text, so its sha256 is the file's), the agent's prose beside it
as ``<run>-<n>.md``. An approver may edit it before approving ("Edit and
approve"): their version passes the same checks, is kept as
``<run>-<n>-edited.json`` with its own sha256 (``protocol.approved_sha256``),
and the record lists what changed (``protocol_changes``), which the runner
quotes to the agent.

"Run with…": a run may be queued with its own model, effort, max turns and
budget (``RunOverrides``), checked as the project's settings are; the project
is unchanged.

The ``grant`` field belongs to the GrantManager (``automation_grants.py``),
which writes it with :meth:`RunStore.set_grant` and moves the phase with
:meth:`RunStore.set_phase`. A grant whose safe state failed (``ended_unsafe``)
makes the run end ``ended_unsafe`` however it finishes. ``on_run_ended`` is
called after a run stops being active (cancelled, timed out, finished), so the
GrantManager can end its grant.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, BinaryIO, Literal

from pydantic import Field, ValidationError

from .agent import supports_effort
from .automation_paths import PathRefused, clean_relative, open_inside, zone_of
from .automation_protocol import MAX_NOTE_CHARACTERS, MAX_PROSE_BYTES, canonical
from .automation_store import PROJECT_ID, AutomationError, AutomationStore, ModelName, refusal, write_json_file
from .config import AGENT_EFFORTS, DEFAULT_AGENT_MODELS
from .experiment_schema import DeviceId, Text, _Strict, problems_from
from .experiment_store import replace_with_retry
from .redaction import redact


LOGGER = logging.getLogger(__name__)
RUN_ID = re.compile(r"run-[0-9]{8}-[0-9]{6}-[a-z2-9]{4}")
_ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
SCHEMA_VERSION = 1
QUEUED, ACTIVE, DONE, FAILED, STOPPED, CANCELLED = "queued", "active", "done", "failed", "stopped", "cancelled"
ENDED_UNSAFE = "ended_unsafe"  # the run's grant couldn't put its devices in their safe state
OPEN = frozenset({QUEUED, ACTIVE})
FINISHED = (DONE, FAILED, STOPPED)  # how a runner may end a run
PLANNING, AWAITING_APPROVAL, EXECUTING, ANALYZING = "planning", "awaiting_approval", "executing", "analyzing"
# How an active run's phase may move (set_phase); staying put is always fine.
PHASE_MOVES = {
    PLANNING: frozenset({AWAITING_APPROVAL}),
    AWAITING_APPROVAL: frozenset({PLANNING, EXECUTING, ANALYZING}),
    EXECUTING: frozenset({ANALYZING}),
    ANALYZING: frozenset(),
}
MAX_SUBMISSIONS = 3  # protocols a run may send for approval (ones the checks refused don't count)
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_CHANGES = 60  # lines of an approver's edits kept in the run record (then "… and N more")
MAX_CHANGE_CHARACTERS = 240
# A run's own settings, given when it is queued ("Run with…"): request key → project setting.
RUN_OVERRIDES = {"model": "run_model", "effort": "run_effort", "max_turns": "run_max_turns",
                 "max_budget_usd": "run_budget_usd"}
EVENT_KIND = re.compile(r"[a-z][a-z0-9_]{0,31}")
MAX_EVENT_TEXT_BYTES = 16 * 1024
MAX_EVENTS_PER_REQUEST = 100
MAX_EVENTS_FILE_BYTES = 16 * 1024 * 1024
MAX_SUMMARY_CHARACTERS = 4000
MAX_RUN_BYTES = 64 * 1024
TASKS_ZONE = "Tasks"
SWEEP_SECONDS = 60.0  # how often sweep_forever looks for runs whose runner went quiet


class RunSettings(_Strict):
    """How the runner runs the agent, resolved when the run was queued."""

    model: ModelName
    effort: Literal[AGENT_EFFORTS] | None  # None: the model has no effort levels (Haiku)
    max_turns: int = Field(ge=1, le=200)
    max_budget_usd: float = Field(gt=0, le=100)


class RunOverrides(_Strict):
    """A run's own settings from "Run with…": each the same kind of value as the project's
    run setting it overrides (``ProjectSettings``); None or left out keeps the project's."""

    model: ModelName | None = None
    effort: Literal[AGENT_EFFORTS] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=200)
    max_budget_usd: float | None = Field(default=None, gt=0, le=100)


Stamp = Annotated[str, Field(max_length=40)]
Sha256 = Annotated[str, Field(pattern=rf"^{SHA256.pattern}$")]


class RunProtocol(_Strict):
    """The run's latest protocol submission (the protocol itself is in state/<project>/protocols/)."""

    submissions: int = Field(ge=1, le=MAX_SUBMISSIONS)
    status: Literal["submitted", "approved", "rejected"]
    sha256: Sha256  # the agent's submission, as normalized
    # Once approved: the version the grant enforces. The submission's sha256, unless the
    # approver edited it (then <run>-<n>-edited.json, edited_by and the list of changes).
    approved_sha256: Sha256 | None = None
    edited_by: Text(64) | None = None
    changes: list[Text(MAX_CHANGE_CHARACTERS)] | None = Field(default=None, max_length=MAX_CHANGES + 1)
    title: Text(120) = ""
    duration_minutes: float = Field(gt=0)  # as requested
    submitted_at: Stamp
    reviewed_by: Text(64) | None = None
    reviewed_at: Stamp | None = None
    approved_minutes: float | None = Field(default=None, gt=0)  # what the approver granted (≤ requested)
    note: Text(MAX_NOTE_CHARACTERS, multiline=True) | None = None  # the reviewer's, on a rejection


class RunGrant(_Strict):
    """The run's device grant, as the GrantManager reports it (``RunStore.set_grant``)."""

    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    status: Literal["active", "ending", "ended", "ended_unsafe", "interrupted"]
    approved_by: Text(64) = ""
    starts_at: Stamp | None = None
    ends_at: Stamp | None = None
    devices: list[DeviceId] = Field(default_factory=list, max_length=16)
    calls: int = Field(default=0, ge=0)
    refused_calls: int = Field(default=0, ge=0)
    ended_reason: Text(500, multiline=True) | None = None
    ended_at: Stamp | None = None


class Run(_Strict):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: Annotated[str, Field(pattern=rf"^{RUN_ID.pattern}$")]
    project_id: Annotated[str, Field(pattern=rf"^{PROJECT_ID.pattern}$")]
    task: Annotated[str, Field(max_length=240)]
    status: Literal["queued", "active", "done", "failed", "stopped", "cancelled", "ended_unsafe"]
    # While active: planning → awaiting_approval → executing → analyzing (None before the claim).
    phase: Literal["planning", "awaiting_approval", "executing", "analyzing"] | None = None
    queued_by: Text(64) = ""
    queued_at: Annotated[str, Field(max_length=40)]
    claimed_by: Annotated[str, Field(max_length=32)] | None = None
    claimed_at: Annotated[str, Field(max_length=40)] | None = None
    last_seen_at: Annotated[str, Field(max_length=40)] | None = None  # older records: claimed_at
    finished_at: Annotated[str, Field(max_length=40)] | None = None
    cancelled_by: Text(64) | None = None
    bytes_uploaded: int = Field(default=0, ge=0)
    quota_bytes: int = Field(ge=0)
    files_uploaded: int = Field(default=0, ge=0)
    summary: Text(MAX_SUMMARY_CHARACTERS, multiline=True) = ""
    settings: RunSettings | None = None  # None only in records from before run settings
    protocol: RunProtocol | None = None
    grant: RunGrant | None = None


def _parse_stamp(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _zip_time(timestamp: float) -> tuple[int, int, int, int, int, int]:
    moment = time.localtime(max(timestamp, 315532800 + 86400))  # zip dates start in 1980
    return moment[:6]


# ---- what an approver changed (both protocols normalized by validate_protocol) ----------------


def _shown(value: Any) -> str:
    """A protocol value as a change line shows it: 90 (not 90.0), true, "CH1", [..], none."""

    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_shown(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{key}: {_shown(item)}" for key, item in value.items()) + "}"
    return str(value)


def _same(old: Any, new: Any) -> bool:
    return json.dumps(old, sort_keys=True) == json.dumps(new, sort_keys=True)  # true ≠ 1, 1 ≠ 1.0


def _call_shown(call: dict[str, Any]) -> str:
    args = ", ".join(f"{name}={_shown(value)}" for name, value in call.get("args", {}).items())
    return f"{call.get('device_id')}.{call.get('method')}({args})"


def _bound_shown(bound: dict[str, Any]) -> str:
    if "choices" in bound:
        return f"choices {_shown(bound['choices'])}"
    text = f"{_shown(bound.get('min'))} to {_shown(bound.get('max'))}"
    return text + (f", max_step {_shown(bound['max_step'])}" if bound.get("max_step") is not None else "")


def protocol_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """What changed from ``before`` (the agent's protocol) to ``after`` (an approver's edit), one
    line each: ``dev01-aom.set_frequency.value.max: 92 → 90``. Both are normalized protocols."""

    changes: list[str] = []

    def changed(path: str, old: Any, new: Any) -> None:
        if not _same(old, new):
            changes.append(f"{path}: {_shown(old)} → {_shown(new)}")

    for key in ("task", "title", "duration_minutes"):
        changed(key, before.get(key), after.get(key))
    old_devices = {device["device_id"]: device for device in before.get("devices", [])}
    new_devices = {device["device_id"]: device for device in after.get("devices", [])}
    for device_id in old_devices:
        if device_id not in new_devices:
            changes.append(f"{device_id}: removed (no calls to it at all)")
    for device_id, device in new_devices.items():
        if device_id not in old_devices:
            changes.append(f"{device_id}: added, with {', '.join(sorted(device['methods']))}")
            continue
        old = old_devices[device_id]
        changed(f"{device_id}.device_type", old.get("device_type"), device.get("device_type"))
        for name in sorted(old["methods"]):
            if name not in device["methods"]:
                changes.append(f"{device_id}.{name}: removed (may not be called)")
        for name in sorted(device["methods"]):
            method, path = device["methods"][name], f"{device_id}.{name}"
            if name not in old["methods"]:
                bounds = "; ".join(f"{field} {_bound_shown(bound)}" for field, bound in method["args"].items())
                changes.append(f"{path}: added (at most {method['max_calls']} calls, "
                               f"{_shown(method['max_rate_hz'])}/s" + (f"; {bounds}" if bounds else "") + ")")
                continue
            was = old["methods"][name]
            for field in sorted(set(was["args"]) | set(method["args"])):
                old_bound, new_bound = was["args"].get(field), method["args"].get(field)
                if old_bound is None:
                    changes.append(f"{path}.{field}: now bounded, {_bound_shown(new_bound)}")
                elif new_bound is None:
                    changes.append(f"{path}.{field}: no longer bounded (may never be sent)")
                else:
                    for key in sorted(set(old_bound) | set(new_bound)):
                        changed(f"{path}.{field}.{key}", old_bound.get(key), new_bound.get(key))
            changed(f"{path}.max_calls", was.get("max_calls"), method.get("max_calls"))
            changed(f"{path}.max_rate_hz", was.get("max_rate_hz"), method.get("max_rate_hz"))
            old_stream, new_stream = was.get("stream") or {}, method.get("stream") or {}
            for key in sorted(set(old_stream) | set(new_stream)):
                changed(f"{path}.stream.{key}", old_stream.get(key), new_stream.get(key))

    old_safe, new_safe = before.get("safe_state", []), after.get("safe_state", [])
    if not _same(old_safe, new_safe):
        same_calls = len(old_safe) == len(new_safe) and all(
            (a["device_id"], a["method"]) == (b["device_id"], b["method"]) for a, b in zip(old_safe, new_safe))
        if same_calls:  # the same calls in the same order: only values changed
            for index, (old, new) in enumerate(zip(old_safe, new_safe)):
                for name in sorted(set(old["args"]) | set(new["args"])):
                    changed(f"safe_state[{index}] {old['device_id']}.{old['method']}.{name}",
                            old["args"].get(name), new["args"].get(name))
        else:
            old_texts, new_texts = [_call_shown(call) for call in old_safe], [_call_shown(call) for call in new_safe]
            remaining = list(new_texts)
            for text in old_texts:
                if text in remaining:
                    remaining.remove(text)
                else:
                    changes.append(f"safe_state: removed {text}")
            leftover = list(old_texts)
            for text in new_texts:
                if text in leftover:
                    leftover.remove(text)
                else:
                    changes.append(f"safe_state: added {text}")
            if sorted(old_texts) == sorted(new_texts):
                changes.append("safe_state: reordered, now " + "; ".join(new_texts))
    return changes


def _clip_changes(changes: list[str]) -> list[str]:
    """``changes`` as the run record keeps them: each line clipped, at most MAX_CHANGES lines."""

    clipped = [text if len(text) <= MAX_CHANGE_CHARACTERS else text[: MAX_CHANGE_CHARACTERS - 1] + "…"
               for text in changes[:MAX_CHANGES]]
    if len(changes) > MAX_CHANGES:
        clipped.append(f"… and {len(changes) - MAX_CHANGES} more changes")
    return clipped


class RunStore:
    def __init__(
        self,
        automation: AutomationStore,
        *,
        quota_bytes: int,
        stale_minutes: float = 10.0,
        models: tuple[str, ...] = DEFAULT_AGENT_MODELS,
        default_model: str = "sonnet",
        default_effort: str = "high",
        approval_hours: float = 24.0,
        grant_max_minutes: float = 120.0,
        on_run_ended: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.automation = automation
        self.quota_bytes = quota_bytes
        self.stale_minutes = stale_minutes
        self.approval_hours = approval_hours  # LABNET_AUTOMATION_APPROVAL_HOURS
        self.grant_max_minutes = grant_max_minutes  # LABNET_AUTOMATION_GRANT_MAX_MINUTES: a protocol's longest duration
        # Called as on_run_ended(project_id, run_id, status) right after a run stops being
        # active: cancelled, failed for going quiet or waiting too long for approval, or
        # finished by its runner. It is called from whichever thread made the change (a
        # worker thread of asyncio.to_thread, the sweeper's, or the event loop's), possibly
        # with this store's lock held: it must not block or call back into the store; it
        # should only schedule the work (loop.call_soon_threadsafe). Its errors are logged.
        # The GrantManager may set it after the store is made.
        self.on_run_ended = on_run_ended
        self.models = models  # LABNET_AGENT_MODELS: what a project may choose
        self.default_model = default_model  # LABNET_AGENT_MODEL and _EFFORT: when it chooses nothing
        self.default_effort = default_effort
        self._lock = threading.RLock()
        # Uploads: run id → [bytes uploaded or streaming in now, the run's quota]. Only
        # memory is touched under this lock, so the event loop can take it per chunk.
        self._usage: dict[str, list[int]] = {}
        self._streamed: dict[str, datetime] = {}  # run id → when an upload's last chunk came in
        self._usage_lock = threading.Lock()
        self._last_stamp = datetime.min.replace(tzinfo=timezone.utc)
        self._started = self._now()

    # ---- records ---------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _stamp(self) -> str:
        """Now, to the microsecond, always later than the last stamp (queue order is claim order)."""

        with self._lock:
            moment = self._now()
            if moment <= self._last_stamp:
                moment = self._last_stamp + timedelta(microseconds=1)
            self._last_stamp = moment
            return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _folder(self, project_id: str) -> Path:
        return self.automation.runs_folder(project_id)  # 404 for a malformed project id

    def _path(self, project_id: str, run_id: str) -> Path:
        if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            raise AutomationError(404, "No such run.")
        return self._folder(project_id) / f"{run_id}.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        raw = path.read_bytes()
        if len(raw) > MAX_RUN_BYTES:
            raise ValueError("the run record is too large")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        run = Run.model_validate(data).model_dump(mode="json")
        if f"{run['id']}.json" != path.name or run["project_id"] != path.parent.parent.name:
            raise ValueError("its id doesn't match its file")
        return run

    def _load(self, project_id: str, run_id: str) -> dict[str, Any]:
        """The run (call with the lock held): marked failed first if its runner went quiet."""

        path = self._path(project_id, run_id)
        try:
            run = self._read(path)
        except FileNotFoundError:
            raise AutomationError(404, "No such run.") from None
        except (OSError, ValueError, ValidationError) as exc:
            raise AutomationError(500, f"Run {run_id} can't be read: {exc}") from exc
        return self._expire(run)

    def _save(self, run: dict[str, Any]) -> None:
        write_json_file(self._folder(run["project_id"]), f"{run['id']}.json", run, "The run is busy; try again.")

    # ---- runners that went quiet ---------------------------------------------------------

    def _last_heard(self, run: dict[str, Any]) -> datetime:
        heard = _parse_stamp(run["last_seen_at"] or run["claimed_at"] or run["queued_at"])
        with self._usage_lock:
            streamed = self._streamed.get(run["id"])  # an upload still coming in counts too
        return max(heard, streamed or heard, self._started)

    def _waited_too_long(self, run: dict[str, Any]) -> bool:
        """True when the run's protocol has waited for a person longer than approval_hours."""

        protocol = run.get("protocol")
        if run.get("phase") != AWAITING_APPROVAL or not protocol or protocol["status"] != "submitted":
            return False
        return self._now() - _parse_stamp(protocol["submitted_at"]) > timedelta(hours=self.approval_hours)

    def _expire(self, run: dict[str, Any]) -> dict[str, Any]:
        """``run``, or (call with the lock held) the same run marked failed if its runner went quiet
        or its protocol waited for approval longer than ``approval_hours``."""

        if run["status"] != ACTIVE:
            return run
        if self._waited_too_long(run):
            hours = f"{self.approval_hours:g} hour{'' if self.approval_hours == 1 else 's'}"
            summary = f"Nobody approved or rejected the protocol within {hours}."
            line = ("approval", f"The protocol waited more than {hours} for approval: the run is marked failed.")
            reason = "waited too long for approval"
        elif self._now() - self._last_heard(run) > timedelta(minutes=self.stale_minutes):
            minutes = f"{self.stale_minutes:g} minute{'' if self.stale_minutes == 1 else 's'}"
            summary = f"The runner stopped reporting (nothing for {minutes})."
            last = run["last_seen_at"] or run["claimed_at"]
            line = ("timeout", f"Nothing from {run['claimed_by']} since {last} "
                               f"(more than {minutes}): the run is marked failed.")
            reason = f"{run['claimed_by']} went quiet"
        else:
            return run
        run = {**run, "status": self._final(run, FAILED), "finished_at": self._stamp(), "summary": summary}
        self._save(run)
        self._forget_usage(run["id"])
        try:
            self._write_events(run, [line])
        except (AutomationError, OSError) as exc:  # a full log: the run still fails
            LOGGER.warning("Couldn't log the end of %s: %s", run["id"], exc)
        LOGGER.warning("Run %s failed: %s", run["id"], reason)
        self._ended(run)
        return run

    @staticmethod
    def _final(run: dict[str, Any], status: str) -> str:
        """The status a run ends with: ended_unsafe whenever its grant's safe state failed."""

        grant = run.get("grant")
        return ENDED_UNSAFE if grant and grant["status"] == ENDED_UNSAFE else status

    def _ended(self, run: dict[str, Any]) -> None:
        """Tell on_run_ended that ``run`` is no longer active."""

        hook = self.on_run_ended
        if hook is None:
            return
        try:
            hook(run["project_id"], run["id"], run["status"])
        except Exception:  # noqa: BLE001 - the run has ended either way
            LOGGER.exception("on_run_ended failed for %s", run["id"])

    def sweep(self) -> list[dict[str, Any]]:
        """Mark every active run whose runner went quiet as failed; returns those runs."""

        expired = []
        with self._lock:
            for project_id in self._project_ids():
                for run in self._runs(project_id, expire=False):
                    if run["status"] == ACTIVE and (after := self._expire(run))["status"] != ACTIVE:
                        expired.append(after)
        return expired

    def _runs(self, project_id: str, *, expire: bool = True) -> list[dict[str, Any]]:
        folder = self._folder(project_id)
        try:
            entries = list(os.scandir(folder))
        except (FileNotFoundError, NotADirectoryError):
            return []
        runs = []
        for entry in entries:
            if not entry.name.endswith(".json") or RUN_ID.fullmatch(entry.name[:-5]) is None:
                continue
            try:
                run = self._read(Path(entry.path))
            except (OSError, ValueError, ValidationError) as exc:
                LOGGER.warning("Skipping the unreadable run record %s: %s", entry.path, exc)
                continue
            runs.append(self._expire(run) if expire else run)
        return runs

    def _project_ids(self) -> list[str]:
        try:
            return sorted(entry.name for entry in os.scandir(self.automation.state)
                          if entry.is_dir(follow_symlinks=False) and PROJECT_ID.fullmatch(entry.name))
        except FileNotFoundError:
            return []

    # ---- what people do ---------------------------------------------------------------

    def _task(self, project_id: str, raw_task: Any) -> str:
        """A Markdown file that exists in the project's Tasks folder, as a clean path."""

        if not isinstance(raw_task, str):
            raise AutomationError(422, "Send the task to run, e.g. Tasks/Task1.md.")
        try:
            relative = clean_relative(raw_task)
        except PathRefused as exc:
            raise refusal(exc) from None
        zone = zone_of(relative)
        if zone is None or zone.path != TASKS_ZONE or len(relative.parts) < 2 or relative.suffix.lower() != ".md":
            raise AutomationError(422, "A task is a Markdown file in the Tasks folder, e.g. Tasks/Task1.md.")
        try:
            file, _info = open_inside(self.automation.folder(project_id), relative)
        except (PathRefused, FileNotFoundError):
            raise AutomationError(404, f"There is no task {relative}.") from None
        file.close()
        return relative.as_posix()

    def check_model(self, model: str | None) -> None:
        """422 unless ``model`` is None (the default) or one LABNET_AGENT_MODELS offers."""

        if model is not None and model not in self.models:
            raise AutomationError(
                422, f"run_model: {model} isn't offered here; choose one of {', '.join(self.models)}.",
                errors=[{"field": "run_model", "message": f"Choose one of {', '.join(self.models)}."}],
            )

    def run_settings(self, project: dict[str, Any]) -> dict[str, Any]:
        """The project's run settings, with the Control Center's defaults filled in."""

        model = project["run_model"] or self.default_model
        if model not in self.models:  # LABNET_AGENT_MODELS changed since the project was saved
            raise AutomationError(
                422, f"This project's runs use {model}, which isn't offered any more. Choose a model in Settings."
            )
        effort = (project["run_effort"] or self.default_effort) if supports_effort(model) else None
        return RunSettings(
            model=model, effort=effort, max_turns=project["run_max_turns"], max_budget_usd=project["run_budget_usd"]
        ).model_dump(mode="json")

    def _overridden(self, project: dict[str, Any], raw: Any) -> dict[str, Any]:
        """``project`` with this run's own settings ("Run with…") in place of its run settings.

        ``raw`` is ``{"model"?, "effort"?, "max_turns"?, "max_budget_usd"?}``; each is checked
        as the project's own setting would be (the model must be one LABNET_AGENT_MODELS
        offers); a missing or null one keeps the project's. The project itself is unchanged.
        """

        if raw is None:
            return project
        if not isinstance(raw, dict):
            raise AutomationError(422, "settings: send an object with model, effort, max_turns or max_budget_usd.")
        try:
            overrides = RunOverrides.model_validate(raw)
        except ValidationError as exc:
            problems = problems_from(exc, raw)
            for problem in problems:
                problem["field"] = f"settings.{problem['field']}" if problem["field"] else "settings"
            first = problems[0]
            raise AutomationError(422, f"{first['field']}: {first['message']}", errors=problems) from None
        if overrides.model is not None and overrides.model not in self.models:
            raise AutomationError(
                422, f"settings.model: {overrides.model} isn't offered here; choose one of {', '.join(self.models)}.",
                errors=[{"field": "settings.model", "message": f"Choose one of {', '.join(self.models)}."}],
            )
        chosen = overrides.model_dump()
        return {**project, **{RUN_OVERRIDES[key]: value for key, value in chosen.items() if value is not None}}

    def queue(self, project_id: str, raw_task: Any, *, author: str, settings: Any = None) -> dict[str, Any]:
        """Queue a task; ``settings`` (optional) overrides the project's run settings for this run only."""

        # Under the lock delete_project takes, so a project deleted meanwhile isn't brought back.
        with self._lock:
            settings = self.run_settings(self._overridden(self.automation.get(project_id), settings))  # 404 if gone
            task = self._task(project_id, raw_task)
            for run in self._runs(project_id):
                if run["status"] in OPEN and run["task"].casefold() == task.casefold():
                    raise AutomationError(409, f"{task} is already {run['status']} ({run['id']}).", run=run)
            folder = self._folder(project_id)
            while True:
                run_id = (f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-"
                          + "".join(secrets.choice(_ID_ALPHABET) for _ in range(4)))
                if not (folder / f"{run_id}.json").exists():
                    break
            run = Run(
                id=run_id, project_id=project_id, task=task, status=QUEUED, queued_by=author,
                queued_at=self._stamp(), quota_bytes=self.quota_bytes, settings=RunSettings(**settings),
            ).model_dump(mode="json")
            self._save(run)
            return run

    def list(self, project_id: str) -> list[dict[str, Any]]:
        """The project's runs, newest first."""

        self.automation.get(project_id)
        with self._lock:
            runs = self._runs(project_id)
        return sorted(runs, key=lambda run: (run["queued_at"], run["id"]), reverse=True)

    def get(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            return self._load(project_id, run_id)

    def find(self, run_id: str) -> dict[str, Any]:
        """A run, whichever project it belongs to."""

        if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            raise AutomationError(404, "No such run.")
        with self._lock:
            for project_id in self._project_ids():
                if (self._folder(project_id) / f"{run_id}.json").is_file():
                    return self._load(project_id, run_id)
        raise AutomationError(404, "No such run.")

    def cancel(self, project_id: str, run_id: str, *, author: str) -> dict[str, Any]:
        with self._lock:
            run = self._load(project_id, run_id)
            if run["status"] not in OPEN:
                raise AutomationError(409, f"This run has already ended ({run['status']}).", status=run["status"])
            was_active = run["status"] == ACTIVE
            run = {**run, "status": self._final(run, CANCELLED), "cancelled_by": author, "finished_at": self._stamp()}
            self._save(run)
        self._forget_usage(run_id)
        if was_active:
            self._ended(run)
        return run

    def delete_project(self, project_id: str, *, version: int | None) -> None:
        """Delete a project, unless one of its runs is queued or active."""

        with self._lock:
            self.automation.get(project_id)
            busy = [run for run in self._runs(project_id) if run["status"] in OPEN]
            if busy:
                raise AutomationError(
                    409, f"{busy[0]['task']} is {busy[0]['status']} in this project. Cancel it first.",
                    run=busy[0],
                )
            self.automation.delete(project_id, version=version)

    def events(self, project_id: str, run_id: str, *, last: int = 500) -> list[dict[str, Any]]:
        """The run's last ``last`` events, oldest first."""

        path = self._path(project_id, run_id).with_name(f"{run_id}.events.jsonl")
        try:
            file = open(path, "rb")
        except FileNotFoundError:
            return []
        with file:
            end = file.seek(0, os.SEEK_END)
            data = b""
            position = end
            while position > 0 and data.count(b"\n") <= last:
                step = min(64 * 1024, position)
                position -= step
                file.seek(position)
                data = file.read(step) + data
        events = []
        for line in data.splitlines()[-last:]:
            try:
                event = json.loads(line)
            except ValueError:
                continue  # the first, partly read line
            if isinstance(event, dict):
                events.append(event)
        return events[-last:]

    # ---- what a runner does -------------------------------------------------------------

    def runner_run(self, run_id: str, runner: str, *, active: bool = True, save: bool = True) -> dict[str, Any]:
        """The run, if ``runner`` claimed it (and, with ``active``, it is still active).

        An active run is stamped as heard from now; ``save=False`` leaves saving that
        to the caller, which is about to save the run anyway.
        """

        with self._lock:
            run = self.find(run_id)  # a run that went quiet is failed here, before the stamp
            if run["claimed_by"] != runner:
                raise AutomationError(404, "No such run.")
            if active and run["status"] != ACTIVE:
                raise AutomationError(409, f"This run is {run['status']}; it takes nothing more.", status=run["status"])
            if run["status"] == ACTIVE:
                run = {**run, "last_seen_at": self._stamp()}
                if save:
                    self._save(run)
            return run

    def heartbeat(self, run_id: str, runner: str) -> str:
        """The runner is still working on the run; returns its status (409 once it has ended)."""

        return self.runner_run(run_id, runner)["status"]

    def claim(self, runner: str) -> dict[str, Any] | None:
        """The oldest queued run of any project, now active and this runner's; None if none is queued."""

        with self._lock:
            queued = [run for project_id in self._project_ids() for run in self._runs(project_id)
                      if run["status"] == QUEUED]  # reading them all also fails runs gone quiet
            for run in sorted(queued, key=lambda run: (run["queued_at"], run["id"])):
                try:
                    self.automation.get(run["project_id"])
                except AutomationError as exc:
                    LOGGER.warning("Not claiming %s: %s", run["id"], exc)
                    continue
                stamp = self._stamp()
                run = {**run, "status": ACTIVE, "phase": PLANNING, "claimed_by": runner, "claimed_at": stamp,
                       "last_seen_at": stamp}
                self._save(run)
                return run
        return None

    def _write_events(self, run: dict[str, Any], lines: list[tuple[str, str]]) -> None:
        """Append ``(kind, text)`` lines to the run's log (with the lock held); 413 when it is full."""

        stamp = self._stamp()
        data = "".join(
            json.dumps({"at": stamp, "kind": kind, "text": text}, ensure_ascii=False) + "\n" for kind, text in lines
        ).encode("utf-8", "replace")
        path = self._path(run["project_id"], run["id"]).with_name(f"{run['id']}.events.jsonl")
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size + len(data) > MAX_EVENTS_FILE_BYTES:
            raise AutomationError(413, f"This run's log is full ({MAX_EVENTS_FILE_BYTES // 2**20} MiB).")
        if data:
            with open(path, "ab") as file:
                file.write(data)

    def append_events(self, run_id: str, runner: str, events: Any) -> str:
        """Add the runner's events to the run's log; returns the run's status."""

        if not isinstance(events, list):
            raise AutomationError(422, "Send {\"events\": [{\"kind\": …, \"text\": …}, …]}.")
        if len(events) > MAX_EVENTS_PER_REQUEST:
            raise AutomationError(413, f"Send at most {MAX_EVENTS_PER_REQUEST} events at once.")
        lines = []
        for index, event in enumerate(events):
            if not isinstance(event, dict) or set(event) != {"kind", "text"}:
                raise AutomationError(422, f"Event {index}: send exactly a kind and a text.")
            kind, text = event["kind"], event["text"]
            if not isinstance(kind, str) or EVENT_KIND.fullmatch(kind) is None:
                raise AutomationError(422, f"Event {index}: the kind must be lowercase letters, digits and _.")
            if not isinstance(text, str) or len(text.encode("utf-8", "surrogatepass")) > MAX_EVENT_TEXT_BYTES:
                raise AutomationError(422, f"Event {index}: the text must be at most {MAX_EVENT_TEXT_BYTES // 1024} KiB.")
            lines.append((kind, redact(text)))
        with self._lock:
            run = self.runner_run(run_id, runner)
            self._write_events(run, lines)
            return run["status"]

    def finish(self, run_id: str, runner: str, status: Any, summary: Any) -> dict[str, Any]:
        if status not in FINISHED:
            raise AutomationError(422, f"A run finishes as {', '.join(FINISHED)}.")
        if summary is None:
            summary = ""
        if not isinstance(summary, str) or len(summary) > MAX_SUMMARY_CHARACTERS:
            raise AutomationError(422, f"The summary must be text of at most {MAX_SUMMARY_CHARACTERS} characters.")
        with self._lock:
            run = self.runner_run(run_id, runner, save=False)
            try:
                run = Run.model_validate({
                    **run, "status": self._final(run, status), "summary": redact(summary.replace("\r\n", "\n")),
                    "finished_at": self._stamp(),
                }).model_dump(mode="json")
            except ValidationError as exc:
                message = str(exc.errors()[0]["msg"]).removeprefix("Value error, ")
                raise AutomationError(422, f"summary: {message}") from None
            self._save(run)
        self._forget_usage(run_id)
        self._ended(run)
        return run

    # ---- protocols: submitted by the runner, approved or rejected by a person -----------

    def _protocols_folder(self, project_id: str) -> Path:
        """``state/<project>/protocols``: the normalized protocols people reviewed."""

        return self._folder(project_id).parent / "protocols"

    @staticmethod
    def _write_bytes(folder: Path, name: str, data: bytes) -> None:
        """Write ``folder/name`` all at once (a temp file, then a replace)."""

        folder.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            replace_with_retry(Path(temporary), folder / name, "The protocol store is busy; try again.")
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _log(self, run: dict[str, Any], kind: str, text: str) -> None:
        """One line in the run's log from the Control Center itself; a full log doesn't stop anything."""

        try:
            self._write_events(run, [(kind, text)])
        except (AutomationError, OSError) as exc:
            LOGGER.warning("Couldn't log %s for %s: %s", kind, run["id"], exc)

    @staticmethod
    def _check_can_submit(run: dict[str, Any]) -> None:
        if run.get("phase") != PLANNING:
            raise AutomationError(
                409, f"This run is {run.get('phase') or 'not planning'}; a protocol can be submitted only while it "
                     "is planning (before one is approved, or after one was rejected).",
                phase=run.get("phase"),
            )
        protocol = run.get("protocol")
        if protocol and protocol["submissions"] >= MAX_SUBMISSIONS:
            raise AutomationError(
                409, f"This run has already sent {MAX_SUBMISSIONS} protocols for approval, the most a run may. "
                     "Finish the run; a person can queue the task again.",
                phase=run.get("phase"),
            )

    def protocol_target(self, run_id: str, runner: str) -> dict[str, Any]:
        """The run, if ``runner`` may submit a protocol for it now: active, its own, planning,
        and with submissions left (404/409 otherwise)."""

        with self._lock:
            run = self.runner_run(run_id, runner)
            self._check_can_submit(run)
            return run

    def protocol_refused(self, run_id: str, runner: str, problems: list[dict[str, str]]) -> None:
        """Log that the checks refused a protocol (it doesn't count as a submission)."""

        with self._lock:
            run = self.runner_run(run_id, runner)
            first = problems[0] if problems else {"path": "", "message": ""}
            where = f"{first['path']}: " if first["path"] else ""
            self._log(run, "protocol", f"The protocol was refused ({len(problems)} problem"
                                       f"{'' if len(problems) == 1 else 's'}); first: {where}{first['message']}")

    def accept_protocol(
        self, run_id: str, runner: str, protocol: dict[str, Any], prose: str | None
    ) -> dict[str, Any]:
        """Keep a protocol that passed the checks and put the run up for approval.

        ``protocol`` is the normalized protocol (``validate_protocol``); it is stored as
        its canonical text, so the file's sha256 is the one a person approves.
        """

        text = canonical(protocol).encode("utf-8")
        sha256 = hashlib.sha256(text).hexdigest()
        with self._lock:
            run = self.runner_run(run_id, runner, save=False)
            self._check_can_submit(run)
            number = (run["protocol"]["submissions"] if run.get("protocol") else 0) + 1
            folder = self._protocols_folder(run["project_id"])
            self._write_bytes(folder, f"{run_id}-{number}.json", text)
            if prose is not None:
                self._write_bytes(folder, f"{run_id}-{number}.md", prose.encode("utf-8"))
            record = RunProtocol(
                submissions=number, status="submitted", sha256=sha256, title=protocol["title"],
                duration_minutes=float(protocol["duration_minutes"]), submitted_at=self._stamp(),
            ).model_dump(mode="json")
            run = {**run, "phase": AWAITING_APPROVAL, "protocol": record}
            self._save(run)
            self._log(run, "protocol", f"Protocol {number} of {MAX_SUBMISSIONS} is waiting for approval: "
                                       f"{protocol['title']} (sha256 {sha256[:12]}…).")
        return run

    def stored_protocol(self, project_id: str, run_id: str) -> dict[str, Any]:
        """The run's latest protocol as stored: ``{"run", "protocol", "sha256", "prose"}``.

        404 if the run never submitted one; 500 if the stored copy no longer matches
        the sha256 recorded when it was submitted (nobody may approve it then).
        """

        with self._lock:
            run = self._load(project_id, run_id)
            record = run.get("protocol")
            if not record:
                raise AutomationError(404, "This run hasn't submitted a protocol.")
            folder = self._protocols_folder(project_id)
            name = f"{run_id}-{record['submissions']}"
            try:
                data = (folder / f"{name}.json").read_bytes()
            except OSError as exc:
                raise AutomationError(500, f"The stored protocol {name}.json can't be read.") from exc
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise AutomationError(500, f"The stored protocol {name}.json doesn't match its sha256; it can't be "
                                           "shown or approved.")
            prose = None
            try:
                with open(folder / f"{name}.md", "rb") as file:
                    prose = file.read(MAX_PROSE_BYTES).decode("utf-8", "replace")
            except FileNotFoundError:
                pass
            except OSError as exc:
                LOGGER.warning("Couldn't read the prose of %s: %s", name, exc)
            approved = None  # the approver's edited version, when there is one
            if record.get("approved_sha256") and record["approved_sha256"] != record["sha256"]:
                try:
                    edited = (folder / f"{name}-edited.json").read_bytes()
                except OSError as exc:
                    raise AutomationError(500, f"The approved protocol {name}-edited.json can't be read.") from exc
                if hashlib.sha256(edited).hexdigest() != record["approved_sha256"]:
                    raise AutomationError(500, f"The approved protocol {name}-edited.json doesn't match its sha256.")
                approved = json.loads(edited.decode("utf-8"))
        return {"run": run, "protocol": json.loads(data.decode("utf-8")), "sha256": record["sha256"], "prose": prose,
                "approved": approved}

    @staticmethod
    def _check_waiting(run: dict[str, Any], sha256: str | None) -> None:
        """409 unless ``run`` is active with a protocol waiting for approval (and, if given, this one)."""

        if run["status"] != ACTIVE:
            raise AutomationError(409, f"This run is {run['status']}; there is nothing to approve.", run=run)
        record = run.get("protocol")
        if run.get("phase") != AWAITING_APPROVAL or not record or record["status"] != "submitted":
            raise AutomationError(409, f"This run has no protocol waiting for approval (it is {run.get('phase')}).",
                                  run=run)
        if sha256 is not None and sha256 != record["sha256"]:
            raise AutomationError(
                409, "The protocol waiting for approval is not the one you reviewed (its sha256 differs). "
                     "Reload it and review it again.", run=run, sha256=record["sha256"],
            )

    def approval_target(self, project_id: str, run_id: str, sha256: str) -> dict[str, Any]:
        """``stored_protocol``, if exactly that protocol (by sha256) is waiting for approval."""

        with self._lock:
            self._check_waiting(self._load(project_id, run_id), sha256)
            return self.stored_protocol(project_id, run_id)

    def mark_approved(
        self, project_id: str, run_id: str, *, sha256: str, by: str, minutes: float,
        edited: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record the approval once the grant exists; the run goes on to executing.

        ``sha256`` is the submission the approver started from. ``edited`` is the approver's
        own version, when they changed it: ``{"protocol" (normalized), "sha256", "changes"}``;
        it is kept as ``<run>-<n>-edited.json`` (its canonical text, so the file's sha256 is
        ``approved_sha256``), and the record gets ``edited_by`` and the list of changes.
        409 if the run ended meanwhile (the caller then ends the grant it just made).
        """

        with self._lock:
            run = self._load(project_id, run_id)
            record = run.get("protocol")
            if run["status"] != ACTIVE or not record or record["sha256"] != sha256 or record["status"] != "submitted":
                raise AutomationError(409, f"This run is {run['status']} ({run.get('phase')}); the approval "
                                           "came too late.", run=run)
            record = {**record, "status": "approved", "reviewed_by": by, "reviewed_at": self._stamp(),
                      "approved_minutes": float(minutes), "approved_sha256": sha256}
            if edited is not None:
                text = canonical(edited["protocol"]).encode("utf-8")
                if hashlib.sha256(text).hexdigest() != edited["sha256"]:
                    raise ValueError("The edited protocol doesn't match its sha256.")
                self._write_bytes(self._protocols_folder(project_id), f"{run_id}-{record['submissions']}-edited.json",
                                  text)
                record.update(approved_sha256=edited["sha256"], edited_by=by,
                              changes=_clip_changes(list(edited["changes"])))
            # The GrantManager may already have moved it on (a grant that ended at once).
            phase = EXECUTING if run["phase"] == AWAITING_APPROVAL else run["phase"]
            run = {**run, "protocol": RunProtocol(**record).model_dump(mode="json"), "phase": phase}
            self._save(run)
            if edited is None:
                self._log(run, "approval", f"Protocol {record['submissions']} approved by {by} for {minutes:g} minutes "
                                           f"(sha256 {sha256[:12]}…).")
            else:
                count = len(edited["changes"])
                self._log(run, "approval", f"Protocol {record['submissions']} approved by {by} for {minutes:g} minutes, "
                                           f"EDITED ({count} change{'' if count == 1 else 's'}; sha256 "
                                           f"{edited['sha256'][:12]}…, from {sha256[:12]}…): "
                                           + "; ".join(record["changes"]))
        return run

    def reject(self, project_id: str, run_id: str, *, by: str, note: str, sha256: str | None = None) -> dict[str, Any]:
        """Send the protocol back to the agent with ``note``: the run is planning again."""

        with self._lock:
            run = self._load(project_id, run_id)
            self._check_waiting(run, sha256)
            record = {**run["protocol"], "status": "rejected", "reviewed_by": by, "reviewed_at": self._stamp(),
                      "note": note}
            try:
                record = RunProtocol(**record).model_dump(mode="json")
            except ValidationError as exc:
                message = str(exc.errors()[0]["msg"]).removeprefix("Value error, ")
                raise AutomationError(422, f"note: {message}") from None
            run = {**run, "protocol": record, "phase": PLANNING}
            self._save(run)
            left = MAX_SUBMISSIONS - record["submissions"]
            self._log(run, "approval", f"Protocol {record['submissions']} rejected by {by or 'someone'} "
                                       f"({left} submission{'' if left == 1 else 's'} left): {note}")
        return run

    # ---- what the GrantManager reports (automation_grants.py) ----------------------------

    def set_phase(self, project_id: str, run_id: str, phase: str) -> dict[str, Any]:
        """Move an active run's phase (see PHASE_MOVES); 409 for a move that isn't allowed.

        A run that is no longer active is left as it is (its grant may end after it).
        """

        if phase not in PHASE_MOVES:
            raise ValueError(f"No such phase: {phase!r}")
        with self._lock:
            run = self._load(project_id, run_id)
            if run["status"] != ACTIVE or run.get("phase") == phase:
                return run
            if phase not in PHASE_MOVES.get(run.get("phase"), frozenset()):
                raise AutomationError(409, f"A run can't go from {run.get('phase')} to {phase}.", run=run)
            run = {**run, "phase": phase}
            self._save(run)
            self._log(run, "phase", f"Phase: {phase.replace('_', ' ')}.")
        return run

    def set_grant(self, project_id: str, run_id: str, grant: dict[str, Any]) -> dict[str, Any]:
        """Record the run's grant as it is now (any status of the run).

        A grant that ended ``ended_unsafe`` makes a run that already ended
        ``ended_unsafe`` too; a run still active becomes so when it finishes.
        """

        try:
            clean = RunGrant.model_validate(grant).model_dump(mode="json")
        except ValidationError as exc:
            raise ValueError(f"Not a valid grant record for the run: {exc}") from None
        with self._lock:
            run = self._load(project_id, run_id)
            was_unsafe = bool(run.get("grant")) and run["grant"]["status"] == ENDED_UNSAFE
            run = {**run, "grant": clean}
            if run["status"] not in OPEN:
                run["status"] = self._final(run, run["status"])
            self._save(run)
            if clean["status"] == ENDED_UNSAFE and not was_unsafe:
                self._log(run, "grant", "The grant ended UNSAFE: its safe state could not be applied. "
                                        "Check the devices by hand.")
        return run

    # ---- uploads: per-run quota, counted as bytes stream in ---------------------------

    def begin_upload(self, run_id: str, runner: str, *, expected: int | None) -> dict[str, Any]:
        """Check the run takes uploads (and, if the size is known, that it fits the quota)."""

        with self._lock:
            run = self.runner_run(run_id, runner)
            with self._usage_lock:
                usage = self._usage.setdefault(run_id, [run["bytes_uploaded"], run["quota_bytes"]])
                if expected is not None and usage[0] + expected > usage[1]:
                    raise self._over_quota(usage)
        return run

    def reserve(self, run_id: str, size: int) -> None:
        """Count ``size`` more bytes of an upload in progress; 413 past the run's quota."""

        with self._usage_lock:
            usage = self._usage.get(run_id)
            if usage is None:
                raise AutomationError(409, "This run has ended; it takes nothing more.")
            if usage[0] + size > usage[1]:
                raise self._over_quota(usage)
            usage[0] += size
            self._streamed[run_id] = self._now()

    def release(self, run_id: str, size: int) -> None:
        """Give back the bytes of an upload that failed."""

        with self._usage_lock:
            usage = self._usage.get(run_id)
            if usage is not None:
                usage[0] -= size

    @staticmethod
    def _over_quota(usage: list[int]) -> AutomationError:
        return AutomationError(
            413, f"This run may upload at most {usage[1] / 2**20:g} MB in all.",
            bytes_uploaded=usage[0], quota_bytes=usage[1],
        )

    def _forget_usage(self, run_id: str) -> None:
        with self._usage_lock:
            self._usage.pop(run_id, None)
            self._streamed.pop(run_id, None)

    def commit_upload(
        self, run_id: str, runner: str, relative: PurePosixPath, temporary: Path, size: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Put an upload in place if the run is still active and this runner's. (file entry, run)"""

        with self._lock:
            run = self.runner_run(run_id, runner, save=False)
            entry = self.automation.commit_agent(run["project_id"], relative, temporary)
            run = {**run, "bytes_uploaded": run["bytes_uploaded"] + size, "files_uploaded": run["files_uploaded"] + 1}
            self._save(run)
        return entry, run

    def build_inputs(self, run_id: str, runner: str) -> tuple[BinaryIO, int]:
        """(a temp file holding the run's inputs as a zip, its size): see ``agent_inputs`` and run.json."""

        run = self.runner_run(run_id, runner)
        project = self.automation.get(run["project_id"])
        folder = self.automation.folder(project["id"])
        inputs, truncated = self.automation.agent_inputs(project["id"])
        manifest = {
            "run": run,
            # As resolved when the run was queued (records from before then: today's defaults).
            "settings": run["settings"] or self.run_settings(project),
            "project": {key: project[key] for key in (
                "id", "name", "description", "library_folders", "library_collections", "allowed_devices",
                "linked_experiments",
            )},
            "library": self.automation.library_scope(project),
            "truncated": truncated,
        }
        output = tempfile.TemporaryFile()
        try:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for relative in inputs:
                    try:
                        file, info = open_inside(folder, relative)
                    except (PathRefused, FileNotFoundError):
                        continue  # a link, a hard link, or gone since the listing
                    with file:
                        item = zipfile.ZipInfo(relative.as_posix(), date_time=_zip_time(info.st_mtime))
                        item.compress_type = zipfile.ZIP_DEFLATED
                        with archive.open(item, "w", force_zip64=True) as target:
                            remaining = info.st_size  # as listed, even if the file grows meanwhile
                            while remaining > 0:
                                chunk = file.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    break
                                target.write(chunk)
                                remaining -= len(chunk)
                archive.writestr("run.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            size = output.tell()
            output.seek(0)
            return output, size
        except BaseException:
            output.close()
            raise

    def open_library_file(
        self, run_id: str, runner: str, raw_path: str
    ) -> tuple[BinaryIO, os.stat_result, PurePosixPath]:
        """A paper from the lab library, if it is within the run's project's part of it."""

        run = self.runner_run(run_id, runner)
        return self.automation.open_scoped_library_file(self.automation.get(run["project_id"]), raw_path)


async def sweep_forever(store: RunStore) -> None:
    """Every SWEEP_SECONDS, fail the active runs whose runner went quiet (run as a task; cancel to stop)."""

    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await asyncio.to_thread(store.sweep)
        except Exception:  # a damaged folder must not end the sweeping
            LOGGER.exception("Looking for runs whose runner went quiet failed")


__all__ = [
    "ACTIVE", "ANALYZING", "AWAITING_APPROVAL", "CANCELLED", "ENDED_UNSAFE", "EXECUTING", "FAILED", "FINISHED",
    "MAX_SUBMISSIONS", "OPEN", "PHASE_MOVES", "PLANNING", "QUEUED", "RUN_ID", "RUN_OVERRIDES", "Run", "RunGrant",
    "RunOverrides", "RunProtocol", "RunSettings", "RunStore", "protocol_changes", "sweep_forever",
]
