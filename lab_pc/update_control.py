from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from labnet.device_manager import DeviceManager
from labnet.grpc import LabnetGrpcServer


class UpdateRuntimeState(str, Enum):
    ACCEPTING = "accepting"
    DRAINING = "draining"
    QUIESCENT = "quiescent"
    STOPPING = "stopping"
    FAILED = "failed"


StopCallback = Callable[[], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class UpdateRuntimeSnapshot:
    state: UpdateRuntimeState
    active_rpc_count: int
    queued_operation_count: int
    running_operation_count: int
    last_error: str | None

    @property
    def quiescent(self) -> bool:
        return (
            self.state is UpdateRuntimeState.QUIESCENT
            and self.active_rpc_count == 0
            and self.queued_operation_count == 0
            and self.running_operation_count == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "quiescent": self.quiescent,
            "active_rpc_count": self.active_rpc_count,
            "queued_operation_count": self.queued_operation_count,
            "running_operation_count": self.running_operation_count,
            "last_error": self.last_error,
        }


class UpdateRuntimeCoordinator:
    """Local lifecycle boundary used by the external Labupdater process."""

    def __init__(
        self,
        manager: DeviceManager,
        grpc_server: LabnetGrpcServer,
        *,
        stop_callback: StopCallback | None = None,
    ) -> None:
        self._manager = manager
        self._grpc_server = grpc_server
        self._stop_callback = stop_callback
        self._state = UpdateRuntimeState.ACCEPTING
        self._last_error: str | None = None
        self._lock = asyncio.Lock()
        self._drain_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> UpdateRuntimeState:
        return self._state

    @property
    def accepting(self) -> bool:
        return self._state is UpdateRuntimeState.ACCEPTING

    def snapshot(self) -> UpdateRuntimeSnapshot:
        rpc = self._grpc_server.activity_snapshot()
        workers = self._manager.activity_snapshots().values()
        return UpdateRuntimeSnapshot(
            state=self._state,
            active_rpc_count=rpc.active_count,
            queued_operation_count=sum(item.queued_count for item in workers),
            running_operation_count=sum(item.running_count for item in workers),
            last_error=self._last_error,
        )

    async def request_drain(self) -> UpdateRuntimeSnapshot:
        async with self._lock:
            if self._state in {
                UpdateRuntimeState.DRAINING,
                UpdateRuntimeState.QUIESCENT,
            }:
                return self.snapshot()
            if self._state is UpdateRuntimeState.STOPPING:
                raise RuntimeError("The Lab-PC runtime is already stopping.")

            self._state = UpdateRuntimeState.DRAINING
            self._last_error = None
            await self._grpc_server.begin_drain()
            self._drain_task = asyncio.create_task(
                self._finish_drain(),
                name="labnet-update-drain",
            )
            return self.snapshot()

    async def wait_quiescent(
        self, *, timeout: float | None = None
    ) -> UpdateRuntimeSnapshot:
        task = self._drain_task
        if task is not None:
            if timeout is None:
                await asyncio.shield(task)
            else:
                async with asyncio.timeout(timeout):
                    await asyncio.shield(task)
        snapshot = self.snapshot()
        if snapshot.state is UpdateRuntimeState.FAILED:
            raise RuntimeError(snapshot.last_error or "The runtime drain failed.")
        return snapshot

    async def resume(self) -> UpdateRuntimeSnapshot:
        async with self._lock:
            if self._state is UpdateRuntimeState.STOPPING:
                raise RuntimeError("A stopping Lab-PC runtime cannot resume.")
            task = self._drain_task
            self._drain_task = None
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._manager.resume()
            await self._grpc_server.resume_admission()
            self._state = UpdateRuntimeState.ACCEPTING
            self._last_error = None
            return self.snapshot()

    async def request_stop(self) -> UpdateRuntimeSnapshot:
        async with self._lock:
            if self._state is not UpdateRuntimeState.QUIESCENT:
                raise RuntimeError(
                    "The Lab-PC runtime must be quiescent before it can stop."
                )
            if self._stop_callback is None:
                raise RuntimeError("Graceful updater-controlled stop is not configured.")
            try:
                # This is the safety acknowledgement consumed by Labupdater:
                # the listener and every physical driver must be closed before
                # the process is allowed to exit and the version pointer moves.
                await self._grpc_server.stop()
                await self._manager.shutdown()
                self._state = UpdateRuntimeState.STOPPING
                result = self._stop_callback()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                self._state = UpdateRuntimeState.QUIESCENT
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise
            return self.snapshot()

    async def close(self) -> None:
        task = self._drain_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _finish_drain(self) -> None:
        try:
            await self._grpc_server.wait_quiescent()
            self._manager.begin_quiesce()
            await self._manager.wait_quiescent()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._state = UpdateRuntimeState.FAILED
            return

        async with self._lock:
            self._state = UpdateRuntimeState.QUIESCENT


__all__ = [
    "StopCallback",
    "UpdateRuntimeCoordinator",
    "UpdateRuntimeSnapshot",
    "UpdateRuntimeState",
]
