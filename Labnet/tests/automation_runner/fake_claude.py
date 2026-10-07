"""A stand-in for ``claude -p --output-format stream-json`` in the supervisor tests.

Run with cwd = the working copy. Reads the prompt on stdin, records what it was
started with (argv, cwd, prompt) into $FAKE_CLAUDE_RECORD, then plays a
scenario ($FAKE_CLAUDE_SCENARIO):

* ``work``: realistic stream-json (init, text, tool_use, tool_result, result) and
  files in the agent's folders and, wrongly, in people's folders;
* ``slow``: init, then a line every 50 ms for up to 30 s (to be stopped);
* ``crash``: some stderr and exit 3 without a result;
* ``unsigned``: an authentication failure, as Claude Code reports it.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


SESSION = "11111111-2222-3333-4444-555555555555"


def say(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def assistant(*blocks: dict) -> None:
    say({"type": "assistant", "session_id": SESSION, "message": {"role": "assistant", "content": list(blocks)}})


def tool_result(tool_id: str, text: str, *, error: bool = False) -> None:
    say({"type": "user", "session_id": SESSION, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": [{"type": "text", "text": text}], "is_error": error},
    ]}})


def write(relative: str, data: str | bytes) -> None:
    path = Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def main() -> int:
    prompt = sys.stdin.read()
    record = os.environ.get("FAKE_CLAUDE_RECORD")
    if record:
        Path(record).write_text(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt}),
                                encoding="utf-8")
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "work")
    say({"type": "system", "subtype": "init", "session_id": SESSION, "model": "claude-sonnet-5-5",
         "cwd": "/work", "tools": ["Read", "Write", "mcp__labnet__search_literature"],
         "mcp_servers": [{"name": "labnet", "status": "connected"}]})

    if scenario == "crash":
        sys.stderr.write("Error: something broke inside Claude Code\n")
        return 3
    if scenario == "unsigned":
        assistant({"type": "text", "text": "Invalid API key · Please run /login"})
        say({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1,
             "result": "Invalid API key · Please run /login", "total_cost_usd": 0, "session_id": SESSION})
        return 1
    if scenario == "slow":
        deadline = time.monotonic() + 30
        n = 0
        while time.monotonic() < deadline:
            n += 1
            assistant({"type": "text", "text": f"Still thinking ({n})."})
            time.sleep(0.05)
        say({"type": "result", "subtype": "success", "is_error": False, "num_turns": n, "result": "late",
             "total_cost_usd": 1.0, "session_id": SESSION})
        return 0

    assistant({"type": "thinking", "thinking": "private"},
              {"type": "text", "text": "I'll read the task and the example data first."})
    assistant({"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/work/Tasks/Task1.md"}})
    tool_result("toolu_1", "# Task 1\nFit the trace.")
    assistant({"type": "tool_use", "id": "toolu_2", "name": "mcp__labnet__search_literature",
               "input": {"query": "Pound Drever Hall", "k": 5}})
    tool_result("toolu_2", "PDH.pdf p.6: the error signal ...")
    assistant({"type": "tool_use", "id": "toolu_3", "name": "mcp__labnet__run_python",
               "input": {"script": "ScriptsUtilized/analysis_scripts/fit.py", "args": ["--fast"]}})
    tool_result("toolu_3", "Traceback: boom", error=True)

    write("ScriptsUtilized/analysis_scripts/fit.py", "print('fit')\n")
    write("Data/new_data/fit.csv", "t,v\n0,1\n")
    write("Data/new_data/DATA_SUMMARY.md", "# Data\nfit.csv: t (s), v (V)\n")
    write("Analysis/outputs/fit.png", b"\x89PNG\r\n\x1a\nfake")
    write("Analysis/Analysis.md", "# Analysis\n\nMethod, results, limitations. PDH.pdf p.6\n")
    # Things the agent must not change: never uploaded.
    write("Tasks/Task1.md", "# Task 1\nrewritten by the agent\n")
    write("Tasks/Task9.md", "# a task the agent made up\n")
    write("notes.txt", "top level\n")
    write("Analysis/.hidden.md", "hidden\n")
    write("Analysis/CLAUDE.md", "instructions for the next run\n")

    assistant({"type": "text", "text": "Done: Analysis/Analysis.md has the fit."})
    say({"type": "result", "subtype": "success", "is_error": False, "num_turns": 7, "duration_ms": 1234,
         "result": "The fit is in Analysis/Analysis.md.", "total_cost_usd": 0.4321, "session_id": SESSION})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
