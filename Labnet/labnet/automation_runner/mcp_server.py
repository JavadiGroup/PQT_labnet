"""The ``labnet`` MCP server inside the agent container (stdio).

    python -m labnet.automation_runner.mcp_server

Claude Code starts it from the run's ``mcp.json``. It holds nothing: every
tool call is forwarded to the supervisor's tools server over the Unix socket
at ``$LABNET_TOOLS_SOCKET`` (``/run/labnet/tools.sock``), which checks it,
does it (literature through the Control Center, scripts in a run container)
and answers. This module only turns those answers into text the agent reads,
and failures into tool errors carrying the server's message.
"""

from __future__ import annotations

import math
import os
from typing import Any

import anyio
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .protocol_text import device_lines, safe_state_lines


SOCKET_ENV = "LABNET_TOOLS_SOCKET"
DEFAULT_SOCKET = "/run/labnet/tools.sock"
REQUEST_TIMEOUT_SECONDS = 120.0
# On top of a script's own time limit: starting and removing its container.
RUN_OVERHEAD_SECONDS = 120.0
MAX_RUN_SECONDS = 6 * 3600.0
PROTOCOL_TIMEOUT_SECONDS = 240.0  # two uploads, then validation against the live network
END_TIMEOUT_SECONDS = 240.0  # the Control Center applies the safe state before it answers
MARK_START, MARK_END = "\x02", "\x03"  # how the Control Center marks matched words in snippets

INSTRUCTIONS = (
    "The PQT lab's tools for this run: search and read the project's literature (cite pages as "
    "`path p.N`, exactly as the results give them), run the Python scripts you write under "
    "ScriptsUtilized/ in a sandbox without network, and, for tasks that need lab devices, submit a "
    "protocol for a person's approval, check the grant and end the experiment."
)

SEARCH_DESCRIPTION = """Search the lab literature this project may use (its share of the lab library plus its own Literature/ papers) by keywords.

Returns the best-matching pages, each with a snippet (matched words in **bold**) and its citation.
Cite anything you use from a paper as `path p.N` exactly as given here, e.g. `lasers_and_stabilization/Pound-Drever-Hall locking.pdf p.4`.
Pages matching all words come first; if none do, pages matching any word. Quote "a phrase" to keep words together.
Use `paper` (a path from list_papers) to search within one paper, and read_paper_pages to read around a hit."""

PAPERS_DESCRIPTION = """List every paper you can search and read, with its path, page count and where it comes from (lab library or this project).

Papers marked "no text layer" (scans) can't be searched or read as text."""

PAGES_DESCRIPTION = """Read the extracted text of pages `first` to `last` (at most 20 per call) of one paper.

`paper` is a path from list_papers or search_literature. Each page is headed with its citation: cite what you use as `path p.N`.
If more pages follow, call again with first = last + 1."""

RUN_DESCRIPTION = """Run a Python script you wrote under ScriptsUtilized/ (e.g. ScriptsUtilized/analysis_scripts/fit.py; write it first) in a fresh sandbox container, then report how it went.

The sandbox: Python 3.11 with numpy, scipy, pandas and matplotlib (Agg backend: save figures, don't show them); the project's working copy is the current directory (/work), so use relative paths: read e.g. Data/example_data/scan.csv, write results to Data/new_data/, Analysis/outputs/ and so on.
It has NO network, no lab devices and no credentials, and limits on CPU, memory, processes and time (timeout_seconds, capped by the runner). The container is deleted afterwards; only files written under /work remain.
`args` become sys.argv[1:]. Scripts run isolated (`python -I`): the script's own folder is not on sys.path, so to import a helper module you wrote, first `sys.path.insert(0, "ScriptsUtilized/src")`. Scripts run one at a time. You get the exit code and the last ~8 KB of stdout and stderr; the full output is saved in the log file named in the result (ScriptsUtilized/logs/...), which you can Read."""


SUBMIT_DESCRIPTION = """Submit this task's protocol for a person's approval: Protocols/<task>.protocol.json (the devices, methods and bounds; the format is in your briefing) and Protocols/<task>.protocol.md (your method in prose, for the reviewer).

Both files are uploaded as they are now and checked by the Control Center against the live lab network. If there are problems you get them as a list: fix the JSON and submit again (a run can have at most 3 protocols reviewed).
If it is accepted, END YOUR TURN at once: the run waits for a person's decision and resumes this conversation with it."""

GRANT_DESCRIPTION = """Show this run's protocol and device grant: the phase (planning, awaiting_approval, executing, analyzing), the review decision and note, and while a grant is active the approved devices, methods and bounds, the time left and the calls made so far."""

END_DESCRIPTION = """End the experiment now: the Control Center refuses further device calls, puts the devices in the protocol's safe state and releases them.

Call it as soon as the measurements are done, or when something goes wrong (a refused call you can't fix, readings that look unsafe or wrong). `reason`: one sentence, recorded with the grant. It can't be undone."""


class ToolsClient:
    """The tools server, over its Unix socket (or any httpx transport, for tests)."""

    def __init__(self, socket_path: str | None = None, *, transport: httpx.BaseTransport | None = None) -> None:
        if transport is None:
            path = socket_path or os.environ.get(SOCKET_ENV) or DEFAULT_SOCKET
            transport = httpx.HTTPTransport(uds=path)
        self._http = httpx.Client(transport=transport, base_url="http://labnet-tools", trust_env=False,
                                  timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=10.0))

    def close(self) -> None:
        self._http.close()

    def call(self, tool: str, payload: dict[str, Any], *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
        try:
            response = self._http.post(f"/v1/{tool}", json=payload,
                                       timeout=httpx.Timeout(timeout, connect=10.0))
        except httpx.TimeoutException:
            raise ToolError(f"{tool}: the lab tools didn't answer within {timeout:g} s.") from None
        except httpx.HTTPError as exc:
            raise ToolError(f"{tool}: the lab tools aren't reachable ({type(exc).__name__}). "
                            "The run may be ending.") from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code != 200:
            message = data.get("error") if isinstance(data, dict) else None
            raise ToolError(str(message or f"{tool} failed (HTTP {response.status_code})."))
        if not isinstance(data, dict):
            raise ToolError(f"{tool}: the lab tools sent an unexpected answer.")
        return data


# ---- answers as text ----------------------------------------------------------------------


def _marks(text: str) -> str:
    return str(text).replace(MARK_START, "**").replace(MARK_END, "**")


def _notes(data: dict[str, Any]) -> list[str]:
    notes = [str(note) for note in data.get("notes") or [] if note]
    return [f"Note: {note}" for note in notes]


def format_search(data: dict[str, Any]) -> str:
    results = data.get("results") or []
    query = data.get("query", "")
    if not results:
        return "\n".join([f"No pages match “{query}”.", *_notes(data)])
    how = {"all": "pages with all the words", "any": "no page has all the words; pages with any of them"}.get(
        str(data.get("mode")), "matching pages")
    lines = [f"{len(results)} result(s) for “{query}” ({how}):", ""]
    for number, item in enumerate(results, 1):
        cite = item.get("cite") or f"{item.get('path')} p.{item.get('page')}"
        lines.append(f"{number}. {cite}  [{item.get('source', '?')}, score {item.get('score', '?')}]")
        snippet = _marks(item.get("snippet") or "").strip()
        if snippet:
            lines.append(f"   {snippet}")
    lines += ["", *_notes(data)]
    return "\n".join(lines).rstrip() + "\n"


def format_papers(data: dict[str, Any]) -> str:
    papers = data.get("papers") or []
    if not papers:
        return "\n".join(["There are no papers in this project's literature.", *_notes(data)])
    lines = [f"{len(papers)} paper(s):"]
    for paper in papers:
        status = paper.get("status")
        pages = paper.get("pages")
        state = {
            "no_text": "no text layer (a scan?): can't be searched",
            "not_indexed": "not indexed yet",
            "error": f"couldn't be read: {paper.get('error') or 'error'}",
        }.get(str(status), f"{pages} pages" if pages is not None else "")
        lines.append(f"- {paper.get('path')}  [{paper.get('source', '?')}{', ' + state if state else ''}]")
    lines += _notes(data)
    return "\n".join(lines) + "\n"


def format_pages(data: dict[str, Any]) -> str:
    path, first, last, total = data.get("path"), data.get("first"), data.get("last"), data.get("pages")
    lines = [f"{path}: pages {first}–{last} of {total} ({data.get('source', '?')})"]
    if data.get("status") not in (None, "ok"):
        lines.append(f"Status: {data.get('status')}{': ' + str(data['error']) if data.get('error') else ''}")
    for page in data.get("page_texts") or []:
        cite = page.get("cite") or f"{path} p.{page.get('page')}"
        text = str(page.get("text") or "").strip() or "(no text on this page)"
        lines += ["", f"===== {cite} =====", text]
    if data.get("more"):
        lines += ["", f"More pages follow: read_paper_pages(paper, first={int(last or 0) + 1}, last=…)."]
    return "\n".join(lines) + "\n"


def format_run(data: dict[str, Any]) -> str:
    seconds = data.get("seconds")
    if data.get("timed_out"):
        head = f"The script was stopped at its time limit after {seconds} s."
    elif data.get("exit_code") is None:
        head = f"The script was stopped after {seconds} s."
    elif data.get("exit_code") == 0:
        head = f"The script finished (exit code 0) in {seconds} s."
    else:
        head = f"The script failed with exit code {data.get('exit_code')} after {seconds} s."
    lines = [head, f"Full output: {data.get('log') or '(no log file)'}"]
    for name in ("stdout", "stderr"):
        text = str(data.get(f"{name}_tail") or "")
        lines += ["", f"--- {name} (end) ---" if text else f"--- {name}: empty ---"]
        if text:
            lines.append(text.rstrip("\n"))
    return "\n".join(lines) + "\n"


def _problem(item: Any) -> str:
    if isinstance(item, dict):
        where = str(item.get("path") or item.get("loc") or "").strip()
        message = str(item.get("message") or item.get("msg") or item).strip()
        return f"- {where}: {message}" if where else f"- {message}"
    return f"- {item}"


def format_submit(data: dict[str, Any]) -> str:
    if data.get("status") == "submitted":
        title = data.get("title")
        named = f": “{title}”" if title else ""
        return (
            f"Protocol submitted for approval{named} (sha256 {data.get('sha256')}).\n\n"
            "A person must now review it in the Control Center. END YOUR TURN NOW: don't call any more tools; "
            "finish with a short summary of what you proposed and why. This run will be resumed in this same "
            "conversation with the decision: if approved, with the approved devices, bounds and end time; "
            "if rejected, with the reviewer's note.\n"
        )
    problems = data.get("problems") or []
    lines = [f"The protocol was NOT accepted: {len(problems)} problem(s)." if problems
             else "The protocol was NOT accepted."]
    if data.get("detail"):
        lines.append(str(data["detail"]))
    lines += ["", *(_problem(item) for item in problems)]
    lines += ["", "Fix Protocols/<task>.protocol.json (and the .md if needed), then call submit_protocol again."]
    if not data.get("submitted"):
        lines.append("(It wasn't sent to the Control Center.)")
    lines.append("A run can have at most 3 protocols reviewed: get this one right before it goes to a person.")
    return "\n".join(lines) + "\n"


def _seconds(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "unknown"
    minutes, seconds = divmod(int(value), 60)
    return f"{minutes} min {seconds:02d} s" if minutes else f"{seconds} s"


def format_grant(data: dict[str, Any]) -> str:
    lines = [f"Phase: {data.get('phase') or 'unknown'} (run {data.get('run_status') or 'unknown'})"]
    protocol = data.get("protocol") or {}
    if protocol:
        text = f"Protocol: {protocol.get('status', '?')}"
        if protocol.get("title"):
            text += f" “{protocol['title']}”"
        if protocol.get("submissions") is not None:
            text += f", submission {protocol['submissions']} of at most 3"
        if protocol.get("reviewed_by"):
            text += f", reviewed by {protocol['reviewed_by']}"
        lines.append(text)
        if protocol.get("note"):
            lines.append(f"Reviewer's note: {protocol['note']}")
    else:
        lines.append("Protocol: none submitted yet.")
    grant = data.get("grant") or {}
    if data.get("active"):
        lines.append(f"Grant: ACTIVE until {grant.get('ends_at', '?')} ({_seconds(data.get('seconds_left'))} left); "
                     f"calls so far {grant.get('calls', '?')}, refused {grant.get('refused_calls', '?')}.")
        lines += ["", "Approved devices and bounds (hard limits, checked on every call):",
                  *device_lines(data.get("devices"))]
        safe = safe_state_lines(data.get("safe_state"))
        if safe:
            lines += ["", "Safe state applied when the grant ends:", *safe]
        lines += ["", "Use them from a run_python script: `import labnet_devices; devices = labnet_devices.connect()`."]
    elif grant:
        reason = data.get("ended_reason") or grant.get("ended_reason") or ""
        lines.append(f"Grant: {grant.get('status', 'ended')}" + (f" ({reason})" if reason else "")
                     + ". No device calls are possible any more.")
    else:
        lines.append("Grant: none. No device calls are possible.")
    if data.get("note"):
        lines += ["", f"Note: {data['note']}"]
    return "\n".join(lines) + "\n"


def format_end(data: dict[str, Any]) -> str:
    if data.get("ended"):
        return ("The experiment has ended: the Control Center refuses further device calls, applied the safe "
                "state and released the devices. Now analyse what you measured and write Analysis/Analysis.md "
                "and Data/new_data/DATA_SUMMARY.md.\n")
    return f"{data.get('detail') or 'The experiment had already ended.'} No device calls are possible any more.\n"


# ---- the server ------------------------------------------------------------------------------


def build_server(client: ToolsClient | None = None) -> FastMCP:
    """The FastMCP server; ``client`` defaults to the socket in $LABNET_TOOLS_SOCKET (opened on first use)."""

    state: dict[str, ToolsClient] = {}
    if client is not None:
        state["client"] = client

    def tools() -> ToolsClient:
        if "client" not in state:
            state["client"] = ToolsClient()
        return state["client"]

    async def call(tool: str, payload: dict[str, Any], timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
        # The blocking HTTP call runs in a thread, so the MCP session stays responsive meanwhile.
        return await anyio.to_thread.run_sync(lambda: tools().call(tool, payload, timeout=timeout))

    server = FastMCP("labnet", instructions=INSTRUCTIONS)

    @server.tool(description=SEARCH_DESCRIPTION, structured_output=False)
    async def search_literature(query: str, k: int = 10, paper: str | None = None) -> str:
        payload: dict[str, Any] = {"query": query, "k": k}
        if paper:
            payload["paper"] = paper
        return format_search(await call("search_literature", payload))

    @server.tool(description=PAPERS_DESCRIPTION, structured_output=False)
    async def list_papers() -> str:
        return format_papers(await call("list_papers", {}))

    @server.tool(description=PAGES_DESCRIPTION, structured_output=False)
    async def read_paper_pages(paper: str, first: int, last: int) -> str:
        return format_pages(await call("read_paper_pages", {"paper": paper, "first": first, "last": last}))

    @server.tool(description=RUN_DESCRIPTION, structured_output=False)
    async def run_python(script: str, args: list[str] = [], timeout_seconds: float = 300) -> str:  # noqa: B006
        payload = {"script": script, "args": list(args), "timeout_seconds": timeout_seconds}
        limit = float(timeout_seconds) if math.isfinite(timeout_seconds) else MAX_RUN_SECONDS
        wait = min(max(limit, 1.0), MAX_RUN_SECONDS) + RUN_OVERHEAD_SECONDS
        return format_run(await call("run_python", payload, timeout=wait))

    @server.tool(description=SUBMIT_DESCRIPTION, structured_output=False)
    async def submit_protocol() -> str:
        return format_submit(await call("submit_protocol", {}, timeout=PROTOCOL_TIMEOUT_SECONDS))

    @server.tool(description=GRANT_DESCRIPTION, structured_output=False)
    async def grant_status() -> str:
        return format_grant(await call("grant_status", {}))

    @server.tool(description=END_DESCRIPTION, structured_output=False)
    async def end_experiment(reason: str) -> str:
        return format_end(await call("end_experiment", {"reason": reason}, timeout=END_TIMEOUT_SECONDS))

    return server


def main() -> None:
    build_server().run()  # stdio


if __name__ == "__main__":
    main()


__all__ = [
    "ToolsClient", "build_server", "format_end", "format_grant", "format_pages", "format_papers", "format_run",
    "format_search", "format_submit", "main",
]
