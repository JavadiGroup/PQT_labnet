"""The run's experiment: what the runner knows about its protocol and grant, and the device relay.

:class:`Experiment` is shared by the supervisor, the tools server and the relay
(one per run). It records each protocol the agent submitted (the tools server
uploads it, so the runner knows the bounds it asked for), and, once a person
approved one, holds the grant's token, which nothing outside this process ever
sees.

:class:`DeviceRelay` is a small JSON-over-HTTP server on a Unix socket,
``<run folder>/devices/gateway.sock``. Only while the run is executing does the
supervisor mount that folder into run containers (``run_python``) at
``/run/labnet-devices``, where ``labnet_devices.connect()`` finds it:

    POST /v1/call    {device_id, method, args: {...}, sample_rate_hz?, sample_count?}   (≤ 16 KiB)
                     → the gateway's status and JSON, unchanged:
                       200 {result, calls_left, seconds_left} or 4xx {detail, code}
    GET  /v1/grant   → the grant as the runner knows it: devices, approved bounds, end time

The relay adds the grant's token to each call and forwards it to the Control
Center's gateway (``ControlCenterClient.gateway_call``), which checks every
call against the approved protocol; the relay itself decides nothing about
devices. Its own refusals use the gateway's shape (``{"detail", "code"}``):
a body that isn't a JSON object or is too large (``refused``), no active grant
(``ended``), the Control Center unreachable (``unavailable``).
"""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import stat
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .tools_server import MAX_UNIX_PATH_BYTES, _UnixServer


LOGGER = logging.getLogger("labnet_automation.devices")

SOCKET_NAME = "gateway.sock"
MAX_CALL_BYTES = 16 * 1024  # the gateway's limit
DRAIN_BYTES = 1024 * 1024
MIN_CALL_TIMEOUT_SECONDS = 30.0
CALL_TIMEOUT_MARGIN_SECONDS = 60.0
MAX_CALL_TIMEOUT_SECONDS = 130 * 60.0
MAX_REFUSAL_EVENTS = 50  # then only counted (the Control Center audits every call anyway)

EventSink = Callable[[str, str], None]

# What a grant summary may show of the Control Center's grant record (no token, nothing else).
_GRANT_FIELDS = ("id", "status", "approved_by", "starts_at", "ends_at", "devices", "calls", "refused_calls",
                 "ended_reason", "ended_at")


def parse_time(value: Any) -> datetime | None:
    """An ISO 8601 time from the Control Center (``…Z`` or ``…+00:00``) as an aware datetime, else None."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def seconds_until(value: Any, *, now: datetime | None = None) -> float | None:
    when = parse_time(value)
    if when is None:
        return None
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


def device_ids(grant: dict[str, Any] | None, protocol: dict[str, Any] | None = None) -> list[str]:
    """The granted devices' ids: from the grant record (ids or ``{"device_id"}`` objects), else the protocol."""

    ids: list[str] = []
    for item in (grant or {}).get("devices") or []:
        device = item if isinstance(item, str) else item.get("device_id") if isinstance(item, dict) else None
        if isinstance(device, str) and device and device not in ids:
            ids.append(device)
    if not ids and isinstance(protocol, dict):
        for item in protocol.get("devices") or []:
            if isinstance(item, dict) and isinstance(item.get("device_id"), str) and item["device_id"] not in ids:
                ids.append(item["device_id"])
    return ids


class Experiment:
    """One run's protocol submissions and grant. Thread-safe; the token never leaves it except to the relay."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self.submissions: list[dict[str, Any]] = []  # each accepted submission: {sha256, title, protocol}
        self.grant: dict[str, Any] = {}
        self.protocol: dict[str, Any] | None = None  # the approved protocol, as the Control Center normalized it
        self.socket_dir: Path | None = None
        self.token_taken = False
        self.ended = False  # no more calls go out
        self.closed = False  # the Control Center confirmed the grant's end (grant/end answered)
        self.end_reason = ""
        self.calls = 0
        self.refused = 0

    # ---- protocol submissions (the tools server) --------------------------------------------

    def record_submission(self, sha256: str, protocol: dict[str, Any]) -> None:
        with self._lock:
            self.submissions.append({"sha256": sha256, "title": protocol.get("title"), "protocol": protocol})

    def submitted(self, sha256: str | None) -> dict[str, Any] | None:
        """The protocol submitted with this sha256 (the Control Center's), or None."""

        with self._lock:
            for item in reversed(self.submissions):
                if sha256 and item["sha256"] == sha256:
                    return item["protocol"]
        return None

    # ---- the grant (the supervisor) ------------------------------------------------------------

    def activate(self, token: str, grant: dict[str, Any], protocol: dict[str, Any] | None, socket_dir: Path) -> None:
        with self._lock:
            self._token = token
            self.token_taken = True
            self.grant = dict(grant)
            self.protocol = protocol
            self.socket_dir = socket_dir
            self.ended = False

    def end(self, reason: str, grant: dict[str, Any] | None = None, *, confirmed: bool = False) -> bool:
        """Forget the token: no call goes out any more. ``confirmed``: the Control Center ended the grant
        (``grant/end`` answered), so the supervisor needn't end it again. False if it had already ended."""

        with self._lock:
            already = self.ended
            self.ended = True
            self.closed = self.closed or confirmed
            self._token = None
            self.socket_dir = None
            if not already:
                self.end_reason = reason
            if grant:
                self.grant.update(grant)
            return not already

    @property
    def active(self) -> bool:
        with self._lock:
            return self._token is not None and not self.ended

    def token(self) -> str | None:
        with self._lock:
            return None if self.ended else self._token

    def devices_dir(self) -> Path | None:
        """The relay's folder, for run containers: only while the grant is active."""

        with self._lock:
            return self.socket_dir if self._token is not None and not self.ended else None

    def count(self, status: int) -> int:
        """Count one call; the number of refused calls so far."""

        with self._lock:
            self.calls += 1
            if status != 200:
                self.refused += 1
            return self.refused

    def note_reply(self, status: int, reply: dict[str, Any]) -> None:
        """What a gateway answer says about the grant (time left; ended or expired)."""

        with self._lock:
            if status == 200 and isinstance(reply.get("seconds_left"), (int, float)):
                self.grant["seconds_left"] = reply["seconds_left"]
            if reply.get("code") in ("ended", "expired"):
                self.grant["status"] = "ended"

    # ---- what the agent sees -------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """The grant for ``GET /v1/grant`` and ``grant_status``: devices, bounds, time left. No token."""

        with self._lock:
            grant = {name: self.grant[name] for name in _GRANT_FIELDS if name in self.grant}
            active = self._token is not None and not self.ended
            protocol = self.protocol
            ids = device_ids(self.grant, protocol)
        left = seconds_until(grant.get("ends_at")) if active else 0.0
        devices: dict[str, Any] = {}
        for item in (protocol or {}).get("devices") or []:
            if isinstance(item, dict) and item.get("device_id") in ids:
                devices[item["device_id"]] = {"device_type": item.get("device_type"),
                                              "methods": item.get("methods") or {}}
        for device in ids:
            devices.setdefault(device, {"device_type": None, "methods": {}})
        return {
            "active": active,
            "grant": grant,
            "seconds_left": None if left is None else round(left, 1),
            "devices": devices,
            "safe_state": (protocol or {}).get("safe_state") or [],
            "ended_reason": self.end_reason or grant.get("ended_reason") or "",
        }


def call_timeout(experiment: Experiment) -> float:
    """How long one device call may take: the grant's time left plus a margin (the gateway times it out)."""

    left = seconds_until(experiment.grant.get("ends_at"))
    if left is None:
        return MAX_CALL_TIMEOUT_SECONDS
    return min(MAX_CALL_TIMEOUT_SECONDS, max(MIN_CALL_TIMEOUT_SECONDS, left + CALL_TIMEOUT_MARGIN_SECONDS))


class DeviceRelay:
    """The run's device relay on ``socket_path``. ``start()`` serves in a thread; ``stop()`` ends it."""

    def __init__(self, experiment: Experiment, client: Any, socket_path: Path, on_event: EventSink,
                 *, run_id: str = "") -> None:
        self.experiment = experiment
        self.client = client
        self.socket_path = Path(socket_path)
        self.on_event = on_event
        self.run_id = run_id
        self._server: Any = None
        self._thread: threading.Thread | None = None

    def _emit(self, kind: str, text: str) -> None:
        try:
            self.on_event(kind, text)
        except Exception:  # noqa: BLE001
            LOGGER.exception("could not emit a %s event", kind)

    # ---- the two routes -------------------------------------------------------------------------

    def call(self, data: Any) -> tuple[int, dict[str, Any]]:
        """One device call (the body already parsed): the gateway's (status, JSON). Never raises."""

        if not isinstance(data, dict):
            return 400, {"detail": "A device call is a JSON object: {device_id, method, args}.", "code": "refused"}
        token = self.experiment.token()
        if token is None:
            return 409, {"detail": "No device grant is active: the experiment has ended (or was never approved). "
                                   "Device calls are refused.", "code": "ended"}
        try:
            status, reply = self.client.gateway_call(token, data, timeout=call_timeout(self.experiment))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("%s: device call failed: %r", self.run_id, exc)
            status, reply = 502, {"detail": "The Control Center couldn't be reached for this device call "
                                            f"({type(exc).__name__}). It may not have reached the device.",
                                  "code": "unavailable"}
        if not isinstance(reply, dict):
            status, reply = 502, {"detail": "The Control Center sent an unexpected answer.", "code": "unavailable"}
        self.experiment.note_reply(status, reply)
        refused = self.experiment.count(status)
        if status != 200 and refused <= MAX_REFUSAL_EVENTS:
            what = f"{str(data.get('device_id'))[:64]}.{str(data.get('method'))[:64]}"
            self._emit("tool", f"device call {what} → {status} {str(reply.get('code', ''))[:32]}: "
                               f"{str(reply.get('detail', ''))[:300]}")
            if refused == MAX_REFUSAL_EVENTS:
                self._emit("log", f"{MAX_REFUSAL_EVENTS} device calls were refused; further refusals are only "
                                  "counted here (the Control Center's audit has each one).")
        return status, reply

    def grant(self) -> tuple[int, dict[str, Any]]:
        return 200, self.experiment.summary()

    def handle(self, method: str, path: str, data: Any) -> tuple[int, dict[str, Any]]:
        """One request without the transport (what the tests and the HTTP layer use)."""

        if path == "/v1/call":
            return self.call(data) if method == "POST" else (405, {"detail": "Use POST.", "code": "refused"})
        if path == "/v1/grant":
            return self.grant() if method == "GET" else (405, {"detail": "Use GET.", "code": "refused"})
        return 404, {"detail": f"Nothing at {path[:100]}.", "code": "refused"}

    # ---- serving --------------------------------------------------------------------------------

    def start(self) -> None:
        if _UnixServer is None or not hasattr(socket, "AF_UNIX"):
            raise RuntimeError("The device relay needs Unix sockets (the runner runs on Linux).")
        path = str(self.socket_path)
        if len(os.fsencode(path)) >= MAX_UNIX_PATH_BYTES:
            raise RuntimeError(f"The socket path is too long for a Unix socket: {path}")
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o755)
        try:
            if stat.S_ISSOCK(os.lstat(path).st_mode):
                os.unlink(path)
        except FileNotFoundError:
            pass
        server = _UnixServer(path, make_relay_handler(self))
        # 0666: run containers run as uid 1000 and the runner as root; only this run's run
        # containers get the folder, and only while its grant is active.
        os.chmod(path, 0o666)
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name=f"labnet-devices-{self.run_id}",
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


def make_relay_handler(relay: DeviceRelay) -> type[BaseHTTPRequestHandler]:
    """An HTTP request handler class serving ``relay`` (Unix socket, or TCP in tests)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "labnet-devices/1"
        sys_version = ""
        timeout = 60  # for reading the request; a device call may take as long as the grant has left

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            if not _finite(payload):  # NaN from the gateway would make JSON the shim can't read
                status, payload = 502, {"detail": "The gateway's answer could not be passed on.",
                                        "code": "unavailable"}
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = self.headers.get("Content-Length", "")
            if not length.isdigit():
                self._send(411, {"detail": "Send a Content-Length.", "code": "refused"})
                return
            if int(length) > MAX_CALL_BYTES:
                remaining = min(int(length), DRAIN_BYTES)  # read it first, or Windows aborts before the 413
                while remaining > 0 and (chunk := self.rfile.read(min(remaining, 64 * 1024))):
                    remaining -= len(chunk)
                self._send(413, {"detail": f"A device call is limited to {MAX_CALL_BYTES} bytes.", "code": "refused"})
                return
            raw = self.rfile.read(int(length))
            try:
                data = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
            except (UnicodeDecodeError, ValueError):
                self._send(400, {"detail": "The body is not JSON (NaN and Infinity aren't JSON either).",
                                 "code": "refused"})
                return
            status, payload = relay.handle("POST", self.path, data)
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            status, payload = relay.handle("GET", self.path, None)
            self._send(status, payload)

        def _refuse(self) -> None:
            self._send(405, {"detail": "Use POST /v1/call or GET /v1/grant.", "code": "refused"})

        do_PUT = do_DELETE = do_PATCH = _refuse  # noqa: N815

        def address_string(self) -> str:
            return "run-container"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            LOGGER.debug("devices %s: %s", self.address_string(), format % args)

    return Handler


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


__all__ = ["DeviceRelay", "Experiment", "MAX_CALL_BYTES", "SOCKET_NAME", "call_timeout", "device_ids",
           "make_relay_handler", "parse_time", "seconds_until"]
