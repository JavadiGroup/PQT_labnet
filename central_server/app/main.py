from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.devices import router as devices_router
from app.api.leases import router as leases_router
from app.services.DeviceRegistry import DeviceRegistry
from app.services.LeaseManager import LeaseManager
from app.services.LogService import LogService
from app.services.UserManager import UserManager
from app.storage.json_storage import JsonStorage
from app.tasks.lease_cleanup import lease_cleanup_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = JsonStorage(data_dir="data")

    app.state.storage = storage
    app.state.user_manager = UserManager(storage)
    app.state.device_registry = DeviceRegistry(storage)
    app.state.lease_manager = LeaseManager(storage)
    app.state.log_service = LogService(storage)

    cleanup_task = asyncio.create_task(
        lease_cleanup_loop(
            lease_manager=app.state.lease_manager,
            interval_seconds=5,
        )
    )

    try:
        yield
    finally:
        cleanup_task.cancel()

        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Lab Network Central Server",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(devices_router)
app.include_router(leases_router)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "central-server",
    }