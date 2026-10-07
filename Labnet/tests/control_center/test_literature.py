"""The literature index: sandboxed PDF text extraction, incremental FTS5 indexes, safe queries, scope, endpoints."""

from __future__ import annotations

import os
import sqlite3
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labnet.control_center import automation_literature, literature_extract
from labnet.control_center.app import create_app
from labnet.control_center.automation_literature import (
    MARK_END,
    MARK_START,
    Literature,
    LiteratureIndex,
    match_expression,
    query_terms,
    text_status,
)
from labnet.control_center.automation_store import AutomationError, AutomationStore, ProjectSettings
from labnet.control_center.literature_extract import Extraction, extract_pdf

from .test_automation import FakeCentral, _settings


RUNNER = "/api/automation/runner"
PDH = "lasers_and_stabilization/Pound-Drever-Hall locking.pdf"
SPIN = "spin_qubits_and_control/Optical spin locking.pdf"
BOOK = "course_notes/Long notes.pdf"
SCAN = "spin_qubits_and_control/Scanned paper.pdf"
BROKEN = "materials/Broken download.pdf"
NOTES = "materials/Reading notes.md"

PDH_PAGES = [
    "The Pound Drever Hall technique locks a laser to an optical cavity. The error signal is antisymmetric "
    "about resonance and its slope sets the discriminator gain for frequency stabilization.",
    "Phase modulation at radio frequencies creates sidebands; the reflected beam is demodulated by a mixer "
    "to produce the error signal used by the servo loop.",
]
SPIN_PAGES = [
    "Optical spin locking of a solid state qubit uses a detuned laser to dress the electron spin and "
    "protect it from nuclear spin noise in a quantum dot.",
    "Coherence times exceed the free induction decay time; the locking pulse acts like a continuous "
    "dynamical decoupling sequence for the hole spin.",
]


# ---- tiny PDFs, written by hand ---------------------------------------------------------------


def make_pdf(pages: list[str | None]) -> bytes:
    """A minimal valid PDF: one Helvetica text stream per page (None: a page with nothing on it)."""

    objects: list[bytes] = []
    page_ids = []
    font_id = 3
    next_id = 4
    contents: list[tuple[int, int, bytes]] = []
    for text in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        stream = b""
        if text is not None:
            lines = textwrap.wrap(text, 80)
            body = b"".join(
                b"(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1") + b") Tj T* "
                for line in lines
            )
            stream = b"BT /F1 11 Tf 14 TL 72 720 Td " + body + b"ET"
        contents.append((page_id, content_id, stream))
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    objects.append(b"2 0 obj << /Type /Pages /Kids [" + kids + b"] /Count %d >> endobj\n" % len(page_ids))
    objects.append(b"3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    for page_id, content_id, stream in contents:
        objects.append(
            b"%d 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >> endobj\n" % (page_id, font_id, content_id)
        )
        objects.append(
            b"%d 0 obj << /Length %d >> stream\n" % (content_id, len(stream)) + stream + b"\nendstream endobj\n"
        )
    output = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for chunk in objects:
        number = int(chunk.split(b" ", 1)[0])
        offsets[number] = len(output)
        output += chunk
    size = max(offsets) + 1
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        output += b"%010d 00000 n \n" % offsets[number]
    output += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(output)


def _write(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def papers(tmp_path: Path) -> Path:
    """A small lab library: two papers, a long one, a scan, a broken download, notes, a reading list."""

    root = tmp_path / "literature"
    _write(root, PDH, make_pdf(PDH_PAGES))
    _write(root, SPIN, make_pdf(SPIN_PAGES))
    _write(root, BOOK, make_pdf([f"Chapter page {n} about cavity ring down spectroscopy and mode matching." * 2
                                 for n in range(1, 26)]))
    _write(root, SCAN, make_pdf([None, None, None]))
    _write(root, BROKEN, b"%PDF-1.4\n this is an HTML error page, not a PDF \n%%EOF")
    _write(root, NOTES, "# Notes\n\nOur cavity has a finesse of about 20000; see the ring down data.".encode())
    _write(root, "_duplicates/copy.pdf", make_pdf(PDH_PAGES))
    _write(root, ".hidden.pdf", make_pdf(PDH_PAGES))
    _write(root, "collections/GRFP_2027.txt", f"# reading list\n{SPIN}\n".encode())
    return root


def _wait(literature: Literature, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = literature.status()
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("the indexer didn't finish")


@pytest.fixture
def literature(tmp_path: Path, papers: Path):
    store = AutomationStore(tmp_path / "automation", papers)
    index = Literature(store)
    yield index
    index.close()


def _new_project(literature: Literature, **settings) -> dict:
    return literature.store.create(ProjectSettings(name="Cavity study", **settings), author="Rob")


# ---- extraction, in its own process ---------------------------------------------------------


def test_text_is_extracted_in_a_separate_process(tmp_path: Path) -> None:
    pdf = _write(tmp_path, "paper.pdf", make_pdf(PDH_PAGES))
    result = extract_pdf(pdf)
    assert result.error is None and result.pages == 2
    assert "Pound Drever Hall" in result.texts[1] and "sidebands" in result.texts[2]


def test_the_extractor_gets_the_allow_list_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LABNET_API_KEY", "central-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    code = ("import json, os, sys; sys.stdin.read(); "
            "print(json.dumps({'page': 1, 'text': ' '.join(sorted(os.environ))})); "
            "print(json.dumps({'done': True, 'pages': 1}))")
    monkeypatch.setattr(literature_extract, "WORKER", [sys.executable, "-c", code])
    names = extract_pdf(tmp_path / "unused.pdf").texts[1].split()
    assert "LABNET_API_KEY" not in names and "ANTHROPIC_API_KEY" not in names
    assert "PYTHONIOENCODING" in names


def test_a_slow_extraction_is_stopped_and_keeps_the_pages_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code = ("import json, sys, time; sys.stdin.read(); "
            "print(json.dumps({'page': 1, 'text': 'first page'}), flush=True); time.sleep(30)")
    monkeypatch.setattr(literature_extract, "WORKER", [sys.executable, "-c", code])
    started = time.monotonic()
    result = extract_pdf(tmp_path / "unused.pdf", timeout=2)
    assert time.monotonic() - started < 15
    assert "longer than 2 s" in result.error and result.texts == {1: "first page"}


def test_a_worker_that_floods_or_crashes_is_an_error_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(literature_extract, "MAX_TOTAL_BYTES", 10_000)
    flood = "import sys; sys.stdin.read(); [print('{\"page\": 1, \"text\": \"' + 'x' * 900 + '\"}') for _ in range(100)]"
    monkeypatch.setattr(literature_extract, "WORKER", [sys.executable, "-c", flood])
    assert "more text" in extract_pdf(tmp_path / "x.pdf").error
    monkeypatch.setattr(literature_extract, "WORKER", [sys.executable, "-c", "import sys; sys.exit(7)"])
    assert "exit code 7" in extract_pdf(tmp_path / "x.pdf").error


def test_broken_and_imageless_pdfs(tmp_path: Path) -> None:
    broken = extract_pdf(_write(tmp_path, "broken.pdf", b"%PDF-1.4\n<html>not found</html>"))
    assert broken.error and "can't be read" in broken.error
    blank = extract_pdf(_write(tmp_path, "scan.pdf", make_pdf([None, None])))
    assert blank.error is None and blank.pages == 2
    assert text_status(blank.pages, blank.texts) == "no_text"
    assert text_status(2, {1: "x" * 80, 2: ""}) == "ok"
    assert text_status(8, {1: "x" * 80}) == "no_text"  # a cover page, then scans


# ---- the library index -------------------------------------------------------------------------


def test_indexing_the_library_flags_and_skips(literature: Literature) -> None:
    literature.start_library()
    status = _wait(literature)
    assert status["state"] == "idle" and status["done"] == status["total"] == 6, status
    assert (status["papers"], status["no_text"], status["errors"]) == (6, 1, 1)
    rows = literature.library_index.rows()
    assert set(rows) == {PDH, SPIN, BOOK, SCAN, BROKEN, NOTES}  # no _duplicates, collections or hidden files
    assert rows[SCAN]["status"] == "no_text" and rows[SCAN]["pages"] == 3
    assert rows[BROKEN]["status"] == "error" and "can't be read" in rows[BROKEN]["error"]
    assert rows[NOTES]["pages"] == 1 and rows[NOTES]["status"] == "ok"
    assert rows[PDH]["pages"] == 2 and len(rows[PDH]["sha256"]) == 64
    assert literature.library_index.path == literature.store.state / "library" / "literature.sqlite"
    assert not list(literature.library_index.path.parent.glob(".extract-*"))  # private copies are removed


def test_indexing_is_incremental(literature: Literature, papers: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    literature.start_library()
    _wait(literature)
    extracted: list[str] = []
    real = literature_extract.extract_pdf

    def counting(path: Path, **options):
        extracted.append(path.name)
        return real(path, **options)

    monkeypatch.setattr(literature_extract, "extract_pdf", counting)

    literature.start_library()
    assert "unchanged" in _wait(literature)["message"]
    assert len(extracted) == 1  # only the broken PDF is tried again on a rebuild

    extracted.clear()
    pdh = papers / PDH
    os.utime(pdh, ns=(time.time_ns(), time.time_ns() + 10**9))  # touched, same bytes: no extraction
    pdh.with_name("new paper.pdf").write_bytes(make_pdf(["A brand new paper on optomechanics and sideband cooling."]))
    (papers / SPIN).write_bytes(make_pdf(["The spin paper was replaced by a corrected version about hole spins."]))
    (papers / NOTES).unlink()
    literature.start_library()
    _wait(literature)
    assert len(extracted) == 3  # the new one, the changed one, and the broken one
    rows = literature.library_index.rows()
    assert NOTES not in rows and "lasers_and_stabilization/new paper.pdf" in rows
    assert rows[PDH]["mtime_ns"] == os.stat(pdh).st_mtime_ns
    assert rows[SPIN]["pages"] == 1
    project = _new_project(literature)
    assert literature.search(project, "corrected hole spins")["results"][0]["path"] == SPIN
    assert not literature.search(project, "Coherence times exceed")["results"]


def test_a_missing_pypdf_is_reported(literature: Literature, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(automation_literature, "extractor_available", lambda: False)
    with pytest.raises(AutomationError) as refused:
        literature.start_library()
    assert refused.value.status_code == 503 and "pypdf" in str(refused.value)
    assert literature.status()["extractor"] is False

    monkeypatch.undo()
    code = ("import json, sys; sys.stdin.read(); "
            "print(json.dumps({'error': 'Text extraction needs pypdf.', 'missing': 'pypdf'}))")
    monkeypatch.setattr(literature_extract, "WORKER", [sys.executable, "-c", code])
    literature.start_library()
    status = _wait(literature)
    assert status["state"] == "failed" and "pypdf" in status["message"]


def test_one_bad_pdf_or_timeout_never_stops_the_job(literature: Literature, monkeypatch: pytest.MonkeyPatch) -> None:
    real = literature_extract.extract_pdf

    def slow_for_the_book(path: Path, **options):
        if path.stat().st_size > 3000:  # the 25-page book
            return Extraction(pages=0, error="Text extraction took longer than 60 s and was stopped.")
        return real(path, **options)

    monkeypatch.setattr(literature_extract, "extract_pdf", slow_for_the_book)
    literature.start_library()
    status = _wait(literature)
    assert status["state"] == "idle" and status["errors"] == 2
    assert literature.library_index.rows()[PDH]["status"] == "ok"


# ---- queries ---------------------------------------------------------------------------------------


HOSTILE = [
    "cavity NEAR(error signal, 3)", "cavity AND NOT spin", "(cavity OR spin", "cav*", "text:cavity",
    "^cavity", "-spin cavity", '"unbalanced quote', '""', '" "', "cavity\x00spin", 'a"b"c', "{path page}: x",
    "NEAR", "*", "OR", "AND AND", "spin + - ~ @", "'single' quotes", "\u202ecavity", "path:x OR page:1",
    "cavity\x02marker\x03", "NEAR/2", "+", ")))", "a" * 500,
]


def test_user_text_never_becomes_fts5_syntax(literature: Literature) -> None:
    literature.start_library()
    _wait(literature)
    project = _new_project(literature)
    for hostile in HOSTILE:
        found = literature.search(project, hostile, k=5)  # never an error
        assert isinstance(found["results"], list), hostile
        for term in query_terms(hostile):
            assert term.startswith('"') and term.endswith('"') and '"' not in term[1:-1], hostile
    # operators are words: OR doesn't widen the search, NOT doesn't exclude, * doesn't make a prefix
    assert literature.search(project, "cavity OR spin")["mode"] == "any"  # "or" is on no page: fell back
    assert SPIN in {item["path"] for item in literature.search(project, "locking NOT spin", k=50)["results"]}
    assert literature.search(project, "cav*")["results"] == []  # no prefix search: "cav" is no word here


def test_operators_are_searched_as_words() -> None:
    assert query_terms("cavity NOT spin") == ['"cavity"', '"NOT"', '"spin"']
    assert query_terms('"error signal" NEAR(slope)') == ['"error signal"', '"NEAR(slope)"']
    assert query_terms('  ***  ""  ') == []
    assert query_terms("Pound–Drever–Hall") == ['"Pound–Drever–Hall"']  # one phrase: the words in order
    assert match_expression(['"a"', '"b"'], every=True) == '"a" "b"'
    assert match_expression(['"a"', '"b"'], every=False) == '"a" OR "b"'
    assert len(query_terms(" ".join(f"w{n}" for n in range(100)))) == 32


def test_search_ranks_cites_and_highlights(literature: Literature) -> None:
    literature.start_library()
    _wait(literature)
    project = _new_project(literature)
    found = literature.search(project, "Pound–Drever–Hall error signal")
    assert found["mode"] == "all"
    best = found["results"][0]
    assert best["path"] == PDH and best["page"] == 1 and best["source"] == "library"
    assert best["cite"] == f"{PDH} p.1" and best["score"] > 0
    assert MARK_START + "Pound Drever Hall" + MARK_END in best["snippet"]  # a phrase is marked whole
    # porter stemming: "stabilizing" finds "stabilization"; and NOT is just a word, not an exclusion
    assert literature.search(project, "stabilizing")["results"][0]["path"] == PDH
    assert {item["path"] for item in literature.search(project, "locking NOT spin")["results"]} >= {SPIN}


def test_and_falls_back_to_or(literature: Literature) -> None:
    literature.start_library()
    _wait(literature)
    project = _new_project(literature)
    found = literature.search(project, "sidebands xylophone")
    assert found["mode"] == "any" and found["results"][0]["path"] == PDH and found["results"][0]["page"] == 2
    assert literature.search(project, "xylophone zeppelin")["results"] == []


def test_scope_paper_filter_and_project_papers(literature: Literature) -> None:
    literature.start_library()
    _wait(literature)
    store = literature.store
    project = _new_project(literature, library_collections=["GRFP_2027"])
    own = store.folder(project["id"]) / "Literature" / "Our cavity paper.pdf"
    own.write_bytes(make_pdf(["Our own measurement of the error signal from the Pound Drever Hall lock in the lab."]))
    (own.parent / ".secret.pdf").write_bytes(make_pdf(["hidden Pound Drever Hall"]))

    found = literature.search(project, "Pound Drever Hall error signal")
    assert [(item["path"], item["source"]) for item in found["results"]] == [("Literature/Our cavity paper.pdf", "project")]
    for query in ("locking", "spin", "cavity", "laser", "error signal"):
        for item in literature.search(project, query, k=50)["results"]:
            assert item["path"] in {SPIN, "Literature/Our cavity paper.pdf"}, (query, item)
    assert (store.state / project["id"] / "literature.sqlite").is_file()

    listing = literature.papers(project)["papers"]
    assert {(item["path"], item["source"], item["status"]) for item in listing} == {
        (SPIN, "library", "ok"), ("Literature/Our cavity paper.pdf", "project", "ok")
    }

    only = literature.search(project, "spin", paper=SPIN)
    assert {item["path"] for item in only["results"]} == {SPIN}
    with pytest.raises(AutomationError) as outside:
        literature.search(project, "Pound", paper=PDH)  # in the library, not in this project's part
    assert outside.value.status_code == 404
    with pytest.raises(AutomationError):
        literature.pages(project, PDH)
    with pytest.raises(AutomationError):
        literature.search(project, "Pound", paper="../literature/" + PDH)

    # a folder scope works the same way, and the whole library when nothing is named
    folders = _new_project(literature, library_folders=["lasers_and_stabilization"])
    assert {item["path"] for item in literature.search(folders, "locking", k=50)["results"]} == {PDH}
    everything = _new_project(literature)
    assert {item["path"] for item in literature.search(everything, "locking", k=50)["results"]} == {PDH, SPIN}


def test_project_papers_are_refreshed_before_a_search(literature: Literature) -> None:
    project = _new_project(literature)
    folder = literature.store.folder(project["id"]) / "Literature"
    (folder / "draft.md").write_text("Notes on the heterodyne beat note.", encoding="utf-8")
    assert literature.search(project, "heterodyne")["results"][0]["cite"] == "Literature/draft.md p.1"
    assert literature._stale(project["id"]) == 0  # indexed as it is: the next search doesn't wait
    (folder / "draft.md").unlink()
    (folder / "other.txt").write_text("Homodyne detection with a balanced detector.", encoding="utf-8")
    assert literature.search(project, "heterodyne")["results"] == []
    assert literature.search(project, "homodyne")["results"][0]["path"] == "Literature/other.txt"
    notes = literature.search(project, "homodyne")["notes"]
    assert any("hasn't been indexed" in note for note in notes)  # the shared library wasn't indexed yet


def test_a_slow_project_refresh_doesnt_hold_up_search(literature: Literature, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    real = literature_extract.extract_pdf

    def blocked(path: Path, **options):
        release.wait(20)
        return real(path, **options)

    monkeypatch.setattr(literature_extract, "extract_pdf", blocked)
    monkeypatch.setattr(automation_literature, "PROJECT_REFRESH_SECONDS", 0.5)
    project = _new_project(literature)
    folder = literature.store.folder(project["id"]) / "Literature"
    (folder / "big.pdf").write_bytes(make_pdf(["Slow paper about frequency combs."]))
    found = literature.refresh_project(project["id"], wait=0.5)
    assert "still being indexed" in found[0]
    release.set()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not literature.project_index(project["id"]).rows():
        time.sleep(0.05)
    assert literature.search(project, "frequency combs")["results"][0]["path"] == "Literature/big.pdf"


def test_pages_are_capped(literature: Literature) -> None:
    literature.start_library()
    _wait(literature)
    project = _new_project(literature)
    read = literature.pages(project, BOOK, 1, 25)
    assert (read["first"], read["last"], read["pages"], read["more"]) == (1, 20, 25, True)
    assert len(read["page_texts"]) == 20 and "Chapter page 3 " in read["page_texts"][2]["text"]
    assert read["page_texts"][4]["cite"] == f"{BOOK} p.5"
    tail = literature.pages(project, BOOK, 21)
    assert (tail["first"], tail["last"], tail["more"]) == (21, 25, False)
    with pytest.raises(AutomationError):
        literature.pages(project, BOOK, 26)
    with pytest.raises(AutomationError):
        literature.pages(project, BOOK, 0)
    scan = literature.pages(project, SCAN)
    assert scan["status"] == "no_text" and [page["text"] for page in scan["page_texts"]] == ["", "", ""]


def test_a_damaged_index_is_rebuilt(literature: Literature) -> None:
    path = literature.library_index.path
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a database" * 100)
    literature.start_library()
    assert _wait(literature)["papers"] == 6
    assert Path(f"{path}.damaged").exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# ---- endpoints -------------------------------------------------------------------------------------


@pytest.fixture
def lab(tmp_path: Path, papers: Path):
    settings = _settings(tmp_path, agent_literature_dir=papers)
    with TestClient(create_app(settings, client_factory=lambda _: FakeCentral())) as client:
        client.tokens = {name: client.app.state.runners.add(name) for name in ("lab-laptop", "central-docker")}
        yield client


def _index(client: TestClient) -> dict:
    reply = client.post("/api/automation/library/index", json={})
    assert reply.status_code == 202, reply.text
    return _wait(client.app.state.literature)


def test_the_index_status_and_rebuild_endpoints(lab: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    status = lab.get("/api/automation/library/index").json()["index"]
    assert status["state"] == "idle" and status["papers"] == 0 and status["configured"] and status["extractor"]
    assert lab.post("/api/automation/library/index", content=b"{}").status_code == 415
    assert lab.post("/api/automation/library/index", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403

    release = threading.Event()
    real = literature_extract.extract_pdf

    def blocked(path: Path, **options):
        release.wait(20)
        return real(path, **options)

    monkeypatch.setattr(literature_extract, "extract_pdf", blocked)
    started = lab.post("/api/automation/library/index", json={})
    assert started.status_code == 202 and started.json()["index"]["state"] == "running"
    again = lab.post("/api/automation/library/index", json={})
    assert again.status_code == 409 and again.json()["index"]["state"] == "running"
    running = lab.get("/api/automation/library/index").json()["index"]
    assert running["state"] == "running" and running["total"] == 6
    release.set()
    done = _wait(lab.app.state.literature)
    assert (done["papers"], done["no_text"], done["errors"]) == (6, 1, 1) and done["finished_at"]


def test_the_people_search_endpoints(lab: TestClient) -> None:
    _index(lab)
    project = lab.post("/api/automation/projects", json={"name": "GRFP", "library_collections": ["GRFP_2027"]}).json()["project"]
    base = f"/api/automation/projects/{project['id']}/literature"
    found = lab.get(f"{base}/search", params={"q": "spin locking", "k": 3})
    assert found.status_code == 200 and found.headers["cache-control"] == "no-store"
    assert {item["path"] for item in found.json()["results"]} == {SPIN}
    assert found.json()["results"][0]["cite"].startswith(SPIN + " p.")
    assert lab.get(f"{base}/search", params={"q": "Pound Drever Hall"}).json()["results"] == []
    assert lab.get(f"{base}/papers").json()["papers"][0]["path"] == SPIN
    assert lab.get(f"{base}/search", params={"q": "x" * 501}).status_code == 422
    assert lab.get(f"{base}/search", params={"q": ""}).status_code == 422
    assert lab.get(f"{base}/search", params={"q": "spin", "k": 51}).status_code == 422
    assert lab.get(f"{base}/search", params={"q": "spin", "k": 0}).status_code == 422
    assert lab.get(f"{base}/search", params={"q": "spin", "paper": PDH}).status_code == 404
    assert lab.get("/api/automation/projects/nope-abcdef/literature/search", params={"q": "spin"}).status_code == 404


def test_the_runner_literature_endpoints(lab: TestClient) -> None:
    _index(lab)
    project = lab.post("/api/automation/projects", json={"name": "GRFP", "library_collections": ["GRFP_2027"]}).json()["project"]
    assert lab.post(f"/api/automation/projects/{project['id']}/runs", json={"task": "Tasks/Task1.md"}).status_code == 201
    token = {"Authorization": f"Bearer {lab.tokens['lab-laptop']}"}
    other = {"Authorization": f"Bearer {lab.tokens['central-docker']}"}
    run = lab.post(f"{RUNNER}/claim", headers=token).json()["run"]
    base = f"{RUNNER}/runs/{run['id']}/literature"

    assert lab.get(f"{base}/search", params={"q": "spin"}).status_code == 401
    assert lab.get(f"{base}/search", params={"q": "spin"}, headers=other).status_code == 404
    assert lab.get(f"{base}/search", params={"q": "spin"}, headers={**token, "Sec-Fetch-Site": "same-origin"}).status_code == 403

    found = lab.get(f"{base}/search", params={"q": "spin locking", "k": 50}, headers=token)
    assert found.status_code == 200 and {item["path"] for item in found.json()["results"]} == {SPIN}
    assert lab.get(f"{base}/search", params={"q": "spin", "k": 99}, headers=token).status_code == 422
    assert lab.get(f"{base}/search", params={"q": "spin", "paper": PDH}, headers=token).status_code == 404
    listing = lab.get(f"{base}/papers", headers=token).json()["papers"]
    assert [item["path"] for item in listing] == [SPIN]
    pages = lab.get(f"{base}/pages", params={"paper": SPIN, "first": 2}, headers=token).json()
    assert pages["page_texts"] == [{"page": 2, "text": pages["page_texts"][0]["text"], "cite": f"{SPIN} p.2"}]
    assert "dynamical decoupling" in pages["page_texts"][0]["text"]
    assert lab.get(f"{base}/pages", params={"paper": PDH}, headers=token).status_code == 404
    assert lab.get(f"{base}/pages", params={"paper": "../../state/runners/lab-laptop.json"}, headers=token).status_code == 404

    finished = lab.post(f"{RUNNER}/runs/{run['id']}/finish", json={"status": "done"}, headers=token)
    assert finished.status_code == 200
    assert lab.get(f"{base}/search", params={"q": "spin"}, headers=token).status_code == 409
    assert lab.get(f"{base}/pages", params={"paper": SPIN}, headers=token).status_code == 409


def test_shutdown_stops_a_running_index(tmp_path: Path, papers: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code = "import sys, time; sys.stdin.read(); time.sleep(60)"
    monkeypatch.setattr(literature_extract, "WORKER", [sys.executable, "-c", code])
    store = AutomationStore(tmp_path / "automation", papers)
    literature = Literature(store)
    literature.start_library()
    time.sleep(1.0)
    started = time.monotonic()
    literature.close()
    assert time.monotonic() - started < 10
    rows = LiteratureIndex(store.state / "library" / "literature.sqlite").rows()
    assert all(row["status"] != "error" or "exit code" not in (row["error"] or "") for row in rows.values())
