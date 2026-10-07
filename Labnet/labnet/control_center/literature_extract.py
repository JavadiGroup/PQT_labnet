"""Text out of a PDF, in a throwaway process: the literature index's extractor.

Papers come from the internet, and a PDF parser is a large attack surface, so
pypdf never runs inside the Control Center. :func:`extract_pdf` starts

    python -m labnet.control_center.literature_extract

for one PDF with the script worker's allow-list environment (no ``LABNET_*``
settings, no credentials), no console window, a time limit and caps on what
it may send back. The worker reads one JSON object from stdin::

    {"path": "<a private copy of the PDF>", "max_pages": 3000, "max_page_chars": 200000}

and writes JSON lines to stdout, one per page and then one that ends it::

    {"page": 1, "text": "..."}
    {"done": true, "pages": 12}            or   {"error": "...", "missing": "pypdf"?}

Pages arrive as they are read, so a PDF that runs out of time still gives the
pages read so far. Nothing the worker writes is trusted beyond its shape.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


EXTRACT_TIMEOUT_SECONDS = 60.0
MAX_PAGES = 3000
MAX_PAGE_CHARS = 200_000
MAX_TOTAL_BYTES = 64 * 2**20  # everything one PDF may send back
MAX_LINE_BYTES = 4 * MAX_PAGE_CHARS + 1024  # one page as UTF-8 JSON, with room for escapes
MAX_STDERR_BYTES = 16 * 1024
# The worker, as a command line (tests swap in a slow or broken one).
WORKER = [sys.executable, "-m", "labnet.control_center.literature_extract"]

# Control characters other than tab and newline (the index uses \x02/\x03 as highlight marks).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\ud800-\udfff]")
_HYPHEN_BREAK = re.compile(r"([a-z])-\n([a-z])")


class ExtractorMissing(RuntimeError):
    """pypdf isn't installed where the worker runs."""


@dataclass
class Extraction:
    """What came out of one PDF: page texts (1-based numbers), and why it stopped, if it failed."""

    pages: int = 0  # how many pages the PDF has (as far as the worker got)
    texts: dict[int, str] = field(default_factory=dict)
    error: str | None = None


def clean_text(text: str, limit: int = MAX_PAGE_CHARS) -> str:
    """Page text as stored: no control characters, words broken by a line-end hyphen rejoined."""

    text = _CONTROL.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    return _HYPHEN_BREAK.sub(r"\1\2", text)[:limit]


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def extract_pdf(
    path: Path,
    *,
    timeout: float | None = None,
    max_pages: int = MAX_PAGES,
    max_page_chars: int = MAX_PAGE_CHARS,
    cwd: Path | None = None,
    on_start: Any = None,
) -> Extraction:
    """Run the worker on ``path`` (a private copy) and collect its pages.

    Raises :class:`ExtractorMissing` when the worker has no pypdf; every other
    failure (encrypted, broken, too slow, too much output) is ``Extraction.error``.
    ``on_start(process)`` lets the caller keep the process to kill it on shutdown.
    """

    from .script_runner import worker_environment  # the same allow-list as script workers

    limit = EXTRACT_TIMEOUT_SECONDS if timeout is None else timeout
    request = json.dumps({"path": str(path), "max_pages": max_pages, "max_page_chars": max_page_chars})
    try:
        process = subprocess.Popen(
            WORKER,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=worker_environment(),
            cwd=str(cwd) if cwd is not None else None,
            creationflags=_creation_flags(),
        )
    except OSError as exc:
        return Extraction(error=f"The text extractor couldn't start: {exc.strerror or exc}")
    if on_start is not None:
        on_start(process)

    result = Extraction()
    state: dict[str, Any] = {"ended": False, "overflow": None, "missing": False, "error": None}
    stderr = bytearray()

    def read_stdout() -> None:
        total = 0
        assert process.stdout is not None
        while True:
            line = process.stdout.readline(MAX_LINE_BYTES + 1)
            if not line:
                return
            total += len(line)
            if len(line) > MAX_LINE_BYTES or total > MAX_TOTAL_BYTES:
                state["overflow"] = "The extractor sent back more text than a paper may have."
                process.kill()
                return
            try:
                message = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            page, text = message.get("page"), message.get("text")
            if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= max_pages and isinstance(text, str):
                result.texts[page] = clean_text(text, max_page_chars)
                result.pages = max(result.pages, page)
            elif message.get("done") is True:
                pages = message.get("pages")
                if isinstance(pages, int) and not isinstance(pages, bool) and pages >= 0:
                    result.pages = pages
                state["ended"] = True
            elif isinstance(message.get("error"), str):
                state["error"] = message["error"][:300]
                state["missing"] = message.get("missing") == "pypdf"
                state["ended"] = True

    def read_stderr() -> None:
        assert process.stderr is not None
        while chunk := process.stderr.read(4096):
            if len(stderr) < MAX_STDERR_BYTES:
                stderr.extend(chunk[: MAX_STDERR_BYTES - len(stderr)])

    readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        assert process.stdin is not None
        try:
            process.stdin.write(request.encode("utf-8"))
            process.stdin.close()
        except OSError:
            pass  # it died at once; its exit code says so below
        try:
            process.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for reader in readers:
            reader.join(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass

    if state["missing"]:
        raise ExtractorMissing(state["error"] or "pypdf is not installed")
    if timed_out:
        read = f" after page {max(result.texts)}" if result.texts else ""
        result.error = f"Text extraction took longer than {limit:g} s and was stopped{read}."
    elif state["overflow"]:
        result.error = state["overflow"]
    elif state["error"]:
        result.error = state["error"]
    elif not state["ended"]:
        last = bytes(stderr).decode("utf-8", "replace").strip().splitlines()
        detail = f": {last[-1][:200]}" if last else ""
        result.error = f"The text extractor stopped unexpectedly (exit code {process.returncode}){detail}."
    return result


# ---- the worker (runs in its own process) ---------------------------------------------------


def _limit_memory() -> None:
    """Best effort on POSIX: a hostile PDF can't make the worker take the machine's memory."""

    try:
        import resource  # POSIX only

        two_gb = 2 * 2**30
        resource.setrlimit(resource.RLIMIT_AS, (two_gb, two_gb))
    except (ImportError, ValueError, OSError):
        pass


def _emit(message: dict[str, Any]) -> None:
    line = json.dumps(message, ensure_ascii=False).encode("utf-8", "replace") + b"\n"
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()


def _short(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def main() -> int:
    _limit_memory()
    try:
        request = json.loads(sys.stdin.buffer.read(64 * 1024).decode("utf-8"))
        path = str(request["path"])
        max_pages = min(int(request.get("max_pages", MAX_PAGES)), MAX_PAGES)
        max_page_chars = min(int(request.get("max_page_chars", MAX_PAGE_CHARS)), MAX_PAGE_CHARS)
    except (ValueError, KeyError, TypeError) as exc:
        _emit({"error": f"Bad request: {_short(exc)}"})
        return 2
    try:
        import logging

        logging.disable(logging.WARNING)  # pypdf warns a lot about sloppy PDFs; not useful here
        import pypdf
        from pypdf.errors import FileNotDecryptedError
    except ImportError:
        _emit({"error": "Text extraction needs pypdf, which isn't installed.", "missing": "pypdf"})
        return 3
    sys.setrecursionlimit(5000)
    try:
        reader = pypdf.PdfReader(path)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    _emit({"error": "The PDF is encrypted and needs a password."})
                    return 0
            except Exception as exc:  # a missing AES library, or a bad key
                _emit({"error": f"The PDF is encrypted and can't be opened ({_short(exc)})."})
                return 0
        count = len(reader.pages)
        for number in range(1, min(count, max_pages) + 1):
            try:
                text = reader.pages[number - 1].extract_text() or ""
            except (FileNotDecryptedError, MemoryError):
                raise
            except Exception:  # one bad page doesn't lose the rest
                text = ""
            _emit({"page": number, "text": text[:max_page_chars]})
        _emit({"done": True, "pages": count})
    except FileNotDecryptedError:
        _emit({"error": "The PDF is encrypted and needs a password."})
    except Exception as exc:  # PdfReadError, RecursionError, MemoryError: anything a broken PDF raises
        _emit({"error": f"The PDF can't be read ({_short(exc)})."})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
