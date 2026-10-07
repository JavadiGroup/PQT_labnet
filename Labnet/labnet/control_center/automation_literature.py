"""The literature index: keyword search over the lab library and each project's papers.

    <LABNET_AUTOMATION_DIR>/state/library/literature.sqlite   the shared library's papers
    <LABNET_AUTOMATION_DIR>/state/<id>/literature.sqlite      one project's own Literature/

Each index is SQLite with FTS5: ``papers`` (one row per file: size, mtime,
sha256, pages, ok | no_text | error) and ``chunks``, one row per page, ranked
with BM25 (``porter unicode61 remove_diacritics 2``, so "stabilized" finds
"stabilization" and "Zeeman" finds "Zeemán"). Search results cite
``<path> p.<N>``.

What is indexed follows the library tree's rules (``AutomationStore.library_tree``):
no ``_``-folders, no ``collections/``, no hidden names, no links, junctions or
hard links; ``.pdf`` files page by page, ``.txt`` and ``.md`` as one page.
PDF text comes from ``literature_extract``, which runs pypdf in a throwaway
process, never in the Control Center.

Indexing is incremental: a file whose size and mtime are unchanged is skipped,
one whose bytes are unchanged (sha256) only has its row updated, and a file
that is gone is dropped. One background thread does all indexing, one paper
at a time: the library when someone presses "Index library", and a project's
own papers just before that project is searched (bounded, so a search never
waits long; between two library papers a project's refresh goes first).

A project sees the part of the library its settings name (``library_folders``
and ``library_collections``; none means all of it, exactly as the runner's
library endpoint decides, see ``AutomationStore.library_scope``) plus its own
papers. Free text never reaches FTS5 as syntax: every word and every
"quoted phrase" becomes a quoted FTS5 string.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import literature_extract
from .automation_paths import PathRefused, clean_relative, open_inside
from .automation_store import AutomationError, AutomationStore
from .experiment_store import replace_with_retry
from .literature_extract import ExtractorMissing, clean_text


LOGGER = logging.getLogger(__name__)

INDEX_NAME = "literature.sqlite"
INDEXED_EXTENSIONS = frozenset({".pdf", ".txt", ".md"})
EXTRACTOR_VERSION = "1"  # bump to re-extract every paper after the extractor changes
MAX_QUERY_CHARS = 500
MAX_K = 50
MAX_TERMS = 32
MAX_PAGES_PER_CALL = 20
MAX_PAPER_BYTES = 256 * 2**20
PROJECT_REFRESH_SECONDS = 30.0  # how long a search waits for a project's new papers
SNIPPET_TOKENS = 32
# Around each hit in a snippet (control characters never occur in indexed text).
MARK_START, MARK_END = "\x02", "\x03"
# A page counts as having text with at least this many letters and digits.
_TEXT_PAGE_CHARACTERS = 50
NO_PYPDF = (
    "Text extraction needs pypdf, which isn't installed on the Control Center: "
    "pip install \"pypdf>=5,<7\" (it comes with the control-center extra)."
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS papers (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    pages INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'no_text', 'error')),
    error TEXT,
    indexed_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    path UNINDEXED, page UNINDEXED, text, tokenize = 'porter unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extractor_available() -> bool:
    """pypdf can be found (without importing it here: it only ever runs in the worker)."""

    try:
        return importlib.util.find_spec("pypdf") is not None
    except (ImportError, ValueError):
        return False


# ---- turning free text into a safe FTS5 query --------------------------------------------------

_PHRASE = re.compile(r'"([^"]*)"')
_QUERY_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def query_terms(text: str) -> list[str]:
    """Words and "quoted phrases" of ``text``, each as a quoted FTS5 string.

    Inside double quotes FTS5 treats everything as text to tokenize, so AND,
    OR, NOT, NEAR, ``*``, ``^``, ``:``, ``-`` and parentheses lose any meaning.
    Double quotes themselves are only ever used to delimit, never passed on.
    A word without a letter or digit is dropped (it would be an empty phrase).
    """

    text = _QUERY_CONTROL.sub(" ", text)
    terms: list[str] = []

    def add(chunk: str) -> None:
        chunk = " ".join(chunk.replace('"', " ").split())
        if chunk and any(character.isalnum() for character in chunk):
            term = f'"{chunk}"'
            if term not in terms:
                terms.append(term)

    position = 0
    for match in _PHRASE.finditer(text):
        for word in text[position:match.start()].split():
            add(word)
        add(match.group(1))
        position = match.end()
    for word in text[position:].split():
        add(word)
    return terms[:MAX_TERMS]


def match_expression(terms: list[str], *, every: bool) -> str:
    """All terms (FTS5's implicit AND), or any of them."""

    return (" " if every else " OR ").join(terms)


# ---- one index file ------------------------------------------------------------------------------


class LiteratureIndex:
    """One SQLite FTS5 index. Readers open their own connection; the indexer thread is the one writer."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.is_file()

    def connect(self) -> sqlite3.Connection:
        """A writer's connection, creating the file and schema (its folder must exist)."""

        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(_SCHEMA)
            version = connection.execute("SELECT value FROM meta WHERE key = 'extractor'").fetchone()
            if version is None or version[0] != EXTRACTOR_VERSION:  # extracted differently: start over
                with connection:
                    connection.execute("DELETE FROM chunks")
                    connection.execute("DELETE FROM papers")
                    connection.execute(
                        "INSERT INTO meta(key, value) VALUES ('extractor', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (EXTRACTOR_VERSION,)
                    )
        except BaseException:
            connection.close()
            raise
        return connection

    def open_writer(self) -> sqlite3.Connection:
        """:meth:`connect`, setting a damaged index aside and starting a new one."""

        try:
            return self.connect()
        except sqlite3.DatabaseError as exc:
            if not self.exists():
                raise
            LOGGER.warning("The literature index %s is damaged (%s); starting a new one.", self.path, exc)
            for suffix in ("", "-wal", "-shm"):
                damaged = Path(f"{self.path}{suffix}")
                if damaged.exists():  # retried: a status poll may have it open for a moment
                    replace_with_retry(damaged, Path(f"{self.path}.damaged{suffix}"))
            return self.connect()

    def _read(self) -> sqlite3.Connection | None:
        if not self.exists():
            return None
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _reading(self, work: Any, empty: Any) -> Any:
        try:
            connection = self._read()
        except sqlite3.Error as exc:
            raise AutomationError(503, f"The literature index can't be read: {exc}") from None
        if connection is None:
            return empty
        try:
            return work(connection)
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return empty  # created but never filled
            raise AutomationError(503, f"The literature index can't be read: {exc}") from None
        except sqlite3.DatabaseError as exc:
            raise AutomationError(503, f"The literature index can't be read: {exc}") from None
        finally:
            connection.close()

    # ---- reading ------------------------------------------------------------------------

    def rows(self) -> dict[str, dict[str, Any]]:
        """Every indexed paper by path."""

        def work(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
            cursor = connection.execute(
                "SELECT path, size, mtime_ns, sha256, pages, status, error, indexed_at FROM papers"
            )
            names = [column[0] for column in cursor.description]
            return {row[0]: dict(zip(names, row)) for row in cursor}

        return self._reading(work, {})

    def counts(self) -> dict[str, int]:
        def work(connection: sqlite3.Connection) -> dict[str, int]:
            found = dict(connection.execute("SELECT status, COUNT(*) FROM papers GROUP BY status").fetchall())
            return {"papers": sum(found.values()), "no_text": found.get("no_text", 0), "errors": found.get("error", 0)}

        return self._reading(work, {"papers": 0, "no_text": 0, "errors": 0})

    def meta(self, key: str) -> str | None:
        def work(connection: sqlite3.Connection) -> str | None:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

        return self._reading(work, None)

    def search(self, expression: str, paths: list[str], k: int) -> list[dict[str, Any]]:
        """The best ``k`` pages matching ``expression`` among ``paths`` (best first)."""

        if not paths:
            return []

        def work(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            cursor = connection.execute(
                "SELECT path, page, bm25(chunks) AS rank, snippet(chunks, 2, ?, ?, '…', ?) FROM chunks "
                "WHERE chunks MATCH ? AND path IN (SELECT value FROM json_each(?)) ORDER BY rank LIMIT ?",
                (MARK_START, MARK_END, SNIPPET_TOKENS, expression, _json_list(paths), k),
            )
            return [{"path": path, "page": int(page), "rank": rank, "snippet": snippet}
                    for path, page, rank, snippet in cursor]

        return self._reading(work, [])

    def page_texts(self, path: str, first: int, last: int) -> dict[int, str]:
        def work(connection: sqlite3.Connection) -> dict[int, str]:
            cursor = connection.execute(
                "SELECT page, text FROM chunks WHERE path = ? AND page BETWEEN ? AND ?", (path, first, last)
            )
            return {int(page): text for page, text in cursor}

        return self._reading(work, {})

    # ---- writing (the indexer thread only) ----------------------------------------------

    @staticmethod
    def put(
        connection: sqlite3.Connection, path: str, *, size: int, mtime_ns: int, sha256: str, pages: int,
        status: str, error: str | None, texts: dict[int, str],
    ) -> None:
        with connection:
            connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
            connection.executemany(
                "INSERT INTO chunks(path, page, text) VALUES (?, ?, ?)",
                [(path, page, text) for page, text in sorted(texts.items()) if text.strip()],
            )
            connection.execute(
                "INSERT INTO papers(path, size, mtime_ns, sha256, pages, status, error, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET size = excluded.size, "
                "mtime_ns = excluded.mtime_ns, sha256 = excluded.sha256, pages = excluded.pages, "
                "status = excluded.status, error = excluded.error, indexed_at = excluded.indexed_at",
                (path, size, mtime_ns, sha256, pages, status, error, _now()),
            )

    @staticmethod
    def touch(connection: sqlite3.Connection, path: str, *, size: int, mtime_ns: int) -> None:
        with connection:
            connection.execute("UPDATE papers SET size = ?, mtime_ns = ? WHERE path = ?", (size, mtime_ns, path))

    @staticmethod
    def remove(connection: sqlite3.Connection, paths: list[str]) -> None:
        with connection:
            for path in paths:
                connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
                connection.execute("DELETE FROM papers WHERE path = ?", (path,))

    @staticmethod
    def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        with connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def _json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def text_status(pages: int, texts: dict[int, str]) -> str:
    """ok, or no_text when (almost) no page has a text layer (a scan, or only pictures)."""

    with_text = sum(1 for text in texts.values() if sum(c.isalnum() for c in text) >= _TEXT_PAGE_CHARACTERS)
    if pages == 0 or with_text == 0:
        return "no_text"
    if pages >= 4 and with_text / pages < 0.25:
        return "no_text"
    return "ok"


# ---- the indexer ---------------------------------------------------------------------------------


class _ProjectRequest:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.error: str | None = None


class Literature:
    """The Control Center's literature indexes, their one indexing thread, and search."""

    def __init__(self, store: AutomationStore) -> None:
        self.store = store
        self.library_index = LiteratureIndex(store.state / "library" / INDEX_NAME)
        self._condition = threading.Condition()
        self._library_job: Generator[None, None, None] | None = None
        self._projects: dict[str, _ProjectRequest] = {}  # pending project refreshes, oldest first
        self._project_errors: dict[str, str] = {}
        self._process: Any = None  # the extractor running now, to stop it on close
        self._closed = False
        self._thread: threading.Thread | None = None
        self._progress: dict[str, Any] = {
            "state": "idle", "done": 0, "total": 0, "current": None,
            "started_at": None, "finished_at": None, "message": None,
        }

    # ---- files ---------------------------------------------------------------------------

    def library_files(self) -> list[PurePosixPath]:
        """The library's papers, as the library tree lists them (.pdf, .txt, .md)."""

        if self.store.library is None or not self.store.library.is_dir():
            return []
        return [PurePosixPath(item["path"]) for item in self.store.library_tree()["entries"]
                if item["kind"] == "file" and "refused" not in item
                and PurePosixPath(item["path"]).suffix.lower() in INDEXED_EXTENSIONS]

    def project_files(self, project_id: str) -> list[PurePosixPath]:
        """A project's own papers: files under Literature/ (no links, no hidden names)."""

        folder = self.store.folder(project_id)
        return [PurePosixPath(item["path"]) for item in self.store._walk(folder, ("Literature",), owners=False)
                if item["kind"] == "file" and "refused" not in item
                and PurePosixPath(item["path"]).suffix.lower() in INDEXED_EXTENSIONS]

    def project_index(self, project_id: str) -> LiteratureIndex:
        self.store.folder(project_id)  # 404 for a malformed id
        return LiteratureIndex(self.store.state / project_id / INDEX_NAME)

    # ---- indexing one file ---------------------------------------------------------------

    def _index_file(
        self, connection: sqlite3.Connection, root: Path, relative: PurePosixPath,
        row: dict[str, Any] | None, workdir: Path, *, retry_errors: bool = False,
    ) -> str:
        """Bring one paper's entry up to date: skipped | updated | indexed | refused | stopped.

        ``retry_errors`` reads a paper that failed last time again even if it is unchanged
        (the library's explicit rebuild does; a refresh before a search doesn't, so a slow
        PDF can't hold up every search).
        """

        path = relative.as_posix()
        try:
            file, info = open_inside(root, relative)  # refuses links, junctions and hard links
        except (PathRefused, FileNotFoundError, OSError):
            if row is not None:
                LiteratureIndex.remove(connection, [path])
            return "refused"
        if retry_errors and row is not None and row.get("status") == "error":
            row = None
        copy: Path | None = None
        try:
            with file:
                if row is not None and row["size"] == info.st_size and row["mtime_ns"] == info.st_mtime_ns:
                    return "skipped"
                if info.st_size > MAX_PAPER_BYTES:
                    LiteratureIndex.put(
                        connection, path, size=info.st_size, mtime_ns=info.st_mtime_ns, sha256="", pages=0,
                        status="error", error=f"Larger than {MAX_PAPER_BYTES // 2**20} MB; not indexed.", texts={},
                    )
                    return "indexed"
                digest = hashlib.sha256()
                is_pdf = relative.suffix.lower() == ".pdf"
                head = bytearray()
                output = None
                if is_pdf:  # a private copy: the worker reads exactly the bytes hashed here
                    handle, name = tempfile.mkstemp(dir=workdir, prefix=".extract-", suffix=".pdf")
                    copy = Path(name)
                    output = os.fdopen(handle, "wb")
                try:
                    while chunk := file.read(1024 * 1024):
                        digest.update(chunk)
                        if output is not None:
                            output.write(chunk)
                        elif len(head) < 4 * literature_extract.MAX_PAGE_CHARS:
                            head.extend(chunk)
                finally:
                    if output is not None:
                        output.close()
            sha256 = digest.hexdigest()
            if row is not None and row["sha256"] == sha256 and row["sha256"]:
                LiteratureIndex.touch(connection, path, size=info.st_size, mtime_ns=info.st_mtime_ns)
                return "updated"
            if is_pdf:
                assert copy is not None
                extraction = literature_extract.extract_pdf(copy, cwd=workdir, on_start=self._started)
                if self._closed:
                    return "stopped"  # killed at shutdown: not the paper's fault, so nothing is recorded
                pages, texts, error = extraction.pages, extraction.texts, extraction.error
            else:
                text = clean_text(bytes(head).decode("utf-8-sig", "replace"))
                pages, texts, error = 1, {1: text}, None
            status = "error" if error else text_status(pages, texts)
            LiteratureIndex.put(
                connection, path, size=info.st_size, mtime_ns=info.st_mtime_ns, sha256=sha256, pages=pages,
                status=status, error=error, texts=texts,
            )
            return "indexed"
        finally:
            self._process = None
            if copy is not None:
                copy.unlink(missing_ok=True)

    def _started(self, process: Any) -> None:
        self._process = process
        if self._closed:
            process.kill()

    @staticmethod
    def _clear_copies(workdir: Path) -> None:
        for leftover in workdir.glob(".extract-*.pdf"):
            leftover.unlink(missing_ok=True)

    # ---- the library job ---------------------------------------------------------------------

    def _set(self, **changes: Any) -> None:
        with self._condition:
            self._progress.update(changes)

    def _library_steps(self, files: list[PurePosixPath]) -> Generator[None, None, None]:
        """Index the library, one paper per step (the thread runs project refreshes in between)."""

        started = time.monotonic()
        workdir = self.library_index.path.parent
        workdir.mkdir(parents=True, exist_ok=True)
        self._clear_copies(workdir)
        connection = self.library_index.open_writer()
        try:
            known = self._rows(connection)
            wanted = {relative.as_posix() for relative in files}
            gone = [path for path in known if path not in wanted]
            LiteratureIndex.remove(connection, gone)
            outcomes: dict[str, int] = {}
            for number, relative in enumerate(files, 1):
                if self._closed:
                    return
                self._set(current=relative.as_posix())
                outcome = self._index_file(
                    connection, self.store.library, relative, known.get(relative.as_posix()), workdir,  # type: ignore[arg-type]
                    retry_errors=True,
                )
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                self._set(done=number)
                yield
            LiteratureIndex.set_meta(connection, "finished_at", _now())
        finally:
            connection.close()
        seconds = time.monotonic() - started
        changed = outcomes.get("indexed", 0)
        self._set(
            state="idle", current=None, finished_at=_now(),
            message=(f"Checked {len(files)} papers in {seconds:.0f} s: {changed} read, "
                     f"{outcomes.get('skipped', 0) + outcomes.get('updated', 0)} unchanged"
                     + (f", {len(gone)} removed" if gone else "")
                     + (f", {outcomes['refused']} skipped (links)" if outcomes.get("refused") else "") + "."),
        )

    @staticmethod
    def _rows(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        cursor = connection.execute("SELECT path, size, mtime_ns, sha256, status FROM papers")
        return {path: {"size": size, "mtime_ns": mtime_ns, "sha256": sha256, "status": status}
                for path, size, mtime_ns, sha256, status in cursor}

    def start_library(self) -> dict[str, Any]:
        """Start (re)indexing the library; 409 while it runs, 503 without pypdf."""

        if self.store.library is None or not self.store.library.is_dir():
            raise AutomationError(409, "No lab library is set up (LABNET_AGENT_LITERATURE_DIR), so there is nothing to index.")
        if not extractor_available():
            raise AutomationError(503, NO_PYPDF)
        with self._condition:
            if self._closed:
                raise AutomationError(503, "The Control Center is shutting down.")
            if self._library_job is not None:
                raise AutomationError(409, "The library is being indexed already.", index=self._status_locked())
            files = self.library_files()
            self._library_job = self._library_steps(files)
            self._progress.update(state="running", done=0, total=len(files), current=None, started_at=_now(),
                                  finished_at=None, message=None)
            self._ensure_thread()
            self._condition.notify_all()
        return self.status()

    def _status_locked(self) -> dict[str, Any]:
        return dict(self._progress)

    def status(self) -> dict[str, Any]:
        with self._condition:
            progress = self._status_locked()
        library = self.store.library
        try:
            counts = self.library_index.counts()
            finished = progress["finished_at"] or self.library_index.meta("finished_at")
        except AutomationError as exc:  # damaged: the next rebuild sets it aside and starts again
            counts, finished = {"papers": 0, "no_text": 0, "errors": 0}, progress["finished_at"]
            if progress["state"] != "running":
                progress["message"] = f"{exc} Press Index library to rebuild it."
        return {
            **progress,
            "finished_at": finished,
            **counts,
            "configured": library is not None and library.is_dir(),
            "extractor": extractor_available(),
        }

    # ---- project refreshes ------------------------------------------------------------------

    def _refresh_project(self, project_id: str) -> None:
        try:
            self.store.get(project_id)
        except AutomationError:
            return  # deleted meanwhile
        index = self.project_index(project_id)
        folder = self.store.folder(project_id)
        workdir = index.path.parent
        if not workdir.is_dir():
            return
        files = self.project_files(project_id)
        if not files and not index.exists():
            return
        self._clear_copies(workdir)
        connection = index.open_writer()
        try:
            known = self._rows(connection)
            wanted = {relative.as_posix() for relative in files}
            LiteratureIndex.remove(connection, [path for path in known if path not in wanted])
            for relative in files:
                if self._closed:
                    return
                self._index_file(connection, folder, relative, known.get(relative.as_posix()), workdir)
        finally:
            connection.close()

    def _stale(self, project_id: str) -> int:
        """How many of a project's papers its index doesn't have as they are now (by size and mtime)."""

        files = self.project_files(project_id)
        rows = self.project_index(project_id).rows()
        folder = self.store.folder(project_id)
        stale = len(set(rows) - {relative.as_posix() for relative in files})
        for relative in files:
            row = rows.get(relative.as_posix())
            try:
                info = os.lstat(folder.joinpath(*relative.parts))
            except OSError:
                continue
            if row is None or row["size"] != info.st_size or row["mtime_ns"] != info.st_mtime_ns:
                stale += 1
        return stale

    def refresh_project(self, project_id: str, *, wait: float = PROJECT_REFRESH_SECONDS) -> list[str]:
        """Bring a project's index up to date before a search; notes if that took too long."""

        stale = self._stale(project_id)
        if not stale:
            error = self._project_errors.get(project_id)
            return [error] if error else []
        with self._condition:
            if self._closed:
                return ["The Control Center is shutting down; this project's newest papers aren't searched."]
            request = self._projects.get(project_id)
            if request is None:
                request = self._projects[project_id] = _ProjectRequest()
            self._ensure_thread()
            self._condition.notify_all()
        if not request.done.wait(wait):
            return [f"{stale} of this project's papers are still being indexed; these results cover what is indexed "
                    "so far. Search again in a minute."]
        return [request.error] if request.error else []

    # ---- the one indexing thread -----------------------------------------------------------

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="labnet-literature", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        unfinished: Generator[None, None, None] | None = None
        while True:
            with self._condition:
                while not self._closed and not self._projects and self._library_job is None:
                    self._condition.wait()
                if self._closed:
                    unfinished, self._library_job = self._library_job, None
                    break
                project = next(iter(self._projects), None)
                request = self._projects.pop(project) if project is not None else None
                job = self._library_job
            if project is not None and request is not None:
                try:
                    self._refresh_project(project)
                    self._project_errors.pop(project, None)
                except ExtractorMissing:
                    request.error = self._project_errors[project] = NO_PYPDF
                except Exception as exc:  # one project's trouble never stops the thread
                    LOGGER.exception("Indexing project %s failed", project)
                    request.error = self._project_errors[project] = f"This project's papers couldn't be indexed: {exc}"
                finally:
                    request.done.set()
                continue
            if job is None:
                continue
            try:
                next(job)
            except StopIteration:
                with self._condition:
                    self._library_job = None
            except ExtractorMissing:
                self._finish_failed(NO_PYPDF)
            except Exception as exc:
                LOGGER.exception("Indexing the library failed")
                self._finish_failed(f"Indexing stopped: {exc}")
        if unfinished is not None:
            # Closed mid-job: end the job here, in the thread that opened its SQLite connection,
            # so the connection is closed now (not later, by the garbage collector, in another thread).
            unfinished.close()

    def _finish_failed(self, message: str) -> None:
        with self._condition:
            self._library_job = None
            self._progress.update(state="failed", current=None, finished_at=_now(), message=message)

    def close(self) -> None:
        """Stop indexing (at shutdown): kill a running extractor, let the thread end."""

        with self._condition:
            self._closed = True
            self._condition.notify_all()
            for request in self._projects.values():
                request.done.set()
            self._projects.clear()
        process = self._process
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=10)

    # ---- what a project (its people, or its agent) can read --------------------------------

    def _scope(self, project: dict[str, Any], paper: str | None) -> tuple[list[str], list[str]]:
        """(library papers, project papers) the project may search, narrowed to ``paper``."""

        library = self.store.library_scope(project)
        own = [relative.as_posix() for relative in self.project_files(project["id"])]
        if paper is None:
            return library, own
        library = [path for path in library if path == paper]
        own = [path for path in own if path == paper]
        if not library and not own:
            raise AutomationError(404, "That paper isn't in this project's literature.")
        return library, own

    @staticmethod
    def clean_paper(raw: str | None) -> str | None:
        if raw is None or raw == "":
            return None
        try:
            return clean_relative(raw).as_posix()
        except PathRefused:
            raise AutomationError(404, "That paper isn't in this project's literature.") from None

    def search(self, project: dict[str, Any], query: str, *, k: int = 10, paper: str | None = None) -> dict[str, Any]:
        """The best pages for ``query`` in the project's part of the library and its own papers."""

        if not isinstance(query, str) or not query.strip():
            raise AutomationError(422, "Send something to search for (q).")
        if len(query) > MAX_QUERY_CHARS:
            raise AutomationError(422, f"Search for at most {MAX_QUERY_CHARS} characters.")
        if not 1 <= k <= MAX_K:
            raise AutomationError(422, f"Ask for 1 to {MAX_K} results (k).")
        paper = self.clean_paper(paper)
        library, own = self._scope(project, paper)
        notes = self.refresh_project(project["id"]) if own or self.project_index(project["id"]).exists() else []
        notes += self._library_notes(library)
        terms = query_terms(query)
        if not terms:
            return {"query": query, "mode": None, "results": [], "notes": [*notes, "There are no words to search for."]}
        project_index = self.project_index(project["id"])
        mode = "all"
        found = self._search_both(match_expression(terms, every=True), library, own, project_index, k)
        if not found and len(terms) > 1:
            mode = "any"
            found = self._search_both(match_expression(terms, every=False), library, own, project_index, k)
        results = [
            {"path": item["path"], "source": item["source"], "page": item["page"],
             "score": round(-item["rank"], 3), "snippet": item["snippet"], "cite": f"{item['path']} p.{item['page']}"}
            for item in found
        ]
        return {"query": query, "mode": mode, "results": results, "notes": notes}

    def _search_both(
        self, expression: str, library: list[str], own: list[str], project_index: LiteratureIndex, k: int
    ) -> list[dict[str, Any]]:
        found = [{**item, "source": "library"} for item in self.library_index.search(expression, library, k)]
        found += [{**item, "source": "project"} for item in project_index.search(expression, own, k)]
        found.sort(key=lambda item: (item["rank"], item["path"], item["page"]))  # BM25: lower is better
        return found[:k]

    def _library_notes(self, library: list[str]) -> list[str]:
        if not library:
            return []
        if not self.library_index.exists():
            return ["The lab library hasn't been indexed yet: press Index library on the Automation tab."]
        rows = self.library_index.rows()
        missing = sum(1 for path in library if path not in rows)
        if missing:
            return [f"{missing} of this project's library papers aren't indexed yet (Index library on the Automation tab)."]
        return []

    def papers(self, project: dict[str, Any]) -> dict[str, Any]:
        """Every paper the project can search, with its page count and whether it has text."""

        library, own = self._scope(project, None)
        notes = self.refresh_project(project["id"]) if own or self.project_index(project["id"]).exists() else []
        notes += self._library_notes(library)
        listing = []
        for source, paths, rows in (
            ("library", library, self.library_index.rows()),
            ("project", own, self.project_index(project["id"]).rows()),
        ):
            for path in paths:
                row = rows.get(path)
                if row is None:
                    listing.append({"path": path, "source": source, "pages": None, "status": "not_indexed", "error": None})
                else:
                    listing.append({"path": path, "source": source, "pages": row["pages"], "status": row["status"],
                                    "error": row["error"]})
        return {"papers": listing, "notes": notes}

    def pages(
        self, project: dict[str, Any], raw_paper: str | None, first: int = 1, last: int | None = None
    ) -> dict[str, Any]:
        """The text of pages ``first``..``last`` (at most 20) of one paper in the project's scope."""

        paper = self.clean_paper(raw_paper)
        if paper is None:
            raise AutomationError(422, "Say which paper (paper=<path>).")
        library, own = self._scope(project, paper)
        if own:
            self.refresh_project(project["id"])
            source, index = "project", self.project_index(project["id"])
        else:
            source, index = "library", self.library_index
        row = index.rows().get(paper)
        if row is None:
            raise AutomationError(404, "That paper hasn't been indexed yet.")
        total = int(row["pages"])
        if last is None:
            last = first + MAX_PAGES_PER_CALL - 1
        if first < 1 or last < first:
            raise AutomationError(422, "Pages are numbered from 1, and last can't come before first.")
        if total and first > total:
            raise AutomationError(422, f"{paper} has {total} pages.")
        last = min(last, first + MAX_PAGES_PER_CALL - 1, max(total, 1))
        texts = index.page_texts(paper, first, last)
        return {
            "path": paper, "source": source, "pages": total, "status": row["status"], "error": row["error"],
            "first": first, "last": last, "more": last < total,
            "page_texts": [{"page": page, "text": texts.get(page, ""), "cite": f"{paper} p.{page}"}
                           for page in range(first, last + 1)],
        }


__all__ = [
    "EXTRACTOR_VERSION",
    "LiteratureIndex",
    "Literature",
    "MARK_END",
    "MARK_START",
    "MAX_K",
    "MAX_PAGES_PER_CALL",
    "MAX_QUERY_CHARS",
    "extractor_available",
    "match_expression",
    "query_terms",
    "text_status",
]
