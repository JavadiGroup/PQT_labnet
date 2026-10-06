"""PQT Lab Control Center: a web front end that is itself a LabNet client.

    labnet-control-center --env-file control-center.env

The browser only talks to this process. This process holds the Central API key
and asks Central for the network picture, so no key ever reaches a browser.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import time
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from labnet.client import AsyncLabClient, LabPCInfo
from labnet.client.core.models import DeviceInfo
from labnet.identifiers import LABNET_IDENTIFIER_REGEX

from .agent import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, AgentRequestError, PhotoAgent
from .config import ControlCenterSettings
from .contracts import describe_device_type, type_label
from .scripting import MAX_SCRIPT_CHARACTERS
from .sessions import SessionError, SessionManager


LOGGER = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
AGENT_NAME = "Photo-Agent"
# First path segments the app itself uses; a device with one of these ids
# could not have a control page at /<device-id>.
RESERVED_PATHS = frozenset({"api", "devices", "favicon.ico", "health", "static", "status"})
ClientFactory = Callable[[ControlCenterSettings], AsyncLabClient]
AgentFactory = Callable[..., PhotoAgent]
UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


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

    def invalidate(self) -> None:
        """Ask Central again on the next request (a lease was just taken or freed)."""

        self._fetched_at = 0.0

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
        # Which control panel the device page loads (static/js/panels/<panel>.js).
        "panel": _panel_for(device.device_type),
        "id": device.device_id,
        "type": device.device_type,
        "type_label": type_label(device.device_type),
        "state": state,
        "detail": detail,
        "runtime_state": device.runtime_state,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
        "control_url": f"/{device.device_id}",
        "known_contract": methods is not None,
        "methods": methods or [],
    }


def _with_activity(snapshot: dict[str, Any], held: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Mark the devices this Control Center holds, so any viewer can pause them.

    ``control_center`` is null for every other device; a leased device without
    it is held by some other LabNet client.
    """

    def device(item: dict[str, Any]) -> dict[str, Any]:
        activity = held.get(item["id"])
        if activity is None:
            return {**item, "control_center": None}
        return {
            **item,
            "state": "leased",  # the cached picture may predate the lease
            "detail": "Held by the Control Center",
            "control_center": {**activity, "page": f"/{activity['session']}"},
        }

    pcs = [{**pc, "devices": [device(item) for item in pc["devices"]]} for pc in snapshot["lab_pcs"]]
    devices = [item for pc in pcs for item in pc["devices"]]
    summary = {
        **snapshot["summary"],
        "available": sum(item["state"] == "available" for item in devices),
        "leased": sum(item["state"] == "leased" for item in devices),
    }
    return {**snapshot, "summary": summary, "lab_pcs": pcs}


def _panel_for(device_type: str) -> str:
    return device_type if (STATIC / "js" / "panels" / f"{device_type}.js").is_file() else "generic"


# Base64 makes a file a third larger.
_MAX_ATTACHMENT_BASE64 = (MAX_ATTACHMENT_BYTES + 2) // 3 * 4


class AgentAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data: str = Field(max_length=_MAX_ATTACHMENT_BASE64)  # the file, base64-encoded


class AgentMessage(BaseModel):
    message: str = Field(default="", max_length=8000)
    # From an earlier answer's events: continue that conversation.
    conversation_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    model: str | None = Field(default=None, max_length=100)
    effort: str | None = Field(default=None, max_length=20)
    attachments: list[AgentAttachment] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


class _BodyLimit:
    """Refuse an oversized request before it is read (pure ASGI, so streamed
    responses and disconnects pass through untouched)."""

    def __init__(self, app: Any, *, path: str, limit: int) -> None:
        self.app = app
        self.path = path
        self.limit = limit

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == self.path:
            length = dict(scope["headers"]).get(b"content-length", b"0")
            if length.isdigit() and int(length) > self.limit:
                response = JSONResponse({"detail": "The attached files are too large."}, status_code=413)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SettingValue(BaseModel):
    method: str = Field(max_length=128)
    value: float


class StreamOptions(BaseModel):
    method: str = Field(max_length=128)
    sample_rate_hz: float
    channel: str | None = Field(default=None, max_length=128)


class PlayRequest(BaseModel):
    """Settings are applied in order before the stream starts."""

    settings: list[SettingValue] = Field(default_factory=list, max_length=32)
    stream: StreamOptions


class ScriptRequest(BaseModel):
    code: str = Field(min_length=1, max_length=MAX_SCRIPT_CHARACTERS)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def _coalesce(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge neighbouring sample batches so a burst becomes one message."""

    merged: list[dict[str, Any]] = []
    for event in events:
        if event["type"] == "samples" and merged and merged[-1]["type"] == "samples":
            merged[-1] = {"type": "samples", "points": merged[-1]["points"] + event["points"]}
        else:
            merged.append(event)
    return merged


def create_app(
    settings: ControlCenterSettings,
    *,
    client_factory: ClientFactory = _default_client,
    agent_factory: AgentFactory = PhotoAgent,
) -> FastAPI:
    async def network_with_activity() -> dict[str, Any]:
        return _with_activity(await app.state.status.snapshot(), app.state.sessions.activity())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = client_factory(settings)
        app.state.status = NetworkStatus(client, settings)
        app.state.sessions = SessionManager(client, settings)
        app.state.agent = agent_factory(settings, network_with_activity)
        try:
            yield
        finally:
            app.state.agent.close()
            await app.state.sessions.close()  # releases every lease
            await client.aclose()

    app = FastAPI(
        title="PQT Lab Control Center",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_middleware(
        _BodyLimit, path="/api/agent/messages", limit=MAX_ATTACHMENTS * _MAX_ATTACHMENT_BASE64 + 64 * 1024
    )

    @app.exception_handler(SessionError)
    async def session_error(_request: Request, exc: SessionError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    def session_for(device_id: str):
        if LABNET_IDENTIFIER_REGEX.fullmatch(device_id) is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return app.state.sessions.get(device_id)

    def page(name: str) -> FileResponse:
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/status")

    @app.get("/status", include_in_schema=False)
    async def status_page() -> FileResponse:
        return page("status.html")

    @app.get("/devices", include_in_schema=False)
    async def devices_page() -> FileResponse:
        return page("devices.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        # Answer here so browsers' automatic request never reaches /{device_id}.
        return Response(status_code=204)

    @app.get("/api/status")
    async def network_status() -> JSONResponse:
        snapshot = await network_with_activity()
        return JSONResponse(
            {**snapshot, "refresh_seconds": settings.refresh_seconds},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/agent")
    async def agent_info() -> dict[str, Any]:
        return {"agent": AGENT_NAME, **app.state.agent.info()}

    @app.post("/api/agent/messages", response_model=None)
    async def agent_message(body: AgentMessage) -> StreamingResponse | dict[str, Any]:
        """Server-sent events: session, delta (answer text), activity, status, done.

        A request the agent refuses (unknown model, unreadable file, ...) is
        answered with an error status before anything streams.
        """

        agent = app.state.agent
        if not agent.enabled:
            if not body.message.strip():
                raise HTTPException(status_code=422, detail="Message is empty.")
            return {
                "agent": AGENT_NAME,
                "connected": False,
                "reply": f"{AGENT_NAME} isn't connected: {agent.info()['reason']}",
            }
        try:
            files = [(item.name, base64.b64decode(item.data, validate=True)) for item in body.attachments]
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail="An attachment is not valid base64.") from None
        try:
            request = agent.prepare(
                body.message,
                body.conversation_id,
                model=body.model,
                effort=body.effort,
                attachments=files,
            )
        except AgentRequestError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

        async def stream():
            # Closing this generator (the browser left or pressed Stop) ends
            # ask(), which kills the claude process.
            async for event in agent.ask(request):
                yield _sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ---- device control (one session per device) --------------------------------

    @app.get("/api/devices/{device_id}/events")
    async def device_events(device_id: str) -> StreamingResponse:
        """Server-sent events: a snapshot, then state, samples and script output."""

        session = session_for(device_id)
        subscriber = session.subscribe()

        async def stream():
            try:
                yield "retry: 2000\n\n" + _sse(session.snapshot_event())
                while not subscriber.dropped:
                    try:
                        first = await asyncio.wait_for(subscriber.queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    events = [first]
                    while not subscriber.queue.empty() and len(events) < 1000:
                        events.append(subscriber.queue.get_nowait())
                    yield "".join(_sse(event) for event in _coalesce(events))
            finally:
                session.unsubscribe(subscriber)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/devices/{device_id}/play")
    async def device_play(device_id: str, body: PlayRequest) -> dict[str, Any]:
        try:
            return await session_for(device_id).play(body.model_dump())
        finally:
            app.state.status.invalidate()

    @app.post("/api/devices/{device_id}/pause")
    async def device_pause(device_id: str) -> dict[str, Any]:
        state = await session_for(device_id).pause()
        app.state.status.invalidate()
        return state

    @app.post("/api/devices/{device_id}/release")
    async def device_release(device_id: str) -> dict[str, Any]:
        """Pause from the Network or Devices page: stops the stream and any script.

        Open to every viewer on purpose, so a page left running on some other
        computer can be stopped by whoever notices.
        """

        state = await session_for(device_id).release("Paused from the device list.")
        app.state.status.invalidate()
        return state

    @app.post("/api/devices/{device_id}/script")
    async def device_script(device_id: str, body: ScriptRequest) -> dict[str, Any]:
        return await session_for(device_id).run_script(body.code)

    @app.post("/api/devices/{device_id}/script/stop")
    async def device_script_stop(device_id: str) -> dict[str, Any]:
        session = session_for(device_id)
        session.stop_script()
        return session.state_event()

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

    # Registered last: every fixed path above wins over a device id.
    @app.get("/{device_id}", include_in_schema=False)
    async def device_page(device_id: str) -> FileResponse:
        """The device control page, e.g. /dev02-wavemeter."""

        if device_id in RESERVED_PATHS or LABNET_IDENTIFIER_REGEX.fullmatch(device_id) is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return page("device.html")

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
