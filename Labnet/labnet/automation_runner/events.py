"""Claude Code's stream-json, as the run's events (``{"kind", "text"}``).

Kinds:

* ``assistant``    what the agent wrote (its text, not its thinking)
* ``tool``         a tool it used, with a one-line summary of the inputs
* ``tool_result``  a short, truncated result; errors start with "error:"
* ``result``       how it ended: status, turns, cost
* ``log``          the supervisor's own notes
* ``dev``          the development-only banner

Partial messages (``stream_event``) are dropped; the complete message follows
anyway. Texts are cut to the Control Center's 16 KiB. Everything here is the
agent's output, so it is untrusted: the Control Center redacts it and the
page shows it as plain text.
"""

from __future__ import annotations

import json
from typing import Any

from .client import MAX_EVENT_TEXT_BYTES, truncate_text


ASSISTANT, TOOL, TOOL_RESULT, RESULT, LOG, DEV = "assistant", "tool", "tool_result", "result", "log", "dev"
MAX_TOOL_LINE = 300
MAX_TOOL_RESULT = 600
MCP_PREFIX = "mcp__labnet__"
NOT_SIGNED_IN = (
    "Claude Code is not signed in on the runner. On the runner host, sign in once into the "
    "login volume (see the runner's README: `claude /login`)."
)


def event(kind: str, text: str) -> dict[str, str]:
    return {"kind": kind, "text": truncate_text(text, MAX_EVENT_TEXT_BYTES)}


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _short_path(value: Any) -> str:
    text = str(value)
    return text.removeprefix("/work/")


def tool_summary(name: str, given: dict[str, Any]) -> str:
    """``Read Analysis/Analysis.md``, ``search_literature "PDH lock" (k=5)``, …: one line."""

    short = name.removeprefix(MCP_PREFIX)
    if name in ("Read", "Write", "Edit") and given.get("file_path"):
        detail = _short_path(given["file_path"])
    elif name == "Glob" and given.get("pattern"):
        detail = str(given["pattern"]) + (f" in {_short_path(given['path'])}" if given.get("path") else "")
    elif name == "Grep" and given.get("pattern"):
        detail = f"“{given['pattern']}”" + (f" in {_short_path(given['path'])}" if given.get("path") else "")
    elif name in ("Agent", "Task"):
        detail = f"{given.get('subagent_type') or 'subagent'}: {given.get('description') or ''}".rstrip(": ")
    elif short == "search_literature":
        detail = f"“{given.get('query', '')}”" + (f" k={given['k']}" if given.get("k") is not None else "") + (
            f" in {given['paper']}" if given.get("paper") else "")
    elif short == "read_paper_pages":
        detail = f"{given.get('paper', '')} pages {given.get('first', '?')}–{given.get('last', given.get('first', '?'))}"
    elif short == "run_python":
        detail = " ".join([str(given.get("script", "")), *map(str, given.get("args") or [])])
    elif given:
        detail = json.dumps(given, ensure_ascii=False, sort_keys=True)
    else:
        detail = ""
    return _one_line(f"{short} {detail}".strip(), MAX_TOOL_LINE)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "image":
                    parts.append("[image]")
        return "\n".join(parts)
    return "" if content is None else str(content)


class Translator:
    """Turns stream-json lines into run events; remembers what the run's end needs."""

    def __init__(self) -> None:
        self.model: str | None = None
        self.session_id: str | None = None
        self.result: dict[str, Any] | None = None  # the final "result" message
        self.auth_failed = False
        self._tools: dict[str, str] = {}  # tool_use id → tool name

    def line(self, raw: str) -> list[dict[str, str]]:
        raw = raw.strip()
        if not raw:
            return []
        try:
            message = json.loads(raw)
        except ValueError:
            return [event(LOG, f"Claude Code printed: {_one_line(raw, 500)}")]
        if not isinstance(message, dict):
            return []
        return self(message)

    def __call__(self, message: dict[str, Any]) -> list[dict[str, str]]:
        kind = message.get("type")
        if kind == "system":
            if message.get("subtype") == "init":
                self.model = message.get("model")
                self.session_id = message.get("session_id")
                servers = message.get("mcp_servers") or []
                states = ", ".join(f"{item.get('name')}: {item.get('status')}" for item in servers
                                   if isinstance(item, dict)) or "none"
                tools = message.get("tools") or []
                return [event(LOG, f"Claude Code started: model {self.model}, {len(tools)} tools, "
                                   f"MCP servers ({states}).")]
            return []
        if kind == "stream_event":
            return []  # partial messages: the whole message follows
        if kind == "assistant":
            if message.get("error") == "authentication_failed":
                self.auth_failed = True
            events = []
            for block in (message.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text", "")).strip():
                    events.append(event(ASSISTANT, str(block["text"]).strip()))
                elif block.get("type") == "tool_use":
                    name = str(block.get("name") or "?")
                    if block.get("id"):
                        self._tools[str(block["id"])] = name
                    given = block.get("input") if isinstance(block.get("input"), dict) else {}
                    events.append(event(TOOL, tool_summary(name, given)))
            return events
        if kind == "user":
            events = []
            content = (message.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                name = self._tools.pop(str(block.get("tool_use_id")), "tool").removeprefix(MCP_PREFIX)
                text = _one_line(_result_text(block.get("content")), MAX_TOOL_RESULT)
                prefix = "error" if block.get("is_error") else "ok"
                events.append(event(TOOL_RESULT, f"{prefix}: {name}: {text}" if text else f"{prefix}: {name}"))
            return events
        if kind == "result":
            self.result = message
            text = str(message.get("result") or "")
            if self.auth_failed or "/login" in text:
                self.auth_failed = True
            return [event(RESULT, self.describe_result())]
        return []

    # ---- how it ended ------------------------------------------------------------------------

    @property
    def succeeded(self) -> bool:
        result = self.result
        return bool(result) and result.get("subtype") == "success" and not result.get("is_error")

    def describe_result(self) -> str:
        """``success after 12 turns, $0.43``"""

        result = self.result or {}
        text = str(result.get("subtype") or "ended")
        if result.get("is_error") and text == "success":
            text = "error"
        turns = result.get("num_turns")
        if isinstance(turns, int) and not isinstance(turns, bool):
            text += f" after {turns} turn{'' if turns == 1 else 's'}"
        cost = result.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            text += f", ${cost:.2f}"
        return text

    def summary(self) -> str:
        """The run's summary: the agent's final answer, or why it ended without one."""

        if self.auth_failed:
            return NOT_SIGNED_IN
        result = self.result
        if result is None:
            return ""
        subtype = result.get("subtype")
        text = str(result.get("result") or "").strip()
        if subtype == "error_max_turns":
            return f"The agent stopped at its turn limit ({self.describe_result()})." + (f"\n\n{text}" if text else "")
        if subtype == "error_max_budget_usd":
            return f"The agent stopped at its budget ({self.describe_result()})." + (f"\n\n{text}" if text else "")
        if not self.succeeded:
            return f"The agent ended with an error ({self.describe_result()})." + (f"\n\n{text}" if text else "")
        return text or f"The agent finished ({self.describe_result()})."


__all__ = ["ASSISTANT", "DEV", "LOG", "RESULT", "TOOL", "TOOL_RESULT", "Translator", "event", "tool_summary"]
