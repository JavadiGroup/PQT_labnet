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

Errors are ``{"error": "<message for the agent>"}`` with a 4xx/5xx status.
The literature tools forward to the Control Center with the runner's token,
which stays on this side. ``run_python`` runs one script that the agent wrote
under ``ScriptsUtilized/`` in a fresh run container (``Backend.run_python``):
one at a time per run, with the time limit capped by the runner's settings.
Its whole output goes to ``ScriptsUtilized/logs/<run id>/<NN>-<script>.log``
(uploaded with the run's results); the agent gets the tails.

Everything the agent sends is untrusted: paths go through the same checks as
the Control Center's (``automation_paths``), and the log file is created with
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
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path, PurePosixPath
from typing import Any

from labnet.control_center.automation_paths import PathRefused, check_handle_inside, clean_relative, resolve_inside
from labnet.control_center.redaction import redact

from .backend import Backend, PythonRequest
from .config import RunnerSettings
from .docker_backend import RUN_ID


LOGGER = logging.getLogger("labnet.automation_runner.tools")

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
    ) -> None:
        run_id = run.get("id") if isinstance(run, dict) else None
        if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            raise ValueError(f"Not a run: {run_id!r}")
        self.run_id = run_id
        self.work = Path(work)
        self.client = client
        self.backend = backend
        self.settings = settings
        self.on_event = on_event
        self._python_lock = threading.Lock()
        self._next_log = 1
        self.routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "/v1/search_literature": self.search_literature,
            "/v1/list_papers": self.list_papers,
            "/v1/read_paper_pages": self.read_paper_pages,
            "/v1/run_python": self.run_python,
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
                result = self.backend.run_python(PythonRequest(
                    run_id=self.run_id, work=self.work, script=shown, args=args, timeout_seconds=timeout))
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
        self._emit("tool", f"run_python {shown} → {outcome}")
        return {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "seconds": round(result.seconds, 2),
            "stdout_tail": tail(result.stdout, where=shown_log or ""),
            "stderr_tail": tail(stderr, where=shown_log or ""),
            "log": shown_log,
        }


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
    ) -> None:
        self.tools = Tools(run, work, client, backend, settings, on_event)
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
