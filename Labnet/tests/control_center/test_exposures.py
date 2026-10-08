"""Existing exposures closed in Phase 1.5: the script worker's environment, lease ids, the Experiments API."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labnet.control_center import script_runner
from labnet.control_center.app import create_app
from labnet.control_center.config import ControlCenterSettings
from labnet.redaction import redact
from labnet.control_center.script_runner import ScriptProcess, worker_environment
from labnet.control_center.sessions import DeviceSession, SessionError

from .test_device_sessions import PLAY, FakeCentral, FakeWavemeter, _settings
from .test_experiments import FakeCentral as ExperimentsCentral, scan


HEX = "0123456789abcdef" * 2
LEASE = f"lease_{HEX}"
SECRETS = {
    "LABNET_API_KEY": "labnet_" + "k" * 32,
    "LABNET_CONTROL_CENTER_PROXY_SECRET": "p" * 40,
    "ANTHROPIC_API_KEY": "sk-ant-" + "a" * 32,
    "ANTHROPIC_AUTH_TOKEN": "t" * 32,
    "CLAUDE_CODE_OAUTH_TOKEN": "o" * 32,
    "GITHUB_TOKEN": "g" * 32,
    "SOME_SERVICE_SECRET": "s" * 32,
}


# ---- the script worker's environment ----------------------------------------------


def test_the_worker_environment_is_an_allow_list() -> None:
    env = worker_environment(
        {
            **SECRETS,
            "PATH": "/usr/bin",
            "SystemRoot": r"C:\Windows",  # Windows keeps names case-insensitively
            "TEMP": "/tmp",
            "LC_ALL": "C.UTF-8",
            "PYTHONPATH": "/opt/labnet",
            "PYTHONIOENCODING": "latin-1",
        }
    )

    assert env == {
        "PATH": "/usr/bin",
        "SystemRoot": r"C:\Windows",
        "TEMP": "/tmp",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": "/opt/labnet",
        "PYTHONIOENCODING": "utf-8",
    }


def test_a_python_started_with_the_worker_environment_sees_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)

    seen = subprocess.run(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        env=worker_environment(), capture_output=True, text=True, timeout=60, check=True,
    )
    child = json.loads(seen.stdout)

    assert not set(SECRETS) & {name.upper() for name in child}
    assert not any(value in child.values() for value in SECRETS.values())
    assert not any(name.upper().startswith(("LABNET_", "ANTHROPIC_", "CLAUDE_")) for name in child)


@pytest.mark.asyncio
async def test_a_real_worker_starts_with_the_cleaned_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    started: list[dict] = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        started.append(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(script_runner.subprocess, "Popen", recording_popen)
    error, output = await _run_script("print('hello', device.read_wavelength())")

    assert error is None and output == ["hello 780.5"]
    env = started[0]["env"]
    assert not set(SECRETS) & {name.upper() for name in env}
    assert env["PYTHONIOENCODING"] == "utf-8"


# ---- redaction ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Lease {LEASE!r} expired before it could be renewed.", "Lease 'lease_…' expired before it could be renewed."),
        (f"lease_{HEX.upper()} and {LEASE}", "lease_… and lease_…"),
        ("bad key labnet_" + "Ab3_-" * 6, "bad key labnet_…"),
        ("runner lnr_" + "x" * 24 + " refused", "runner lnr_… refused"),
        ("lease_abc and labnet_short stay", "lease_abc and labnet_short stay"),
        ("No device 'dev02-wavemeter' is registered.", "No device 'dev02-wavemeter' is registered."),
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert redact(text) == expected


class LeakyWavemeter(FakeWavemeter):
    """A device whose errors quote the lease, as Lab PC and lease-renewal errors do."""

    async def set_exposure(self, value: float):
        raise RuntimeError(f"Lease {LEASE!r} expired before it could be renewed.")


class LeakyStreamWavemeter(FakeWavemeter):
    """Settings work; the stream then fails with the lease in its error."""

    def stream_wavelength(self, *, sample_rate_hz: float, **_ignored):
        return LeakyStream()


class LeakyStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0.05)
        raise RuntimeError(f"Lease {LEASE!r} is no longer active.")

    async def aclose(self) -> None:
        pass


class LeakyCentral(FakeCentral):
    def __init__(self, wavemeter: type[FakeWavemeter] = LeakyWavemeter) -> None:
        super().__init__()
        self.wavemeter = wavemeter

    async def acquire_device(self, contract, device_id: str):
        self.log.append("lease acquired")
        return self.wavemeter(self.log)


def _events(viewer) -> list[dict]:
    return [viewer.queue.get_nowait() for _ in range(viewer.queue.qsize())]


def _assert_no_lease(*values) -> None:
    text = json.dumps(values)
    assert HEX not in text and HEX.upper() not in text


@pytest.mark.asyncio
async def test_a_device_error_is_redacted_in_the_session_error_and_events() -> None:
    session = DeviceSession("dev02-wavemeter", LeakyCentral(), _settings())
    viewer = session.subscribe()

    with pytest.raises(SessionError) as failure:
        await session.play(PLAY)

    assert failure.value.status_code == 502 and "lease_…" in str(failure.value)
    events = _events(viewer)
    assert any("lease_…" in (event.get("message") or "") for event in events)
    _assert_no_lease(str(failure.value), events, session.snapshot_event())


@pytest.mark.asyncio
async def test_a_stream_stopped_by_a_lease_error_is_redacted() -> None:
    session = DeviceSession("dev02-wavemeter", LeakyCentral(LeakyStreamWavemeter), _settings())
    viewer = session.subscribe()

    await session.play(PLAY)
    for _ in range(50):
        if session.state_event()["state"] == "error":
            break
        await asyncio.sleep(0.05)

    state = session.state_event()
    assert state["state"] == "error" and "lease_…" in state["message"]
    _assert_no_lease(_events(viewer), state)


@pytest.mark.asyncio
async def test_a_failed_script_call_is_redacted_on_the_page() -> None:
    session = DeviceSession("dev02-wavemeter", LeakyCentral(), _settings())
    viewer = session.subscribe()

    await session.run_script("print('lease_" + HEX + "')\ndevice.set_exposure(20)")
    for _ in range(200):
        if not session.state_event()["script_running"]:
            break
        await asyncio.sleep(0.05)

    events = _events(viewer)
    error = next(event for event in events if event["type"] == "script" and event["kind"] == "error")
    assert error["text"].startswith("Line 2:") and "lease_…" in error["text"]
    _assert_no_lease(events)


def test_a_device_error_is_redacted_in_the_http_reply() -> None:
    app = create_app(_settings(), client_factory=lambda _: LeakyCentral())
    with TestClient(app) as browser:
        reply = browser.post("/api/devices/dev02-wavemeter/play", json=PLAY)
        state = browser.get("/api/status")  # still answers; nothing leaked into the snapshot either

    assert reply.status_code == 502 and "lease_…" in reply.json()["detail"]
    _assert_no_lease(reply.text, state.text, app.state.sessions.get("dev02-wavemeter").snapshot_event())


async def _run_script(code: str, on_call=None) -> tuple[str | None, list[str]]:
    loop = asyncio.get_running_loop()
    events: list[dict] = []
    finished: asyncio.Future = loop.create_future()

    async def default_call(handle, method, args, kwargs):
        return 780.5

    async def on_lease(device_id):
        raise ValueError("no leases in this test")

    async def on_finished(error):
        finished.set_result(error)

    process = ScriptProcess(
        code=code,
        devices={"device": {"label": "dev02-wavemeter", "methods": ["read_wavelength", "set_exposure"]}},
        on_call=on_call or default_call,
        on_lease=on_lease,
        on_event=events.append,
        on_finished=on_finished,
        loop=loop,
        timeout_seconds=20.0,
    )
    process.start()
    error = await asyncio.wait_for(finished, timeout=30)
    return error, [event["text"] for event in events if event["kind"] == "output"]


@pytest.mark.asyncio
async def test_a_script_sees_a_redacted_call_error() -> None:
    async def failing():
        raise RuntimeError(f"Lease {LEASE!r} is no longer active.")

    loop = asyncio.get_running_loop()
    process = ScriptProcess(
        code="", devices={}, on_call=None, on_lease=None, on_event=lambda _event: None,
        on_finished=None, loop=loop, timeout_seconds=1.0,
    )
    reply = await asyncio.to_thread(process._run_on_loop, failing())
    assert reply == {"error": "RuntimeError: Lease 'lease_…' is no longer active."}

    async def leaky_call(handle, method, args, kwargs):
        raise RuntimeError(f"Lease {LEASE!r} is no longer active.")

    error, _output = await _run_script("x = 1\ndevice.set_exposure(1)", on_call=leaky_call)
    assert error == "Line 2: RuntimeError: Lease 'lease_…' is no longer active."


# ---- the Experiments API ------------------------------------------------------------------


@pytest.fixture
def browser(tmp_path: Path):
    settings = ControlCenterSettings(
        server_url="http://127.0.0.1:8008", api_key="key", ca_file=None, bind_host="127.0.0.1", port=8080,
        refresh_seconds=60.0, experiments_dir=tmp_path / "experiments",
    )
    with TestClient(create_app(settings, client_factory=lambda _: ExperimentsCentral())) as client:
        yield client


CROSS_SITE = {"Sec-Fetch-Site": "cross-site"}


def test_experiment_changes_from_another_site_are_refused(browser: TestClient) -> None:
    created = browser.post("/api/experiments", json=scan(), headers={"Sec-Fetch-Site": "same-origin"})
    assert created.status_code == 201
    experiment = created.json()["experiment"]
    address = f"/api/experiments/{experiment['id']}"

    refused = [
        browser.post("/api/experiments", json=scan(), headers=CROSS_SITE),
        browser.post("/api/experiments/validate", json=scan(), headers=CROSS_SITE),
        browser.put(address, json={**experiment, "name": "Hijacked"}, headers=CROSS_SITE),
        browser.delete(f"{address}?version=1", headers=CROSS_SITE),
        browser.post("/api/experiments", json=scan(), headers={"Sec-Fetch-Site": "same-site"}),
    ]

    assert [reply.status_code for reply in refused] == [403] * 5
    assert "own pages" in refused[0].json()["detail"]
    assert [item["id"] for item in browser.get("/api/experiments").json()["experiments"]] == [experiment["id"]]
    assert browser.get(address).json()["experiment"]["name"] == "AOM frequency scan"
    assert browser.delete(f"{address}?version=1", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 204


def test_experiment_changes_must_be_json(browser: TestClient) -> None:
    experiment = browser.post("/api/experiments", json=scan()).json()["experiment"]
    body = json.dumps(scan())

    for method, address in [
        ("POST", "/api/experiments"),
        ("POST", "/api/experiments/validate"),
        ("PUT", f"/api/experiments/{experiment['id']}"),
    ]:
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
            reply = browser.request(method, address, content=body, headers={"content-type": content_type})
            assert reply.status_code == 415, (method, address, content_type)
            assert "application/json" in reply.json()["detail"]

    assert browser.post("/api/experiments/validate", json=scan()).json()["report"]["exportable"] is True
    charset = browser.post(
        "/api/experiments", content=body, headers={"content-type": "application/json; charset=utf-8"}
    )
    assert charset.status_code == 201
    assert browser.post("/api/experiments", json=[scan()]).json()["detail"] == "Send an experiment as a JSON object."


def test_the_experiments_api_refuses_other_sites_by_itself(tmp_path: Path) -> None:
    """The route checks hold even without HumanIngress in front (it refuses cross-site changes first)."""

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from labnet.control_center.experiment_api import create_experiment_router
    from labnet.control_center.experiment_store import ExperimentError, ExperimentStore

    async def offline() -> dict:
        return {"lab_pcs": [], "central": {"ok": False}}

    app = FastAPI()
    app.state.experiments = ExperimentStore(tmp_path / "experiments")
    app.include_router(create_experiment_router(offline))

    @app.exception_handler(ExperimentError)
    async def error(_request, exc: ExperimentError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    with TestClient(app) as client:
        assert client.post("/api/experiments", json=scan(), headers=CROSS_SITE).status_code == 403
        assert client.post("/api/experiments/validate", json=scan(), headers=CROSS_SITE).status_code == 403
        assert client.post("/api/experiments", content=json.dumps(scan()),
                           headers={"Content-Type": "text/plain"}).status_code == 415
        assert client.get("/api/experiments").json()["experiments"] == []
        assert client.post("/api/experiments", json=scan()).status_code == 201


def test_deeply_nested_json_is_refused_not_a_server_error(browser: TestClient) -> None:
    nested = "[" * 100_000 + "]" * 100_000  # 200 KB, under the size cap
    reply = browser.post("/api/experiments", content=nested, headers={"Content-Type": "application/json"})
    assert reply.status_code == 422


class CentralWithLeakyError(ExperimentsCentral):
    async def list_lab_pcs(self):
        raise RuntimeError(f"lease lease_{'ab' * 16} expired; key labnet_{'k' * 43}")


def test_centrals_error_is_redacted_on_the_status_page(tmp_path: Path) -> None:
    settings = ControlCenterSettings(
        server_url="http://127.0.0.1:8008", api_key="key", ca_file=None, bind_host="127.0.0.1", port=8080,
        refresh_seconds=60.0, experiments_dir=tmp_path / "experiments",
    )
    with TestClient(create_app(settings, client_factory=lambda _: CentralWithLeakyError())) as client:
        central = client.get("/api/status").json()["central"]
    assert central["ok"] is False
    assert "lease_…" in central["error"] and "labnet_…" in central["error"]
    assert "ab" * 16 not in central["error"] and "k" * 43 not in central["error"]


def test_claude_credentials_are_redacted() -> None:
    assert redact("token sk-ant-oat01-" + "a" * 40 + " end") == "token sk-ant-… end"
