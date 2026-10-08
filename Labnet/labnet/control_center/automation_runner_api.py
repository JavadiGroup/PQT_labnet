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
    POST /api/automation/runner/runs/{id}/protocol        check Protocols/<task>.protocol.json and send it for approval
    POST /api/automation/runner/runs/{id}/grant/token     the approved grant's token, once
    POST /api/automation/runner/runs/{id}/grant/end       end the grant now (safe state, leases released)
    POST /api/automation/runner/gateway/calls             one device call: grant token, not a runner token

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
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .automation_api import file_response, flush_to_disk, open_temporary
from .automation_protocol import (
    MAX_PROSE_BYTES,
    MAX_PROTOCOL_BYTES,
    NetworkSource,
    network_devices,
    parse_protocol,
    protocol_files,
    validate_protocol,
)
from .automation_grants import GrantError
from .automation_runners import RunnerRegistry
from .automation_runs import RunStore
from .automation_store import AutomationError, AutomationStore
from .ingress import RUNNER_PREFIX
from .redaction import redact
from .request_checks import read_json


MAX_EVENTS_REQUEST_BYTES = 256 * 1024
MAX_GATEWAY_REQUEST_BYTES = 16 * 1024  # one device call
MAX_GRANT_REQUEST_BYTES = 4 * 1024
MAX_GRANT_REASON_CHARACTERS = 500


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


def _read_project_file(automation: AutomationStore, project_id: str, path: str, limit: int) -> bytes | None:
    """Up to ``limit + 1`` bytes of a project file (checked like any read), or None if it isn't there."""

    try:
        file, _info, _relative = automation.open_file(project_id, path)
    except AutomationError as exc:
        if exc.status_code == 404:
            return None
        raise
    with file:
        return file.read(limit + 1)


def create_runner_router(*, max_file_bytes: int, network: NetworkSource | None = None) -> APIRouter:
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

    @router.post("/runs/{run_id}/protocol", response_model=None)
    async def submit_protocol(request: Request, run_id: str, runner: str = Depends(runner_name)) -> Any:
        """Check the protocol the runner uploaded (Protocols/<task stem>.protocol.json) against
        the project and the live network; if it passes, a person is asked to approve it.

        200 ``{"phase": "awaiting_approval", "sha256", "submission"}``; 422 ``{"detail",
        "problems": [{"path", "message"}]}`` (nothing changes, and it doesn't count as one of
        the run's submissions); 409 in the wrong phase or after MAX_SUBMISSIONS; 503 while
        Central can't be reached (try again).
        """

        data = await read_json(request)
        if data:
            raise AutomationError(422, "Send an empty JSON object: the protocol is read from the project.")
        store = runs(request)
        run = await asyncio.to_thread(store.protocol_target, run_id, runner)
        project_id = run["project_id"]
        json_path, prose_path = protocol_files(run["task"])
        raw = await asyncio.to_thread(_read_project_file, automation(request), project_id, json_path.as_posix(),
                                      MAX_PROTOCOL_BYTES)
        prose_bytes = await asyncio.to_thread(_read_project_file, automation(request), project_id,
                                              prose_path.as_posix(), MAX_PROSE_BYTES)
        problems: list[dict[str, str]] = []
        normalized = None
        if raw is None:
            problems = [{"path": "", "message": f"There is no {json_path}. Write it, upload it, then submit again."}]
        else:
            parsed, problems = parse_protocol(raw)
            if prose_bytes is not None and len(prose_bytes) > MAX_PROSE_BYTES:
                problems.append({"path": "", "message": f"{prose_path} is larger than {MAX_PROSE_BYTES // 1024} KiB; "
                                                        "shorten it."})
            if not problems:
                live = await network_devices(network) if network is not None else None
                if live is None:
                    raise AutomationError(503, "Central can't be reached now, so the devices can't be checked. "
                                               "Submit again in a minute.")
                project = await asyncio.to_thread(automation(request).get, project_id)
                canvas = await asyncio.to_thread(automation(request).snapshot_devices, project_id)
                normalized, problems = validate_protocol(
                    parsed, project=project, live_devices=live, canvas_devices=canvas,
                    max_minutes=store.grant_max_minutes, task=run["task"],
                )
        if problems or normalized is None:
            await asyncio.to_thread(store.protocol_refused, run_id, runner, problems)
            count = len(problems)
            return JSONResponse({
                "detail": f"{json_path} has {count} problem{'' if count == 1 else 's'}. Fix "
                          f"{'it' if count == 1 else 'them'}, upload the file again, and submit again.",
                "problems": problems,
            }, status_code=422)
        prose = None
        if prose_bytes is not None:
            prose = "\n".join(prose_bytes.decode("utf-8", "replace").splitlines())
        run = await asyncio.to_thread(store.accept_protocol, run_id, runner, normalized, prose)
        return {"phase": run["phase"], "sha256": run["protocol"]["sha256"],
                "submission": run["protocol"]["submissions"]}

    # ---- device grants (see automation_grants.py) ------------------------------------------

    @router.post("/runs/{run_id}/grant/token")
    async def grant_token(request: Request, run_id: str, runner: str = Depends(runner_name)) -> JSONResponse:
        """The active grant's token, once: {"token": "lng_…", "grant", "protocol"}. 409 once taken.

        ``protocol`` is the normalized protocol the person approved (what the gateway enforces),
        so the runner tells the agent those bounds, not its own file's."""

        await read_json(request, limit=MAX_GRANT_REQUEST_BYTES)
        await asyncio.to_thread(runs(request).runner_run, run_id, runner)  # this runner's, and active
        grants = request.app.state.grants
        token, grant = grants.take_token(run_id)
        return JSONResponse({"token": token, "grant": grant, "protocol": grants.protocol_of(grant["id"])},
                            headers={"Cache-Control": "no-store"})

    @router.post("/runs/{run_id}/grant/end")
    async def grant_end(request: Request, run_id: str, runner: str = Depends(runner_name)) -> dict[str, Any]:
        """End the run's grant now (safe state, then the leases are released): {"grant"}."""

        data = await read_json(request, limit=MAX_GRANT_REQUEST_BYTES)
        reason = data.get("reason", "")
        if not isinstance(reason, str) or len(reason) > MAX_GRANT_REASON_CHARACTERS:
            raise AutomationError(422, f"The reason must be text of at most {MAX_GRANT_REASON_CHARACTERS} characters.")
        await asyncio.to_thread(runs(request).runner_run, run_id, runner)
        grants = request.app.state.grants
        reason = " ".join(reason.split())
        grant = await grants.end_for_run(run_id, reason=f"ended by the runner: {reason}" if reason else "ended by the runner")
        if grant is None:
            grant = grants.grant_for_run(run_id)
        if grant is None:
            raise AutomationError(409, "This run has no grant.", code="ended")
        return {"grant": grant}

    return router


async def _gateway_body(request: Request) -> Any:
    """The call's JSON, at most MAX_GATEWAY_REQUEST_BYTES; NaN and Infinity are not JSON."""

    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise GrantError(415, "refused", "Send JSON (Content-Type: application/json).")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_GATEWAY_REQUEST_BYTES:
        raise GrantError(413, "refused", f"A call can be at most {MAX_GATEWAY_REQUEST_BYTES // 1024} KiB.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_GATEWAY_REQUEST_BYTES:
            raise GrantError(413, "refused", f"A call can be at most {MAX_GATEWAY_REQUEST_BYTES // 1024} KiB.")

    def no_constant(name: str) -> Any:
        raise ValueError(f"{name} is not a JSON number")

    try:
        return json.loads(body.decode("utf-8"), parse_constant=no_constant)
    except (ValueError, RecursionError):  # also bad UTF-8 and over-long integers
        raise GrantError(422, "refused", "The call is not valid JSON.") from None


def create_gateway_router() -> APIRouter:
    """POST /api/automation/runner/gateway/calls: one device call, with a grant token (not a runner token).

    Under the runner prefix, so HumanIngress lets it through without a Caddy login;
    the grant token is the only credential, and a browser's request is refused.
    Replies: 200 {"result", "calls_left", "seconds_left"}, or 4xx {"detail", "code"}
    with code refused | busy | expired | ended | device_error.
    """

    router = APIRouter(prefix=RUNNER_PREFIX.rstrip("/"))

    @router.post("/gateway/calls")
    async def gateway_call(request: Request) -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        try:
            if "sec-fetch-site" in request.headers:
                raise GrantError(403, "refused", "The device gateway is for Automation runners, not browsers.")
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            token = token.strip()
            if scheme.lower() != "bearer" or not token.startswith("lng_"):
                raise GrantError(401, "ended", "Send the grant token: Authorization: Bearer lng_…")
            grants = request.app.state.grants
            grants.authenticate(token)  # 401 before reading a body for an unknown token
            try:
                data = await _gateway_body(request)
            except GrantError as exc:  # not JSON, too large, a number too long: a refused call of this grant
                raise grants.refused_before_call(token, exc) from None
            reply = await grants.call(token, data)
        except GrantError as exc:
            extra = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else {}
            return JSONResponse({"detail": redact(exc.detail), "code": exc.code}, status_code=exc.status_code,
                                headers={**headers, **extra})
        return JSONResponse(reply, headers=headers)

    return router


__all__ = ["create_gateway_router", "create_runner_router", "runner_name"]
