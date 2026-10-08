"""Photo-Agent: claude -p runs, streaming, conversations, models, files, limits.

A fake ``claude`` (a Python script) replays the stream-json lines the real CLI
was observed to print, keeps conversations like it does (``--session-id``
starts one, ``--resume`` continues one), and records how it was started.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labnet_control_center import agent as agent_module
from labnet_control_center.agent import (
    NOT_SIGNED_IN,
    AgentRequestError,
    PhotoAgent,
    model_label,
    render_network,
    safe_filename,
)
from labnet_control_center.app import _BodyLimit, create_app
from labnet_control_center.config import ControlCenterSettings


FAKE_CLAUDE = textwrap.dedent(
    '''
    import json, os, sys, time

    prompt = sys.stdin.read()
    argv = sys.argv[1:]
    def option(name):
        return argv[argv.index(name) + 1] if name in argv else None

    with open(os.environ["FAKE_CLAUDE_RECORD"], "a", encoding="utf-8") as record:
        record.write(json.dumps({"argv": argv, "prompt": prompt, "cwd": os.getcwd(),
                                 "env": sorted(os.environ)}) + "\\n")
    known = os.environ["FAKE_CLAUDE_RECORD"] + ".sessions"
    sessions = open(known, encoding="utf-8").read().split() if os.path.exists(known) else []

    def emit(message):
        print(json.dumps(message), flush=True)

    session = option("--resume")
    if session is not None and session not in sessions:  # as the real CLI does
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "", "session_id": None})
        print(f"No conversation found with session ID: {session}", file=sys.stderr)
        sys.exit(1)
    if session is None:
        session = option("--session-id")
        if session in sessions:
            print(f"Error: Session ID {session} is already in use.", file=sys.stderr)
            sys.exit(1)
        with open(known, "a", encoding="utf-8") as handle:
            handle.write(session + "\\n")

    def delta(text):
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
              "delta": {"type": "text_delta", "text": text}}})

    model = option("--model")
    resolved = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5",
                "haiku": "claude-haiku-4-5-20251001"}.get(model, model)
    emit({"type": "system", "subtype": "init", "session_id": session, "model": resolved})
    if "logged out" in prompt:
        emit({"type": "assistant", "error": "authentication_failed",
              "message": {"content": [{"type": "text", "text": "Not logged in"}]}})
        emit({"type": "result", "subtype": "success", "is_error": True,
              "result": "Not logged in \\u00b7 Please run /login", "session_id": session})
        sys.exit(1)
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read",
          "input": {"file_path": "C:\\\\lab\\\\Labnet\\\\labnet\\\\client\\\\core\\\\lab_client.py"}}]}})
    emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 1,
          "content_block": {"type": "text", "text": ""}}})
    delta("Hello ")
    if "slow" in prompt:
        time.sleep(60)
    delta("there.")
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "Hello there.",
          "session_id": session, "total_cost_usd": 0.01})
    '''
)

SNAPSHOT = {
    "central": {"url": "http://127.0.0.1:8008", "ok": True, "error": None},
    "summary": {"lab_pcs": 1, "online": 1, "devices": 2, "available": 1, "leased": 1},
    "lab_pcs": [
        {
            "id": "lab-pc-dev-02", "address": "127.0.0.1", "port": "50052", "secure": False,
            "online": True, "last_seen": None,
            "devices": [
                {
                    "id": "dev02-wavemeter", "type": "simulated_wavemeter",
                    "type_label": "Simulated Wavemeter", "state": "leased", "detail": "Held",
                    "control_url": "/dev02-wavemeter",
                    "control_center": {"session": "dev02-wavemeter", "streaming": True},
                    "python_class": "SimulatedWavemeter",
                    "hints": {"channels": ["A", "B"], "limits": {"set_exposure.value": [1.0, 1000.0]}},
                    "methods": [{"name": "read_wavelength", "parameters": ["channel=None"],
                                 "returns": "float", "kind": "read", "role": "record", "unit": "nm",
                                 "sweep_field": None}],
                },
                {
                    "id": "dev02-dummy-a", "type": "dummy_device", "type_label": "Dummy Device",
                    "state": "available", "detail": "Ready to lease", "control_url": "/dev02-dummy-a",
                    "control_center": None, "methods": [],
                },
            ],
        }
    ],
}
SECRETS = ("LABNET_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    for name in SECRETS:  # must never reach the agent's process
        monkeypatch.setenv(name, "secret")

    def runs() -> list[dict]:
        if not record.exists():
            return []
        return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]

    sources = tmp_path / "sources"
    sources.mkdir()
    return {"command": [sys.executable, str(script)], "runs": runs, "tmp": tmp_path, "sources": sources}


def _settings(fake, **overrides) -> ControlCenterSettings:
    values = dict(
        server_url="http://127.0.0.1:8008", api_key="key", ca_file=None,
        bind_host="127.0.0.1", port=8080, refresh_seconds=60.0,
        agent_workspace=fake["tmp"] / "agent", agent_sources=(fake["sources"],),
        experiments_dir=fake["tmp"] / "experiments",
        agent_timeout_seconds=30.0, agent_max_concurrent=2,
    )
    values.update(overrides)
    return ControlCenterSettings(**values)


def _agent(fake, **overrides) -> PhotoAgent:
    async def snapshot():
        return SNAPSHOT

    return PhotoAgent(_settings(fake, **overrides), snapshot, command=fake["command"])


async def _collect(agent: PhotoAgent, message: str, conversation_id: str | None = None, **options) -> list[dict]:
    request = agent.prepare(message, conversation_id, **options)
    return [event async for event in agent.ask(request)]


def _option(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


# ---- conversations ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_answer_streams_and_the_conversation_continues(fake) -> None:
    agent = _agent(fake)

    events = await _collect(agent, "How do leases work?")
    conversation = events[0]["conversation_id"]
    follow_up = await _collect(agent, "And renewals?", conversation)

    assert [event["type"] for event in events] == ["session", "activity", "delta", "delta", "done"]
    assert events[1]["text"] == "Reading client/core/lab_client.py"
    assert "".join(event["text"] for event in events if event["type"] == "delta") == "Hello there."
    assert events[-1]["is_error"] is False and events[-1]["conversation_id"] == conversation
    assert events[-1]["model"] == "Sonnet 5.5"  # Claude Code said what "sonnet" means
    assert follow_up[-1]["is_error"] is False and follow_up[0]["conversation_id"] == conversation
    first, second = fake["runs"]()
    assert first["prompt"] == "How do leases work?"  # on stdin, not the command line
    assert _option(first["argv"], "--session-id") == conversation  # the Control Center picks the id
    assert _option(second["argv"], "--resume") == conversation
    assert agent.signed_in is True and agent.info()["ready"] is True


@pytest.mark.asyncio
async def test_a_lost_conversation_starts_again_under_the_same_id(fake) -> None:
    agent = _agent(fake)
    unknown = str(uuid.uuid4())  # e.g. its first message failed before Claude Code saved it

    events = await _collect(agent, "Still there?", unknown)

    text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert text.startswith("_The earlier conversation could not be continued")
    assert events[-1]["is_error"] is False and events[-1]["conversation_id"] == unknown
    resumed, started = fake["runs"]()
    assert _option(resumed["argv"], "--resume") == unknown
    assert _option(started["argv"], "--session-id") == unknown


# ---- model and effort ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_and_effort_are_chosen_per_message(fake) -> None:
    agent = _agent(fake)

    await _collect(agent, "hi", model="opus", effort="xhigh")
    await _collect(agent, "hi", model="haiku", effort="max")
    await _collect(agent, "hi")

    opus, haiku, default = (run["argv"] for run in fake["runs"]())
    assert _option(opus, "--model") == "opus" and _option(opus, "--effort") == "xhigh"
    assert _option(haiku, "--model") == "haiku" and "--effort" not in haiku  # Haiku has no effort levels
    assert _option(default, "--model") == "sonnet" and _option(default, "--effort") == "high"

    info = agent.info()
    menu = {model["id"]: model for model in info["models"]}
    assert list(menu) == ["fable", "opus", "sonnet", "haiku"]
    assert menu["opus"]["label"] == "Opus 5.5" and menu["haiku"]["label"] == "Haiku 4.5"
    assert menu["fable"]["label"] == "Fable"  # not used yet, so its version is unknown
    assert menu["haiku"]["effort"] is False and menu["opus"]["effort"] is True
    assert info["model"] == "sonnet" and info["effort"] == "high"
    assert [effort["id"] for effort in info["efforts"]] == ["low", "medium", "high", "xhigh", "max"]


def test_unknown_models_and_efforts_are_refused(fake) -> None:
    agent = _agent(fake)
    with pytest.raises(AgentRequestError, match="Unknown model"):
        agent.prepare("hi", model="gpt-5")
    with pytest.raises(AgentRequestError, match="Unknown effort"):
        agent.prepare("hi", effort="ludicrous")
    with pytest.raises(AgentRequestError, match="not a conversation id"):
        agent.prepare("hi", "../../etc")
    assert fake["runs"]() == []


def test_model_labels() -> None:
    assert model_label("sonnet", "claude-sonnet-5-5") == "Sonnet 5.5"
    assert model_label("haiku", "claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert model_label("claude-opus-5-5") == "Opus 5.5"
    assert model_label("fable") == "Fable"


# ---- attachments -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachments_are_saved_for_their_conversation_only(fake) -> None:
    agent = _agent(fake)
    csv_data, png = b"time,wavelength\n0,780.241\n", b"\x89PNG\r\n\x1a\nfake"

    first = await _collect(agent, "What is in these?", attachments=[("../../x/scan.csv", csv_data), ("plot.png", png)])
    conversation = first[0]["conversation_id"]
    await _collect(agent, "Compare", conversation, attachments=[("scan.csv", csv_data)])
    await _collect(agent, "Another chat")

    folder = fake["tmp"] / "agent" / "uploads" / conversation
    assert sorted(path.name for path in folder.iterdir()) == ["plot.png", "scan (2).csv", "scan.csv"]
    assert (folder / "scan.csv").read_bytes() == csv_data  # not the ../../x path it was sent with
    with_files, follow_up, other_chat = fake["runs"]()
    assert with_files["prompt"].startswith("Attached to this message (open them with the Read tool):")
    assert f"- {folder / 'scan.csv'} (26 bytes)" in with_files["prompt"]
    assert with_files["prompt"].endswith("What is in these?")

    def added(run):
        return [run["argv"][i + 1] for i, arg in enumerate(run["argv"]) if arg == "--add-dir"]

    assert str(folder) in added(with_files) and str(folder) in added(follow_up)
    assert str(folder) not in added(other_chat)  # another conversation can't read them


@pytest.mark.asyncio
async def test_a_file_alone_is_a_message(fake) -> None:
    agent = _agent(fake)
    await _collect(agent, "  ", attachments=[("notes.txt", b"hello")])
    assert fake["runs"]()[0]["prompt"].endswith("Please look at the attached files.")


def test_unreadable_or_oversized_attachments_are_refused(fake, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent(fake)
    cases = [
        ([("results.xlsx", b"PK")], 415, "can't read this kind of file"),
        ([("empty.csv", b"")], 422, "empty"),
        ([(f"f{n}.txt", b"x") for n in range(6)], 422, "at most 5 files"),
    ]
    for attachments, status, message in cases:
        with pytest.raises(AgentRequestError, match=message) as refused:
            agent.prepare("look", attachments=attachments)
        assert refused.value.status_code == status

    monkeypatch.setattr(agent_module, "MAX_ATTACHMENT_BYTES", 10)
    with pytest.raises(AgentRequestError, match="larger than") as refused:
        agent.prepare("look", attachments=[("big.csv", b"x" * 11)])
    assert refused.value.status_code == 413
    assert list((fake["tmp"] / "agent" / "uploads").iterdir()) == []  # nothing was saved


def test_file_names_are_made_safe() -> None:
    assert safe_filename("../../etc/passwd.txt") == "passwd.txt"
    assert safe_filename("C:\\Users\\me\\Run 3 (final).CSV") == "Run 3 (final).csv"
    assert safe_filename("CON.txt") == "_CON.txt"
    assert safe_filename("a<b>|c?.png") == "a_b_c_.png"
    assert safe_filename("...") == "file"


def test_old_uploads_are_removed(fake) -> None:
    uploads = fake["tmp"] / "agent" / "uploads"
    old, recent = uploads / str(uuid.uuid4()), uploads / str(uuid.uuid4())
    for folder in (old, recent):
        folder.mkdir(parents=True)
        (folder / "data.csv").write_text("1,2\n", encoding="utf-8")
    weeks_ago = time.time() - 15 * 24 * 3600
    os.utime(old, (weeks_ago, weeks_ago))

    _agent(fake)  # tidies up when it starts

    assert not old.exists() and recent.exists()


# ---- confinement and the live network ----------------------------------------------


@pytest.mark.asyncio
async def test_the_agent_is_confined(fake) -> None:
    literature = fake["tmp"] / "papers"
    literature.mkdir()
    agent = _agent(fake, agent_literature_dir=literature)

    await _collect(agent, "hi")

    run = fake["runs"]()[0]
    argv = run["argv"]
    assert _option(argv, "--tools") == "Read,Glob,Grep"
    assert _option(argv, "--permission-mode") == "dontAsk"
    assert "--strict-mcp-config" in argv and "Read(**/*.env)" in argv
    added = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--add-dir"]
    assert added == [str(fake["sources"]), str(literature)]
    assert Path(run["cwd"]) == fake["tmp"] / "agent" / "home"
    env = set(run["env"])
    assert not env & set(SECRETS)
    assert {"ENABLE_CLAUDEAI_MCP_SERVERS", "CLAUDE_CODE_DISABLE_AUTO_MEMORY"} <= env
    briefing = (fake["tmp"] / "agent" / "briefing.md").read_text(encoding="utf-8")
    assert str(fake["sources"]) in briefing and str(literature) in briefing


@pytest.mark.asyncio
async def test_the_live_network_is_written_before_each_message(fake) -> None:
    agent = _agent(fake)
    await _collect(agent, "What is online?")

    network = (fake["tmp"] / "agent" / "home" / "live" / "network.md").read_text(encoding="utf-8")
    assert "dev02-wavemeter" in network and "held by the Control Center page /dev02-wavemeter" in network
    assert "dev02-dummy-a" in network and "available" in network
    assert "  - `read_wavelength(channel=None)` → float [record, nm]" in network
    assert '- Python: `SimulatedWavemeter(device_id="dev02-wavemeter", client=central)`' in network
    assert "- Channels: A, B" in network and "- Range of set_exposure.value: 1 to 1000" in network
    assert render_network(SNAPSHOT).startswith("# Live LabNet network")


@pytest.mark.asyncio
async def test_signed_out_claude_code_is_reported(fake) -> None:
    agent = _agent(fake)
    events = await _collect(agent, "logged out")

    assert events[-1]["is_error"] is True and events[-1]["text"] == NOT_SIGNED_IN
    assert agent.info()["ready"] is False and agent.info()["reason"] == NOT_SIGNED_IN


# ---- limits -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaving_kills_the_process(fake) -> None:
    agent = _agent(fake)
    answer = agent.ask(agent.prepare("slow"))
    async for event in answer:
        if event["type"] == "delta":
            break
    run = next(iter(agent._running))
    await answer.aclose()  # what Stop / closing the tab does

    for _ in range(50):
        if not run.running:
            break
        await asyncio.sleep(0.1)
    assert not run.running and not agent._running


@pytest.mark.asyncio
async def test_the_time_limit_ends_an_answer(fake) -> None:
    agent = _agent(fake, agent_timeout_seconds=1.0)
    events = await _collect(agent, "slow")

    assert events[-1]["type"] == "done" and events[-1]["is_error"] is True
    assert events[-1]["text"] == "Stopped: no answer within 1 s."


@pytest.mark.asyncio
async def test_extra_messages_wait_their_turn(fake) -> None:
    agent = _agent(fake, agent_max_concurrent=1)
    first = agent.ask(agent.prepare("slow"))
    async for event in first:
        if event["type"] == "delta":
            break

    second = agent.ask(agent.prepare("hi"))
    assert (await second.__anext__())["type"] == "session"
    assert (await second.__anext__()) == {"type": "status", "text": "Waiting for another answer to finish…"}
    await first.aclose()
    rest = [event async for event in second]
    assert rest[-1]["type"] == "done" and rest[-1]["is_error"] is False


# ---- the HTTP endpoint ---------------------------------------------------------------


class _NoCentral:
    async def list_lab_pcs(self):
        return []

    async def aclose(self) -> None:
        pass


def _app(fake):
    return create_app(
        _settings(fake),
        client_factory=lambda _: _NoCentral(),
        agent_factory=lambda settings, snapshot: PhotoAgent(settings, snapshot, command=fake["command"]),
    )


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_the_chat_endpoint_streams_with_choices_and_files(fake) -> None:
    with TestClient(_app(fake)) as browser:
        info = browser.get("/api/agent").json()
        response = browser.post(
            "/api/agent/messages",
            json={
                "message": "What is in this file?",
                "model": "opus",
                "effort": "low",
                "attachments": [{"name": "scan.csv", "data": base64.b64encode(b"a,b\n1,2\n").decode()}],
            },
        )
        bad_id = browser.post("/api/agent/messages", json={"message": "hi", "conversation_id": "../../etc"})
        bad_type = browser.post(
            "/api/agent/messages",
            json={"message": "hi", "attachments": [{"name": "a.xlsx", "data": base64.b64encode(b"PK").decode()}]},
        )
        bad_data = browser.post(
            "/api/agent/messages", json={"message": "hi", "attachments": [{"name": "a.csv", "data": "not base64!"}]}
        )
        bad_model = browser.post("/api/agent/messages", json={"message": "hi", "model": "gpt-5"})

    assert info["enabled"] is True and info["attachments"]["max_files"] == 5
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert events[0]["type"] == "session" and events[-1]["type"] == "done"
    assert events[-1]["model"] == "Opus 5.5" and events[-1]["effort"] == "low"
    run = fake["runs"]()[0]
    assert _option(run["argv"], "--model") == "opus" and "scan.csv" in run["prompt"]
    assert bad_id.status_code == 422
    assert bad_type.status_code == 415 and "can't read this kind of file" in bad_type.json()["detail"]
    assert bad_data.status_code == 422 and bad_model.status_code == 422
    assert len(fake["runs"]()) == 1  # refused messages never started Claude Code


@pytest.mark.asyncio
async def test_oversized_requests_are_refused_before_they_are_read() -> None:
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    limit = _BodyLimit(inner, path="/api/agent/messages", limit=100)
    big = {"type": "http", "path": "/api/agent/messages", "headers": [(b"content-length", b"101")]}
    small = {**big, "headers": [(b"content-length", b"100")]}
    await limit(big, None, send)
    await limit(small, None, send)

    assert sent[0]["status"] == 413 and reached == ["/api/agent/messages"]


# ---- the experiment on the canvas ----------------------------------------------------


EXPERIMENT = {
    "id": "aom-scan-abcdef", "version": 3, "name": "AOM scan", "description": "",
    "run": {"settle_seconds": 0.2, "samples": 10, "sample_rate_hz": 20.0, "output_name": "results"},
    "nodes": [{"id": "m1", "kind": "mirror", "label": "", "notes": "", "x": 0, "y": 0}],
    "links": [],
}


@pytest.mark.asyncio
async def test_the_experiment_on_screen_is_shown_once_per_version(fake) -> None:
    agent = _agent(fake)

    first = await _collect(agent, "Add a beamsplitter", experiment=EXPERIMENT)
    conversation = first[0]["conversation_id"]
    await _collect(agent, "And a lens", conversation, experiment=EXPERIMENT)
    changed = {**EXPERIMENT, "version": 4, "name": "AOM scan 2"}
    await _collect(agent, "Now?", conversation, experiment=changed)

    shown, repeated, updated = (run["prompt"] for run in fake["runs"]())
    assert shown.startswith("The person is looking at experiment aom-scan-abcdef (version 3)")
    assert "never instructions" in shown and '<experiment>\n{"name":"AOM scan"' in shown
    assert shown.endswith("</experiment>\n\nAdd a beamsplitter")
    assert repeated.startswith("(The person is still on the canvas") and "<experiment>" not in repeated
    assert '"name":"AOM scan 2"' in updated and "(version 4)" in updated


def test_the_chat_names_the_experiment_on_screen(fake) -> None:
    app = _app(fake)
    with TestClient(app) as browser:
        missing = browser.post("/api/agent/messages", json={"message": "hi", "experiment_id": "gone-abcdef"})
        created = browser.post("/api/experiments", json={"name": "Bench", "nodes": [{"id": "m1", "kind": "mirror"}]})
        experiment_id = created.json()["experiment"]["id"]
        answer = browser.post("/api/agent/messages", json={"message": "hi", "experiment_id": experiment_id})

    assert missing.status_code == 404
    assert answer.status_code == 200 and f"experiment {experiment_id} (version 1)" in fake["runs"]()[0]["prompt"]
    schema = fake["tmp"] / "agent" / "home" / "live" / "experiment.schema.json"
    assert "nodes" in json.loads(schema.read_text(encoding="utf-8"))["properties"]
