"""ControlCenterClient against the real Control Center (in-process), and its retry rules."""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from labnet.automation_runner.client import ControlCenterClient, RunnerApiError, truncate_text

from .supervisor_fakes import TOKEN, control_center, runner_client, runner_settings


@pytest.fixture
def lab(tmp_path: Path):
    with control_center(tmp_path) as (people, token):
        client = runner_client(people, token, tmp_path, retry_delays=())
        people.runner = client
        try:
            yield people
        finally:
            client.close()


def _queue(people, name: str = "AOM study") -> tuple[dict, str]:
    project = people.post("/api/automation/projects", json={"name": name}).json()["project"]
    run = people.post(f"/api/automation/projects/{project['id']}/runs", json={"task": "Tasks/Task1.md"}).json()["run"]
    return run, f"/api/automation/projects/{project['id']}"


def test_a_run_through_the_runner_api(lab, tmp_path: Path) -> None:
    client: ControlCenterClient = lab.runner
    assert client.whoami() == "lab-runner"
    assert client.claim() is None  # 204: nothing queued

    queued, api = _queue(lab)
    run = client.claim()
    assert run["id"] == queued["id"] and run["status"] == "active" and run["settings"]["max_turns"] == 60
    assert client.get_run(run["id"])["claimed_by"] == "lab-runner"

    archive = tmp_path / "inputs.zip"
    client.download_inputs(run["id"], archive)
    with zipfile.ZipFile(archive) as opened:
        manifest = json.loads(opened.read("run.json"))
        assert "Tasks/Task1.md" in opened.namelist()
    assert manifest["run"]["id"] == run["id"] and manifest["settings"]["model"] == "sonnet"

    # Uploads stream from a file; every path segment is quoted (spaces, Unicode, '#').
    figure = tmp_path / "figure.png"
    figure.write_bytes(b"\x89PNG" + bytes(range(256)) * 300)
    reply = client.upload(run["id"], "Analysis/outputs/fit 1 – µ #2.png", figure)
    assert reply["file"]["path"] == "Analysis/outputs/fit 1 – µ #2.png" and reply["bytes_uploaded"] == figure.stat().st_size
    assert lab.get(f"{api}/files/Analysis/outputs/fit 1 – µ %232.png").content == figure.read_bytes()

    for refused, status in (("Tasks/Task1.md", 403), ("Analysis/CLAUDE.md", 403), ("Analysis/outputs", 403)):
        with pytest.raises(RunnerApiError) as error:
            client.upload(run["id"], refused, figure)
        assert error.value.status_code == status, refused
    with pytest.raises(ValueError):
        client.upload(run["id"], "Analysis/../Tasks/x.md", figure)

    # Events: more than one request's worth, each text cut to 16 KiB instead of refused.
    events = [{"kind": "assistant", "text": f"{index}: " + "x" * 9000} for index in range(130)]
    events.append({"kind": "tool", "text": "y" * 40_000})
    assert client.events(run["id"], events) == "active"
    stored = lab.get(f"{api}/runs/{run['id']}").json()["events"]
    assert len(stored) == 131 and stored[0]["text"].startswith("0: ") and stored[129]["text"].startswith("129: ")
    assert len(stored[-1]["text"].encode()) <= 16 * 1024 and stored[-1]["text"].endswith("(truncated)")

    assert client.heartbeat(run["id"]) == "active"
    finished = client.finish(run["id"], "done", "All good.")
    assert finished["status"] == "done" and finished["summary"] == "All good."
    with pytest.raises(RunnerApiError) as late:
        client.heartbeat(run["id"])
    assert late.value.status_code == 409 and late.value.run_status == "done"


def test_a_cancel_reaches_the_runner_as_409_with_the_status(lab) -> None:
    client: ControlCenterClient = lab.runner
    queued, api = _queue(lab)
    run = client.claim()
    assert lab.post(f"{api}/runs/{run['id']}/cancel", json={}).status_code == 200
    for call in (lambda: client.heartbeat(run["id"]), lambda: client.events(run["id"], [{"kind": "log", "text": "x"}]),
                 lambda: client.finish(run["id"], "done", "")):
        with pytest.raises(RunnerApiError) as error:
            call()
        assert error.value.status_code == 409 and error.value.run_status == "cancelled"
        assert "cancelled" in error.value.detail


def test_the_literature_tools_backends(lab) -> None:
    client: ControlCenterClient = lab.runner
    assert lab.post("/api/automation/library/index", json={}).status_code == 202
    deadline = time.monotonic() + 60
    while lab.app.state.literature.status()["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    _queue(lab)
    run = client.claim()
    papers = client.literature_papers(run["id"])
    assert "lasers/pdh-notes.txt" in json.dumps(papers)
    found = client.literature_search(run["id"], "Pound Drever Hall", k=3)
    assert "lasers/pdh-notes.txt" in json.dumps(found)
    pages = client.literature_pages(run["id"], "lasers/pdh-notes.txt", 1, 1)
    assert "error signal" in json.dumps(pages)


def test_a_wrong_token_is_refused(tmp_path: Path) -> None:
    with control_center(tmp_path) as (people, _token):
        client = runner_client(people, TOKEN, tmp_path, retry_delays=())
        with pytest.raises(RunnerApiError) as error:
            client.whoami()
        assert error.value.status_code == 401
        client.close()


# ---- retries ----------------------------------------------------------------------------------------


def _client(tmp_path: Path, handler, sleeps: list[float]) -> ControlCenterClient:
    return ControlCenterClient(runner_settings(tmp_path), transport=httpx.MockTransport(handler),
                               retry_delays=(0.1, 0.2, 0.4), sleep=sleeps.append)


def test_gateway_errors_and_failed_connections_are_retried(tmp_path: Path) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if len(calls) == 1:
            raise httpx.ConnectError("refused", request=request)
        if len(calls) < 4:
            return httpx.Response(503 if len(calls) == 2 else 502)
        return httpx.Response(200, json={"status": "active"})

    assert _client(tmp_path, handler, sleeps).heartbeat("run-20261007-120000-abcd") == "active"
    assert calls == ["POST"] * 4 and sleeps == [0.1, 0.2, 0.4]


def test_refusals_are_never_retried_and_retries_end(tmp_path: Path) -> None:
    sleeps: list[float] = []
    calls: list[int] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, json={"detail": "No such run."})

    with pytest.raises(RunnerApiError) as error:
        _client(tmp_path, refuse, sleeps).get_run("run-20261007-120000-abcd")
    assert error.value.status_code == 404 and error.value.detail == "No such run." and calls == [1] and sleeps == []

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RunnerApiError) as error:
        _client(tmp_path, down, sleeps).claim()
    assert error.value.status_code == 0 and sleeps == [0.1, 0.2, 0.4]

    def always_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    with pytest.raises(RunnerApiError) as error:
        _client(tmp_path, always_503, []).whoami()
    assert error.value.status_code == 503


def test_a_post_that_may_have_arrived_is_not_sent_again(tmp_path: Path) -> None:
    calls: list[str] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise httpx.ReadTimeout("slow", request=request)

    client = _client(tmp_path, timeout, [])
    with pytest.raises(RunnerApiError):
        client.finish("run-20261007-120000-abcd", "done", "")
    assert calls == ["POST"]
    calls.clear()
    with pytest.raises(RunnerApiError):
        client.get_run("run-20261007-120000-abcd")
    assert calls == ["GET"] * 4  # a GET can be repeated


def test_an_upload_is_streamed_again_on_retry(tmp_path: Path) -> None:
    bodies: list[bytes] = []
    file = tmp_path / "data.csv"
    file.write_bytes(b"a,b\n" * 100_000)

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        assert request.headers["content-length"] == str(file.stat().st_size)
        assert request.url.raw_path.endswith(b"/files/Data/new_data/my%20data.csv")
        if len(bodies) == 1:
            return httpx.Response(502)
        return httpx.Response(200, json={"file": {"path": "Data/new_data/my data.csv"}})

    _client(tmp_path, handler, []).upload("run-20261007-120000-abcd", "Data/new_data/my data.csv", file)
    assert bodies == [file.read_bytes()] * 2


def test_events_are_split_by_count_and_size(tmp_path: Path) -> None:
    requests: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert len(body) <= 256 * 1024
        requests.append(json.loads(body)["events"])
        return httpx.Response(200, json={"status": "active"})

    client = _client(tmp_path, handler, [])
    client.events("run-20261007-120000-abcd", [{"kind": "log", "text": "x"}] * 250)
    assert [len(batch) for batch in requests] == [100, 100, 50]
    requests.clear()
    client.events("run-20261007-120000-abcd", [{"kind": "log", "text": "é" * 8000}] * 40)  # 16 KB each
    assert sum(len(batch) for batch in requests) == 40 and len(requests) >= 3


def test_truncate_text_counts_utf8_bytes() -> None:
    assert truncate_text("short") == "short"
    cut = truncate_text("µ" * 20_000, 1000)
    assert len(cut.encode()) <= 1000 and cut.endswith("(truncated)")


def test_download_streams_to_the_file(tmp_path: Path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("run.json", "{}")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload.getvalue(), headers={"content-type": "application/zip"})

    target = tmp_path / "in.zip"
    _client(tmp_path, handler, []).download_inputs("run-20261007-120000-abcd", target)
    assert target.read_bytes() == payload.getvalue()
