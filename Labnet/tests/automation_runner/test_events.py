"""Claude Code's stream-json → run events."""

from __future__ import annotations

import json

from labnet.automation_runner.events import NOT_SIGNED_IN, Translator, tool_summary


def _lines(translator: Translator, *messages: dict) -> list[dict]:
    return [event for message in messages for event in translator.line(json.dumps(message))]


def test_a_session_becomes_readable_events() -> None:
    translator = Translator()
    events = _lines(
        translator,
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "session_id": "s", "tools": ["Read"] * 9,
         "mcp_servers": [{"name": "labnet", "status": "connected"}]},
        {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}},
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "secret plan"},
            {"type": "text", "text": "  I'll look at the data.  "},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/work/Data/example_data/a.csv"}},
            {"type": "tool_use", "id": "t2", "name": "mcp__labnet__read_paper_pages",
             "input": {"paper": "lasers/pdh.pdf", "first": 3, "last": 5}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "t,v\n0,1\n" * 500},
            {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
             "content": [{"type": "text", "text": "No such paper"}]},
        ]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "total_cost_usd": 0.0049,
         "result": "All done."},
    )
    assert events[0] == {"kind": "log", "text": "Claude Code started: model claude-sonnet-5-5, 9 tools, "
                                                 "MCP servers (labnet: connected)."}
    assert events[1] == {"kind": "assistant", "text": "I'll look at the data."}
    assert events[2] == {"kind": "tool", "text": "Read Data/example_data/a.csv"}
    assert events[3] == {"kind": "tool", "text": "read_paper_pages lasers/pdh.pdf pages 3–5"}
    assert events[4]["kind"] == "tool_result" and events[4]["text"].startswith("ok: Read: t,v 0,1")
    assert len(events[4]["text"]) <= 620 and events[4]["text"].endswith("…")
    assert events[5] == {"kind": "tool_result", "text": "error: read_paper_pages: No such paper"}
    assert events[6] == {"kind": "result", "text": "success after 1 turn, $0.00"}
    assert len(events) == 7 and "secret plan" not in json.dumps(events)
    assert translator.succeeded and translator.summary() == "All done." and translator.session_id == "s"


def test_texts_are_cut_to_the_control_centers_limit() -> None:
    [event] = _lines(Translator(), {"type": "assistant", "message": {"content": [{"type": "text", "text": "µ" * 20_000}]}})
    assert len(event["text"].encode()) <= 16 * 1024 and event["text"].endswith("(truncated)")


def test_garbage_and_unknown_messages() -> None:
    translator = Translator()
    assert translator.line("") == []
    assert translator.line("[1, 2]") == []
    assert translator.line(json.dumps({"type": "rate_limit_event"})) == []
    [note] = translator.line("Error: not JSON " + "x" * 1000)
    assert note["kind"] == "log" and note["text"].startswith("Claude Code printed: Error: not JSON") and len(note["text"]) < 600


def test_tool_summaries_are_one_short_line() -> None:
    assert tool_summary("Glob", {"pattern": "**/*.csv", "path": "/work/Data"}) == "Glob **/*.csv in Data"
    assert tool_summary("Grep", {"pattern": "lock"}) == "Grep “lock”"
    assert tool_summary("Agent", {"subagent_type": "literature-reviewer", "description": "Find PDH papers"}) == (
        "Agent literature-reviewer: Find PDH papers")
    assert tool_summary("mcp__labnet__run_python", {"script": "ScriptsUtilized/analysis_scripts/fit.py",
                                                     "args": ["--n", 3]}) == (
        "run_python ScriptsUtilized/analysis_scripts/fit.py --n 3")
    assert tool_summary("mcp__labnet__list_papers", {}) == "list_papers"
    long = tool_summary("Write", {"file_path": "/work/Analysis/" + "a" * 1000 + ".md", "content": "x"})
    assert len(long) <= 300 and long.endswith("…")
    assert "\n" not in tool_summary("Other", {"text": "two\nlines"})


def test_how_a_run_ended_becomes_its_summary() -> None:
    def ended(**result) -> Translator:
        translator = Translator()
        _lines(translator, {"type": "result", **result})
        return translator

    turns = ended(subtype="error_max_turns", is_error=True, num_turns=60, total_cost_usd=2.5)
    assert not turns.succeeded and turns.summary() == "The agent stopped at its turn limit (error_max_turns after 60 turns, $2.50)."
    budget = ended(subtype="error_max_budget_usd", is_error=True, num_turns=9, total_cost_usd=5.01)
    assert "budget" in budget.summary()
    error = ended(subtype="error_during_execution", is_error=True, num_turns=2, result="API overloaded")
    assert error.summary().startswith("The agent ended with an error") and "API overloaded" in error.summary()
    assert ended(subtype="success", is_error=False, num_turns=3).summary() == "The agent finished (success after 3 turns)."
    unsigned = ended(subtype="success", is_error=True, result="Invalid API key · Please run /login")
    assert not unsigned.succeeded and unsigned.summary() == NOT_SIGNED_IN
    assert Translator().summary() == ""
