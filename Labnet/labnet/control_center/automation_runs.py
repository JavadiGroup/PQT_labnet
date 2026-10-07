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
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, BinaryIO, Literal

from pydantic import Field, ValidationError

from .agent import supports_effort
from .automation_paths import PathRefused, clean_relative, open_inside, zone_of
from .automation_store import PROJECT_ID, AutomationError, AutomationStore, ModelName, refusal, write_json_file
from .config import AGENT_EFFORTS, DEFAULT_AGENT_MODELS
from .experiment_schema import Text, _Strict
from .redaction import redact


LOGGER = logging.getLogger(__name__)
RUN_ID = re.compile(r"run-[0-9]{8}-[0-9]{6}-[a-z2-9]{4}")
_ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
SCHEMA_VERSION = 1
QUEUED, ACTIVE, DONE, FAILED, STOPPED, CANCELLED = "queued", "active", "done", "failed", "stopped", "cancelled"
OPEN = frozenset({QUEUED, ACTIVE})
FINISHED = (DONE, FAILED, STOPPED)  # how a runner may end a run
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


class Run(_Strict):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: Annotated[str, Field(pattern=rf"^{RUN_ID.pattern}$")]
    project_id: Annotated[str, Field(pattern=rf"^{PROJECT_ID.pattern}$")]
    task: Annotated[str, Field(max_length=240)]
    status: Literal["queued", "active", "done", "failed", "stopped", "cancelled"]
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


def _parse_stamp(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _zip_time(timestamp: float) -> tuple[int, int, int, int, int, int]:
    moment = time.localtime(max(timestamp, 315532800 + 86400))  # zip dates start in 1980
    return moment[:6]


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
    ) -> None:
        self.automation = automation
        self.quota_bytes = quota_bytes
        self.stale_minutes = stale_minutes
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

    def _expire(self, run: dict[str, Any]) -> dict[str, Any]:
        """``run``, or (call with the lock held) the same run marked failed if its runner went quiet."""

        if run["status"] != ACTIVE or self._now() - self._last_heard(run) <= timedelta(minutes=self.stale_minutes):
            return run
        minutes = f"{self.stale_minutes:g} minute{'' if self.stale_minutes == 1 else 's'}"
        run = {
            **run, "status": FAILED, "finished_at": self._stamp(),
            "summary": f"The runner stopped reporting (nothing for {minutes}).",
        }
        self._save(run)
        self._forget_usage(run["id"])
        last = run["last_seen_at"] or run["claimed_at"]
        try:
            self._write_events(run, [("timeout", f"Nothing from {run['claimed_by']} since {last} "
                                                 f"(more than {minutes}): the run is marked failed.")])
        except (AutomationError, OSError) as exc:  # a full log: the run still fails
            LOGGER.warning("Couldn't log the timeout of %s: %s", run["id"], exc)
        LOGGER.warning("Run %s failed: %s went quiet", run["id"], run["claimed_by"])
        return run

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

    def queue(self, project_id: str, raw_task: Any, *, author: str) -> dict[str, Any]:
        # Under the lock delete_project takes, so a project deleted meanwhile isn't brought back.
        with self._lock:
            settings = self.run_settings(self.automation.get(project_id))  # 404 if gone
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
            run = {**run, "status": CANCELLED, "cancelled_by": author, "finished_at": self._stamp()}
            self._save(run)
        self._forget_usage(run_id)
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
                run = {**run, "status": ACTIVE, "claimed_by": runner, "claimed_at": stamp, "last_seen_at": stamp}
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
                    **run, "status": status, "summary": redact(summary.replace("\r\n", "\n")),
                    "finished_at": self._stamp(),
                }).model_dump(mode="json")
            except ValidationError as exc:
                message = str(exc.errors()[0]["msg"]).removeprefix("Value error, ")
                raise AutomationError(422, f"summary: {message}") from None
            self._save(run)
        self._forget_usage(run_id)
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
    "ACTIVE", "CANCELLED", "FAILED", "FINISHED", "OPEN", "QUEUED", "RUN_ID", "Run", "RunSettings", "RunStore",
    "sweep_forever",
]
