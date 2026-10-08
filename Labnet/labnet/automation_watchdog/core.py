"""The safe-state watchdog: applies a grant's safe state when the Control Center can't.

Every ``poll_seconds`` it reads the Control Center's grant records
(``state/<project>/grants/<id>.json``, see ``control_center/automation_grants.py``)
and decides, per grant:

* ``ended`` / ``ended_unsafe``, or a final claim of its own: nothing to do.
* ``active`` / ``ending`` (the grant holds leases): the Control Center rewrites
  ``alive_at`` every 5 s from its event loop. The heartbeat counts as stale
  when the value hasn't changed for ``stale_seconds`` (measured on this
  process's own monotonic clock, so a wall-clock jump can't fake it). A stale
  grant is taken over when the Control Center's ``/health/live`` has also not
  answered for ``stale_seconds`` (it died), or answers while the heartbeat
  stays stale for twice as long (its grant loop hung).
* ``interrupted`` by a restart, not yet safed, last heartbeat recent: the old
  process is gone for sure, so it is taken over at once.
* Older than ``max_age_minutes`` since its last heartbeat: never acted on
  (its devices may have been used since), only logged.

Taking over (one task per grant):

1. Write the claim ``<id>.watchdog.json`` = ``claimed`` and an audit line.
   ``claimed`` binds nobody: until a lease is taken, a fresh heartbeat (or
   the Control Center ending the grant itself) makes the watchdog *stand down*.
2. Ask Central for each device the safe state needs, every ``retry_seconds``,
   for up to ``lease_wait_seconds``: Central refuses while the Control
   Center's lease lives (it lapses within a minute once renewals stop). With
   the first lease the claim becomes ``safing``: from then on the grant is the
   watchdog's, and a Control Center that comes back hands it over.
3. Apply each safe-state call of the approved protocol (checked again against
   its bounds, its sha256 checked against the approved one) with up to three
   tries, re-taking a lost lease once; release every lease.
4. The claim ends ``ended`` or ``ended_unsafe`` with the reason, plus an audit
   line. The Control Center copies that into its record and the run.

Every decision is logged, and each step is a ``"kind": "watchdog"`` line in
the grant's audit file. The watchdog writes nothing else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from labnet.client import ResourceConflictError
from labnet.control_center.automation_grants import (
    CLAIMED,
    ENDED,
    ENDED_UNSAFE,
    GRANT_ID,
    INTERRUPTED,
    LIVE,
    SAFE_STATE_CALL_SECONDS,
    SAFE_STATE_TRIES,
    SAFING,
    STOOD_DOWN,
    WATCHDOG_FINAL,
    GrantCallState,
    check_call,
    read_grant_record,
    read_watchdog_claim,
    watchdog_claim_path,
)
from labnet.control_center.automation_protocol import protocol_sha256
from labnet.control_center.automation_store import PROJECT_ID, write_json_file
from labnet.control_center.device_calls import (
    STREAM_PATTERN,
    call_device,
    device_error,
    find_method,
    lease_lost_before_call,
    lease_rejected,
)
from labnet.grpc.generated.device_contracts import DEVICE_CONTRACT_BY_TYPE, DeviceContract, RpcSpec

from .config import WatchdogSettings


LOGGER = logging.getLogger("labnet.automation_watchdog")

RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
LEASE_SECONDS = 20.0  # one request for one lease
REASON_STOPPED = "applied by the watchdog: the Control Center stopped answering"
REASON_RESTARTED = "applied by the watchdog: the Control Center restarted during the grant"


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass
class GrantFile:
    """One grant record as read from disk, with the watchdog's claim on it (if any)."""

    project_id: str
    grant_id: str
    folder: Path  # state/<project>/grants
    record: dict[str, Any]
    claim: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return self.folder / f"{self.grant_id}.json"

    @property
    def label(self) -> str:
        return f"{self.grant_id} ({self.project_id}/{self.record.get('run_id')})"


@dataclass
class Step:
    """One safe-state call, checked; ``problem`` says why it can't be made."""

    index: int
    device_id: str
    method: str
    args: dict[str, Any]
    spec: RpcSpec | None = None
    problem: str | None = None


@dataclass
class _Plan:
    steps: list[Step] = field(default_factory=list)
    contracts: dict[str, DeviceContract] = field(default_factory=dict)  # device id → contract (leasable)
    problem: str | None = None  # nothing at all may be done


class Watchdog:
    """Watches every grant record under ``automation_dir/state`` and takes over the abandoned ones.

    ``client`` is an ``AsyncLabClient`` with the watchdog's own Central key (or anything with
    ``acquire_device(contract, device_id)``). ``probe`` answers whether the Control Center's
    ``/health/live`` answers (default: an HTTP GET). ``clock`` is monotonic, ``wall`` the wall clock.
    """

    def __init__(
        self,
        settings: WatchdogSettings,
        client: Any,
        *,
        probe: Callable[[], Awaitable[bool]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._client = client
        self._probe = probe or self._probe_health
        self._clock = clock
        self._wall = wall
        self._state_dir = Path(settings.automation_dir) / "state"
        self._seen: dict[str, tuple[Any, float]] = {}  # grant id → (alive_at, clock when it last changed)
        self._health_down_since: float | None = None
        self._health_ok: bool | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._noted: dict[str, str] = {}  # grant id → the last decision logged about it
        self._quiet_until: dict[str, float] = {}  # grant id → clock before which it isn't taken over again
        self._cache: dict[Path, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._http: httpx.AsyncClient | None = None
        self.last_scan_at: float | None = None

    # ---- what the health page shows -----------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "last_scan_at": _iso(self.last_scan_at) if self.last_scan_at is not None else None,
            "handling": sorted(self._tasks),
            "control_center": {None: "unknown", True: "answering", False: "not answering"}[self._health_ok],
        }

    def ready(self) -> bool:
        limit = max(3 * self.settings.poll_seconds, 10.0)
        return self.last_scan_at is not None and self._wall() - self.last_scan_at <= limit

    # ---- the loop ----------------------------------------------------------------------------

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        LOGGER.info("Watching the grants under %s; the Control Center's health at %s; stale after %g s",
                    self._state_dir, self.settings.health_url, self.settings.stale_seconds)
        while not stop.is_set():
            started = self._clock()
            try:
                await self.scan_once()
            except Exception:  # noqa: BLE001 - the next scan tries again
                LOGGER.exception("A scan failed")
            delay = max(0.1, self.settings.poll_seconds - (self._clock() - started))
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except TimeoutError:
                pass

    async def wait_idle(self) -> None:
        """Until every takeover under way has finished (tests, and a clean shutdown)."""

        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def aclose(self) -> None:
        """Cancel takeovers under way (each releases the leases it holds; a ``safing`` claim
        is resumed at the next start), and close the health client."""

        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def scan_once(self) -> None:
        files = await asyncio.to_thread(self._read_grants)
        live = [item for item in files if item.record.get("status") in LIVE and item.grant_id not in self._tasks
                and (item.claim is None or item.claim["status"] in (CLAIMED, STOOD_DOWN))]
        if live:
            await self._check_health()
        else:
            self._health_down_since = None  # a new failure window starts with the next live grant
        present = {item.grant_id for item in files}
        for grant_id in list(self._seen):
            if grant_id not in present:
                self._seen.pop(grant_id, None)
        for item in files:
            try:
                self._consider(item)
            except Exception:  # noqa: BLE001 - one odd record never stops the others
                LOGGER.exception("Could not consider grant %s", item.label)
        self.last_scan_at = self._wall()

    def _read_grants(self) -> list[GrantFile]:
        """Every grant record (links never followed), each with the watchdog's claim on it."""

        found: list[GrantFile] = []
        try:
            projects = [entry for entry in os.scandir(self._state_dir)
                        if entry.is_dir(follow_symlinks=False) and PROJECT_ID.fullmatch(entry.name)]
        except (FileNotFoundError, NotADirectoryError):
            return found
        for project in projects:
            folder = Path(project.path) / "grants"
            try:
                entries = list(os.scandir(folder))
            except (FileNotFoundError, NotADirectoryError):
                continue
            for entry in entries:
                if not entry.name.endswith(".json") or GRANT_ID.fullmatch(entry.name[:-5]) is None:
                    continue  # also skips the claims (<id>.watchdog.json)
                path = Path(entry.path)
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    key = (info.st_mtime_ns, info.st_size)
                    cached = self._cache.get(path)
                    if cached is not None and cached[0] == key:
                        record = cached[1]
                    else:
                        record = read_grant_record(path)
                        self._cache[path] = (key, record)
                except (OSError, ValueError) as exc:  # mid-replace on Windows, or not a record: next scan
                    LOGGER.debug("Skipping %s this time: %s", path, exc)
                    continue
                item = GrantFile(project.name, entry.name[:-5], folder, record)
                if record.get("status") not in (ENDED, ENDED_UNSAFE):
                    item.claim = read_watchdog_claim(folder, item.grant_id)
                found.append(item)
        return found

    def _note(self, grant_id: str, key: str, level: int, message: str, *args: Any) -> None:
        """Log a decision about a grant once (until the decision changes)."""

        if self._noted.get(grant_id) != key:
            self._noted[grant_id] = key
            LOGGER.log(level, message, *args)

    async def _check_health(self) -> None:
        ok = await self._probe()
        if ok:
            if self._health_ok is False:
                LOGGER.info("The Control Center answers again (%s)", self.settings.health_url)
            self._health_down_since = None
        else:
            if self._health_down_since is None:
                self._health_down_since = self._clock()
            if self._health_ok is not False:
                LOGGER.warning("The Control Center doesn't answer %s", self.settings.health_url)
        self._health_ok = ok

    async def _probe_health(self) -> bool:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.settings.health_timeout_seconds, verify=True)
        try:
            reply = await self._http.get(self.settings.health_url)
            return reply.status_code == 200
        except Exception:  # noqa: BLE001 - refused, timed out, reset: not answering
            return False

    def _consider(self, item: GrantFile) -> None:
        grant_id, record, claim = item.grant_id, item.record, item.claim
        if grant_id in self._tasks:
            return
        status = record.get("status")
        if status in (ENDED, ENDED_UNSAFE):
            self._seen.pop(grant_id, None)
            return
        if claim is not None and claim["status"] in WATCHDOG_FINAL:
            return
        if claim is not None and claim["status"] == SAFING:
            # An earlier watchdog held a lease of it and stopped before it finished: finish it.
            LOGGER.warning("Grant %s: resuming a takeover an earlier watchdog started (claim safing)", item.label)
            self._start(item, why=str(claim.get("why") or "resumed"), stand_down=False, claim=claim)
            return
        if status not in LIVE and status != INTERRUPTED:
            return
        alive = _number(record.get("alive_at"))
        if alive is None or not isinstance(record.get("protocol"), dict):
            if status in LIVE:
                self._note(grant_id, "no-heartbeat", logging.WARNING,
                           "Grant %s has no heartbeat or protocol in its record (a Control Center older than the "
                           "watchdog?): it can't be watched", item.label)
            return
        wall = self._wall()
        too_old = wall - alive > self.settings.max_age_minutes * 60
        if status == INTERRUPTED:
            if record.get("safe_state_by") is not None:
                return
            if too_old:
                self._note(grant_id, "too-old", logging.WARNING,
                           "Grant %s was interrupted more than %g min ago: not acting on it; check its devices by "
                           "hand", item.label, self.settings.max_age_minutes)
                return
            self._start(item, why="the Control Center restarted during the grant, so no safe state was applied",
                        stand_down=False)
            return

        # active / ending: is the heartbeat stale, and is the Control Center gone or hung?
        now = self._clock()
        seen = self._seen.get(grant_id)
        if seen is None:
            changed_at = now - max(0.0, wall - alive)
        elif seen[0] != alive:
            changed_at = now
        else:
            changed_at = seen[1]
        self._seen[grant_id] = (alive, changed_at)
        stale = now - changed_at
        threshold = self.settings.stale_seconds
        if stale < threshold:
            if self._noted.get(grant_id) not in (None, "fresh"):
                LOGGER.info("Grant %s: the heartbeat is fresh again; standing by", item.label)
            self._noted[grant_id] = "fresh"
            return
        if too_old:
            self._note(grant_id, "too-old", logging.WARNING,
                       "Grant %s has had no heartbeat for more than %g min: not acting on it; check its devices by "
                       "hand", item.label, self.settings.max_age_minutes)
            return
        if now < self._quiet_until.get(grant_id, float("-inf")):
            return
        down = None if self._health_down_since is None else now - self._health_down_since
        if down is not None and down >= threshold:
            why = (f"the Control Center stopped answering: no heartbeat for {stale:.0f} s and "
                   f"{self.settings.health_url} unanswered for {down:.0f} s")
        elif stale >= 2 * threshold:
            why = (f"the Control Center's grant heartbeat stopped for {stale:.0f} s although "
                   f"{self.settings.health_url} {'answers' if self._health_ok else 'answered recently'} "
                   "(its event loop hung?)")
        else:
            self._note(grant_id, "suspect", logging.WARNING,
                       "Grant %s: no heartbeat for %.0f s; watching the Control Center's health before acting",
                       item.label, stale)
            return
        self._start(item, why=why, stand_down=True)

    def _start(self, item: GrantFile, *, why: str, stand_down: bool, claim: dict[str, Any] | None = None) -> None:
        self._noted[item.grant_id] = "takeover"
        task = asyncio.create_task(self._take_over(item, why=why, stand_down=stand_down, claim=claim),
                                   name=f"watchdog-{item.grant_id}")
        self._tasks[item.grant_id] = task

        def finished(_task: asyncio.Task[None], grant_id: str = item.grant_id) -> None:
            if self._tasks.get(grant_id) is _task:
                self._tasks.pop(grant_id, None)

        task.add_done_callback(finished)

    # ---- taking a grant over -----------------------------------------------------------------

    async def _take_over(self, item: GrantFile, *, why: str, stand_down: bool,
                         claim: dict[str, Any] | None) -> None:
        record = item.record
        restarted = record.get("status") == INTERRUPTED
        reason = REASON_RESTARTED if restarted else REASON_STOPPED
        held: dict[str, Any] = {}
        if claim is None:
            claim = {
                "schema_version": 1,
                "grant_id": item.grant_id,
                "project_id": item.project_id,
                "run_id": record.get("run_id"),
                "status": CLAIMED,
                "claimed_at": _iso(self._wall()),
                "why": why,
                "reason": reason,
                "watchdog": {"pid": os.getpid(), "host": socket.gethostname()},
            }
            if not self._write_claim(item, claim):
                return  # not even a claim: the next scan tries again
            LOGGER.warning("Taking over grant %s: %s", item.label, why)
            self._audit(item, {"event": "claimed", "why": why, "grant_status": record.get("status")})
        else:
            claim = dict(claim)
            reason = str(claim.get("reason") or reason)
            self._audit(item, {"event": "resumed", "claim": claim.get("status")})
        try:
            plan = self._plan(record)
            if plan.problem is not None:
                await self._finish(item, claim, held, ENDED_UNSAFE,
                                   f"the watchdog could not apply the safe state: {plan.problem}")
                return
            if not plan.steps:
                await self._finish(item, claim, held, ENDED, f"{reason} (the protocol has no safe state)")
                return
            binding = claim["status"] == SAFING
            alive_then = record.get("alive_at")
            deadline = self._clock() + self.settings.lease_wait_seconds
            waiting: dict[str, str] = {}
            while True:
                if stand_down and not binding:
                    back = await asyncio.to_thread(self._came_back, item, alive_then)
                    if back is not None:
                        self._stand_down(item, claim, back)
                        return
                for device_id, contract in plan.contracts.items():
                    if device_id in held:
                        continue
                    device, note = await self._lease(item, device_id, contract)
                    if device is None:
                        if waiting.get(device_id) != note:
                            waiting[device_id] = note
                            LOGGER.info("Grant %s: no lease on %s yet: %s", item.label, device_id, note)
                        continue
                    held[device_id] = device
                    if not binding:
                        binding = True
                        claim.update({"status": SAFING, "safing_at": _iso(self._wall())})
                        if not self._write_claim(item, claim):
                            LOGGER.error("Grant %s: holding a lease but the claim couldn't be marked safing", item.label)
                        LOGGER.warning("Grant %s: leased %s; the grant is the watchdog's now", item.label, device_id)
                if len(held) == len(plan.contracts) or self._clock() >= deadline:
                    break
                await asyncio.sleep(self.settings.retry_seconds)
            if not held and not binding and stand_down and await self._probe():
                # Nothing leased in all that time and the Control Center answers: it still holds them.
                self._quiet_until[item.grant_id] = self._clock() + self.settings.lease_wait_seconds
                self._stand_down(item, claim, "the Control Center answers and still holds the leases, but its "
                                              "grant heartbeat is stale: check the Control Center's log")
                return
            missing = sorted(set(plan.contracts) - set(held))
            safe = await self._apply(item, plan, held)
            if safe and not missing:
                await self._finish(item, claim, held, ENDED, reason)
            else:
                detail = (f"no lease on {', '.join(missing)} within {self.settings.lease_wait_seconds:g} s"
                          if missing else "a safe-state call failed")
                await self._finish(item, claim, held, ENDED_UNSAFE,
                                   f"the watchdog could not apply the safe state ({detail}); "
                                   f"{reason.split(': ', 1)[-1]}")
        except asyncio.CancelledError:
            await asyncio.shield(self._release_all(item, held))
            LOGGER.warning("Grant %s: takeover interrupted (the watchdog is stopping); claim left %s", item.label,
                           claim.get("status"))
            raise
        except Exception as exc:  # noqa: BLE001 - never leave leases behind
            LOGGER.exception("Grant %s: the takeover failed", item.label)
            if claim.get("status") == SAFING or held:
                await self._finish(item, claim, held, ENDED_UNSAFE,
                                   f"the watchdog could not apply the safe state: {device_error(exc)}")
            else:
                await self._release_all(item, held)

    def _came_back(self, item: GrantFile, alive_then: Any) -> str | None:
        """Why the Control Center is back in charge of the grant, or None."""

        try:
            record = read_grant_record(item.path)
        except (OSError, ValueError):
            return None
        status = record.get("status")
        if status in (ENDED, ENDED_UNSAFE):
            return f"the Control Center ended the grant itself ({status})"
        if status in LIVE and record.get("alive_at") != alive_then:
            return "the Control Center's heartbeat came back"
        return None

    def _stand_down(self, item: GrantFile, claim: dict[str, Any], why: str) -> None:
        claim.update({"status": STOOD_DOWN, "stood_down_at": _iso(self._wall()), "ended_reason": why})
        self._write_claim(item, claim)
        self._audit(item, {"event": "stood_down", "why": why})
        self._noted[item.grant_id] = "stood-down"
        LOGGER.warning("Grant %s: standing down, no lease taken: %s", item.label, why)

    def _plan(self, record: dict[str, Any]) -> _Plan:
        """The approved safe state, checked as the Control Center checks it."""

        plan = _Plan()
        protocol = record.get("protocol")
        if not isinstance(protocol, dict):
            plan.problem = "the grant record holds no protocol"
            return plan
        try:
            if protocol_sha256(protocol) != record.get("sha256"):
                plan.problem = "the grant record's protocol doesn't match the approved sha256"
                return plan
        except (TypeError, ValueError):
            plan.problem = "the grant record's protocol isn't plain JSON"
            return plan
        devices = protocol.get("devices")
        calls = protocol.get("safe_state") or []
        if not isinstance(devices, list) or not isinstance(calls, list):
            plan.problem = "the grant record's protocol is malformed"
            return plan
        types = {item.get("device_id"): item.get("device_type") for item in devices if isinstance(item, dict)}
        granted = record.get("devices") if isinstance(record.get("devices"), list) else list(types)
        for index, item in enumerate(calls):
            if not isinstance(item, dict):
                plan.steps.append(Step(index, "?", "?", {}, problem="not a call"))
                continue
            step = Step(index, str(item.get("device_id")), str(item.get("method")),
                        dict(item.get("args") or {}) if isinstance(item.get("args"), dict) else {})
            plan.steps.append(step)
            contract = DEVICE_CONTRACT_BY_TYPE.get(types.get(step.device_id))  # type: ignore[arg-type]
            if contract is None or step.device_id not in granted:
                step.problem = f"{step.device_id} is not a device of this grant with a known contract"
                continue
            step.spec = find_method(contract, step.method)
            if step.spec is None or step.spec.pattern == STREAM_PATTERN:
                step.problem = f"{step.method} is not a method that sets {step.device_id}"
                continue
            problem = check_call(protocol, GrantCallState(), {"device_id": step.device_id, "method": step.method,
                                 "args": step.args}, now=0.0, seconds_left=1e9, steps=False)
            if problem is not None:
                step.problem = problem
                continue
            plan.contracts.setdefault(step.device_id, contract)
        return plan

    async def _lease(self, item: GrantFile, device_id: str, contract: DeviceContract) -> tuple[Any, str]:
        """(the leased device, "") or (None, why not). A request that outlives its time is released when it ends."""

        def release_late(finished: asyncio.Future[Any]) -> None:
            if not finished.cancelled() and finished.exception() is None:
                asyncio.ensure_future(finished.result().aclose())

        task = asyncio.ensure_future(self._client.acquire_device(contract, device_id))
        try:
            done, _pending = await asyncio.wait({task}, timeout=LEASE_SECONDS)
        except asyncio.CancelledError:  # stopping: a lease that still arrives is released, never kept
            task.add_done_callback(release_late)
            raise
        if not done:
            task.add_done_callback(release_late)
            return None, f"Central didn't answer within {LEASE_SECONDS:g} s"
        try:
            device = task.result()
        except ResourceConflictError:
            return None, "still leased (Central refuses until the Control Center's lease lapses)"
        except Exception as exc:  # noqa: BLE001 - unavailable, unreachable: try again
            return None, device_error(exc)
        self._audit(item, {"event": "lease", "device_id": device_id, "outcome": "taken"})
        return device, ""

    async def _apply(self, item: GrantFile, plan: _Plan, held: dict[str, Any]) -> bool:
        safe = True
        for step in plan.steps:
            entry = {"event": "safe_state", "index": step.index, "device_id": step.device_id, "method": step.method,
                     "args": step.args}
            if step.problem is not None:
                self._audit(item, {**entry, "outcome": "failed", "message": step.problem})
                LOGGER.error("Grant %s: safe-state call %d can't be made: %s", item.label, step.index, step.problem)
                safe = False
                continue
            if held.get(step.device_id) is None:
                self._audit(item, {**entry, "outcome": "failed", "message": "no lease"})
                safe = False
                continue
            if not await self._safe_call(item, step, plan, held, entry):
                safe = False
        return safe

    async def _safe_call(self, item: GrantFile, step: Step, plan: _Plan, held: dict[str, Any],
                         entry: dict[str, Any]) -> bool:
        retaken = False
        for attempt in range(1, SAFE_STATE_TRIES + 1):
            started = self._clock()
            try:
                device = held.get(step.device_id)
                if device is None:
                    raise RuntimeError(f"the lease on {step.device_id} could not be taken again")
                await asyncio.wait_for(call_device(device, step.spec, dict(step.args)), SAFE_STATE_CALL_SECONDS)
            except Exception as exc:  # noqa: BLE001 - recorded, tried again
                self._audit(item, {**entry, "attempt": attempt, "outcome": "failed", "message": device_error(exc),
                                   "duration_ms": round((self._clock() - started) * 1000, 3)})
                LOGGER.warning("Grant %s: %s.%s failed (try %d): %s", item.label, step.device_id, step.method,
                               attempt, device_error(exc))
                if (lease_lost_before_call(exc) or lease_rejected(exc)) and not retaken:
                    retaken = True
                    old = held.pop(step.device_id, None)
                    if old is not None:
                        await self._release(item, step.device_id, old)
                    device, _note = await self._lease(item, step.device_id, plan.contracts[step.device_id])
                    if device is not None:
                        held[step.device_id] = device
                continue
            self._audit(item, {**entry, "attempt": attempt, "outcome": "ok",
                               "duration_ms": round((self._clock() - started) * 1000, 3)})
            LOGGER.info("Grant %s: safe state %s.%s(%s) applied", item.label, step.device_id, step.method,
                        json.dumps(step.args))
            return True
        return False

    async def _release(self, item: GrantFile, device_id: str, device: Any) -> bool:
        try:
            await device.aclose()
        except Exception as exc:  # noqa: BLE001 - it lapses within a minute anyway
            LOGGER.warning("Grant %s: releasing %s failed: %s", item.label, device_id, device_error(exc))
            self._audit(item, {"event": "release", "device_id": device_id, "outcome": "failed",
                               "message": device_error(exc)})
            return False
        self._audit(item, {"event": "release", "device_id": device_id, "outcome": "ok"})
        return True

    async def _release_all(self, item: GrantFile, held: dict[str, Any]) -> bool:
        released = True
        for device_id in list(held):
            released = await self._release(item, device_id, held.pop(device_id)) and released
        return released

    async def _finish(self, item: GrantFile, claim: dict[str, Any], held: dict[str, Any], status: str,
                      reason: str) -> None:
        released = await self._release_all(item, held)
        claim.update({"status": status, "ended_at": _iso(self._wall()), "ended_reason": reason,
                      "released": released})
        if not self._write_claim(item, claim):
            LOGGER.error("Grant %s: could not record the outcome (%s) in %s", item.label, status,
                         watchdog_claim_path(item.folder, item.grant_id))
        self._audit(item, {"event": "end", "outcome": status, "reason": reason, "released": released})
        self._noted[item.grant_id] = "finished"
        if status == ENDED_UNSAFE:
            LOGGER.error("Grant %s ended UNSAFE: %s. Check its devices by hand.", item.label, reason)
        else:
            LOGGER.warning("Grant %s ended: %s", item.label, reason)

    # ---- the two things the watchdog writes ----------------------------------------------------

    def _write_claim(self, item: GrantFile, claim: dict[str, Any]) -> bool:
        claim["updated_at"] = _iso(self._wall())
        try:
            write_json_file(item.folder, watchdog_claim_path(item.folder, item.grant_id).name, claim)
            return True
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not write the watchdog's claim on grant %s", item.label)
            return False

    def _audit(self, item: GrantFile, entry: dict[str, Any]) -> None:
        run_id = item.record.get("run_id")
        if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            LOGGER.warning("Grant %s has no usable run id; audit line not written: %s", item.grant_id, entry)
            return
        path = item.folder.parent / "audit" / run_id / f"grant-{item.grant_id}.jsonl"
        line = json.dumps({"at": _iso(self._wall()), "grant": item.grant_id, "kind": "watchdog", **entry},
                          ensure_ascii=False, default=str) + "\n"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "ab") as file:
                file.write(line.encode("utf-8", "replace"))
        except OSError:
            LOGGER.exception("Could not write the audit line for grant %s", item.label)


__all__ = ["GrantFile", "REASON_RESTARTED", "REASON_STOPPED", "Watchdog"]
