"""The run image's labnet_devices shim (docker/automation/labnet_devices.py), against a fake relay and the real
relay handler: keyword-only calls, what goes over the wire, results, and errors by the gateway's code."""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import socket
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from labnet.automation_runner.devices_relay import DeviceRelay, Experiment, make_relay_handler

from .test_experiment import GRANT, GRANT_TOKEN, PROTOCOL, LabClient


SHIM = Path(__file__).resolve().parents[3] / "docker" / "automation" / "labnet_devices.py"


def _load():
    spec = importlib.util.spec_from_file_location("labnet_devices", SHIM)
    module = importlib.util.module_from_spec(spec)
    sys.modules["labnet_devices"] = module  # importable by name, as in the run image
    spec.loader.exec_module(module)
    return module


labnet_devices = _load()

SUMMARY = {"active": True, "grant": {"id": "grant-1", "ends_at": "2999-01-01T00:00:00Z"}, "seconds_left": 1200.0,
           "devices": {"dev01-aom": {"device_type": "dummy_aom", "methods": {"set_frequency": {}, "read_x": {}}}},
           "safe_state": [], "ended_reason": ""}


class FakeRelay:
    """Answers like the device relay; records each request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict | None, dict]] = []
        self.summary = dict(SUMMARY)
        self.answers: list[tuple[int, dict | bytes]] = []

    def handler(self):
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, status, payload):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                relay.requests.append(("GET", self.path, None, dict(self.headers)))
                self._answer(200, relay.summary)

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                relay.requests.append(("POST", self.path, body, dict(self.headers)))
                status, payload = relay.answers.pop(0) if relay.answers else (
                    200, {"result": 0.5, "calls_left": 10, "seconds_left": 100.0})
                self._answer(status, payload)

            def log_message(self, *_args):
                pass

        return Handler


@pytest.fixture
def fake():
    relay = FakeRelay()
    server = ThreadingHTTPServer(("127.0.0.1", 0), relay.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    relay.factory = lambda: http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    yield relay
    server.shutdown()
    server.server_close()


def test_connect_lists_the_granted_devices_and_calls_send_keyword_arguments(fake):
    devices = labnet_devices.connect(connection_factory=fake.factory)
    assert list(devices) == ["dev01-aom"] and len(devices) == 1 and "dev01-aom" in devices
    aom = devices["dev01-aom"]
    assert "set_frequency" in dir(aom) and "dummy_aom" in repr(aom)

    assert aom.set_frequency(value=80.0) == 0.5
    fake.answers.append((200, {"result": [0.1, 0.2, 0.3], "calls_left": 9, "seconds_left": 90.0}))
    assert aom.stream_x(channel="CH1", sample_rate_hz=10, sample_count=3) == [0.1, 0.2, 0.3]
    assert devices.last_reply == {"result": [0.1, 0.2, 0.3], "calls_left": 9, "seconds_left": 90.0}
    assert aom.read_x() == 0.5

    posts = [(path, body) for method, path, body, _headers in fake.requests if method == "POST"]
    assert posts == [
        ("/v1/call", {"device_id": "dev01-aom", "method": "set_frequency", "args": {"value": 80.0}}),
        ("/v1/call", {"device_id": "dev01-aom", "method": "stream_x", "sample_rate_hz": 10, "sample_count": 3,
                      "args": {"channel": "CH1"}}),
        ("/v1/call", {"device_id": "dev01-aom", "method": "read_x", "args": {}}),
    ]
    assert all("Authorization" not in headers for *_rest, headers in fake.requests)  # the shim holds no token
    assert devices.grant()["seconds_left"] == 1200.0


def test_positional_arguments_nan_and_unknown_devices_never_reach_the_relay(fake):
    devices = labnet_devices.connect(connection_factory=fake.factory)
    aom = devices["dev01-aom"]
    with pytest.raises(TypeError, match="keyword arguments only"):
        aom.set_frequency(80.0)
    with pytest.raises(ValueError, match="finite"):
        aom.set_frequency(value=float("nan"))
    with pytest.raises(ValueError):
        aom.set_frequency(value=[float("inf")])
    with pytest.raises(KeyError, match="granted devices are: dev01-aom"):
        devices["dev02-laser"]
    with pytest.raises(AttributeError):
        aom._private  # noqa: B018
    assert not any(method == "POST" for method, *_rest in fake.requests)


@pytest.mark.parametrize("status, reply, kind", [
    (403, {"detail": "value 95 is above the approved maximum 90", "code": "refused"}, "Refused"),
    (409, {"detail": "dev01-aom is busy", "code": "busy"}, "Busy"),
    (409, {"detail": "The grant has expired.", "code": "expired"}, "GrantEnded"),
    (409, {"detail": "The grant was stopped by bob.", "code": "ended"}, "GrantEnded"),
    (502, {"detail": "The AOM didn't answer.", "code": "device_error"}, "DeviceError"),
    (502, {"detail": "The Control Center couldn't be reached.", "code": "unavailable"}, "GatewayUnavailable"),
    (400, {"detail": "Something else."}, "Refused"),
    (503, b"<html>down</html>", "GatewayUnavailable"),
])
def test_errors_raise_by_the_gateways_code_with_its_detail(fake, status, reply, kind):
    aom = labnet_devices.connect(connection_factory=fake.factory)["dev01-aom"]
    fake.answers.append((status, reply))
    with pytest.raises(getattr(labnet_devices, kind)) as error:
        aom.set_frequency(value=95.0)
    assert isinstance(error.value, labnet_devices.LabnetDevicesError)
    if isinstance(reply, dict):
        assert error.value.detail == reply["detail"] and error.value.code == reply.get("code")
        assert error.value.status == status
    assert issubclass(labnet_devices.Busy, labnet_devices.Refused)


def test_connect_without_an_active_grant(fake, tmp_path):
    fake.summary = {**SUMMARY, "active": False, "ended_reason": "expired"}
    with pytest.raises(labnet_devices.GrantEnded, match="expired"):
        labnet_devices.connect(connection_factory=fake.factory)
    if hasattr(socket, "AF_UNIX"):
        with pytest.raises(labnet_devices.GrantEnded, match="doesn't exist"):
            labnet_devices.connect(str(tmp_path / "missing.sock"))


def test_the_shim_through_the_real_relay(tmp_path):
    """Script → labnet_devices → the relay's HTTP handler → the gateway (a fake client)."""

    client, experiment = LabClient(), Experiment()
    relay = DeviceRelay(experiment, client, tmp_path / "gateway.sock", lambda *_: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_relay_handler(relay))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    factory = lambda: http.client.HTTPConnection("127.0.0.1", port, timeout=10)  # noqa: E731
    try:
        with pytest.raises(labnet_devices.GrantEnded):
            labnet_devices.connect(connection_factory=factory)  # no grant yet
        experiment.activate(GRANT_TOKEN, GRANT, PROTOCOL, tmp_path)
        devices = labnet_devices.connect(connection_factory=factory)
        assert list(devices) == ["dev01-aom"] and "set_frequency" in dir(devices["dev01-aom"])
        assert devices["dev01-aom"].set_frequency(value=80.0) == 0.42
        assert client.calls[-1][1:3] == (GRANT_TOKEN, {"device_id": "dev01-aom", "method": "set_frequency",
                                                       "args": {"value": 80.0}})
        client.gateway = (403, {"detail": "value 95 is above 90", "code": "refused"})
        with pytest.raises(labnet_devices.Refused, match="above 90"):
            devices["dev01-aom"].set_frequency(value=95.0)
        experiment.end("Stopped by bob", confirmed=True)
        with pytest.raises(labnet_devices.GrantEnded):
            devices["dev01-aom"].read_diffraction_efficiency()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(not hasattr(socketserver, "ThreadingUnixStreamServer"), reason="needs Unix sockets")
def test_the_default_transport_is_the_unix_socket(tmp_path):
    relay = FakeRelay()

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    path = str(tmp_path / "gateway.sock")

    class Handler(relay.handler()):
        def address_string(self):
            return "test"

    server = Server(path, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        devices = labnet_devices.connect(path, timeout=10)
        assert devices["dev01-aom"].set_frequency(value=1.0) == 0.5
    finally:
        server.shutdown()
        server.server_close()
        os.unlink(path)
    with pytest.raises(labnet_devices.GrantEnded):  # the socket is gone: the grant ended
        devices["dev01-aom"].set_frequency(value=1.0)
