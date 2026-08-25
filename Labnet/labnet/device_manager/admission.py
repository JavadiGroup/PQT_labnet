from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator

from .errors import DeviceUnavailableError


class AdmissionState(str, Enum):
    """Whether new network operations may enter the Lab-PC runtime."""

    ACCEPTING = "accepting"
    DRAINING = "draining"


@dataclass(frozen=True, slots=True)
class AdmissionSnapshot:
    state: AdmissionState
    active_count: int

    @property
    def idle(self) -> bool:
        return self.active_count == 0


class AdmissionGate:
    """Race-free admission and activity barrier shared by every gRPC service.

    Closing the gate and observing an active count of zero happen under one
    condition lock. A request therefore either enters before draining and is
    counted until its entire handler finishes, or observes the closed gate and
    is rejected. There is no check-then-enter window during an update.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._state = AdmissionState.ACCEPTING
        self._active_count = 0

    @property
    def state(self) -> AdmissionState:
        return self._state

    @property
    def accepting(self) -> bool:
        return self._state is AdmissionState.ACCEPTING

    @property
    def active_count(self) -> int:
        return self._active_count

    def snapshot(self) -> AdmissionSnapshot:
        return AdmissionSnapshot(
            state=self._state,
            active_count=self._active_count,
        )

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._state is not AdmissionState.ACCEPTING:
                raise DeviceUnavailableError(
                    "The Lab PC is draining for a software update.",
                    details={"admission_state": self._state.value},
                )
            self._active_count += 1

        try:
            yield
        finally:
            async with self._condition:
                self._active_count -= 1
                if self._active_count == 0:
                    self._condition.notify_all()

    async def begin_drain(self) -> AdmissionSnapshot:
        async with self._condition:
            self._state = AdmissionState.DRAINING
            if self._active_count == 0:
                self._condition.notify_all()
            return self.snapshot()

    async def resume(self) -> AdmissionSnapshot:
        async with self._condition:
            self._state = AdmissionState.ACCEPTING
            self._condition.notify_all()
            return self.snapshot()

    async def wait_idle(self, *, timeout: float | None = None) -> AdmissionSnapshot:
        if timeout is not None and (
            not math.isfinite(timeout) or timeout <= 0
        ):
            raise ValueError("timeout must be finite and positive when supplied.")

        async def wait() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: self._active_count == 0)

        if timeout is None:
            await wait()
        else:
            async with asyncio.timeout(timeout):
                await wait()
        return self.snapshot()


__all__ = [
    "AdmissionGate",
    "AdmissionSnapshot",
    "AdmissionState",
]
