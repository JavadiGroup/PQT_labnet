"""What the agent's MCP tools call: a small JSON-over-HTTP server on a Unix socket.

The supervisor starts one per run, on ``<run folder>/sock/tools.sock``; only
that folder is mounted into the run's agent container (at /run/labnet), so
only that agent can reach it, and it needs no token. The MCP server inside
the container (``mcp_server``) forwards each tool call here:

    POST /v1/search_literature   {query, k?, paper?}        → the Control Center's search result
    POST /v1/list_papers         {}                         → the papers the run may search
    POST /v1/read_paper_pages    {paper, first, last}       → up to 20 pages' text
    POST /v1/run_python          {script, args?, timeout_seconds?}
                                 → {exit_code, timed_out, seconds, stdout_tail, stderr_tail, log}
    POST /v1/submit_protocol     {}  → {status: submitted, sha256, phase} | {status: problems, problems, detail}
    POST /v1/grant_status        {}  → {phase, protocol, grant, devices, seconds_left, ...}
    POST /v1/end_experiment      {reason}                   → {ended, grant}

Errors are ``{"error": "<message for the agent>"}`` with a 4xx/5xx status.
The literature tools forward to the Control Center with the runner's token,
which stays on this side. ``run_python`` runs one script that the agent wrote
under ``ScriptsUtilized/`` in a fresh run container (``Backend.run_python``):
one at a time per run, with the time limit capped by the runner's settings.
Its whole output goes to ``ScriptsUtilized/logs/<run id>/<NN>-<script>.log``
(uploaded with the run's results); the agent gets the tails.

``submit_protocol`` uploads ``Protocols/<task stem>.protocol.json`` and ``.md``
from the working copy, then asks the Control Center to validate them; an
accepted submission is recorded in the run's :class:`~.devices_relay.Experiment`
(the supervisor and the device relay read the approved bounds from there).
While a grant is active, ``run_python`` mounts the device relay's folder into
the run container; ``end_experiment`` ends the grant early.

Everything the agent sends is untrusted: paths go through the same checks as
the Control Center's (``labnet.safe_paths``), and the log file is created with
O_EXCL so a link the agent planted can't redirect it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shlex
import socket
import socketserver
import stat
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from labnet.safe_paths import (
    PathRefused, check_handle_inside, clean_relative, open_inside, resolve_inside,
)
from labnet.redaction import redact

from .backend import Backend, PythonRequest
from .config import RunnerSettings
from .docker_backend import RUN_ID


if TYPE_CHECKING:
    from .devices_relay import Experiment


LOGGER = logging.getLogger("labnet_automation.tools")

SCRIPTS = "ScriptsUtilized"
LOGS = "logs"
MAX_BODY_BYTES = 256 * 1024
DRAIN_BYTES = 4 * 1024 * 1024  # an oversized body up to this is read before the 413
MAX_ARGS = 32
MAX_ARG_CHARS = 1000
MAX_QUERY_CHARS = 1000
TAIL_CHARS = 8000
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_UNIX_PATH_BYTES = 107  # sun_path is 108 bytes on Linux, with the NUL
PROTOCOLS = "Protocols"
MAX_PROTOCOL_BYTES = 64 * 1024  # the Control Center's limit, for the JSON and for the prose
MAX_REASON_CHARS = 500

EventSink = Callable[[str, str], None]


class ToolError(Exception):
    """A refusal or failure the agent should read (``status`` for the HTTP answer)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def tail(text: str, limit: int = TAIL_CHARS, *, where: str = "") -> str:
    if len(text) <= limit:
        return text
    note = f"[… {len(text) - limit} earlier characters{f' in {where}' if where else ''}]\n"
    return note + text[-limit:]


def _integer(data: dict[str, Any], name: str, default: Any = ...) -> int:
    value = data.get(name, default)
    if value is ...:
        raise ToolError(400, f"{name} is required.")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolError(400, f"{name} must be a whole number.")
    return value


def _text(data: dict[str, Any], name: str, *, required: bool = True, limit: int = 1000) -> str | None:
    value = data.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ToolError(400, f"{name} must be text.")
    if len(value) > limit:
        raise ToolError(400, f"{name} is longer than {limit} characters.")
    return value


def _only(data: dict[str, Any], *names: str) -> None:
    extra = sorted(set(data) - set(names))
    if extra:
        raise ToolError(400, f"Unknown field(s): {', '.join(extra)}. Expected: {', '.join(names) or 'none'}.")


class Tools:
    """The tool calls themselves, without any transport (``call`` is what the HTTP layer uses)."""

    def __init__(
        self,
        run: dict[str, Any],
        work: Path,
        client: Any,
        backend: Backend,
        settings: RunnerSettings,
        on_event: EventSink,
        experiment: "Experiment | None" = None,
    ) -> None:
        run_id = run.get("id") if isinstance(run, dict) else None
        if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            raise ValueError(f"Not a run: {run_id!r}")
        self.run_id = run_id
        self.task = run.get("task") if isinstance(run.get("task"), str) else ""
        self.work = Path(work)
        self.client = client
        self.backend = backend
        self.settings = settings
        self.on_event = on_event
        if experiment is None:
            from .devices_relay import Experiment

            experiment = Experiment()
        self.experiment = experiment
        self._python_lock = threading.Lock()
        self._protocol_lock = threading.Lock()
        self._next_log = 1
        self.routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "/v1/search_literature": self.search_literature,
            "/v1/list_papers": self.list_papers,
            "/v1/read_paper_pages": self.read_paper_pages,
            "/v1/run_python": self.run_python,
            "/v1/submit_protocol": self.submit_protocol,
            "/v1/grant_status": self.grant_status,
            "/v1/end_experiment": self.end_experiment,
        }

    def call(self, path: str, data: Any) -> tuple[int, dict[str, Any]]:
        """(HTTP status, JSON body) for one request. Never raises."""

        route = self.routes.get(path)
        if route is None:
            return 404, {"error": f"No tool at {path[:100]}."}
        if not isinstance(data, dict):
            return 400, {"error": "Send a JSON object."}
        try:
            return 200, route(data)
        except ToolError as exc:
            return exc.status, {"error": redact(exc.message)}
        except Exception as exc:  # noqa: BLE001  (the agent gets a message, the log the details)
            LOGGER.exception("tool %s failed", path)
            return 500, {"error": f"The tool failed on the runner's side ({type(exc).__name__})."}

    def _emit(self, kind: str, text: str) -> None:
        try:
            self.on_event(kind, text)
        except Exception:  # noqa: BLE001  (an event that can't be sent mustn't fail the tool)
            LOGGER.exception("could not emit a %s event", kind)

    # ---- literature: forwarded to the Control Center ---------------------------------

    def _forward(self, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            result = call()
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and status > 0:  # 0: the client got no answer, even after retries
                detail = getattr(exc, "detail", None) or str(exc)
                # The Control Center's 4xx messages are for the caller; anything else is "try later".
                code = status if 400 <= status < 500 else 502
                raise ToolError(code, f"The Control Center answered {status}: {detail}") from None
            LOGGER.warning("literature call failed: %r", exc)
            raise ToolError(502, f"The Control Center couldn't be reached ({type(exc).__name__}); try again shortly.") from None
        if not isinstance(result, dict):
            raise ToolError(502, "The Control Center sent an unexpected answer.")
        return result

    def search_literature(self, data: dict[str, Any]) -> dict[str, Any]:
        _only(data, "query", "k", "paper")
        query = _text(data, "query", limit=MAX_QUERY_CHARS)
        k = _integer(data, "k", 10)
        if not 1 <= k <= 50:
            raise ToolError(400, "k must be between 1 and 50.")
        paper = _text(data, "paper", required=False, limit=400) or None
        return self._forward(lambda: self.client.literature_search(self.run_id, query, k=k, paper=paper))

    def list_papers(self, data: dict[str, Any]) -> dict[str, Any]:
        _only(data)
        return self._forward(lambda: self.client.literature_papers(self.run_id))

    def read_paper_pages(self, data: dict[str, Any]) -> dict[str, Any]:
        _only(data, "paper", "first", "last")
        paper = _text(data, "paper", limit=400)
        first = _integer(data, "first")
        last = _integer(data, "last", first)
        if first < 1 or last < first:
            raise ToolError(400, "Pages are numbered from 1, and last can't come before first.")
        return self._forward(lambda: self.client.literature_pages(self.run_id, paper, first, last))

    # ---- run_python ------------------------------------------------------------------

    def _script(self, raw: Any) -> PurePosixPath:
        if not isinstance(raw, str):
            raise ToolError(400, "script must be a path like ScriptsUtilized/analysis_scripts/fit.py.")
        try:
            relative = clean_relative(raw)
        except PathRefused as exc:
            raise ToolError(400, f"script: {exc}") from None
        parts = relative.parts
        if parts[0] != SCRIPTS or len(parts) < 2 or parts[1] == LOGS:
            raise ToolError(400, f"run_python runs a .py file you wrote under {SCRIPTS}/ "
                                 f"(e.g. {SCRIPTS}/analysis_scripts/fit.py), not under {SCRIPTS}/{LOGS}/.")
        if relative.suffix != ".py":
            raise ToolError(400, "run_python runs .py files only.")
        try:
            path = resolve_inside(self.work, relative)
            info = os.lstat(path)
        except PathRefused as exc:
            raise ToolError(400, f"script: {exc}") from None
        except (FileNotFoundError, NotADirectoryError):
            raise ToolError(404, f"{relative.as_posix()} doesn't exist. Write it first, then run it.") from None
        if not stat.S_ISREG(info.st_mode):
            raise ToolError(400, f"{relative.as_posix()} is not a regular file.")
        if info.st_nlink > 1:
            raise ToolError(400, f"{relative.as_posix()} is a hard link; it is not run.")
        return relative

    @staticmethod
    def _args(raw: Any) -> list[str]:
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > MAX_ARGS:
            raise ToolError(400, f"args must be a list of at most {MAX_ARGS} strings.")
        for arg in raw:
            if not isinstance(arg, str) or len(arg) > MAX_ARG_CHARS or "\0" in arg:
                raise ToolError(400, f"Each argument must be text of at most {MAX_ARG_CHARS} characters.")
        return list(raw)

    def _timeout(self, raw: Any) -> float:
        cap = self.settings.python_timeout_seconds
        if raw is None:
            return min(DEFAULT_TIMEOUT_SECONDS, cap)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(raw) or raw <= 0:
            raise ToolError(400, "timeout_seconds must be a positive number.")
        return min(float(raw), cap)

    def _open_log(self, stem: str) -> tuple[PurePosixPath, Any]:
        folder = PurePosixPath(SCRIPTS, LOGS, self.run_id)
        try:
            directory = resolve_inside(self.work, folder)
            directory.mkdir(parents=True, exist_ok=True)
            directory = resolve_inside(self.work, folder)  # again, now that it exists: still no links
        except PathRefused as exc:
            raise ToolError(400, f"{folder.as_posix()}: {exc}") from None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        stem = stem[:100]
        for _attempt in range(1000):
            name = f"{self._next_log:02d}-{stem}.log"
            self._next_log += 1
            try:
                handle = os.open(directory / name, flags, 0o644)
            except FileExistsError:
                continue  # left by an earlier run_python, or planted: never overwritten
            try:
                check_handle_inside(handle, self.work, directory / name)
            except BaseException:
                os.close(handle)
                raise
            return folder / name, os.fdopen(handle, "w", encoding="utf-8", errors="replace", newline="\n")
        raise ToolError(500, f"Too many logs in {folder.as_posix()}.")

    def run_python(self, data: dict[str, Any]) -> dict[str, Any]:
        _only(data, "script", "args", "timeout_seconds")
        script = self._script(data.get("script"))
        args = self._args(data.get("args"))
        timeout = self._timeout(data.get("timeout_seconds"))
        shown = script.as_posix()
        with self._python_lock:  # one run container at a time per run
            started = datetime.now(timezone.utc)
            try:
                devices = self.experiment.devices_dir()  # only while a grant is active
                result = self.backend.run_python(PythonRequest(
                    run_id=self.run_id, work=self.work, script=shown, args=args, timeout_seconds=timeout,
                    devices_socket_dir=devices))
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("run_python %s could not run", shown)
                self._emit("tool", f"run_python {shown} → could not start: {type(exc).__name__}")
                raise ToolError(500, f"The run container could not be started ({type(exc).__name__}: "
                                     f"{str(exc)[:300]}).") from None
            if result.timed_out:
                outcome = f"timed out after {result.seconds:.1f} s (limit {timeout:g} s)"
            elif result.exit_code is None:
                outcome = f"stopped after {result.seconds:.1f} s"
            else:
                outcome = f"exit {result.exit_code} in {result.seconds:.1f} s"
            stderr = result.stderr
            try:
                log, file = self._open_log(script.stem)
                with file:
                    file.write(f"# run_python {shlex.join([shown, *args])}\n")
                    file.write(f"# run {self.run_id}, started {started.isoformat(timespec='seconds')}: {outcome}\n")
                    file.write("# ---- stdout ----\n")
                    file.write(result.stdout)
                    if result.stdout and not result.stdout.endswith("\n"):
                        file.write("\n")
                    file.write("# ---- stderr ----\n")
                    file.write(result.stderr)
                shown_log: str | None = log.as_posix()
            except (ToolError, PathRefused, OSError) as exc:
                # The script ran; the agent still gets its output, and the event log says why there's no file.
                LOGGER.warning("run_python %s: no log file: %s", shown, exc)
                shown_log = None
                stderr += f"\n[labnet: the log file could not be written: {getattr(exc, 'message', exc)}]\n"
                outcome += " (no log file)"
        self._emit("tool", f"run_python {shown} → {outcome}{' (with devices)' if devices else ''}")
        return {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "seconds": round(result.seconds, 2),
            "stdout_tail": tail(result.stdout, where=shown_log or ""),
            "stderr_tail": tail(stderr, where=shown_log or ""),
            "log": shown_log,
        }


    # ---- the protocol and the experiment ----------------------------------------------

    def _ask(self, call: Callable[[], Any], what: str) -> Any:
        """A Control Center call for a tool; its refusals become ToolErrors the agent reads."""

        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if status == 409:
                run_status = getattr(exc, "run_status", None)
                detail = getattr(exc, "detail", None) or str(exc)
                raise ToolError(409, f"{what}: the Control Center refused ({detail})"
                                     + (f"; the run is {run_status}." if run_status else ".")) from None
            if isinstance(status, int) and status > 0:
                detail = getattr(exc, "detail", None) or str(exc)
                raise ToolError(status if 400 <= status < 500 else 502,
                                f"{what}: the Control Center answered {status}: {detail}") from None
            LOGGER.warning("%s failed: %r", what, exc)
            raise ToolError(502, f"{what}: the Control Center couldn't be reached ({type(exc).__name__}); "
                                 "try again shortly.") from None

    def _protocol_paths(self) -> tuple[PurePosixPath, PurePosixPath]:
        task = PurePosixPath(self.task) if self.task.startswith("Tasks/") else None
        if task is None or not task.stem:
            raise ToolError(400, "This run has no task file, so it can't have a protocol.")
        return (PurePosixPath(PROTOCOLS, f"{task.stem}.protocol.json"),
                PurePosixPath(PROTOCOLS, f"{task.stem}.protocol.md"))

    def _read_protocol_file(self, relative: PurePosixPath) -> bytes:
        try:
            file, info = open_inside(self.work, relative)
        except FileNotFoundError:
            raise ToolError(404, f"{relative.as_posix()} doesn't exist. Write it first (the format is in your "
                                 "briefing), then call submit_protocol again.") from None
        except PathRefused as exc:
            raise ToolError(400, f"{relative.as_posix()}: {exc}") from None
        with file:
            data = file.read(MAX_PROTOCOL_BYTES + 1)
        if len(data) > MAX_PROTOCOL_BYTES or info.st_size > MAX_PROTOCOL_BYTES:
            raise ToolError(413, f"{relative.as_posix()} is larger than {MAX_PROTOCOL_BYTES // 1024} KiB.")
        return data

    def _upload_bytes(self, relative: PurePosixPath, data: bytes) -> None:
        """Upload exactly ``data`` (read once, so what is validated is what was checked here)."""

        handle, name = tempfile.mkstemp(prefix="labnet-protocol-")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
            self._ask(lambda: self.client.upload(self.run_id, relative.as_posix(), Path(name)),
                      f"uploading {relative.as_posix()}")
        finally:
            try:
                os.unlink(name)
            except OSError:
                pass

    @staticmethod
    def _not_submitted(relative: PurePosixPath, message: str) -> dict[str, Any]:
        return {"status": "problems", "submitted": False,
                "problems": [{"path": relative.as_posix(), "message": message}],
                "detail": "Not submitted: fix this first (it didn't count as a submission)."}

    def submit_protocol(self, data: dict[str, Any]) -> dict[str, Any]:
        _only(data)
        json_path, prose_path = self._protocol_paths()
        with self._protocol_lock:
            if self.experiment.token_taken:
                raise ToolError(409, "This run's protocol was already approved and its experiment has started "
                                     "(or ended); a run gets one experiment. Use grant_status.")
            raw = self._read_protocol_file(json_path)
            prose = self._read_protocol_file(prose_path)
            try:
                protocol = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                return self._not_submitted(json_path, f"isn't valid JSON ({exc}). Comments aren't allowed in JSON.")
            if not isinstance(protocol, dict):
                return self._not_submitted(json_path, "must be one JSON object (see the format in your briefing).")
            if not prose.strip():
                return self._not_submitted(prose_path, "is empty: explain the method, why these bounds, and what "
                                                       "will be measured, for the person who approves it.")
            self._upload_bytes(json_path, raw)
            self._upload_bytes(prose_path, prose)
            reply = self._ask(lambda: self.client.submit_protocol(self.run_id), "submit_protocol")
            if not isinstance(reply, dict):
                raise ToolError(502, "submit_protocol: the Control Center sent an unexpected answer.")
            sha256 = reply.get("sha256")
            if not isinstance(sha256, str) or not sha256:
                problems = [item for item in reply.get("problems") or [] if isinstance(item, (dict, str))]
                self._emit("tool", f"submit_protocol → not accepted ({len(problems)} problem(s))")
                return {"status": "problems", "submitted": True, "problems": problems[:200],
                        "detail": redact(str(reply.get("detail") or "The protocol was not accepted."))}
            self.experiment.record_submission(sha256, protocol)
            self._emit("tool", f"submit_protocol → submitted for approval (sha256 {sha256[:12]}…)")
            return {"status": "submitted", "sha256": sha256, "phase": reply.get("phase"),
                    "title": protocol.get("title") if isinstance(protocol.get("title"), str) else None}

    def grant_status(self, data: dict[str, Any]) -> dict[str, Any]:
        _only(data)
        summary = self.experiment.summary()
        note = ""
        try:
            run = self.client.get_run(self.run_id)
        except Exception as exc:  # noqa: BLE001  (what the runner knows is still worth answering)
            LOGGER.warning("grant_status: get_run failed: %r", exc)
            run, note = {}, "The Control Center couldn't be asked just now; this is what the runner knows."
        run = run if isinstance(run, dict) else {}
        grant = run.get("grant") if isinstance(run.get("grant"), dict) else {}
        if grant:
            summary["grant"] = {**summary["grant"], **grant}
        if summary["active"] and grant.get("status") not in (None, "active"):
            summary["active"] = False
        if summary["active"]:
            from .devices_relay import seconds_until

            left = seconds_until(summary["grant"].get("ends_at"))
            summary["seconds_left"] = None if left is None else round(left, 1)
        protocol = run.get("protocol") if isinstance(run.get("protocol"), dict) else {}
        return {
            "phase": run.get("phase"),
            "run_status": run.get("status"),
            "protocol": {key: protocol.get(key) for key in ("status", "submissions", "sha256", "title",
                                                             "reviewed_by", "reviewed_at", "note", "approved_sha256",
                                                             "edited_by", "changes") if key in protocol},
            **summary,
            "note": note,
        }

    def end_experiment(self, data: dict[str, Any]) -> dict[str, Any]:
        _only(data, "reason")
        reason = _text(data, "reason", limit=MAX_REASON_CHARS)
        if not self.experiment.token_taken:
            raise ToolError(409, "No experiment is running in this run (no approved protocol has started).")
        if not self.experiment.active:
            return {"ended": False, "detail": "The experiment had already ended.", **self.experiment.summary()}
        self.experiment.end(f"The agent ended the experiment: {reason}")  # no call goes out from now on
        grant = self._ask(lambda: self.client.end_grant(self.run_id, reason), "end_experiment")
        self.experiment.end("", grant if isinstance(grant, dict) else None, confirmed=True)
        self._emit("tool", f"end_experiment → the grant ended ({str(reason)[:200]})")
        return {"ended": True, "detail": "The experiment has ended: the Control Center applied the safe state.",
                **self.experiment.summary()}


def make_handler(tools: Tools) -> type[BaseHTTPRequestHandler]:
    """An HTTP request handler class serving ``tools`` (any socketserver: Unix socket, or TCP in tests)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "labnet-tools/1"
        sys_version = ""
        timeout = 60  # for reading the request; the answer may take as long as a script runs

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = self.headers.get("Content-Length", "")
            if not length.isdigit():
                self._send(411, {"error": "Send a Content-Length."})
                return
            if int(length) > MAX_BODY_BYTES:
                # Read (and drop) a moderately oversized body first: answering while the client is
                # still sending makes some systems (Windows) abort the connection before it sees the 413.
                remaining = min(int(length), DRAIN_BYTES)
                while remaining > 0 and (chunk := self.rfile.read(min(remaining, 64 * 1024))):
                    remaining -= len(chunk)
                self._send(413, {"error": f"Requests are limited to {MAX_BODY_BYTES} bytes."})
                return
            raw = self.rfile.read(int(length))
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, ValueError):
                self._send(400, {"error": "The body is not JSON."})
                return
            status, payload = tools.call(self.path, data)
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            self._send(405, {"error": "Use POST."})

        do_PUT = do_DELETE = do_PATCH = do_GET  # noqa: N815

        def address_string(self) -> str:  # a Unix socket peer has no address
            return "agent"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            LOGGER.debug("tools %s: %s", self.address_string(), format % args)

    return Handler


if hasattr(socketserver, "ThreadingUnixStreamServer"):
    class _UnixServer(socketserver.ThreadingUnixStreamServer):  # type: ignore[name-defined, misc]
        daemon_threads = True
        allow_reuse_address = False
else:  # Windows: no AF_UNIX in socketserver
    _UnixServer = None  # type: ignore[assignment, misc]


class ToolsServer:
    """One run's tools server on ``socket_path``. ``start()`` serves in a thread; ``stop()`` ends it."""

    def __init__(
        self,
        run: dict[str, Any],
        work: Path,
        socket_path: Path,
        client: Any,
        backend: Backend,
        settings: RunnerSettings,
        on_event: EventSink,
        experiment: "Experiment | None" = None,
    ) -> None:
        self.tools = Tools(run, work, client, backend, settings, on_event, experiment)
        self.socket_path = Path(socket_path)
        self._server: Any = None
        self._thread: threading.Thread | None = None

    def handle(self, path: str, data: Any) -> tuple[int, dict[str, Any]]:
        """One request without the transport (what the tests and the HTTP layer use)."""

        return self.tools.call(path, data)

    def start(self) -> None:
        if _UnixServer is None or not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("The tools server needs Unix sockets (the runner runs on Linux).")
        path = str(self.socket_path)
        if len(os.fsencode(path)) > MAX_UNIX_PATH_BYTES:
            raise RuntimeError(f"The socket path is too long for a Unix socket: {path}")
        try:
            if stat.S_ISSOCK(os.lstat(path).st_mode):
                os.unlink(path)  # left by a runner that crashed
        except FileNotFoundError:
            pass
        server = _UnixServer(path, make_handler(self.tools))
        # 0666: the agent runs as uid 1000 and the runner as root; only this run's agent
        # container has the folder mounted, so this widens nothing beyond it.
        os.chmod(path, 0o666)
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name=f"labnet-tools-{self.tools.run_id}",
                                        kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        try:
            if stat.S_ISSOCK(os.lstat(self.socket_path).st_mode):
                os.unlink(self.socket_path)
        except (FileNotFoundError, AttributeError):
            pass


__all__ = ["ToolError", "Tools", "ToolsServer", "make_handler", "tail"]
