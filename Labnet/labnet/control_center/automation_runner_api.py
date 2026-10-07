"""The runner API: how an Automation runner works through a project's tasks.

    GET  /api/automation/runner/whoami
    POST /api/automation/runner/claim                     the oldest queued run (200), or 204
    GET  /api/automation/runner/runs/{id}                 its record (to notice a cancel)
    GET  /api/automation/runner/runs/{id}/inputs          the project's inputs as a zip, plus run.json
    GET  /api/automation/runner/runs/{id}/library/{path}  a paper within the project's library scope
    GET  /api/automation/runner/runs/{id}/literature/search?q=&k=&paper=   pages matching q, with citations
    GET  /api/automation/runner/runs/{id}/literature/papers                the papers it may search
    GET  /api/automation/runner/runs/{id}/literature/pages?paper=&first=&last=   up to 20 pages' text
    PUT  /api/automation/runner/runs/{id}/files/{path}    a result, into the agent's folders only
    POST /api/automation/runner/runs/{id}/events          progress and transcript lines
    POST /api/automation/runner/runs/{id}/finish          done, failed or stopped, with a summary
    POST /api/automation/runner/runs/{id}/heartbeat       still working on it (any request above counts too)

A runner is a program, not a person: it proves itself with ``Authorization:
Bearer <runner token>`` (see ``automation_runners.py``) instead of a Caddy
login, which is why ``ingress.py`` lets this prefix through without the proxy
secret. A request a browser made (it says so in ``Sec-Fetch-Site``) is refused,
so a web page can't use a token it somehow learned, nor a person's session.

Only the runner that claimed a run may touch it, and only while it is active;
anything else is answered 409 with the run's status, which is how a runner
learns that a person cancelled. What a runner uploads passes the same path
checks as a person's upload, and ``check_agent_write`` instead of
``check_user_write``: the Control Center, not the runner's machine, decides
what the agent may change.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .automation_api import file_response, flush_to_disk, open_temporary
from .automation_runners import RunnerRegistry
from .automation_runs import RunStore
from .automation_store import AutomationError, AutomationStore
from .ingress import RUNNER_PREFIX
from .request_checks import read_json


MAX_EVENTS_REQUEST_BYTES = 256 * 1024


async def runner_name(request: Request) -> str:
    """The runner the bearer token belongs to; 401 without a valid one, 403 from a browser."""

    if "sec-fetch-site" in request.headers:
        raise AutomationError(403, "The runner API is for Automation runners, not browsers.")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    name = None
    if scheme.lower() == "bearer" and token.strip():
        registry: RunnerRegistry = request.app.state.runners
        name = await asyncio.to_thread(registry.authenticate, token.strip())
    if name is None:
        raise HTTPException(
            401, "Send a valid runner token: Authorization: Bearer lnr_…", headers={"WWW-Authenticate": "Bearer"}
        )
    return name


def create_runner_router(*, max_file_bytes: int) -> APIRouter:
    router = APIRouter(prefix=RUNNER_PREFIX.rstrip("/"), dependencies=[Depends(runner_name)])

    def runs(request: Request) -> RunStore:
        return request.app.state.runs

    def automation(request: Request) -> AutomationStore:
        return request.app.state.automation

    @router.get("/whoami")
    async def whoami(runner: str = Depends(runner_name)) -> dict[str, Any]:
        return {"runner": runner}

    @router.post("/claim", response_model=None)
    async def claim(request: Request, runner: str = Depends(runner_name)) -> Response | dict[str, Any]:
        run = await asyncio.to_thread(runs(request).claim, runner)
        if run is None:
            return Response(status_code=204)
        return {"run": run}

    @router.get("/runs/{run_id}")
    async def get_run(request: Request, run_id: str, runner: str = Depends(runner_name)) -> dict[str, Any]:
        return {"run": await asyncio.to_thread(runs(request).runner_run, run_id, runner, active=False)}

    @router.get("/runs/{run_id}/inputs")
    async def inputs(request: Request, run_id: str, runner: str = Depends(runner_name)) -> StreamingResponse:
        """A zip of what people gave the project (not the agent's folders), with run.json at its root."""

        file, size = await asyncio.to_thread(runs(request).build_inputs, run_id, runner)

        def chunks():
            with file:  # a temp file: gone once closed
                while data := file.read(256 * 1024):
                    yield data

        return StreamingResponse(chunks(), media_type="application/zip", headers={
            "Content-Length": str(size),
            "Content-Disposition": f'attachment; filename="{run_id}-inputs.zip"',
            "Cache-Control": "no-store",
        })

    @router.get("/runs/{run_id}/library/{path:path}")
    async def library_file(request: Request, run_id: str, path: str, runner: str = Depends(runner_name)) -> StreamingResponse:
        file, info, relative = await asyncio.to_thread(runs(request).open_library_file, run_id, runner, path)
        return file_response(file, info, relative, download=True)

    # The agent's literature tools (search_literature, list_papers, read_paper_pages) call these
    # through the runner: the same scope as the library endpoint, plus the project's own papers.

    def run_project(request: Request, run_id: str, runner: str) -> dict[str, Any]:
        run = runs(request).runner_run(run_id, runner)  # 404 for another runner's run, 409 once it ended
        return automation(request).get(run["project_id"])

    @router.get("/runs/{run_id}/literature/search")
    async def search_literature(
        request: Request, run_id: str, q: str = "", k: int = 10, paper: str | None = None,
        runner: str = Depends(runner_name),
    ) -> dict[str, Any]:
        project = await asyncio.to_thread(run_project, request, run_id, runner)
        return await asyncio.to_thread(request.app.state.literature.search, project, q, k=k, paper=paper)

    @router.get("/runs/{run_id}/literature/papers")
    async def literature_papers(request: Request, run_id: str, runner: str = Depends(runner_name)) -> dict[str, Any]:
        project = await asyncio.to_thread(run_project, request, run_id, runner)
        return await asyncio.to_thread(request.app.state.literature.papers, project)

    @router.get("/runs/{run_id}/literature/pages")
    async def literature_pages(
        request: Request, run_id: str, paper: str = "", first: int = 1, last: int | None = None,
        runner: str = Depends(runner_name),
    ) -> dict[str, Any]:
        """The text of up to 20 pages of one paper in the run's scope, each with its citation."""

        project = await asyncio.to_thread(run_project, request, run_id, runner)
        return await asyncio.to_thread(request.app.state.literature.pages, project, paper, first, last)

    @router.put("/runs/{run_id}/files/{path:path}")
    async def upload(request: Request, run_id: str, path: str, runner: str = Depends(runner_name)) -> dict[str, Any]:
        """Create or replace a file in the agent's folders. The body is the file."""

        store = runs(request)
        length = request.headers.get("content-length", "")
        expected = int(length) if length.isdigit() else None
        if expected is not None and expected > max_file_bytes:
            raise AutomationError(413, f"Files can be at most {max_file_bytes / 2**20:g} MB.")
        run = await asyncio.to_thread(store.begin_upload, run_id, runner, expected=expected)
        project_id = run["project_id"]
        relative, folder = await asyncio.to_thread(automation(request).agent_upload_target, project_id, path)
        size = 0
        temporary: Path | None = None
        try:
            file, temporary = await asyncio.to_thread(open_temporary, automation(request), project_id, folder)
            with file:
                async for chunk in request.stream():
                    if size + len(chunk) > max_file_bytes:
                        raise AutomationError(413, f"{relative.name} is larger than {max_file_bytes / 2**20:g} MB.")
                    store.reserve(run_id, len(chunk))
                    size += len(chunk)
                    file.write(chunk)
                await asyncio.to_thread(flush_to_disk, file)
            entry, run = await asyncio.to_thread(store.commit_upload, run_id, runner, relative, temporary, size)
        except BaseException:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            store.release(run_id, size)
            raise
        return {"file": entry, "bytes_uploaded": run["bytes_uploaded"], "quota_bytes": run["quota_bytes"]}

    @router.post("/runs/{run_id}/events")
    async def events(request: Request, run_id: str, runner: str = Depends(runner_name)) -> dict[str, Any]:
        data = await read_json(request, limit=MAX_EVENTS_REQUEST_BYTES)
        status = await asyncio.to_thread(runs(request).append_events, run_id, runner, data.get("events"))
        return {"status": status}

    @router.post("/runs/{run_id}/finish")
    async def finish(request: Request, run_id: str, runner: str = Depends(runner_name)) -> dict[str, Any]:
        data = await read_json(request)
        run = await asyncio.to_thread(runs(request).finish, run_id, runner, data.get("status"), data.get("summary"))
        return {"run": run}

    @router.post("/runs/{run_id}/heartbeat")
    async def heartbeat(request: Request, run_id: str, runner: str = Depends(runner_name)) -> dict[str, Any]:
        """Still working on it. A run not heard from for LABNET_AUTOMATION_RUN_STALE_MINUTES is failed."""

        return {"status": await asyncio.to_thread(runs(request).heartbeat, run_id, runner)}

    return router


__all__ = ["create_runner_router", "runner_name"]
