"""labnet_devices: the lab's devices, from a run_python script, while an approved experiment runs.

    import labnet_devices

    devices = labnet_devices.connect()            # the devices of the active grant, by id
    aom = devices["dev01-aom"]
    aom.set_frequency(value=80.0)                 # keyword arguments only
    efficiency = aom.read_diffraction_efficiency()
    trace = aom.stream_diffraction_efficiency(channel="CH1", sample_rate_hz=10, sample_count=50)
    print(devices.grant())                        # devices, approved bounds, time left

Each call is sent over the Unix socket ``/run/labnet-devices/gateway.sock``,
which the runner mounts into run containers only while the run's device grant
is active. The runner relays it to the Control Center's gateway, which checks
it against the approved protocol (device, method, argument ranges and steps,
call counts and rates, time left) before any device sees it. This module holds
no token and decides nothing; it only turns calls into requests and answers
into results or exceptions:

* ``Refused``      the gateway refused the call (outside the approved bounds,
                   an argument that isn't allowed, too fast, …); ``Busy`` (a
                   kind of Refused): another call to that device is in flight;
* ``DeviceError``  the device itself failed;
* ``GrantEnded``   the grant expired, was stopped by a person, or ended;
* ``GatewayUnavailable``  the Control Center couldn't be reached (the call may
                   or may not have reached the device).

All derive from ``LabnetDevicesError``; ``detail`` is the gateway's message.
A refused call is final: fix the script, don't retry it.

Standard library only (it is installed into the run image's site-packages).
"""

from __future__ import annotations

import http.client
import json
import math
import os
import socket
from collections.abc import Callable, Iterator, Mapping
from typing import Any


__all__ = [
    "Busy", "DEFAULT_SOCKET", "Device", "DeviceError", "Devices", "GatewayUnavailable", "GrantEnded",
    "LabnetDevicesError", "Refused", "connect",
]

DEFAULT_SOCKET = "/run/labnet-devices/gateway.sock"
STREAM_FIELDS = ("sample_rate_hz", "sample_count")  # sent beside "args", as the gateway expects
MAX_REPLY_BYTES = 64 * 1024 * 1024


class LabnetDevicesError(Exception):
    """A device call that didn't succeed. ``detail``: the gateway's message; ``code``, ``status``: its code
    (refused, busy, expired, ended, device_error, unavailable) and HTTP status, when there was an answer."""

    def __init__(self, detail: str, *, code: str | None = None, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status = status


class Refused(LabnetDevicesError):
    """The gateway refused the call: it is outside the approved protocol. Final: fix the script."""


class Busy(Refused):
    """Another call to this device is still in flight (one at a time per device)."""


class DeviceError(LabnetDevicesError):
    """The device failed to carry out the call."""


class GrantEnded(LabnetDevicesError):
    """The experiment's grant is over (expired, stopped, or ended): no more device calls."""


class GatewayUnavailable(LabnetDevicesError):
    """The Control Center couldn't be reached; the call may or may not have reached the device."""


_BY_CODE: dict[str, type[LabnetDevicesError]] = {
    "refused": Refused, "busy": Busy, "device_error": DeviceError, "expired": GrantEnded, "ended": GrantEnded,
    "unavailable": GatewayUnavailable,
}


def _error(status: int, reply: Mapping[str, Any]) -> LabnetDevicesError:
    code = reply.get("code") if isinstance(reply.get("code"), str) else None
    detail = reply.get("detail")
    detail = detail if isinstance(detail, str) and detail else f"The device call failed (HTTP {status})."
    kind = _BY_CODE.get(code or "")
    if kind is None:
        kind = GatewayUnavailable if status >= 500 else Refused
    return kind(detail, code=code, status=status)


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float | None) -> None:
        super().__init__("labnet-devices", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self._path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


ConnectionFactory = Callable[[], http.client.HTTPConnection]


class _Transport:
    """JSON requests to the relay, one connection per request."""

    def __init__(self, factory: ConnectionFactory, path: str | None) -> None:
        self._factory = factory
        self._path = path

    def request(self, method: str, route: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        payload = None if body is None else json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        connection = self._factory()
        try:
            try:
                connection.request(method, route, body=payload, headers=headers)
                response = connection.getresponse()
                raw = response.read(MAX_REPLY_BYTES + 1)
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                if self._path is not None and not os.path.exists(self._path):
                    raise GrantEnded("The device grant isn't active in this run container any more (its socket is "
                                     "gone).", code="ended") from exc
                raise GatewayUnavailable(f"The device relay isn't answering ({type(exc).__name__}).") from exc
            except (OSError, http.client.HTTPException) as exc:
                raise GatewayUnavailable(f"The device call broke off ({type(exc).__name__}: {exc}); it may or may "
                                         "not have reached the device.") from exc
        finally:
            connection.close()
        try:
            reply = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_REPLY_BYTES else None
        except (UnicodeDecodeError, ValueError):
            reply = None
        if not isinstance(reply, dict):
            raise GatewayUnavailable(f"The device relay sent an unreadable answer (HTTP {response.status}).",
                                     status=response.status)
        return response.status, reply


class _Method:
    def __init__(self, device: Device, name: str) -> None:
        self._device = device
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        device_id = self._device.device_id
        if args:
            raise TypeError(f"{device_id}.{self._name}() takes keyword arguments only, "
                            f"e.g. {self._name}(value=80.0); got {len(args)} positional argument(s).")
        body: dict[str, Any] = {"device_id": device_id, "method": self._name}
        for field in STREAM_FIELDS:
            if field in kwargs:
                body[field] = kwargs.pop(field)
        body["args"] = kwargs
        for name, value in [*kwargs.items(), *((field, body[field]) for field in STREAM_FIELDS if field in body)]:
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{device_id}.{self._name}(): {name}={value!r} isn't a finite number.")
        status, reply = self._device._devices._transport.request("POST", "/v1/call", body)
        if status != 200:
            raise _error(status, reply)
        self._device._devices.last_reply = reply
        return reply.get("result")

    def __repr__(self) -> str:
        return f"<labnet_devices method {self._device.device_id}.{self._name}>"


class Device:
    """One granted device. Any attribute is a method call: ``device.set_frequency(value=80.0)``."""

    def __init__(self, devices: Devices, device_id: str) -> None:
        self._devices = devices
        self.device_id = device_id

    def __getattr__(self, name: str) -> _Method:
        if name.startswith("_"):
            raise AttributeError(name)
        return _Method(self, name)

    def __dir__(self) -> list[str]:
        methods = (self._devices._info.get(self.device_id) or {}).get("methods") or {}
        return sorted({*object.__dir__(self), *methods})

    def __repr__(self) -> str:
        kind = (self._devices._info.get(self.device_id) or {}).get("device_type")
        return f"<labnet_devices Device {self.device_id}{f' ({kind})' if kind else ''}>"


class Devices(Mapping[str, Device]):
    """The granted devices by id (from ``connect()``). ``grant()`` shows the grant as it is now."""

    def __init__(self, transport: _Transport, summary: dict[str, Any]) -> None:
        self._transport = transport
        info = summary.get("devices")
        self._info: dict[str, dict[str, Any]] = {
            str(key): value if isinstance(value, dict) else {} for key, value in (info or {}).items()
        } if isinstance(info, dict) else {str(key): {} for key in info or []}
        self.last_reply: dict[str, Any] | None = None  # the last successful call's reply (calls_left, seconds_left)

    def grant(self) -> dict[str, Any]:
        """The grant now: ``{"active", "grant", "seconds_left", "devices", "safe_state", "ended_reason"}``."""

        status, reply = self._transport.request("GET", "/v1/grant")
        if status != 200:
            raise _error(status, reply)
        return reply

    def __getitem__(self, device_id: str) -> Device:
        if device_id not in self._info:
            raise KeyError(f"{device_id!r} isn't in this grant; the granted devices are: "
                           f"{', '.join(self._info) or 'none'}.")
        return Device(self, device_id)

    def __iter__(self) -> Iterator[str]:
        return iter(self._info)

    def __len__(self) -> int:
        return len(self._info)

    def __repr__(self) -> str:
        return f"<labnet_devices Devices {list(self._info)}>"


def connect(path: str = DEFAULT_SOCKET, *, timeout: float | None = None,
            connection_factory: ConnectionFactory | None = None) -> Devices:
    """The devices of the run's active grant, by id. Raises GrantEnded when no grant is active.

    ``timeout``: seconds per call (default: none; the gateway ends each call at the grant's time left).
    ``connection_factory``: makes an ``http.client.HTTPConnection`` per request instead of the Unix socket
    (for tests).
    """

    if connection_factory is None:
        if not hasattr(socket, "AF_UNIX"):
            raise GatewayUnavailable("labnet_devices needs Unix sockets (it runs in the lab's run containers).")
        if not os.path.exists(path):
            raise GrantEnded(f"No device grant is active in this run container ({path} doesn't exist): devices "
                             "are only reachable while an approved experiment runs.", code="ended")

        def connection_factory() -> http.client.HTTPConnection:
            return _UnixHTTPConnection(path, timeout)

        transport = _Transport(connection_factory, path)
    else:
        transport = _Transport(connection_factory, None)
    status, summary = transport.request("GET", "/v1/grant")
    if status != 200:
        raise _error(status, summary)
    if not summary.get("active"):
        reason = summary.get("ended_reason") or "it has ended"
        raise GrantEnded(f"The device grant isn't active ({reason}).", code="ended", status=status)
    return Devices(transport, summary)
