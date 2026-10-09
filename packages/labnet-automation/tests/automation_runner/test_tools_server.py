"""The tools server: literature forwarding, run_python's checks/limits/logs, and its HTTP transports."""

from __future__ import annotations

import os
import socket
import stat
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from labnet_automation.backend import PythonRequest, PythonResult
from labnet_automation.client import RunnerApiError
from labnet_automation.config import RunnerSettings
from labnet_automation.tools_server import TAIL_CHARS, Tools, ToolsServer, make_handler


RUN = {"id": "run-20261007-120000-abcd", "project_id": "p1", "task": "Tasks/Task1.md"}
RUN_ID = RUN["id"]
SCRIPT = "ScriptsUtilized/analysis_scripts/fit.py"


def settings(**overrides) -> RunnerSettings:
    values = dict(control_center_url="https://control-center.example", token="lnr_" + "A" * 43,
                  python_timeout_seconds=30.0)
    values.update(overrides)
    return RunnerSettings(**values)


class FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    def _answer(self, call, result):
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return result

    def literature_search(self, run_id, query, k=10, paper=None):
        return self._answer(("search", run_id, query, k, paper),
                            {"query": query, "mode": "all", "results": [{"cite": "a.pdf p.3"}], "notes": []})

    def literature_papers(self, run_id):
        return self._answer(("papers", run_id), {"papers": [{"path": "a.pdf"}], "notes": []})

    def literature_pages(self, run_id, paper, first, last):
        return self._answer(("pages", run_id, paper, first, last), {"path": paper, "page_texts": []})


class FakeBackend:
    def __init__(self, result: PythonResult | None = None, delay: float = 0.0):
        self.requests: list[PythonRequest] = []
        self.result = result or PythonResult(exit_code=0, stdout="fit ok\n", stderr="", seconds=1.234, timed_out=False)
        self.delay = delay
        self.active = 0
        self.most_active = 0
        self.error: Exception | None = None
        self._lock = threading.Lock()

    def run_python(self, request: PythonRequest) -> PythonResult:
        with self._lock:
            self.active += 1
            self.most_active = max(self.most_active, self.active)
        try:
            self.requests.append(request)
            if self.error:
                raise self.error
            time.sleep(self.delay)
            return self.result
        finally:
            with self._lock:
                self.active -= 1

    def start_agent(self, spec):  # pragma: no cover - not used here
        raise AssertionError

    def stop_run(self, run_id):  # pragma: no cover
        raise AssertionError


@pytest.fixture
def work(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    (root / "ScriptsUtilized" / "analysis_scripts").mkdir(parents=True)
    (root / "ScriptsUtilized" / "logs").mkdir()
    (root / "Data").mkdir()
    (root / SCRIPT).write_text("print('fit ok')\n", encoding="utf-8")
    return root


@pytest.fixture
def env(work):
    client, backend, events = FakeClient(), FakeBackend(), []
    tools = Tools(RUN, work, client, backend, settings(), lambda kind, text: events.append((kind, text)))
    return tools, client, backend, events


# ---- literature -------------------------------------------------------------------


def test_search_forwards_to_the_control_center(env):
    tools, client, _backend, _events = env
    status, body = tools.call("/v1/search_literature", {"query": "Pound-Drever-Hall", "k": 5, "paper": "a.pdf"})
    assert status == 200 and body["results"] == [{"cite": "a.pdf p.3"}]
    assert client.calls == [("search", RUN_ID, "Pound-Drever-Hall", 5, "a.pdf")]
    assert tools.call("/v1/search_literature", {"query": "x"})[0] == 200
    assert client.calls[-1] == ("search", RUN_ID, "x", 10, None)


@pytest.mark.parametrize("data", [
    {}, {"query": ""}, {"query": 3}, {"query": "x", "k": 0}, {"query": "x", "k": 51}, {"query": "x", "k": True},
    {"query": "x", "k": "5"}, {"query": "x", "paper": 1}, {"query": "x", "run_id": "other"}, {"query": "q" * 1001},
])
def test_search_refuses_bad_requests(env, data):
    tools, client, _backend, _events = env
    status, body = tools.call("/v1/search_literature", data)
    assert status == 400 and body["error"]
    assert client.calls == []


def test_list_papers_and_read_pages(env):
    tools, client, _backend, _events = env
    assert tools.call("/v1/list_papers", {}) == (200, {"papers": [{"path": "a.pdf"}], "notes": []})
    assert tools.call("/v1/read_paper_pages", {"paper": "a.pdf", "first": 2, "last": 4})[0] == 200
    assert tools.call("/v1/read_paper_pages", {"paper": "a.pdf", "first": 7})[0] == 200
    assert client.calls[1:] == [("pages", RUN_ID, "a.pdf", 2, 4), ("pages", RUN_ID, "a.pdf", 7, 7)]
    for bad in ({"paper": "a.pdf", "first": 0}, {"paper": "a.pdf", "first": 3, "last": 2}, {"first": 1},
                {"paper": "a.pdf", "first": 1.5}):
        assert tools.call("/v1/read_paper_pages", bad)[0] == 400
    assert tools.call("/v1/list_papers", {"x": 1})[0] == 400


def test_control_center_errors_become_tool_errors(env):
    tools, client, _backend, _events = env
    client.error = RunnerApiError(404, "That paper isn't in this project's literature.")
    status, body = tools.call("/v1/read_paper_pages", {"paper": "nope.pdf", "first": 1})
    assert status == 404 and "isn't in this project's literature" in body["error"]
    client.error = RunnerApiError(409, "The run is cancelled.")
    assert tools.call("/v1/list_papers", {})[0] == 409
    client.error = RunnerApiError(503, "busy")
    assert tools.call("/v1/list_papers", {})[0] == 502
    client.error = httpx.ConnectError("refused")
    status, body = tools.call("/v1/search_literature", {"query": "x"})
    assert status == 502 and "couldn't be reached" in body["error"]


def test_unknown_routes_and_bodies(env):
    tools = env[0]
    assert tools.call("/v1/shell", {})[0] == 404
    assert tools.call("/v1/list_papers", ["not", "an", "object"])[0] == 400


def test_run_must_have_a_run_id(work):
    with pytest.raises(ValueError):
        Tools({"id": "../x"}, work, FakeClient(), FakeBackend(), settings(), lambda *_: None)


# ---- run_python ---------------------------------------------------------------------


def test_run_python_runs_the_script_and_logs_it(env, work):
    tools, _client, backend, events = env
    status, body = tools.call("/v1/run_python", {"script": SCRIPT, "args": ["--n", "3"], "timeout_seconds": 12})
    assert status == 200
    assert body == {"exit_code": 0, "timed_out": False, "seconds": 1.23, "stdout_tail": "fit ok\n", "stderr_tail": "",
                    "log": f"ScriptsUtilized/logs/{RUN_ID}/01-fit.log"}
    (request,) = backend.requests
    assert request == PythonRequest(run_id=RUN_ID, work=work, script=SCRIPT, args=["--n", "3"], timeout_seconds=12.0)
    log = (work / body["log"]).read_text(encoding="utf-8")
    assert log.startswith(f"# run_python {SCRIPT} --n 3\n")
    assert "exit 0 in 1.2 s" in log and "# ---- stdout ----\nfit ok\n# ---- stderr ----\n" in log
    assert events == [("tool", f"run_python {SCRIPT} → exit 0 in 1.2 s")]


def test_run_python_timeouts_are_capped(env):
    tools, _client, backend, _events = env
    tools.call("/v1/run_python", {"script": SCRIPT})
    tools.call("/v1/run_python", {"script": SCRIPT, "timeout_seconds": 10**9})
    tools.call("/v1/run_python", {"script": SCRIPT, "timeout_seconds": 2.5})
    assert [r.timeout_seconds for r in backend.requests] == [30.0, 30.0, 2.5]  # settings cap: 30 s


def test_run_python_log_numbers_count_up_and_never_overwrite(env, work):
    tools = env[0]
    folder = work / "ScriptsUtilized" / "logs" / RUN_ID
    folder.mkdir(parents=True)
    (folder / "01-fit.log").write_text("earlier", encoding="utf-8")
    logs = [tools.call("/v1/run_python", {"script": SCRIPT})[1]["log"] for _ in range(2)]
    assert logs == [f"ScriptsUtilized/logs/{RUN_ID}/02-fit.log", f"ScriptsUtilized/logs/{RUN_ID}/03-fit.log"]
    assert (folder / "01-fit.log").read_text(encoding="utf-8") == "earlier"


def test_run_python_tails_are_capped_and_the_log_is_whole(work):
    long = "".join(f"line {n}\n" for n in range(5000))
    backend = FakeBackend(PythonResult(exit_code=1, stdout=long, stderr="Traceback …\nValueError: bad\n",
                                       seconds=0.5, timed_out=False))
    tools = Tools(RUN, work, FakeClient(), backend, settings(), lambda *_: None)
    status, body = tools.call("/v1/run_python", {"script": SCRIPT})
    assert status == 200 and body["exit_code"] == 1
    assert body["stdout_tail"].startswith("[… ") and body["log"] in body["stdout_tail"].splitlines()[0]
    assert body["stdout_tail"].endswith("line 4999\n")
    assert len(body["stdout_tail"]) < TAIL_CHARS + 200
    assert body["stderr_tail"].endswith("ValueError: bad\n")
    assert long in (work / body["log"]).read_text(encoding="utf-8")


def test_run_python_reports_a_time_limit(work):
    events = []
    backend = FakeBackend(PythonResult(exit_code=None, stdout="", stderr="[stopped]", seconds=30.0, timed_out=True))
    tools = Tools(RUN, work, FakeClient(), backend, settings(), lambda kind, text: events.append(text))
    status, body = tools.call("/v1/run_python", {"script": SCRIPT})
    assert status == 200 and body["timed_out"] and body["exit_code"] is None
    assert events == [f"run_python {SCRIPT} → timed out after 30.0 s (limit 30 s)"]


@pytest.mark.parametrize("script, status", [
    ("../fit.py", 400), ("/work/ScriptsUtilized/fit.py", 400), ("ScriptsUtilized\\fit.py", 400),
    ("Data/fit.py", 400), ("Tasks/fit.py", 400), ("ScriptsUtilized", 400), ("ScriptsUtilized/logs/x.py", 400),
    ("ScriptsUtilized/analysis_scripts/notes.txt", 400), ("ScriptsUtilized/.hidden.py", 400),
    ("ScriptsUtilized/analysis_scripts/../../x.py", 400), ("ScriptsUtilized/missing.py", 404),
    ("scriptsutilized/analysis_scripts/fit.py", 400), (3, 400), (None, 400),
])
def test_run_python_refuses_scripts_outside_scriptsutilized(env, script, status):
    tools, _client, backend, events = env
    code, body = tools.call("/v1/run_python", {"script": script})
    assert code == status and body["error"]
    assert backend.requests == [] and events == []


def test_run_python_refuses_a_folder_named_like_a_script(env, work):
    (work / "ScriptsUtilized" / "dir.py").mkdir()
    assert env[0].call("/v1/run_python", {"script": "ScriptsUtilized/dir.py"})[0] == 400


def test_run_python_refuses_links(env, work, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    try:
        os.symlink(outside, work / "ScriptsUtilized" / "link.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra rights here")
    assert env[0].call("/v1/run_python", {"script": "ScriptsUtilized/link.py"})[0] == 400


def test_run_python_refuses_hard_links(env, work):
    try:
        os.link(work / SCRIPT, work / "ScriptsUtilized" / "hard.py")
    except (OSError, NotImplementedError):
        pytest.skip("hard links not available")
    assert env[0].call("/v1/run_python", {"script": "ScriptsUtilized/hard.py"})[0] == 400


def test_a_planted_link_never_redirects_the_log(work, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (work / "ScriptsUtilized" / "logs").rmdir()
    try:
        os.symlink(outside, work / "ScriptsUtilized" / "logs", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra rights here")
    tools = Tools(RUN, work, FakeClient(), FakeBackend(), settings(), lambda *_: None)
    status, body = tools.call("/v1/run_python", {"script": SCRIPT})
    assert status == 200 and body["log"] is None and "log file could not be written" in body["stderr_tail"]
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("data", [
    {"script": SCRIPT, "args": "--n 3"}, {"script": SCRIPT, "args": [1]}, {"script": SCRIPT, "args": ["x"] * 33},
    {"script": SCRIPT, "args": ["x" * 1001]}, {"script": SCRIPT, "args": ["a\0b"]},
    {"script": SCRIPT, "timeout_seconds": 0}, {"script": SCRIPT, "timeout_seconds": -1},
    {"script": SCRIPT, "timeout_seconds": True}, {"script": SCRIPT, "timeout_seconds": "60"},
    {"script": SCRIPT, "timeout_seconds": float("nan")}, {"script": SCRIPT, "network": True},
])
def test_run_python_refuses_bad_arguments(env, data):
    tools, _client, backend, _events = env
    assert tools.call("/v1/run_python", data)[0] == 400
    assert backend.requests == []


def test_run_python_calls_run_one_at_a_time(work):
    backend = FakeBackend(delay=0.1)
    tools = Tools(RUN, work, FakeClient(), backend, settings(), lambda *_: None)
    results = []
    threads = [threading.Thread(target=lambda: results.append(tools.call("/v1/run_python", {"script": SCRIPT})))
               for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert [r[0] for r in results] == [200, 200, 200]
    assert backend.most_active == 1
    assert sorted(r[1]["log"][-10:] for r in results) == ["01-fit.log", "02-fit.log", "03-fit.log"]


def test_backend_failure_is_a_tool_error(work):
    backend = FakeBackend()
    backend.error = RuntimeError("Cannot run docker: not found")
    events = []
    tools = Tools(RUN, work, FakeClient(), backend, settings(), lambda kind, text: events.append(text))
    status, body = tools.call("/v1/run_python", {"script": SCRIPT})
    assert status == 500 and "could not be started" in body["error"]
    assert events == [f"run_python {SCRIPT} → could not start: RuntimeError"]


def test_an_event_sink_that_fails_does_not_fail_the_tool(work):
    def broken(kind, text):
        raise RuntimeError("events down")

    tools = Tools(RUN, work, FakeClient(), FakeBackend(), settings(), broken)
    assert tools.call("/v1/run_python", {"script": SCRIPT})[0] == 200


def test_errors_are_redacted(env):
    tools, client, _backend, _events = env
    client.error = RunnerApiError(422, "lease lease_" + "0123456789abcdef" * 2 + " is gone")
    _status, body = tools.call("/v1/list_papers", {})
    assert "0123456789abcdef0123456789abcdef" not in body["error"]


# ---- transports -------------------------------------------------------------------------


@pytest.fixture
def tcp(env):
    tools = env[0]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tools))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_http_layer(tcp, env):
    _tools, client, _backend, _events = env
    with httpx.Client(base_url=tcp, trust_env=False) as http:
        response = http.post("/v1/search_literature", json={"query": "laser"})
        assert response.status_code == 200 and response.json()["results"][0]["cite"] == "a.pdf p.3"
        assert http.post("/v1/run_python", json={"script": SCRIPT}).json()["exit_code"] == 0
        assert http.post("/v1/run_python", json={"script": "../x.py"}).status_code == 400
        assert http.post("/v1/nothing", json={}).status_code == 404
        assert http.get("/v1/list_papers").status_code == 405
        assert http.post("/v1/list_papers", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 400
        assert http.post("/v1/list_papers", content=b"x" * (300 * 1024)).status_code == 413
        assert http.post("/v1/list_papers").status_code == 200  # an empty body is {}


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32", reason="needs Unix sockets")
def test_unix_socket_transport(work, tmp_path):
    sock = tmp_path / "sock" / "tools.sock"
    sock.parent.mkdir()
    server = ToolsServer(run=RUN, work=work, socket_path=sock, client=FakeClient(), backend=FakeBackend(),
                         settings=settings(), on_event=lambda *_: None)
    server.start()
    try:
        assert stat.S_IMODE(os.stat(sock).st_mode) == 0o666
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(sock)), base_url="http://labnet-tools") as http:
            assert http.post("/v1/list_papers", json={}).json()["papers"] == [{"path": "a.pdf"}]
            assert http.post("/v1/run_python", json={"script": SCRIPT}).json()["exit_code"] == 0
    finally:
        server.stop()
    assert not sock.exists()


@pytest.mark.skipif(hasattr(socket, "AF_UNIX") and sys.platform != "win32", reason="Windows only")
def test_start_explains_that_unix_sockets_are_needed(work, tmp_path):
    server = ToolsServer(run=RUN, work=work, socket_path=tmp_path / "tools.sock", client=FakeClient(),
                         backend=FakeBackend(), settings=settings(), on_event=lambda *_: None)
    with pytest.raises(RuntimeError, match="Unix sockets"):
        server.start()
    server.stop()  # harmless when it never started
    assert server.handle("/v1/list_papers", {})[0] == 200


def test_no_answer_after_retries_is_unreachable(env):
    tools, client, _backend, _events = env
    client.error = RunnerApiError(0, "no answer after 4 attempts")
    status, body = tools.call("/v1/list_papers", {})
    assert status == 502 and "couldn't be reached" in body["error"]
