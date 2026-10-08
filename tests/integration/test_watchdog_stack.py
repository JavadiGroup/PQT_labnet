"""The safe-state watchdog against a real Central and Lab PC: Central refuses its lease while the
dead Control Center's lease lives, grants it once that lapses, and the safe state reaches the device."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from labnet_control_center.watchdog import Watchdog, WatchdogSettings
from labnet.client import ResourceConflictError
from labnet_control_center.automation_grants import GrantManager, watchdog_claim_path
from labnet_control_center.automation_protocol import protocol_sha256
from labnet_control_center.automation_store import AutomationStore
from labnet_sim._generated.contracts import DUMMY_DEVICE_CONTRACT
from test_full_stack import DEVICE_ID, _renewal_stack


PROJECT = "dummy-study-abc234"
RUN = "run-20261008-120000-abcd"
PROTOCOL: dict[str, Any] = {
    "schema_version": 1,
    "task": "Tasks/Task1.md",
    "title": "Dummy value sweep",
    "duration_minutes": 10.0,
    "devices": [{
        "device_id": DEVICE_ID,
        "device_type": "dummy_device",
        "methods": {"write_value": {"args": {"value": {"min": 0.0, "max": 10.0}}, "max_calls": 10.0,
                                    "max_rate_hz": 5.0}},
    }],
    "safe_state": [{"device_id": DEVICE_ID, "method": "write_value", "args": {"value": 0.0}}],
}


class ShortLeases:
    """The Control Center's client, with leases that lapse two seconds after its loop stops."""

    def __init__(self, client) -> None:
        self.client = client

    async def get_device(self, device_id: str):
        return await self.client.get_device(device_id)

    async def acquire_device(self, contract, device_id: str):
        return await self.client.acquire_device(contract, device_id, duration_seconds=2, auto_renew=False)


class CountingCentral:
    def __init__(self, client) -> None:
        self.client = client
        self.refused = 0

    async def acquire_device(self, contract, device_id: str):
        try:
            return await self.client.acquire_device(contract, device_id, duration_seconds=10,
                                                    renewal_interval_seconds=2.0)
        except ResourceConflictError:
            self.refused += 1
            raise


class Runs:
    def set_grant(self, *_args) -> None:
        pass

    def set_phase(self, *_args) -> None:
        pass

    def get(self, *_args) -> dict:
        return {"status": "active"}


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_the_watchdog_applies_a_dead_control_centers_safe_state_through_central(tmp_path: Path) -> None:
    async with _renewal_stack(tmp_path) as stack:
        async with stack.client() as control_center, stack.client(stack.other_user_key) as watchdog_user:
            manager = GrantManager(ShortLeases(control_center), AutomationStore(tmp_path / "automation"), Runs(), None,
                                   heartbeat_seconds=3600)
            grant = await manager.create(project_id=PROJECT, run_id=RUN, protocol=PROTOCOL,
                                         sha256=protocol_sha256(PROTOCOL), duration_minutes=5, approved_by="alice")
            await manager._grants[grant["id"]].devices[DEVICE_ID].write_value(value=7.0)
            # The Control Center's loop stops: no heartbeat, no renewal, no safe state.
            manager._monitor_task.cancel()
            manager._grants[grant["id"]].timer.cancel()

            central = CountingCentral(watchdog_user)
            clock = Clock()

            async def down() -> bool:
                return False

            settings = WatchdogSettings(server_url="http://127.0.0.1", api_key="key",
                                        automation_dir=tmp_path / "automation", retry_seconds=0.2)
            watchdog = Watchdog(settings, central, probe=down, clock=clock, wall=lambda: time.time() + 30)
            await watchdog.scan_once()
            clock.now += 21
            await watchdog.scan_once()
            await asyncio.wait_for(watchdog.wait_idle(), 30)

            claim = json.loads(watchdog_claim_path(tmp_path / "automation" / "state" / PROJECT / "grants",
                                                   grant["id"]).read_text("utf-8"))
            assert claim["status"] == "ended", claim
            assert central.refused >= 1  # Central refused while the old lease lived
            assert stack.app.state.lease_service.list_active() == []  # the watchdog released its lease
            check = await watchdog_user.acquire_device(DUMMY_DEVICE_CONTRACT, DEVICE_ID)
            try:
                assert (await check.read_value()).value == 0.0  # the safe state reached the device
            finally:
                await check.aclose()
            await watchdog.aclose()
