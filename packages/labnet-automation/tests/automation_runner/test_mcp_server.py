"""The labnet MCP server: forwarding to the tools server, text answers, tool errors, one real MCP round trip."""

from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

from labnet_automation import mcp_server
from labnet_automation.mcp_server import (
    ToolsClient, build_server, format_end, format_grant, format_pages, format_papers, format_run, format_search,
    format_submit,
)


SEARCH = {
    "query": "PDH error signal", "mode": "all", "notes": ["2 of this project's library papers aren't indexed yet."],
    "results": [{"path": "lasers/PDH.pdf", "source": "library", "page": 4, "score": 12.5,
                 "snippet": "the \x02error\x03 \x02signal\x03 is", "cite": "lasers/PDH.pdf p.4"}],
}
PAPERS = {"papers": [
    {"path": "lasers/PDH.pdf", "source": "library", "pages": 12, "status": "ok", "error": None},
    {"path": "Literature/scan.pdf", "source": "project", "pages": 3, "status": "no_text", "error": None},
    {"path": "Literature/new.pdf", "source": "project", "pages": None, "status": "not_indexed", "error": None},
], "notes": []}
PAGES = {"path": "lasers/PDH.pdf", "source": "library", "pages": 12, "status": "ok", "error": None, "first": 4,
         "last": 5, "more": True, "page_texts": [{"page": 4, "text": "Page four.", "cite": "lasers/PDH.pdf p.4"},
                                                  {"page": 5, "text": "", "cite": "lasers/PDH.pdf p.5"}]}
RUN = {"exit_code": 0, "timed_out": False, "seconds": 1.2, "stdout_tail": "slope = 3.1\n", "stderr_tail": "",
       "log": "ScriptsUtilized/logs/run-20261007-120000-abcd/01-fit.log"}


class FakeToolsServer:
    """An httpx MockTransport handler standing in for the tools server."""

    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.answers = {"/v1/search_literature": (200, SEARCH), "/v1/list_papers": (200, PAPERS),
                        "/v1/read_paper_pages": (200, PAGES), "/v1/run_python": (200, RUN)}
        self.timeouts: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        self.requests.append((request.url.path, json.loads(request.content)))
        self.timeouts.append(request.extensions.get("timeout"))
        status, body = self.answers[request.url.path]
        return httpx.Response(status, json=body)


@pytest.fixture
def fake():
    return FakeToolsServer()


@pytest.fixture
def client(fake):
    return ToolsClient(transport=httpx.MockTransport(fake))


def test_client_posts_json_to_the_tool_route(client, fake):
    assert client.call("list_papers", {}) == PAPERS
    assert fake.requests == [("/v1/list_papers", {})]


def test_client_turns_errors_into_tool_errors(client, fake):
    fake.answers["/v1/run_python"] = (400, {"error": "run_python runs a .py file you wrote under ScriptsUtilized/"})
    with pytest.raises(ToolError, match="under ScriptsUtilized/"):
        client.call("run_python", {"script": "x.py"})
    fake.answers["/v1/list_papers"] = (502, {"nope": 1})
    with pytest.raises(ToolError, match="HTTP 502"):
        client.call("list_papers", {})


def test_client_reports_an_unreachable_socket():
    def down(request):
        raise httpx.ConnectError("No such file or directory")

    with pytest.raises(ToolError, match="aren't reachable"):
        ToolsClient(transport=httpx.MockTransport(down)).call("list_papers", {})


def test_client_uses_the_socket_from_the_environment(monkeypatch):
    seen = {}

    class Transport(httpx.HTTPTransport):
        def __init__(self, *, uds=None, **kwargs):
            seen["uds"] = uds
            super().__init__(**kwargs)

    monkeypatch.setattr(mcp_server.httpx, "HTTPTransport", Transport)
    monkeypatch.setenv("LABNET_TOOLS_SOCKET", "/run/labnet/tools.sock")
    ToolsClient().close()
    assert seen["uds"] == "/run/labnet/tools.sock"


def test_search_text_shows_citations_and_bold_matches():
    text = format_search(SEARCH)
    assert "1. lasers/PDH.pdf p.4  [library, score 12.5]" in text
    assert "the **error** **signal** is" in text
    assert "\x02" not in text and "\x03" not in text
    assert "Note: 2 of this project's library papers aren't indexed yet." in text
    assert format_search({"query": "x", "results": [], "notes": ["There are no words to search for."]}).startswith("No pages match")


def test_papers_text():
    text = format_papers(PAPERS)
    assert "- lasers/PDH.pdf  [library, 12 pages]" in text
    assert "no text layer" in text and "not indexed yet" in text


def test_pages_text():
    text = format_pages(PAGES)
    assert "===== lasers/PDH.pdf p.4 =====\nPage four." in text
    assert "===== lasers/PDH.pdf p.5 =====\n(no text on this page)" in text
    assert "first=6" in text


def test_run_text():
    assert format_run(RUN).startswith("The script finished (exit code 0) in 1.2 s.\nFull output: ScriptsUtilized/logs/")
    assert "slope = 3.1" in format_run(RUN) and "--- stderr: empty ---" in format_run(RUN)
    assert "exit code 2" in format_run({**RUN, "exit_code": 2})
    assert "time limit" in format_run({**RUN, "exit_code": None, "timed_out": True})


async def test_mcp_round_trip(client, fake):
    server = build_server(client)
    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()
        tools = {tool.name: tool for tool in listed.tools}
        assert set(tools) == {"search_literature", "list_papers", "read_paper_pages", "run_python",
                              "submit_protocol", "grant_status", "end_experiment"}
        assert tools["submit_protocol"].inputSchema.get("properties", {}) == {}
        assert set(tools["end_experiment"].inputSchema["properties"]) == {"reason"}
        assert "END YOUR TURN" in tools["submit_protocol"].description
        assert "p.N" in tools["search_literature"].description
        assert "ScriptsUtilized/" in tools["run_python"].description and "NO network" in tools["run_python"].description
        schema = tools["run_python"].inputSchema
        assert schema["required"] == ["script"] and set(schema["properties"]) == {"script", "args", "timeout_seconds"}
        assert set(tools["search_literature"].inputSchema["properties"]) == {"query", "k", "paper"}

        result = await session.call_tool("search_literature", {"query": "PDH error signal"})
        assert not result.isError
        assert "lasers/PDH.pdf p.4" in result.content[0].text
        result = await session.call_tool("run_python", {"script": "ScriptsUtilized/fit.py", "args": ["3"],
                                                         "timeout_seconds": 60})
        assert "exit code 0" in result.content[0].text
        result = await session.call_tool("read_paper_pages", {"paper": "lasers/PDH.pdf", "first": 4, "last": 5})
        assert "Page four." in result.content[0].text
        assert "lasers/PDH.pdf" in (await session.call_tool("list_papers", {})).content[0].text

        fake.answers["/v1/read_paper_pages"] = (404, {"error": "That paper isn't in this project's literature."})
        result = await session.call_tool("read_paper_pages", {"paper": "nope.pdf", "first": 1, "last": 2})
        assert result.isError and "isn't in this project's literature" in result.content[0].text

    assert fake.requests[:3] == [
        ("/v1/search_literature", {"query": "PDH error signal", "k": 10}),
        ("/v1/run_python", {"script": "ScriptsUtilized/fit.py", "args": ["3"], "timeout_seconds": 60}),
        ("/v1/read_paper_pages", {"paper": "lasers/PDH.pdf", "first": 4, "last": 5}),
    ]
    # run_python waits for the script's time limit plus the container overhead.
    assert fake.timeouts[1]["read"] == 60 + mcp_server.RUN_OVERHEAD_SECONDS


def test_module_starts_as_a_stdio_server(tmp_path):
    """`python -m labnet_automation.mcp_server` answers an MCP initialize on stdio."""

    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
    process = subprocess.run(
        [sys.executable, "-m", "labnet_automation.mcp_server"],
        input=json.dumps(initialize) + "\n", capture_output=True, text=True, timeout=60,
        env={**__import__("os").environ, "LABNET_TOOLS_SOCKET": str(tmp_path / "none.sock")},
    )
    first = json.loads(process.stdout.splitlines()[0])
    assert first["id"] == 1 and first["result"]["serverInfo"]["name"] == "labnet"


# ---- Phase 3: the protocol and the experiment ---------------------------------------------------

PROTOCOL_DEVICES = {"dev01-aom": {"device_type": "dummy_aom", "methods": {
    "set_frequency": {"args": {"value": {"min": 70.0, "max": 90.0, "max_step": 2.0}}, "max_calls": 200,
                      "max_rate_hz": 5.0},
    "stream_diffraction_efficiency": {"args": {"channel": {"choices": ["CH1"]}},
                                      "stream": {"max_sample_rate_hz": 20.0, "max_samples": 1000},
                                      "max_calls": 50, "max_rate_hz": 1.0},
}}}


def test_submit_text_tells_the_agent_to_end_its_turn_or_lists_the_problems():
    text = format_submit({"status": "submitted", "sha256": "ab" * 32, "phase": "awaiting_approval",
                          "title": "AOM efficiency"})
    assert "END YOUR TURN NOW" in text and "ab" * 32 in text and "resumed" in text

    text = format_submit({"status": "problems", "submitted": True, "detail": "The protocol has 2 problems.",
                          "problems": [{"path": "devices[0].methods.set_frequency.args.value.max",
                                        "message": "100 is above the driver's limit 95"},
                                       {"path": "", "message": "duration_minutes is required"}]})
    assert "NOT accepted: 2 problem(s)" in text
    assert "- devices[0].methods.set_frequency.args.value.max: 100 is above the driver's limit 95" in text
    assert "- duration_minutes is required" in text and "at most 3 protocols reviewed" in text
    assert "END YOUR TURN" not in text and "wasn't sent" not in text
    assert "wasn't sent to the Control Center" in format_submit({"status": "problems", "submitted": False, "problems": [
        {"path": "Protocols/Task1.protocol.json", "message": "isn't valid JSON"}]})


def test_grant_text_shows_bounds_time_and_ending():
    active = format_grant({
        "phase": "executing", "run_status": "active", "active": True, "seconds_left": 754.0,
        "protocol": {"status": "approved", "title": "AOM", "submissions": 1, "reviewed_by": "alice"},
        "grant": {"ends_at": "2026-10-07T12:20:00Z", "calls": 3, "refused_calls": 1}, "devices": PROTOCOL_DEVICES,
        "safe_state": [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False}}],
    })
    assert "Grant: ACTIVE until 2026-10-07T12:20:00Z (12 min 34 s left)" in active
    assert "set_frequency(value: 70 … 90, each change ≤ 2): at most 200 calls, ≤ 5 calls/s" in active
    assert 'stream_diffraction_efficiency(channel: one of "CH1"; sample_rate_hz ≤ 20; sample_count ≤ 1000)' in active
    assert "dev01-aom.set_rf_enabled(enabled=false)" in active and "labnet_devices.connect()" in active
    assert "reviewed by alice" in active

    ended = format_grant({"phase": "analyzing", "run_status": "active", "active": False,
                          "grant": {"status": "ended", "ended_reason": "expired"}, "protocol": {"status": "approved"}})
    assert "Grant: ended (expired). No device calls are possible any more." in ended
    rejected = format_grant({"phase": "planning", "protocol": {"status": "rejected", "note": "Too fast."},
                             "grant": {}, "active": False})
    assert "Reviewer's note: Too fast." in rejected and "Grant: none" in rejected
    assert "already ended" in format_end({"ended": False, "detail": "The experiment had already ended."})
    assert "safe state" in format_end({"ended": True})


async def test_experiment_tools_forward_to_the_tools_server(client, fake):
    fake.answers.update({
        "/v1/submit_protocol": (200, {"status": "submitted", "sha256": "c" * 64, "phase": "awaiting_approval"}),
        "/v1/grant_status": (200, {"phase": "awaiting_approval", "protocol": {"status": "submitted"}, "grant": {},
                                   "active": False}),
        "/v1/end_experiment": (409, {"error": "No experiment is running in this run."}),
    })
    async with create_connected_server_and_client_session(build_server(client)) as session:
        assert "END YOUR TURN NOW" in (await session.call_tool("submit_protocol", {})).content[0].text
        assert "Phase: awaiting_approval" in (await session.call_tool("grant_status", {})).content[0].text
        result = await session.call_tool("end_experiment", {"reason": "done"})
        assert result.isError and "No experiment is running" in result.content[0].text
    assert [path for path, _body in fake.requests] == ["/v1/submit_protocol", "/v1/grant_status", "/v1/end_experiment"]
    assert fake.requests[2][1] == {"reason": "done"}
    assert fake.timeouts[0]["read"] == mcp_server.PROTOCOL_TIMEOUT_SECONDS
