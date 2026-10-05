"""PQT Lab Control Center: a web front end that is itself a LabNet client.

    labnet-control-center --env-file control-center.env

The browser only talks to this process. This process holds the Central API key
and asks Central for the network picture, so no key ever reaches a browser.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from labnet.client import AsyncLabClient, LabPCInfo
from labnet.client.core.models import DeviceInfo

from .config import ControlCenterSettings
from .contracts import describe_device_type, type_label


LOGGER = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
AGENT_NAME = "Photo-Agent"
ClientFactory = Callable[[ControlCenterSettings], AsyncLabClient]


def _default_client(settings: ControlCenterSettings) -> AsyncLabClient:
    return AsyncLabClient(
        settings.server_url,
        settings.api_key,
        ca_file=settings.ca_file,
        authenticate_on_start=False,
    )


class NetworkStatus:
    """Cached network picture, refreshed from Central at most every N seconds.

    However many browsers poll, Central sees one request per interval. If
    Central stops answering, the last good picture is kept and marked stale.
    """

    def __init__(self, client: AsyncLabClient, settings: ControlCenterSettings) -> None:
        self._client = client
        self._settings = settings
        self._lock = asyncio.Lock()
        self._snapshot: dict[str, Any] | None = None
        self._fetched_at = 0.0

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            if (
                self._snapshot is not None
                and time.monotonic() - self._fetched_at < self._settings.refresh_seconds
            ):
                return self._snapshot
            try:
                lab_pcs = await self._client.list_lab_pcs()
            except Exception as exc:  # show any failure on the page instead of a 500
                LOGGER.warning("Could not read the network from Central: %s", exc)
                previous = self._snapshot or _snapshot([], central_url=self._settings.server_url)
                self._snapshot = {
                    **previous,
                    "central": {
                        "url": self._settings.server_url,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                }
            else:
                self._snapshot = _snapshot(lab_pcs, central_url=self._settings.server_url)
            self._fetched_at = time.monotonic()
            return self._snapshot

    @property
    def central_ok(self) -> bool:
        return bool(self._snapshot and self._snapshot["central"]["ok"])


def _snapshot(lab_pcs: Sequence[LabPCInfo], *, central_url: str) -> dict[str, Any]:
    pcs = [_lab_pc(pc) for pc in lab_pcs]
    devices = [device for pc in pcs for device in pc["devices"]]
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "central": {"url": central_url, "ok": True, "error": None},
        "summary": {
            "lab_pcs": len(pcs),
            "online": sum(pc["online"] for pc in pcs),
            "devices": len(devices),
            "available": sum(device["state"] == "available" for device in devices),
            "leased": sum(device["state"] == "leased" for device in devices),
        },
        "lab_pcs": pcs,
    }


def _lab_pc(pc: LabPCInfo) -> dict[str, Any]:
    target = pc.endpoint.target
    address, _, port = target.rpartition(":")
    return {
        "id": pc.manager_id,
        "host": pc.host,
        "address": address.strip("[]") or target,
        "port": port,
        "secure": pc.endpoint.secure,
        "online": pc.online,
        "last_seen": pc.last_seen.isoformat() if pc.last_seen else None,
        "devices": [_device(device, lab_pc_online=pc.online) for device in pc.devices],
    }


def _device(device: DeviceInfo, *, lab_pc_online: bool) -> dict[str, Any]:
    if device.effective_available:
        state, detail = "available", "Ready to lease"
    elif device.leased:
        state, detail = "leased", "In use by another client"
    elif not lab_pc_online:
        state, detail = "unavailable", "Lab PC is offline"
    else:
        state = "unavailable"
        detail = device.status_text or f"{device.runtime_state}, {device.availability}"
    methods = describe_device_type(device.device_type)
    return {
        "id": device.device_id,
        "type": device.device_type,
        "type_label": type_label(device.device_type),
        "state": state,
        "detail": detail,
        "runtime_state": device.runtime_state,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
        "control_url": f"/devices/{device.device_id}",
        "known_contract": methods is not None,
        "methods": methods or [],
    }


class AgentMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def create_app(
    settings: ControlCenterSettings,
    *,
    client_factory: ClientFactory = _default_client,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = client_factory(settings)
        app.state.status = NetworkStatus(client, settings)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="PQT Lab Control Center",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def page(name: str) -> FileResponse:
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/status")

    @app.get("/status", include_in_schema=False)
    async def status_page() -> FileResponse:
        return page("status.html")

    @app.get("/devices", include_in_schema=False)
    @app.get("/devices/{device_id}", include_in_schema=False)
    async def devices_page(device_id: str | None = None) -> FileResponse:
        return page("devices.html")

    @app.get("/api/status")
    async def network_status() -> JSONResponse:
        snapshot = await app.state.status.snapshot()
        return JSONResponse(
            {**snapshot, "refresh_seconds": settings.refresh_seconds},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/agent/messages")
    async def agent_message(body: AgentMessage) -> dict[str, Any]:
        # The chat box is wired end to end; the agent itself comes later.
        if not body.message.strip():
            raise HTTPException(status_code=422, detail="Message is empty.")
        return {
            "agent": AGENT_NAME,
            "connected": False,
            "reply": (
                f"{AGENT_NAME} isn't connected yet, so I can't answer. Your message "
                "reached the Control Center; experiment planning arrives with the agent."
            ),
        }

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive", "service": "labnet-control-center"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        await app.state.status.snapshot()
        ok = app.state.status.central_ok
        return JSONResponse(
            {"status": "ok" if ok else "central_unreachable"},
            status_code=200 if ok else 503,
        )

    return app


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="labnet-control-center",
        description="Serve the PQT Lab Control Center web interface.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Settings file such as control-center.env (process environment wins).",
    )
    arguments = parser.parse_args(argv)
    try:
        settings = ControlCenterSettings.from_env(arguments.env_file)
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"labnet-control-center: error: {exc}\n")
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.port)


__all__ = ["NetworkStatus", "create_app", "main"]
