from __future__ import annotations

import asyncio

import pytest

from labnet.device_manager import AdmissionGate, AdmissionState, DeviceUnavailableError


@pytest.mark.asyncio
async def test_drain_is_an_atomic_barrier_for_new_admissions() -> None:
    gate = AdmissionGate()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def admitted_work() -> None:
        async with gate.admit():
            entered.set()
            await release.wait()

    active = asyncio.create_task(admitted_work())
    await entered.wait()
    snapshot = await gate.begin_drain()
    assert snapshot.state is AdmissionState.DRAINING
    assert snapshot.active_count == 1

    with pytest.raises(DeviceUnavailableError):
        async with gate.admit():
            raise AssertionError("a draining gate admitted new work")

    waiting = asyncio.create_task(gate.wait_idle())
    await asyncio.sleep(0)
    assert waiting.done() is False
    release.set()
    await active
    assert (await waiting).idle is True

    await gate.resume()
    async with gate.admit():
        assert gate.active_count == 1
    assert gate.active_count == 0
