"""The safe-state watchdog: the Control Center's heartbeat and hand-over, and the watchdog's takeover."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import grpc
import pytest

from labnet.automation_watchdog import Watchdog, WatchdogSettings
from labnet.automation_watchdog.core import REASON_RESTARTED, REASON_STOPPED
from labnet.automation_watchdog.service import start_health_server
from labnet.client import DeviceUnavailableError, ResourceConflictError
from labnet.control_center import automation_grants
from labnet.control_center.automation_grants import HANDED_REASON, GrantManager, watchdog_claim_path
from labnet.control_center.automation_protocol import protocol_sha256
from labnet.control_center.automation_store import AutomationStore

from .test_automation_grants import AOM_ONLY, PROJECT, RUN, Clock, FakeLab, FakeRuns, _audit, _record, _until


SHA = protocol_sha256(AOM_ONLY)
GRANTS = Path("automation") / "state" / PROJECT / "grants"


def _claim(tmp_path: Path, grant_id: str) -> dict | None:
    path = watchdog_claim_path(tmp_path / GRANTS, grant_id)
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def _write_claim(tmp_path: Path, grant_id: str, status: str, **fields: Any) -> None:
    path = watchdog_claim_path(tmp_path / GRANTS, grant_id)
    path.write_text(json.dumps({"schema_version": 1, "grant_id": grant_id, "status": status, **fields}), "utf-8")


async def _start_grant(tmp_path: Path, lab: FakeLab, runs: FakeRuns | None = None, *, heartbeat: float = 3600.0,
                       minutes: float = 5.0) -> tuple[GrantManager, str]:
    manager = GrantManager(lab, AutomationStore(tmp_path / "automation"), runs or FakeRuns(), None,
                           heartbeat_seconds=heartbeat)
    grant = await manager.create(project_id=PROJECT, run_id=RUN, protocol=AOM_ONLY, sha256=SHA,
                                 duration_minutes=minutes, approved_by="alice")
    return manager, grant["id"]


def _freeze(manager: GrantManager) -> None:
    """The Control Center's event loop stops: no heartbeat, no expiry, the leases left as they are."""

    if manager._monitor_task is not None:
        manager._monitor_task.cancel()
    for grant in manager._grants.values():
        if grant.timer is not None:
            grant.timer.cancel()


class WatchdogCentral:
    """Central as the watchdog's own user sees it: each device is refused (``refusals`` times) while
    the Control Center's lease lives, then leased from the lab."""

    def __init__(self, lab: FakeLab, refusals: int = 2, on_refusal=None) -> None:
        self.lab = lab
        self.left: dict[str, int] = {}
        self.refusals = refusals
        self.on_refusal = on_refusal
        self.requests: list[str] = []

    async def acquire_device(self, contract, device_id: str):
        self.requests.append(device_id)
        left = self.left.setdefault(device_id, self.refusals)
        if left > 0:
            self.left[device_id] = left - 1
            if self.on_refusal is not None:
                self.on_refusal()
            raise ResourceConflictError("Device already has an active lease.")
        return await self.lab.acquire_device(contract, device_id)


class Health:
    def __init__(self, up: bool = False) -> None:
        self.up = up
        self.probes = 0

    async def __call__(self) -> bool:
        self.probes += 1
        return self.up


def _watchdog(tmp_path: Path, central: Any, health: Health, clock: Clock, *, stale_by: float = 30.0,
              **settings: Any) -> Watchdog:
    """A watchdog whose wall clock runs ``stale_by`` seconds ahead (the heartbeat looks that old)."""

    values = dict(server_url="http://127.0.0.1:8008", api_key="key", automation_dir=tmp_path / "automation",
                  stale_seconds=20.0, poll_seconds=2.0, lease_wait_seconds=120.0, retry_seconds=0.001)
    values.update(settings)
    return Watchdog(WatchdogSettings(**values), central, probe=health, clock=clock,
                    wall=lambda: time.time() + stale_by)


def _watchdog_lines(tmp_path: Path, grant_id: str) -> list[dict]:
    return [line for line in _audit(tmp_path, grant_id) if line["kind"] == "watchdog"]


# ---- the Control Center's side: heartbeat and hand-over -----------------------------------------


@pytest.mark.asyncio
async def test_the_heartbeat_is_written_while_the_grant_is_active_and_stops_when_it_ends(tmp_path: Path) -> None:
    lab = FakeLab()
    manager, grant_id = await _start_grant(tmp_path, lab, heartbeat=0.02)
    record = _record(tmp_path, grant_id)
    # Everything the watchdog needs is in the record.
    assert record["protocol"] == AOM_ONLY and record["sha256"] == SHA and record["heartbeat_seconds"] == 0.02
    assert record["status"] == "active" and record["devices"] == ["dev01-aom"] and record["ends_at"]
    first = record["alive_at"]
    await _until(lambda: _record(tmp_path, grant_id)["alive_at"] > first)
    await manager.stop(grant_id, reason="stopped", by="alice")
    ended = _record(tmp_path, grant_id)
    assert ended["status"] == "ended"
    await _until(lambda: manager._monitor_task is None or manager._monitor_task.done())
    await asyncio.sleep(0.1)
    assert _record(tmp_path, grant_id)["alive_at"] == ended["alive_at"]  # no heartbeat once it ended
    await manager.close()


@pytest.mark.asyncio
async def test_a_heartbeat_that_cannot_be_written_ends_the_grant_with_its_safe_state(tmp_path: Path,
                                                                                    monkeypatch) -> None:
    lab = FakeLab()
    manager, grant_id = await _start_grant(tmp_path, lab, heartbeat=0.01)
    real = automation_grants.write_json_file

    def failing(folder, name, data, *args):
        if name.endswith(".json") and data.get("status") == "active":
            raise OSError("disk full")
        return real(folder, name, data, *args)

    monkeypatch.setattr(automation_grants, "write_json_file", failing)
    await _until(lambda: _record(tmp_path, grant_id)["status"] == "ended")
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}] and lab.held() == set()
    assert "heartbeat" in _record(tmp_path, grant_id)["ended_reason"]
    await manager.close()


@pytest.mark.asyncio
async def test_a_claimed_claim_does_not_stop_the_control_center(tmp_path: Path) -> None:
    lab = FakeLab()
    manager, grant_id = await _start_grant(tmp_path, lab, heartbeat=0.01)
    _write_claim(tmp_path, grant_id, "claimed")
    first = _record(tmp_path, grant_id)["alive_at"]
    await _until(lambda: _record(tmp_path, grant_id)["alive_at"] > first)  # the heartbeat that stands it down
    assert _record(tmp_path, grant_id)["status"] == "active" and lab.held() == {"dev01-aom"}
    await manager.close()


@pytest.mark.asyncio
async def test_a_control_center_that_comes_back_hands_a_taken_grant_over(tmp_path: Path) -> None:
    lab, runs = FakeLab(), FakeRuns()
    manager, grant_id = await _start_grant(tmp_path, lab, runs, heartbeat=0.01)
    _write_claim(tmp_path, grant_id, "safing")  # the watchdog holds a lease
    await _until(lambda: _record(tmp_path, grant_id)["status"] == "interrupted")
    record = _record(tmp_path, grant_id)
    assert record["handed_to_watchdog"] is True and record["ended_reason"] == HANDED_REASON
    assert lab.calls_to("set_rf_enabled") == [] and lab.held() == set()  # no safe state of its own; released
    await _until(lambda: runs.phases == ["analyzing"])
    assert runs.grants[-1]["status"] == "interrupted"
    assert _audit(tmp_path, grant_id)[-1]["outcome"] == "handed_to_watchdog"
    # The watchdog finishes: its outcome is copied into the record and the run.
    _write_claim(tmp_path, grant_id, "ended", ended_reason=REASON_STOPPED, ended_at="2026-10-08T12:00:00Z")
    await _until(lambda: runs.grants[-1]["status"] == "ended")
    assert _record(tmp_path, grant_id)["safe_state_by"] == "watchdog"
    assert _record(tmp_path, grant_id)["status"] == "ended" and runs.grants[-1]["ended_reason"] == REASON_STOPPED
    await manager.close()


def test_mark_interrupted_keeps_what_the_watchdog_did(tmp_path: Path) -> None:
    runs = FakeRuns()
    folder = tmp_path / GRANTS
    folder.mkdir(parents=True)
    base = {"approved_by": "alice", "starts_at": "2026-10-07T12:00:00Z", "ends_at": "2026-10-07T12:20:00Z",
            "devices": ["dev01-aom"], "calls": 3, "refused_calls": 0, "ended_reason": None, "ended_at": None,
            "run_id": RUN, "project_id": PROJECT, "token_sha256": "0" * 64, "protocol": AOM_ONLY, "sha256": SHA,
            "alive_at": time.time()}
    records = {
        "grant-00000000000000a1": ("active", "ended"),  # the watchdog finished while the CC was down
        "grant-00000000000000a2": ("ending", "safing"),  # the watchdog is applying it now
        "grant-00000000000000a3": ("interrupted", "ended_unsafe"),  # finished after an earlier restart
        "grant-00000000000000a4": ("active", None),  # no watchdog: interrupted as before
        "grant-00000000000000a5": ("active", "stood_down"),
    }
    for grant_id, (status, claim) in records.items():
        (folder / f"{grant_id}.json").write_text(json.dumps({**base, "id": grant_id, "status": status}), "utf-8")
        if claim:
            _write_claim(tmp_path, grant_id, claim, ended_reason=f"by the watchdog ({claim})",
                         ended_at="2026-10-07T12:05:00Z")

    manager = GrantManager(FakeLab(), AutomationStore(tmp_path / "automation"), runs, None)
    manager.mark_interrupted()

    def read(grant_id: str) -> dict:
        return json.loads((folder / f"{grant_id}.json").read_text("utf-8"))

    assert read("grant-00000000000000a1")["status"] == "ended"
    assert read("grant-00000000000000a1")["safe_state_by"] == "watchdog"
    assert read("grant-00000000000000a1")["ended_reason"] == "by the watchdog (ended)"
    assert read("grant-00000000000000a2")["status"] == "interrupted"
    assert read("grant-00000000000000a2")["handed_to_watchdog"] is True
    assert read("grant-00000000000000a2")["ended_reason"] == HANDED_REASON
    assert read("grant-00000000000000a3")["status"] == "ended_unsafe"
    assert read("grant-00000000000000a4")["status"] == read("grant-00000000000000a5")["status"] == "interrupted"
    assert "restarted" in read("grant-00000000000000a4")["ended_reason"]
    assert sorted(manager._handed) == ["grant-00000000000000a2", "grant-00000000000000a4", "grant-00000000000000a5"]
    statuses = sorted(grant["status"] for grant in runs.grants)
    assert statuses.count("ended_unsafe") == 1 and statuses.count("interrupted") == 3 and "ended" in statuses
    # The run's copy carries the watchdog's outcome too (seen live: the last write lost reason and time).
    a1 = [grant for grant in runs.grants if grant["id"] == "grant-00000000000000a1"]
    assert a1[-1]["ended_reason"] == "by the watchdog (ended)" and a1[-1]["ended_at"] == "2026-10-07T12:05:00Z"
    # A second start changes nothing that is settled.
    runs.grants.clear()
    GrantManager(FakeLab(), AutomationStore(tmp_path / "automation"), runs, None).mark_interrupted()
    assert runs.grants == [] and read("grant-00000000000000a1")["status"] == "ended"


# ---- the watchdog --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_watchdog_takes_over_when_the_heartbeat_is_stale_and_the_control_center_is_down(
        tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=False)
    manager, grant_id = await _start_grant(tmp_path, lab)
    _freeze(manager)  # the Control Center died: its lease lives on for a while
    central = WatchdogCentral(lab, refusals=2)
    watchdog = _watchdog(tmp_path, central, health, clock)

    await watchdog.scan_once()  # stale, but the health check has only just failed
    assert _claim(tmp_path, grant_id) is None and health.probes == 1
    clock.now += 21
    await watchdog.scan_once()
    await watchdog.wait_idle()

    claim = _claim(tmp_path, grant_id)
    assert claim["status"] == "ended" and claim["ended_reason"] == REASON_STOPPED and claim["released"] is True
    assert "stopped answering" in claim["why"]
    assert central.requests == ["dev01-aom"] * 3  # refused while the old lease lived, then granted
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}]
    watchdog_device = lab.devices[-1]
    assert watchdog_device.closed  # released
    events = [line["event"] for line in _watchdog_lines(tmp_path, grant_id)]
    assert events == ["claimed", "lease", "safe_state", "release", "end"]
    assert _record(tmp_path, grant_id)["status"] == "active"  # the Control Center's record is never written

    # The Control Center comes back (its loop had hung): it hands the grant over (releasing its own
    # lapsed lease, no safe state of its own) and records the watchdog's outcome.
    manager._beat()
    await _until(lambda: _record(tmp_path, grant_id)["status"] == "ended")
    assert _record(tmp_path, grant_id)["safe_state_by"] == "watchdog"
    assert _record(tmp_path, grant_id)["handed_to_watchdog"] is True
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}]  # still only the watchdog's
    assert lab.held() == set()
    await manager.close()
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_the_watchdog_stands_down_when_the_heartbeat_returns_before_it_has_a_lease(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=False)
    manager, grant_id = await _start_grant(tmp_path, lab)
    _freeze(manager)
    grant = manager._grants[grant_id]
    # While the watchdog waits for the lease, the Control Center's loop wakes up and beats again.
    central = WatchdogCentral(lab, refusals=50, on_refusal=lambda: manager._save(grant))
    watchdog = _watchdog(tmp_path, central, health, clock)
    await watchdog.scan_once()
    clock.now += 21
    await watchdog.scan_once()
    await watchdog.wait_idle()

    claim = _claim(tmp_path, grant_id)
    assert claim["status"] == "stood_down" and "heartbeat came back" in claim["ended_reason"]
    assert len(central.requests) == 1 and lab.calls_to("set_rf_enabled") == []
    assert lab.held() == {"dev01-aom"}  # still the Control Center's
    # A stood-down claim doesn't stop the Control Center.
    manager._beat()
    await asyncio.sleep(0)
    assert _record(tmp_path, grant_id)["status"] == "active"
    await manager.close()
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_the_watchdog_takes_over_a_hung_control_center_whose_health_still_answers(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=True)
    manager, grant_id = await _start_grant(tmp_path, lab)
    _freeze(manager)
    central = WatchdogCentral(lab, refusals=1)
    watchdog = _watchdog(tmp_path, central, health, clock)
    await watchdog.scan_once()  # 30 s stale but /health/live answers: watch a while longer
    assert _claim(tmp_path, grant_id) is None
    clock.now += 5
    await watchdog.scan_once()
    assert _claim(tmp_path, grant_id) is None
    clock.now += 6  # 41 s ≥ twice the threshold
    await watchdog.scan_once()
    await watchdog.wait_idle()
    claim = _claim(tmp_path, grant_id)
    assert claim["status"] == "ended" and "hung" in claim["why"]
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}]
    await manager.close()
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_a_fresh_heartbeat_is_never_taken_over(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=False)
    manager, grant_id = await _start_grant(tmp_path, lab, heartbeat=0.01)
    watchdog = _watchdog(tmp_path, WatchdogCentral(lab), health, clock, stale_by=0.0)
    for _ in range(3):
        await watchdog.scan_once()
        clock.now += 30  # the heartbeat changes between scans, so it is never stale
        await asyncio.sleep(0.05)
    assert _claim(tmp_path, grant_id) is None
    await manager.close()
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_a_safe_state_that_fails_ends_unsafe_and_still_releases(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=False)
    manager, grant_id = await _start_grant(tmp_path, lab)
    _freeze(manager)
    lab.failures[("dev01-aom", "set_rf_enabled")] = [DeviceUnavailableError(grpc.StatusCode.UNAVAILABLE, "Lab PC unreachable") for _ in range(3)]
    watchdog = _watchdog(tmp_path, WatchdogCentral(lab, refusals=0), health, clock)
    await watchdog.scan_once()
    clock.now += 21
    await watchdog.scan_once()
    await watchdog.wait_idle()
    claim = _claim(tmp_path, grant_id)
    assert claim["status"] == "ended_unsafe" and "could not apply the safe state" in claim["ended_reason"]
    assert len(lab.calls_to("set_rf_enabled")) == 3 and lab.devices[-1].closed
    attempts = [line for line in _watchdog_lines(tmp_path, grant_id) if line["event"] == "safe_state"]
    assert [line["outcome"] for line in attempts] == ["failed"] * 3
    await manager.close()
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_no_lease_within_the_wait_ends_unsafe(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=False)
    manager, grant_id = await _start_grant(tmp_path, lab)
    _freeze(manager)

    def time_passes() -> None:
        clock.now += 50

    central = WatchdogCentral(lab, refusals=100, on_refusal=time_passes)
    watchdog = _watchdog(tmp_path, central, health, clock)
    await watchdog.scan_once()
    clock.now += 21
    await watchdog.scan_once()
    await watchdog.wait_idle()
    claim = _claim(tmp_path, grant_id)
    assert claim["status"] == "ended_unsafe" and "no lease on dev01-aom" in claim["ended_reason"]
    assert len(central.requests) == 3 and lab.calls_to("set_rf_enabled") == []
    await manager.close()
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_a_grant_interrupted_by_a_restart_is_safed_and_the_outcome_copied_in(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=True)
    manager, grant_id = await _start_grant(tmp_path, lab)
    _freeze(manager)
    runs = FakeRuns()
    restarted = GrantManager(lab, AutomationStore(tmp_path / "automation"), runs, None)
    restarted.mark_interrupted()  # the Control Center came back as a new process
    assert _record(tmp_path, grant_id)["status"] == "interrupted"
    watchdog = _watchdog(tmp_path, WatchdogCentral(lab, refusals=1), health, clock, stale_by=0.0)
    await watchdog.scan_once()  # at once: the old process is gone for sure
    await watchdog.wait_idle()
    claim = _claim(tmp_path, grant_id)
    assert claim["status"] == "ended" and claim["ended_reason"] == REASON_RESTARTED
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}]
    await restarted._follow_handed()
    assert _record(tmp_path, grant_id)["status"] == "ended" and runs.grants[-1]["status"] == "ended"
    await watchdog.scan_once()
    assert len(lab.calls_to("set_rf_enabled")) == 1  # done once
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_nothing_is_done_for_ended_or_too_old_grants(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=False)
    manager, ended_id = await _start_grant(tmp_path, lab)
    await manager.close()  # ended normally, safe state applied by the Control Center
    lab.calls.clear()
    central = WatchdogCentral(lab, refusals=0)
    # Far later, the Control Center gone: an ended grant is never touched, nor one stale for hours.
    old = {**_record(tmp_path, ended_id), "id": "grant-00000000000000ff", "status": "active",
           "alive_at": time.time() - 3 * 3600}
    (tmp_path / GRANTS / "grant-00000000000000ff.json").write_text(json.dumps(old), "utf-8")
    watchdog = _watchdog(tmp_path, central, health, clock, stale_by=3600.0 * 2)
    for _ in range(3):
        await watchdog.scan_once()
        clock.now += 60
    await watchdog.wait_idle()
    assert central.requests == [] and lab.calls == []
    assert _claim(tmp_path, ended_id) is None and _claim(tmp_path, "grant-00000000000000ff") is None
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_a_tampered_protocol_is_never_applied(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=True)
    manager, grant_id = await _start_grant(tmp_path, lab)
    _freeze(manager)
    path = tmp_path / GRANTS / f"{grant_id}.json"
    record = json.loads(path.read_text("utf-8"))
    record["protocol"]["safe_state"][0]["args"]["enabled"] = True
    record["status"] = "interrupted"
    path.write_text(json.dumps(record), "utf-8")
    central = WatchdogCentral(lab, refusals=0)
    watchdog = _watchdog(tmp_path, central, health, clock, stale_by=0.0)
    await watchdog.scan_once()
    await watchdog.wait_idle()
    claim = _claim(tmp_path, grant_id)
    assert claim["status"] == "ended_unsafe" and "sha256" in claim["ended_reason"]
    assert central.requests == [] and lab.calls_to("set_rf_enabled") == []
    await watchdog.aclose()


@pytest.mark.asyncio
async def test_a_takeover_another_watchdog_left_safing_is_finished(tmp_path: Path) -> None:
    lab, clock, health = FakeLab(), Clock(), Health(up=True)
    manager, grant_id = await _start_grant(tmp_path, lab, heartbeat=0.01)  # alive, beating
    _write_claim(tmp_path, grant_id, "safing", project_id=PROJECT, run_id=RUN, reason=REASON_STOPPED)
    await _until(lambda: _record(tmp_path, grant_id)["status"] == "interrupted")  # handed over
    watchdog = _watchdog(tmp_path, WatchdogCentral(lab, refusals=0), health, clock, stale_by=0.0)
    await watchdog.scan_once()
    await watchdog.wait_idle()
    assert _claim(tmp_path, grant_id)["status"] == "ended"
    assert lab.calls_to("set_rf_enabled") == [{"enabled": False}]
    await _until(lambda: _record(tmp_path, grant_id)["status"] == "ended")
    await manager.close()
    await watchdog.aclose()


# ---- the process: settings and its health page ---------------------------------------------------


def test_settings_are_checked(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "automation").mkdir()
    env = tmp_path / "watchdog.env"
    env.write_text("LABNET_API_KEY=key\nLABNET_AUTOMATION_DIR=automation\n", "utf-8")
    for name in ("LABNET_API_KEY", "LABNET_AUTOMATION_DIR", "LABNET_WATCHDOG_STALE_SECONDS", "LABNET_WATCHDOG_HOST",
                 "LABNET_WATCHDOG_PORT", "LABNET_SERVER_URL", "LABNET_CA_FILE"):
        monkeypatch.delenv(name, raising=False)
    settings = WatchdogSettings.from_env(env)
    assert settings.automation_dir == (tmp_path / "automation").resolve()
    assert settings.health_url == "http://127.0.0.1:8080/health/live" and settings.port == 8081
    for name, value, message in (("LABNET_WATCHDOG_STALE_SECONDS", "5", "STALE_SECONDS"),
                                 ("LABNET_WATCHDOG_HOST", "0.0.0.0", "loopback"),
                                 ("LABNET_WATCHDOG_LEASE_WAIT_SECONDS", "30", "LEASE_WAIT")):
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError, match=message):
            WatchdogSettings.from_env(env)
        monkeypatch.delenv(name)


@pytest.mark.asyncio
async def test_the_health_page_answers_live_and_ready(tmp_path: Path) -> None:
    (tmp_path / "automation").mkdir()
    watchdog = _watchdog(tmp_path, WatchdogCentral(FakeLab()), Health(), Clock(), stale_by=0.0)
    server = start_health_server("127.0.0.1", 0, watchdog)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(base + path, timeout=5) as reply:
                return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    try:
        assert await asyncio.to_thread(get, "/health/live") == (
            200, {"status": "alive", "service": "labnet-automation-watchdog"})
        assert (await asyncio.to_thread(get, "/health/ready"))[0] == 503  # no scan yet
        await watchdog.scan_once()
        status, body = await asyncio.to_thread(get, "/health/ready")
        assert status == 200 and body["handling"] == []
        # A second watchdog can't take the same port.
        with pytest.raises(OSError):
            start_health_server("127.0.0.1", server.server_address[1], watchdog)
    finally:
        server.shutdown()
        server.server_close()
        await watchdog.aclose()

