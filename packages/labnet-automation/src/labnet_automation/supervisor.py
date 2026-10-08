"""The runner's main loop: claim a run, run the agent on it, upload what it made, finish it.

    labnet-runner [--env-file runner.env] [run]       work through queued runs until stopped
    labnet-runner [--env-file runner.env] self-check  check the isolation, print it, exit 0 / 1

One run at a time:

1. claim (every ``poll_seconds`` while nothing is queued);
2. download the inputs, unpack them into a fresh working copy, preflight it
   (a refusal fails the run with the reason);
3. write the agent's config (``agent_config``), start the tools server on the
   run's socket, start the agent container (``Backend.start_agent``);
4. while it works: its stream-json goes to ``transcript.jsonl`` and, translated
   (``events``), to the Control Center at least every 2 s; a heartbeat every
   ``heartbeat_seconds``. A run the Control Center no longer calls active
   (Stop pressed: 409 or a status other than ``active``) is stopped at once,
   and so is one past ``agent_timeout_minutes`` or when the runner is told to
   shut down (SIGTERM);
5. when Claude Code exits, the run's phase at the Control Center decides what
   comes next (Phase 3; the agent works in one session throughout, resumed with
   ``--resume <session id>``):

   * ``awaiting_approval`` (the agent submitted a protocol): wait, with
     heartbeats, polling the run every ``poll_seconds``, until a person decides
     (or the run is cancelled, goes stale or the approval times out: the run
     isn't active any more, so the runner stops working on it);
   * ``executing`` (approved): take the grant's token, start the device relay
     (``devices_relay``) and resume the agent with the approved devices, bounds
     and end time; run containers get the relay's socket while the grant lasts;
   * ``planning`` after a rejection: resume the agent with the reviewer's note
     (at most 3 reviews: the Control Center allows 3 submissions per run);
   * when the agent exits after executing: end the grant (``grant/end``: the
     safe state), and resume it once more if it hasn't written
     ``Analysis/Analysis.md``.

   However the run ends, a grant whose token was taken is ended;
6. afterwards: upload the new or changed files in the agent's folders, then the
   transcript and the runner's log to ``ScriptsUtilized/logs/<run_id>/``, and
   finish the run (done, failed or stopped) with a summary;
7. stop the device relay and the tools server and delete the run's folder
   (unless ``LABNET_RUNNER_KEEP_RUNS=1``).

Nothing one run does can stop the loop: every failure is logged and the
runner goes back to polling. It refuses to start at all unless every
self-check passes.
"""

from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import agent_config
from .backend import AgentProcess, AgentSpec, Backend
from .client import ControlCenterClient, RunnerApiError
from .config import RunnerSettings
from .devices_relay import SOCKET_NAME, DeviceRelay, Experiment
from .events import DEV, LOG, Translator, event
from .protocol_text import protocol_sha256
from .workcopy import (
    Refused,
    RunFolder,
    collect_outputs,
    redact_credentials,
    hand_over,
    make_skeleton,
    preflight_tree,
    remove_tree,
    snapshot,
    unpack_inputs,
)


LOGGER = logging.getLogger("labnet_automation")
RUN_ID = re.compile(r"run-[0-9]{8}-[0-9]{6}-[a-z2-9]{4}")  # as the Control Center makes them
ACTIVE, DONE, FAILED, STOPPED, CANCELLED = "active", "done", "failed", "stopped", "cancelled"
PLANNING, AWAITING, EXECUTING = "planning", "awaiting_approval", "executing"  # run phases (Phase 3)
MAX_REVIEWS = 3  # protocol submissions per run (the Control Center's limit), so at most 3 decisions
MIN_RESUME_BUDGET_USD = 0.05  # less than this left of the run's budget: not resumed
MAX_SESSIONS = 1 + MAX_REVIEWS + 1  # the first, one per decision, the analysis
MAX_GRANT_END_ATTEMPTS = 2  # then the Control Center ends it (the run's end, or the grant's end time)
FLUSH_SECONDS = 2.0
TICK_SECONDS = 0.2
KILL_AFTER_SECONDS = 30.0  # after a stop: kill the agent process if it hasn't ended
GIVE_UP_AFTER_SECONDS = 60.0  # ... and stop waiting for it
MAX_BUFFERED_EVENTS = 5000
MAX_TRANSCRIPT_BYTES = 90 * 1024 * 1024
MAX_RUNNER_LOG_BYTES = 20 * 1024 * 1024
DEV_BANNER = (
    "DEVELOPMENT RUNNER (LABNET_RUNNER_DEV=1): this run was worked on a development machine, "
    "possibly over plain HTTP and without the lab runner's isolation. Don't rely on it for lab decisions."
)

ToolsFactory = Callable[..., Any]
SelfCheck = Callable[[RunnerSettings, Any, Any], Sequence[Any]]


def _default_tools_factory(**kwargs: Any) -> Any:
    from .tools_server import ToolsServer

    return ToolsServer(**kwargs)


def _default_selfcheck(settings: RunnerSettings, backend: Any, client: Any) -> Sequence[Any]:
    from . import selfcheck

    return selfcheck.run_all(settings, backend, client)


class _Cancelled(Exception):
    """The Control Center says the run isn't active any more."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class _RunState:
    """One run in progress: its events (buffered for the Control Center), its log, how it is stopping."""

    def __init__(self, run_id: str, folder: RunFolder) -> None:
        self.run_id = run_id
        self.folder = folder
        self._lock = threading.Lock()
        self._buffer: list[dict[str, str]] = []
        self._dropped = 0
        self._log_bytes = 0
        self._log = open(folder.log, "a", encoding="utf-8")
        self.stop_status: str | None = None  # stopped / cancelled / failed, once a stop was decided
        self.stop_reason = ""
        self.stopped_at: float | None = None
        self.process: AgentProcess | None = None
        self.tools: Any = None  # the run's tools server, once started
        self.experiment = Experiment()  # protocol submissions and the grant (shared with the tools server)
        self.relay: Any = None  # the DeviceRelay, while a grant is active
        self.spent_usd = 0.0  # what the agent's sessions cost so far (Claude Code's own estimate)
        self.transcript_bytes = 0
        self.handled_submissions = 0  # protocol decisions already acted on
        self.reviews_resumed = 0
        self.analysis_resumed = False
        self.grant_end_attempts = 0

    def emit(self, kind: str, text: str) -> None:
        """Queue an event for the Control Center and write it to runner.log (any thread)."""

        item = event(kind, text)
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {item['kind']}: {item['text']}\n"
        with self._lock:
            if len(self._buffer) >= MAX_BUFFERED_EVENTS:
                self._buffer.pop(0)
                self._dropped += 1
            self._buffer.append(item)
            if not self._log.closed and self._log_bytes < MAX_RUNNER_LOG_BYTES:
                self._log.write(line)
                self._log_bytes += len(line)

    def note(self, text: str, *, level: int = logging.INFO) -> None:
        LOGGER.log(level, "%s: %s", self.run_id, text)
        self.emit(LOG, text)

    def take(self) -> list[dict[str, str]]:
        with self._lock:
            batch, self._buffer = self._buffer, []
            if self._dropped:
                batch.insert(0, event(LOG, f"{self._dropped} events were dropped while the Control Center "
                                           "couldn't be reached (they are in runner.log)."))
                self._dropped = 0
            return batch

    def put_back(self, batch: list[dict[str, str]]) -> None:
        with self._lock:
            self._buffer[:0] = batch[-MAX_BUFFERED_EVENTS:]
            excess = len(self._buffer) - MAX_BUFFERED_EVENTS
            if excess > 0:
                del self._buffer[:excess]
                self._dropped += excess

    def flush_log(self) -> None:
        with self._lock:
            if not self._log.closed:
                self._log.flush()

    def close(self) -> None:
        with self._lock:
            self._log.close()


class Supervisor:
    def __init__(
        self,
        settings: RunnerSettings,
        client: ControlCenterClient,
        backend: Backend,
        *,
        tools_factory: ToolsFactory | None = None,
        relay_factory: Callable[..., Any] | None = None,
        selfcheck: SelfCheck | None = None,
        clock: Callable[[], float] = time.monotonic,
        tick_seconds: float = TICK_SECONDS,
        flush_seconds: float = FLUSH_SECONDS,
    ) -> None:
        self.settings = settings
        self.client = client
        self.backend = backend
        self._tools_factory = tools_factory or _default_tools_factory
        self._relay_factory = relay_factory or DeviceRelay
        self._selfcheck = selfcheck or _default_selfcheck
        self._clock = clock
        self._tick = tick_seconds
        self._flush_seconds = flush_seconds
        self._stopping = threading.Event()
        self._state_lock = threading.Lock()
        self._current: _RunState | None = None

    # ---- starting and stopping ------------------------------------------------------------

    def request_stop(self) -> None:
        """Stop polling, and stop the current run cleanly ("stopped"). Safe from a signal handler."""

        self._stopping.set()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def self_check(self) -> bool:
        """Every self-check passed (each one is logged)."""

        try:
            checks = list(self._selfcheck(self.settings, self.backend, self.client))
        except Exception as exc:  # noqa: BLE001  (a check that crashes is a failed check)
            LOGGER.error("Self-check could not run: %r", exc)
            checks = None
        if not checks:
            LOGGER.error("The runner refuses to start: the self-check reported nothing.")
            return False
        failed = [check for check in checks if not check.ok]
        for check in checks:
            LOGGER.log(logging.INFO if check.ok else logging.ERROR, "self-check %s: %s: %s",
                       "ok" if check.ok else "FAILED", check.name, check.detail)
        if failed:
            LOGGER.error("The runner refuses to start: %d self-check(s) failed (%s).", len(failed),
                         ", ".join(check.name for check in failed))
            return False
        return True

    def run_forever(self) -> int:
        """Poll and work through runs until ``request_stop``. 2 if the self-check fails, else 0."""

        if self.settings.dev:
            LOGGER.warning(DEV_BANNER)
        if not self.self_check():
            return 2
        try:
            LOGGER.info("Runner %s polling %s every %g s.", self.client.whoami(), self.settings.control_center_url,
                        self.settings.poll_seconds)
        except RunnerApiError as exc:
            LOGGER.warning("Couldn't ask the Control Center who this runner is: %s", exc)
        while not self._stopping.is_set():
            try:
                run = self.client.claim()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Claiming a run failed: %s", exc)
                run = None
            if run is None:
                self._stopping.wait(self.settings.poll_seconds)
                continue
            self.run_once(run)
        LOGGER.info("Runner stopped.")
        return 0

    def run_once(self, run: dict[str, Any] | None = None) -> str | None:
        """Work through ``run`` (or claim one); the status it ended with, None if nothing was queued.

        Never raises: a run that goes wrong is failed and logged.
        """

        if run is None:
            run = self.client.claim()
            if run is None:
                return None
        try:
            return self._execute(run)
        except Exception:  # noqa: BLE001  (one bad run must not end the loop)
            LOGGER.exception("Run %s went wrong outside its handling", run.get("id") if isinstance(run, dict) else run)
            return FAILED

    # ---- one run ------------------------------------------------------------------------------

    def _execute(self, run: dict[str, Any]) -> str:
        run_id = run.get("id")
        if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            LOGGER.error("The Control Center handed out a run with an unusable id: %r", run_id)
            return FAILED
        folder = RunFolder(self.settings.work_dir / "runs" / run_id)
        try:
            folder.create()
            state = _RunState(run_id, folder)
        except OSError as exc:
            LOGGER.exception("Run %s: couldn't create its folder", run_id)
            try:
                self.client.finish(run_id, FAILED, f"The runner couldn't create the run's folder: {exc}")
            except RunnerApiError as error:
                LOGGER.error("%s: finishing the run failed: %s", run_id, error)
            return FAILED
        with self._state_lock:
            self._current = state
        status, summary = FAILED, ""
        try:
            try:
                status, summary = self._work(run, folder, state)
            except _Cancelled as exc:
                status, summary = CANCELLED, f"The run is {exc.status} at the Control Center."
            except RunnerApiError as exc:
                if exc.status_code == 409:
                    status, summary = CANCELLED, f"The run is {exc.run_status or 'no longer active'} at the Control Center."
                else:
                    status, summary = FAILED, f"The runner couldn't talk to the Control Center: {exc}"
                    state.note(summary, level=logging.ERROR)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Run %s failed in the runner", run_id)
                status, summary = FAILED, f"The runner failed: {exc!r}"
                state.note(summary, level=logging.ERROR)
            self._end_grant(state, "The run ended.")  # before the run is finished, whatever happened
            self._stop_relay(state)
            if status == CANCELLED:
                LOGGER.info("%s: %s Stopped working on it.", run_id, summary)
            else:
                status = self._finish(state, folder, status, summary)
            return status
        finally:
            self._end_grant(state, "The run ended.")  # never leave a grant behind
            self._stop_relay(state)
            if state.tools is not None:
                try:
                    state.tools.stop()
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Stopping the tools server of %s failed", run_id)
            try:
                self.backend.stop_run(run_id)  # nothing of this run may keep running
            except Exception:  # noqa: BLE001
                LOGGER.exception("Stopping the containers of %s failed", run_id)
            with self._state_lock:
                self._current = None
            state.close()
            if self.settings.keep_runs:
                LOGGER.info("%s: kept %s (LABNET_RUNNER_KEEP_RUNS).", run_id, folder.root)
            else:
                try:
                    remove_tree(folder.root)
                except OSError as exc:
                    LOGGER.warning("Couldn't delete %s: %s", folder.root, exc)

    def _work(self, run: dict[str, Any], folder: RunFolder, state: _RunState) -> tuple[str, str]:
        """Prepare, run and collect: (status, summary)."""

        run_id = state.run_id
        if self.settings.dev:
            state.emit(DEV, DEV_BANNER)
        state.note(f"Claimed {run.get('task')} of project {run.get('project_id')}.")

        self.client.download_inputs(run_id, folder.inputs)
        try:
            manifest = unpack_inputs(folder.inputs, folder.work)
            folder.inputs.unlink()
            preflight_tree(folder.work)
            make_skeleton(folder.work)
            settings = agent_config.check_settings(manifest.get("settings"))
            task_text = agent_config.read_task(folder.work, run.get("task"))
        except (Refused, agent_config.BadRunSettings) as exc:
            state.note(f"Refused: {exc}", level=logging.WARNING)
            return FAILED, f"Refused: {exc}"
        agent_config.write_config(folder.config)
        hand_over([folder.work, folder.sock])
        before = snapshot(folder.work)
        if self._stopping.is_set():
            return STOPPED, "The runner was shut down before the agent started."

        state.tools = self._tools_factory(
            run=run, work=folder.work, socket_path=folder.sock / "tools.sock", client=self.client,
            backend=self.backend, settings=self.settings, on_event=state.emit, experiment=state.experiment,
        )
        state.tools.start()
        session_id = str(uuid.uuid4())
        spec = AgentSpec(
            run_id=run_id, work=folder.work, config=folder.config, socket_dir=folder.sock,
            argv=agent_config.claude_argv(settings, session_id),
            env=agent_config.agent_environment(self.settings),
            stdin=agent_config.prompt(manifest, task_text),
        )
        effort = f", effort {settings['effort']}" if settings["effort"] else ""
        state.note(f"Starting the agent: {settings['model']}{effort}, at most {settings['max_turns']} turns "
                   f"and ${settings['max_budget_usd']:g}.")
        sessions = 1
        translator, exit_code, stderr = self._session(state, spec)
        while state.stop_status is None:
            follow_up = self._next_step(state, folder, settings)
            if follow_up is None or state.stop_status is not None:
                break
            left = settings["max_budget_usd"] - state.spent_usd
            if left < MIN_RESUME_BUDGET_USD:
                state.note(f"Not resuming the agent: its budget is spent (${state.spent_usd:.2f} of "
                           f"${settings['max_budget_usd']:g}).", level=logging.WARNING)
                break
            if sessions >= MAX_SESSIONS:
                state.note(f"Not resuming the agent: it already had {sessions} sessions.", level=logging.WARNING)
                break
            sessions += 1
            spec = AgentSpec(
                run_id=run_id, work=folder.work, config=folder.config, socket_dir=folder.sock,
                argv=agent_config.claude_argv(settings, session_id, resume=True, max_budget_usd=left),
                env=agent_config.agent_environment(self.settings), stdin=follow_up,
            )
            state.note(f"Resuming the agent (session {sessions}, ${left:.2f} of its budget left).")
            translator, exit_code, stderr = self._session(state, spec)
        if state.experiment.token_taken:
            reason = (f"The run is ending ({state.stop_reason})." if state.stop_status is not None
                      else "The agent's session ended.")
            self._end_grant(state, reason)
            self._stop_relay(state)
        if state.stop_status == CANCELLED:
            return CANCELLED, state.stop_reason

        if state.stop_status is not None:
            status, summary = state.stop_status, state.stop_reason
            if translator.result is not None:
                summary += f"\n\n{translator.summary()}"
        elif translator.succeeded:
            status, summary = DONE, translator.summary()
        else:
            status = FAILED
            summary = translator.summary() or f"Claude Code exited ({exit_code}) without a result."
            if stderr.strip():
                state.note(f"Claude Code's last error output:\n{stderr.strip()[-2000:]}", level=logging.WARNING)
                if translator.result is None:
                    summary += f" {stderr.strip().splitlines()[-1][:500]}"
        state.note(f"The agent ended ({status}, exit code {exit_code}).")

        self._upload_outputs(state, folder, before)
        return status, summary

    def _session(self, state: _RunState, spec: AgentSpec) -> tuple[Translator, int | None, str]:
        """One Claude Code session (first or resumed), watched until it ends: (translator, exit code, stderr)."""

        translator = Translator()
        state.process = self.backend.start_agent(spec)
        exit_code, stderr = self._watch(state, translator)
        cost = (translator.result or {}).get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
            state.spent_usd += float(cost)
        return translator, exit_code, stderr

    # ---- Phase 3: after a session, the protocol's review and the grant -------------------------

    def _next_step(self, state: _RunState, folder: RunFolder, settings: dict[str, Any]) -> str | None:
        """What the agent is resumed with after a session ended, or None when the run is over.

        May wait (for a person's decision); sets ``state.stop_status`` if the run must stop meanwhile.
        """

        experiment = state.experiment
        if experiment.token_taken:  # the session that ran the experiment ended
            self._end_grant(state, "The agent's session ended.")
            self._stop_relay(state)
            if state.analysis_resumed or (folder.work / "Analysis" / "Analysis.md").is_file():
                return None
            state.analysis_resumed = True
            state.note("Analysis/Analysis.md is missing: resuming the agent once more for its analysis.")
            return agent_config.analysis_prompt(experiment.grant)

        run = self._get_run(state)
        if state.stop_status is not None:
            return None
        if run is None and not experiment.submissions:
            return None  # can't tell, and nothing was submitted: the run is over
        phase = run.get("phase") if run is not None else AWAITING
        if phase == AWAITING:
            if settings["max_budget_usd"] - state.spent_usd < MIN_RESUME_BUDGET_USD:
                state.note("The protocol awaits approval, but the agent's budget is spent: not waiting for it.",
                           level=logging.WARNING)
                return None
            state.note("The protocol awaits a person's approval in the Control Center; waiting.")
            run = self._await_decision(state)
            if run is None:
                return None
            phase = run.get("phase")
        review = run.get("protocol") if isinstance(run.get("protocol"), dict) else {}
        submissions = review.get("submissions")
        submissions = submissions if isinstance(submissions, int) and not isinstance(submissions, bool) else 0
        if phase == EXECUTING:
            state.handled_submissions = max(state.handled_submissions, submissions)
            return self._start_experiment(state, folder, run)
        if phase == PLANNING and review.get("status") == "rejected":
            if submissions <= state.handled_submissions:
                return None  # this rejection was answered already; the agent didn't submit again
            state.handled_submissions = submissions
            if state.reviews_resumed >= MAX_REVIEWS:
                state.note(f"The protocol was rejected again; not resuming (at most {MAX_REVIEWS} reviews).")
                return None
            state.reviews_resumed += 1
            left = max(0, MAX_REVIEWS - submissions)
            if left == 0:
                state.analysis_resumed = True  # this resume is the last: it writes the analysis
            state.note(f"The protocol was rejected by {review.get('reviewed_by') or 'a reviewer'}; resuming the "
                       "agent with the note.")
            return agent_config.rejection_prompt(run, submissions_left=left)
        if review.get("status") == "approved":
            # Approved, but the grant ended before this runner took it (the Control Center stopped
            # answering and the watchdog applied the safe state, Stop was pressed, it expired): the
            # experiment never ran, so the run must not end "done" on the planning session's answer.
            grant = run.get("grant") if isinstance(run.get("grant"), dict) else {}
            why = grant.get("ended_reason") or f"the grant is {grant.get('status') or 'gone'}"
            self._stop(state, FAILED, f"The approved experiment never started: its grant ended before the runner "
                                      f"could take it ({why}). Queue the task again to retry.")
        return None

    def _get_run(self, state: _RunState) -> dict[str, Any] | None:
        """The run's record from the Control Center; None if it couldn't be read. A run that isn't
        active any more (Stop, stale, approval timed out) is stopped (``state.stop_status``)."""

        try:
            run = self.client.get_run(state.run_id)
        except RunnerApiError as exc:
            if exc.status_code == 409:
                self._cancelled(state, exc.run_status)
            else:
                LOGGER.warning("%s: reading the run failed: %s", state.run_id, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("%s: reading the run failed: %r", state.run_id, exc)
            return None
        if not isinstance(run, dict):
            return None
        if run.get("status", ACTIVE) != ACTIVE:
            self._cancelled(state, str(run.get("status")))
            return None
        return run

    def _await_decision(self, state: _RunState) -> dict[str, Any] | None:
        """Wait until the run leaves ``awaiting_approval``: its record, or None if the run must stop."""

        last_poll = last_flush = last_beat = self._clock()
        while True:
            if self._stopping.wait(self._tick):
                self._stop(state, STOPPED, "Stopped: the runner was shut down while the protocol awaited approval.")
                return None
            now = self._clock()
            if now - last_flush >= self._flush_seconds:
                self._flush(state)
                last_flush = now
            if state.stop_status is None and now - last_beat >= self.settings.heartbeat_seconds:
                self._heartbeat(state)
                last_beat = now
            if state.stop_status is None and now - last_poll >= self.settings.poll_seconds:
                last_poll = now
                run = self._get_run(state)
                if state.stop_status is None and run is not None and run.get("phase") != AWAITING:
                    return run
            if state.stop_status is not None:
                return None

    def _start_experiment(self, state: _RunState, folder: RunFolder, run: dict[str, Any]) -> str | None:
        """Approved: take the grant's token, start the device relay; the prompt to resume the agent with."""

        try:
            token, grant, approved = self.client.take_grant_token(state.run_id)
        except RunnerApiError as exc:
            if exc.status_code == 409 and exc.run_status not in (None, ACTIVE):
                self._cancelled(state, exc.run_status)
                return None
            state.note(f"The grant's token couldn't be taken ({exc}); ending the grant.", level=logging.ERROR)
            try:  # the leases are held: release them now rather than at the grant's expiry
                self.client.end_grant(state.run_id, "The runner couldn't take the grant's token.")
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("%s: ending the grant failed: %s", state.run_id, error)
            return None
        review = run.get("protocol") if isinstance(run.get("protocol"), dict) else {}
        # What the agent is told comes from the protocol the person approved (the Control Center's
        # normalized copy, exactly what the gateway enforces), never from the agent's own file.
        protocol = approved
        if protocol is None:
            state.note("The Control Center sent no approved protocol with the grant; the agent is told the "
                       "granted devices only.", level=logging.WARNING)
        elif (expected := review.get("approved_sha256") or review.get("sha256")) and protocol_sha256(protocol) != expected:
            state.note("The protocol sent with the grant doesn't match the approved sha256; the agent is told the "
                       "granted devices only.", level=logging.WARNING)
            protocol = None
        state.experiment.activate(token, grant, protocol, folder.devices)
        relay = self._relay_factory(state.experiment, self.client, folder.devices / SOCKET_NAME, state.emit,
                                    run_id=state.run_id)
        try:
            relay.start()
        except Exception as exc:  # noqa: BLE001
            state.note(f"The device relay couldn't start ({exc}); ending the grant.", level=logging.ERROR)
            self._end_grant(state, "The runner couldn't start its device relay.")
            return None
        state.relay = relay
        approved_by = grant.get("approved_by") or review.get("reviewed_by") or "a reviewer"
        state.note(f"The protocol was approved by {approved_by}; the grant runs until {grant.get('ends_at', '?')}. "
                   "Resuming the agent with the approved devices and bounds.")
        return agent_config.approval_prompt(run, grant, protocol)

    def _end_grant(self, state: _RunState, reason: str) -> None:
        """End the run's grant at the Control Center (safe state), once, if its token was taken. Never raises."""

        experiment = state.experiment
        if not experiment.token_taken or experiment.closed or state.grant_end_attempts >= MAX_GRANT_END_ATTEMPTS:
            return
        state.grant_end_attempts += 1
        experiment.end(reason)  # the relay refuses calls from now on
        try:
            grant = self.client.end_grant(state.run_id, reason)
        except RunnerApiError as exc:
            if exc.status_code == 409:  # it had ended already, or the run did (which ends its grant)
                experiment.end(reason, confirmed=True)
                LOGGER.info("%s: the grant had already ended: %s", state.run_id, exc)
            else:
                state.note(f"Ending the grant failed ({exc}); the Control Center ends it when the run ends or "
                           "at its end time.", level=logging.ERROR)
            return
        except Exception as exc:  # noqa: BLE001
            state.note(f"Ending the grant failed ({exc!r}); the Control Center ends it when the run ends or at "
                       "its end time.", level=logging.ERROR)
            return
        experiment.end(reason, grant, confirmed=True)
        status = grant.get("status") or "ended"
        state.note(f"The grant ended ({status}; {experiment.calls} device call(s) relayed, "
                   f"{experiment.refused} refused).",
                   level=logging.ERROR if status == "ended_unsafe" else logging.INFO)

    def _stop_relay(self, state: _RunState) -> None:
        relay, state.relay = state.relay, None
        if relay is not None:
            try:
                relay.stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Stopping the device relay of %s failed", state.run_id)

    def _watch(self, state: _RunState, translator: Translator) -> tuple[int | None, str]:
        """Relay the agent's output until it ends; heartbeats, Stop, time limit. (exit code, stderr tail)"""

        process = state.process
        assert process is not None
        reader_done = threading.Event()
        reader = threading.Thread(target=self._read, args=(state, process, translator, reader_done),
                                  name=f"labnet-runner-{state.run_id}", daemon=True)
        reader.start()
        started = last_flush = last_beat = self._clock()
        limit = self.settings.agent_timeout_minutes * 60
        while True:
            finished = reader_done.wait(self._tick)
            now = self._clock()
            if not finished:
                if self._stopping.is_set():
                    self._stop(state, STOPPED, "Stopped: the runner was shut down.")
                elif now - started >= limit:
                    self._stop(state, STOPPED, f"Stopped: the agent's time limit "
                                               f"({self.settings.agent_timeout_minutes:g} minutes) was reached.")
            if state.stop_status is None and (finished or now - last_flush >= self._flush_seconds):
                self._flush(state)
                last_flush = now
            if not finished and state.stop_status is None and now - last_beat >= self.settings.heartbeat_seconds:
                self._heartbeat(state)
                last_beat = now
            if finished:
                break
            if state.stopped_at is not None:
                if now - state.stopped_at >= GIVE_UP_AFTER_SECONDS:
                    LOGGER.error("%s: the agent didn't end after it was stopped; not waiting any longer.", state.run_id)
                    return None, ""
                if now - state.stopped_at >= KILL_AFTER_SECONDS:
                    try:
                        process.kill()
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Killing the agent of %s failed", state.run_id)
        reader.join(timeout=5)
        try:
            exit_code: int | None = process.wait()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("%s: couldn't get the agent's exit code: %r", state.run_id, exc)
            exit_code = None
        try:
            stderr = process.stderr_tail() or ""
        except Exception:  # noqa: BLE001
            stderr = ""
        return exit_code, stderr

    def _read(self, state: _RunState, process: AgentProcess, translator: Translator, done: threading.Event) -> None:
        written = state.transcript_bytes  # one cap for all the run's sessions
        try:
            with open(state.folder.transcript, "a", encoding="utf-8") as transcript:
                for line in process.lines():
                    line = line.rstrip("\r\n")
                    if written < MAX_TRANSCRIPT_BYTES:
                        transcript.write(line + "\n")
                        written += len(line) + 1
                        if written >= MAX_TRANSCRIPT_BYTES:
                            state.note("The transcript reached its size limit; later lines are not kept.")
                    for item in translator.line(line):
                        state.emit(item["kind"], item["text"])
        except Exception as exc:  # noqa: BLE001
            state.note(f"Reading the agent's output failed: {exc!r}", level=logging.ERROR)
        finally:
            state.transcript_bytes = written
            done.set()

    def _stop(self, state: _RunState, status: str, reason: str) -> None:
        """Stop the run's containers now; the first reason given is the one kept."""

        if state.stop_status is not None:
            return
        state.stop_status, state.stop_reason, state.stopped_at = status, reason, self._clock()
        if status == CANCELLED:
            LOGGER.info("%s: %s", state.run_id, reason)
        else:
            state.note(reason, level=logging.WARNING)
        try:
            self.backend.stop_run(state.run_id)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Stopping the containers of %s failed", state.run_id)
            if state.process is not None:
                try:
                    state.process.kill()
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Killing the agent of %s failed", state.run_id)

    def _cancelled(self, state: _RunState, run_status: str | None) -> None:
        self._stop(state, CANCELLED, f"The run is {run_status or 'no longer active'} at the Control Center; "
                                     "stopping it.")

    def _flush(self, state: _RunState) -> None:
        """Send the buffered events; notice a cancel. Raises nothing."""

        batch = state.take()
        if not batch:
            return
        try:
            status = self.client.events(state.run_id, batch)
        except RunnerApiError as exc:
            if exc.status_code == 409:
                self._cancelled(state, exc.run_status)
            elif exc.status_code in (0, 502, 503, 504):
                state.put_back(batch)  # try again with the next flush
                LOGGER.warning("%s: sending events failed: %s", state.run_id, exc)
            else:  # 413 (the run's log is full), 422: these events will never be taken
                LOGGER.warning("%s: the Control Center refused %d events: %s", state.run_id, len(batch), exc)
            return
        except Exception as exc:  # noqa: BLE001
            state.put_back(batch)
            LOGGER.warning("%s: sending events failed: %r", state.run_id, exc)
            return
        if status != ACTIVE:
            self._cancelled(state, status)

    def _heartbeat(self, state: _RunState) -> None:
        try:
            status = self.client.heartbeat(state.run_id)
        except RunnerApiError as exc:
            if exc.status_code == 409:
                self._cancelled(state, exc.run_status)
            else:
                LOGGER.warning("%s: heartbeat failed: %s", state.run_id, exc)
            return
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("%s: heartbeat failed: %r", state.run_id, exc)
            return
        if status != ACTIVE:
            self._cancelled(state, status)

    # ---- after the agent ------------------------------------------------------------------------

    def _upload(self, state: _RunState, relative: str, path: Path) -> bool:
        """Upload one file; False if the Control Center refused it (409 raises _Cancelled)."""

        try:
            self.client.upload(state.run_id, relative, path)
            return True
        except RunnerApiError as exc:
            if exc.status_code == 409:
                raise _Cancelled(exc.run_status or "no longer active") from exc
            state.note(f"not uploaded: {relative} ({exc.status_code or 'no answer'}: {exc.detail})",
                       level=logging.WARNING)
            return False

    def _upload_redacted(self, state: _RunState, relative: str, path: Path) -> bool:
        """Upload a copy with any Claude credential blanked: the transcript records what the agent read."""

        copy = path.with_name(path.name + ".redacted")
        try:
            redact_credentials(path, copy)
            return self._upload(state, relative, copy)
        finally:
            copy.unlink(missing_ok=True)

    def _upload_outputs(self, state: _RunState, folder: RunFolder, before: dict[str, str]) -> None:
        outputs = collect_outputs(folder.work, before)
        for note in outputs.ignored:
            state.emit(LOG, note)
        uploaded = sum(self._upload(state, item.relative, item.path) for item in outputs.files)
        state.note(f"Uploaded {uploaded} of {len(outputs.files)} file(s) the agent wrote "
                   f"({outputs.total_bytes / 2**20:.1f} MB).")

    def _finish(self, state: _RunState, folder: RunFolder, status: str, summary: str) -> str:
        """Upload the transcript and log, then finish the run; the status it ended with."""

        try:
            logs = f"ScriptsUtilized/logs/{state.run_id}"
            if folder.transcript.is_file():
                self._upload_redacted(state, f"{logs}/transcript.jsonl", folder.transcript)
            state.note(f"Finishing the run as {status}.")
            state.flush_log()
            self._upload_redacted(state, f"{logs}/runner.log", folder.log)
            self._flush(state)
            if state.stop_status == CANCELLED:
                return CANCELLED
            self.client.finish(state.run_id, status, summary)
            LOGGER.info("%s: finished as %s.", state.run_id, status)
            return status
        except _Cancelled as exc:
            LOGGER.info("%s: the run is %s at the Control Center; not finishing it.", state.run_id, exc.status)
            return CANCELLED
        except RunnerApiError as exc:
            if exc.status_code == 409:
                LOGGER.info("%s: the run is %s at the Control Center; not finishing it.", state.run_id,
                            exc.run_status or "no longer active")
                return CANCELLED
            LOGGER.error("%s: finishing the run failed: %s (the Control Center will time it out)", state.run_id, exc)
            return FAILED


# ---- the command ---------------------------------------------------------------------------------


def _make_backend(settings: RunnerSettings) -> Backend:
    from .docker_backend import DockerBackend

    return DockerBackend(settings)


def main(
    argv: Sequence[str] | None = None,
    *,
    make_backend: Callable[[RunnerSettings], Backend] | None = None,
    make_client: Callable[[RunnerSettings], ControlCenterClient] | None = None,
    selfcheck: SelfCheck | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="labnet-runner", description="The PQT Lab Automation runner: works through queued Automation runs."
    )
    parser.add_argument("--env-file", help="runner settings (KEY=value lines); the environment wins")
    parser.add_argument("command", nargs="?", default="run", choices=("run", "self-check"),
                        help="run (default): work through runs until stopped; self-check: check the isolation")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        settings = RunnerSettings.from_env(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"labnet-runner: {exc}", file=sys.stderr)
        return 2
    backend = (make_backend or _make_backend)(settings)
    client = (make_client or ControlCenterClient)(settings)
    try:
        if args.command == "self-check":
            try:
                checks = list((selfcheck or _default_selfcheck)(settings, backend, client))
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED  self-check: it could not run: {exc!r}")
                return 1
            for check in checks:
                print(f"{'ok    ' if check.ok else 'FAILED'}  {check.name}: {check.detail}")
            return 0 if checks and all(check.ok for check in checks) else 1

        supervisor = Supervisor(settings, client, backend, selfcheck=selfcheck)

        def stop(signum: int, _frame: Any) -> None:
            LOGGER.info("Signal %s: stopping after the current run is stopped cleanly.", signum)
            supervisor.request_stop()

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
        return supervisor.run_forever()
    finally:
        client.close()


__all__ = ["DEV_BANNER", "Supervisor", "main"]
