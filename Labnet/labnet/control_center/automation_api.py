"""The Automation API: projects, their files, linked experiments, the lab library.

People write only to their own folders (Literature, Data/example_data, Tasks
and Experiment_layout/LAYOUT.md); the agent's folders and the experiment
mirrors are read-only here. Uploads are the raw request body of a PUT,
counted as they stream, so leaving out Content-Length can't get a larger file
through. Replacing a file needs the ETag it was opened with (If-Match), so two
people editing a task never silently overwrite each other.

Changing requests must come from the Control Center's own pages: JSON bodies
(a cross-site form can't send them) and, where the browser says, same-origin
(see ``request_checks.py``). With logins, the signed-in person is the author.

People queue a project's tasks as runs and can cancel them here; the runner
that works on them has its own API (``automation_runner_api.py``).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .automation_paths import ZONES
from .automation_runs import RunStore
from .automation_store import RUN_SETTINGS, AutomationError, AutomationStore, etag, parse_settings
from .experiment_schema import EXPERIMENT_ID
from .experiment_store import ExperimentError, ExperimentStore
from .request_checks import author as _author
from .request_checks import read_json as _json
from .request_checks import same_origin


NetworkSource = Callable[[], Awaitable[dict[str, Any]]]
_SETTINGS_FIELDS = ("name", "description", "library_folders", "library_collections", "allowed_devices", *RUN_SETTINGS)
_RUN_EVENTS = 500  # how many of a run's latest events its page shows

# How each kind of file is served. Anything not listed is a download.
_INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_TEXT_TYPES = frozenset({
    ".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".py", ".log", ".dat", ".bib", ".ris", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".tex", ".m", ".ipynb", ".proto", ".sh", ".ps1", ".c", ".h", ".cpp", ".js", ".ts",
})
# An SVG can carry script; sandboxed, it is only a picture.
_SVG_POLICY = "sandbox; default-src 'none'; style-src 'unsafe-inline'"


def open_temporary(automation: AutomationStore, project_id: str, folder: Path) -> tuple[BinaryIO, Path]:
    """A new temp file for an upload in ``folder``, checked to be inside the project."""

    handle, name = tempfile.mkstemp(dir=folder, prefix=".upload-", suffix=".tmp")
    temporary = Path(name)
    file = os.fdopen(handle, "wb")
    try:
        automation.check_temporary(project_id, file.fileno(), temporary)
    except BaseException:
        file.close()
        temporary.unlink(missing_ok=True)
        raise
    return file, temporary


def flush_to_disk(file: BinaryIO) -> None:
    """Flush and fsync (seconds for a large file on a slow disk: run it in a thread)."""

    file.flush()
    os.fsync(file.fileno())


def file_response(file: BinaryIO, info: os.stat_result, relative: PurePosixPath, *, download: bool) -> StreamingResponse:
    """Stream an already-opened (and checked) file with headers that keep it inert."""

    suffix = relative.suffix.lower()
    headers = {
        "Cache-Control": "no-cache",
        "ETag": etag(info),
        "Content-Length": str(info.st_size),
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    disposition = "inline"
    if download:
        media, disposition = "application/octet-stream", "attachment"
    elif suffix in _INLINE_TYPES:
        media = _INLINE_TYPES[suffix]
    elif suffix == ".svg":
        media = "image/svg+xml"
        headers["Content-Security-Policy"] = _SVG_POLICY
    elif suffix in _TEXT_TYPES:
        media = "text/plain; charset=utf-8"
    else:  # HTML, XML and anything unknown: never rendered here
        media, disposition = "application/octet-stream", "attachment"
    headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(relative.name)}"

    def chunks():
        remaining = info.st_size  # what the headers promised, even if the file grows
        with file:
            while remaining > 0:
                data = file.read(min(64 * 1024, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(chunks(), media_type=media, headers=headers)


def create_automation_router(network: NetworkSource, *, max_file_bytes: int) -> APIRouter:
    router = APIRouter(prefix="/api/automation")
    changes = [Depends(same_origin)]

    def store(request: Request) -> AutomationStore:
        return request.app.state.automation

    def experiments(request: Request) -> ExperimentStore:
        return request.app.state.experiments

    def runs(request: Request) -> RunStore:
        return request.app.state.runs

    async def live_devices() -> dict[str, dict[str, Any]] | None:
        try:
            snapshot = await network()
        except Exception:  # DEVICES.md just won't mention the network
            return None
        if not (snapshot.get("lab_pcs") or snapshot["central"]["ok"]):
            return None
        return {device["id"]: device for pc in snapshot["lab_pcs"] for device in pc["devices"]}

    async def project_reply(request: Request, project: dict[str, Any], *, mirror: bool = False) -> dict[str, Any]:
        automation = store(request)
        if mirror:
            await asyncio.to_thread(automation.mirror_layout, project["id"], await live_devices())
        devices = await asyncio.to_thread(automation.snapshot_devices, project["id"])
        return {"project": project, "devices": devices}

    # ---- projects ------------------------------------------------------------------

    @router.get("/meta")
    async def meta(request: Request) -> dict[str, Any]:
        library = store(request).library
        return {
            "zones": [zone.describe() for zone in ZONES],
            "max_file_bytes": max_file_bytes,
            "library": {"configured": library is not None and library.is_dir()},
        }

    @router.get("/projects")
    async def list_projects(request: Request) -> dict[str, Any]:
        projects, unreadable = await asyncio.to_thread(store(request).list)
        return {"projects": projects, "unreadable": unreadable}

    @router.post("/projects", status_code=201, dependencies=changes)
    async def create_project(request: Request) -> JSONResponse:
        data = await _json(request)
        settings = parse_settings({key: data[key] for key in _SETTINGS_FIELDS if key in data})
        runs(request).check_model(settings.run_model)  # one LABNET_AGENT_MODELS offers
        project = await asyncio.to_thread(store(request).create, settings, author=_author(request, data, "created_by"))
        return JSONResponse(
            await project_reply(request, project), status_code=201,
            headers={"Location": f"/automation/{project['id']}"},
        )

    @router.get("/projects/{project_id}")
    async def get_project(request: Request, project_id: str) -> dict[str, Any]:
        project = await asyncio.to_thread(store(request).get, project_id)
        return await project_reply(request, project)

    @router.put("/projects/{project_id}", dependencies=changes)
    async def save_project(request: Request, project_id: str) -> dict[str, Any]:
        data = await _json(request)
        version = data.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise AutomationError(422, "Send the version this edit is based on.")
        settings = parse_settings({key: data[key] for key in _SETTINGS_FIELDS if key in data})
        runs(request).check_model(settings.run_model)  # one LABNET_AGENT_MODELS offers
        project = await asyncio.to_thread(
            store(request).update, project_id, settings, base_version=version, author=_author(request, data, "updated_by")
        )
        return await project_reply(request, project, mirror=True)  # DEVICES.md says what is allowed

    @router.delete("/projects/{project_id}", status_code=204, dependencies=changes)
    async def delete_project(request: Request, project_id: str, version: int | None = None) -> Response:
        await asyncio.to_thread(runs(request).delete_project, project_id, version=version)  # 409 while a run is open
        return Response(status_code=204)

    # ---- files ---------------------------------------------------------------------

    @router.get("/projects/{project_id}/tree")
    async def tree(request: Request, project_id: str) -> JSONResponse:
        listing = await asyncio.to_thread(store(request).tree, project_id)
        return JSONResponse(listing, headers={"Cache-Control": "no-store"})

    @router.get("/projects/{project_id}/files/{path:path}")
    async def read_file(request: Request, project_id: str, path: str, download: bool = False) -> StreamingResponse:
        file, info, relative = await asyncio.to_thread(store(request).open_file, project_id, path)
        return file_response(file, info, relative, download=download)

    @router.put("/projects/{project_id}/files/{path:path}", dependencies=changes)
    async def write_file(request: Request, project_id: str, path: str) -> dict[str, Any]:
        """Create a file, or replace one with ``If-Match: <its ETag>``. The body is the file."""

        automation = store(request)
        relative, folder = await asyncio.to_thread(automation.upload_target, project_id, path)
        file, temporary = await asyncio.to_thread(open_temporary, automation, project_id, folder)
        try:
            with file:
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > max_file_bytes:
                        raise AutomationError(413, f"{relative.name} is larger than {max_file_bytes // 2**20} MB.")
                    file.write(chunk)
                await asyncio.to_thread(flush_to_disk, file)
            entry = await asyncio.to_thread(
                automation.commit, project_id, relative, temporary, if_match=request.headers.get("if-match")
            )
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return {"file": entry}

    @router.delete("/projects/{project_id}/files/{path:path}", status_code=204, dependencies=changes)
    async def delete_file(request: Request, project_id: str, path: str) -> Response:
        """Moves the file (or folder) to the project's trash, kept 30 days."""

        await asyncio.to_thread(
            store(request).trash_file, project_id, path, if_match=request.headers.get("if-match")
        )
        return Response(status_code=204)

    @router.post("/projects/{project_id}/folders", status_code=201, dependencies=changes)
    async def make_folder(request: Request, project_id: str) -> dict[str, Any]:
        data = await _json(request)
        if not isinstance(data.get("path"), str):
            raise AutomationError(422, "Send the new folder's path.")
        return {"folder": await asyncio.to_thread(store(request).make_folder, project_id, data["path"])}

    @router.post("/projects/{project_id}/move", dependencies=changes)
    async def move(request: Request, project_id: str) -> dict[str, Any]:
        data = await _json(request)
        if not isinstance(data.get("from"), str) or not isinstance(data.get("to"), str):
            raise AutomationError(422, "Send the path to move (from) and its new path (to).")
        return {"item": await asyncio.to_thread(store(request).move, project_id, data["from"], data["to"])}

    # ---- linked experiments ----------------------------------------------------------

    @router.post("/projects/{project_id}/experiments", dependencies=changes)
    async def link_experiment(request: Request, project_id: str) -> dict[str, Any]:
        data = await _json(request)
        experiment_id = data.get("experiment_id")
        if not isinstance(experiment_id, str) or EXPERIMENT_ID.fullmatch(experiment_id) is None:
            raise AutomationError(422, "Send the id of a saved experiment.")
        experiment = await asyncio.to_thread(experiments(request).get, experiment_id)  # 404 if gone
        project = await asyncio.to_thread(
            store(request).link_experiment, project_id, experiment, author=_author(request, data, "linked_by")
        )
        return await project_reply(request, project, mirror=True)

    @router.delete("/projects/{project_id}/experiments/{experiment_id}", dependencies=changes)
    async def unlink_experiment(request: Request, project_id: str, experiment_id: str) -> dict[str, Any]:
        project = await asyncio.to_thread(
            store(request).unlink_experiment, project_id, experiment_id, author=_author(request, {}, "unlinked_by")
        )
        return await project_reply(request, project, mirror=True)

    @router.post("/projects/{project_id}/experiments/refresh", dependencies=changes)
    async def refresh_experiments(request: Request, project_id: str) -> dict[str, Any]:
        """Take the current saved version of every linked experiment, and rewrite the mirror."""

        automation = store(request)
        project = await asyncio.to_thread(automation.get, project_id)
        missing = []
        for linked in project["linked_experiments"]:
            try:
                experiment = await asyncio.to_thread(experiments(request).get, linked["id"])
            except ExperimentError:  # deleted from the Experiments tab: keep the last snapshot
                missing.append(linked["id"])
                continue
            if experiment["version"] != linked["version"]:
                project = await asyncio.to_thread(
                    automation.link_experiment, project_id, experiment, author=linked.get("linked_by", "")
                )
        return {**await project_reply(request, project, mirror=True), "missing": missing}

    # ---- runs: tasks queued for an Automation runner (see automation_runs.py) -----------

    @router.get("/projects/{project_id}/runs")
    async def list_runs(request: Request, project_id: str) -> JSONResponse:
        found = await asyncio.to_thread(runs(request).list, project_id)
        return JSONResponse({"runs": found}, headers={"Cache-Control": "no-store"})

    @router.post("/projects/{project_id}/runs", status_code=201, dependencies=changes)
    async def queue_run(request: Request, project_id: str) -> JSONResponse:
        """Queue one task, e.g. {"task": "Tasks/Task1.md"}; the next free runner claims it."""

        data = await _json(request)
        run = await asyncio.to_thread(
            runs(request).queue, project_id, data.get("task"), author=_author(request, data, "queued_by")
        )
        return JSONResponse({"run": run}, status_code=201)

    @router.get("/projects/{project_id}/runs/{run_id}")
    async def get_run(request: Request, project_id: str, run_id: str) -> JSONResponse:
        """The run and its last 500 events. Events were written by the runner: show them as text."""

        run = await asyncio.to_thread(runs(request).get, project_id, run_id)
        events = await asyncio.to_thread(runs(request).events, project_id, run_id, last=_RUN_EVENTS)
        return JSONResponse({"run": run, "events": events}, headers={"Cache-Control": "no-store"})

    @router.post("/projects/{project_id}/runs/{run_id}/cancel", dependencies=changes)
    async def cancel_run(request: Request, project_id: str, run_id: str) -> dict[str, Any]:
        data = await _json(request)
        run = await asyncio.to_thread(
            runs(request).cancel, project_id, run_id, author=_author(request, data, "cancelled_by")
        )
        return {"run": run}

    # ---- the lab library ------------------------------------------------------------

    @router.get("/library")
    async def library(request: Request) -> JSONResponse:
        listing = await asyncio.to_thread(store(request).library_tree)
        return JSONResponse(listing, headers={"Cache-Control": "no-store"})

    @router.get("/library/files/{path:path}")
    async def library_file(request: Request, path: str, download: bool = False) -> StreamingResponse:
        file, info, relative = await asyncio.to_thread(store(request).open_library_file, path)
        return file_response(file, info, relative, download=download)

    # ---- literature search (see automation_literature.py) ----------------------------

    @router.get("/library/index")
    async def library_index(request: Request) -> JSONResponse:
        """How far the library's search index is: papers, ones without a text layer, errors, progress."""

        status = await asyncio.to_thread(request.app.state.literature.status)
        return JSONResponse({"index": status}, headers={"Cache-Control": "no-store"})

    @router.post("/library/index", status_code=202, dependencies=changes)
    async def index_library(request: Request) -> JSONResponse:
        """Bring the library's index up to date in the background (409 while it already runs)."""

        await _json(request)
        status = await asyncio.to_thread(request.app.state.literature.start_library)
        return JSONResponse({"index": status}, status_code=202)

    @router.get("/projects/{project_id}/literature/search")
    async def search_literature(
        request: Request, project_id: str, q: str = "", k: int = 10, paper: str | None = None
    ) -> JSONResponse:
        """Pages matching q in the project's part of the library and its own papers, best first."""

        project = await asyncio.to_thread(store(request).get, project_id)
        found = await asyncio.to_thread(request.app.state.literature.search, project, q, k=k, paper=paper)
        return JSONResponse(found, headers={"Cache-Control": "no-store"})

    @router.get("/projects/{project_id}/literature/papers")
    async def literature_papers(request: Request, project_id: str) -> JSONResponse:
        project = await asyncio.to_thread(store(request).get, project_id)
        listing = await asyncio.to_thread(request.app.state.literature.papers, project)
        return JSONResponse(listing, headers={"Cache-Control": "no-store"})

    return router


__all__ = ["create_automation_router", "file_response", "flush_to_disk", "open_temporary", "same_origin"]
