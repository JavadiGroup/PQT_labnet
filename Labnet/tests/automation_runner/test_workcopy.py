"""The working copy: safe unpacking, preflight, and which changed files count as outputs."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path

import pytest

from labnet.automation_runner.workcopy import (
    Refused, RunFolder, collect_outputs, make_skeleton, preflight_tree, snapshot, unpack_inputs,
)


def _zip(entries: list[tuple[str | zipfile.ZipInfo, bytes]], *, run_json: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
        if run_json:
            archive.writestr("run.json", json.dumps({"run": {"id": "r"}, "settings": {}}))
    return buffer.getvalue()


def _raw(name: str, mode: int | None = None) -> zipfile.ZipInfo:
    """A ZipInfo with exactly this name (ZipInfo() would turn '\\' into '/' on Windows)."""

    info = zipfile.ZipInfo("placeholder")
    info.filename = name
    info.orig_filename = name
    if mode is not None:
        info.external_attr = mode << 16
    return info


def _unpack(tmp_path: Path, data: bytes, **caps) -> tuple[dict, Path]:
    """Unpack into a fresh folder under tmp_path/unpacked/."""

    folder = Path(tempfile.mkdtemp(dir=tmp_path))
    archive = folder / "inputs.zip"
    archive.write_bytes(data)
    work = folder / "work"
    work.mkdir()
    return unpack_inputs(archive, work, **caps), work


def test_inputs_unpack_into_the_working_copy_without_run_json(tmp_path: Path) -> None:
    manifest, work = _unpack(tmp_path, _zip([
        ("Tasks/Task1.md", b"# Task"), ("Literature/Pound–Drever–Hall µ.pdf", b"%PDF"), ("Data/example_data/", b""),
    ]))
    assert manifest == {"run": {"id": "r"}, "settings": {}}
    assert (work / "Tasks" / "Task1.md").read_bytes() == b"# Task"
    assert (work / "Literature" / "Pound–Drever–Hall µ.pdf").is_file() and (work / "Data" / "example_data").is_dir()
    assert not (work / "run.json").exists()
    make_skeleton(work)
    for folder in ("Data/new_data", "Analysis/outputs", "ScriptsUtilized/logs", "Protocols"):
        assert (work / folder).is_dir()
    preflight_tree(work)


@pytest.mark.parametrize("name", [
    "../evil.txt", "Tasks/../../evil.txt", "/evil.txt", "C:/evil.txt", "c:evil.txt", "Tasks\\evil.txt",
    "Tasks//x.md", "./Tasks/x.md", "Tasks/x.md:stream", "Tasks/nul\x00.md",
])
def test_paths_that_could_escape_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(Refused):
        _unpack(tmp_path, _zip([(_raw(name), b"evil")]))
    assert not list(tmp_path.rglob("evil.txt"))


def test_links_and_special_files_are_refused(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="link"):
        _unpack(tmp_path, _zip([(_raw("Tasks/link.md", stat.S_IFLNK | 0o777), b"/etc/passwd")]))
    with pytest.raises(Refused):
        _unpack(tmp_path, _zip([(_raw("Tasks/fifo", stat.S_IFIFO | 0o644), b"")]))
    _unpack(tmp_path, _zip([(_raw("Tasks/ok.md", stat.S_IFREG | 0o644), b"fine")]))  # a regular file is fine


def test_counts_and_sizes_are_capped_while_unpacking(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="entries"):
        _unpack(tmp_path, _zip([(f"Tasks/t{index}.md", b"x") for index in range(20)]), max_entries=10)
    bomb = _zip([("Data/example_data/zeros.csv", b"0" * 5_000_000)])
    assert len(bomb) < 50_000
    with pytest.raises(Refused, match="MB"):
        _unpack(tmp_path, bomb, max_bytes=1_000_000)
    with pytest.raises(Refused, match="twice"):
        _unpack(tmp_path, _zip([("Tasks/A.md", b"1"), ("Tasks/a.md", b"2")]))


def test_a_zip_without_run_json_or_not_a_zip_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Refused, match="run.json"):
        _unpack(tmp_path, _zip([("Tasks/Task1.md", b"#")], run_json=False))
    with pytest.raises(Refused, match="zip"):
        _unpack(tmp_path, b"not a zip")


@pytest.mark.parametrize("name", [
    ".claude/settings.json", "Tasks/CLAUDE.md", "Literature/Claude-Notes.MD", ".mcp.json", "Data/.cache/x",
    "Tasks/.hidden.md",
])
def test_preflight_refuses_what_would_configure_claude(tmp_path: Path, name: str) -> None:
    with pytest.raises(Refused, match="can't be run"):
        _unpack(tmp_path, _zip([(name, b"x")]))
    assert [path.name for path in tmp_path.rglob("*") if path.is_file()] == ["inputs.zip"]  # refused before writing


def test_preflight_tree_checks_the_disk_too(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "Tasks").mkdir(parents=True)
    (work / "Tasks" / "claude.md").write_text("x", encoding="utf-8")
    with pytest.raises(Refused):
        preflight_tree(work)


def _write(work: Path, relative: str, data: bytes | str = b"x") -> Path:
    path = work / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def test_outputs_are_new_or_changed_files_in_agent_folders(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _write(work, "Tasks/Task1.md", "# Task")
    _write(work, "Data/example_data/trace.csv", "t,v\n")
    _write(work, "Analysis/old.md", "unchanged")
    make_skeleton(work)
    before = snapshot(work)
    assert set(before) == {"Tasks/Task1.md", "Data/example_data/trace.csv", "Analysis/old.md"}

    _write(work, "Analysis/Analysis.md", "# Analysis")
    _write(work, "Analysis/outputs/fig.png", b"\x89PNG")
    _write(work, "Data/new_data/out.csv", "a\n")
    _write(work, "Protocols/Task1.protocol.md", "p")
    _write(work, "ScriptsUtilized/src/lib.py", "x = 1")
    _write(work, "Tasks/Task1.md", "# Task (edited)")
    _write(work, "Literature/new.pdf", "%PDF")
    _write(work, "top.txt", "x")
    _write(work, "Analysis/.secret", "x")
    _write(work, "Analysis/CLAUDE.md", "x")
    (work / "Data" / "example_data" / "trace.csv").unlink()

    outputs = collect_outputs(work, before)
    assert sorted(item.relative for item in outputs.files) == [
        "Analysis/Analysis.md", "Analysis/outputs/fig.png", "Data/new_data/out.csv", "Protocols/Task1.protocol.md",
        "ScriptsUtilized/src/lib.py",
    ]
    assert outputs.total_bytes == sum(item.path.stat().st_size for item in outputs.files)
    ignored = "\n".join(outputs.ignored)
    for path in ("Tasks/Task1.md", "Literature/new.pdf", "top.txt"):
        assert f"ignored: {path} (not an agent folder)" in outputs.ignored
    assert "ignored: Analysis/.secret (" in ignored and "ignored: Analysis/CLAUDE.md (" in ignored
    assert "ignored: deleting Data/example_data/trace.csv" in ignored
    assert "Analysis/old.md" not in ignored


def test_output_caps_and_hard_links(tmp_path: Path) -> None:
    work = tmp_path / "work"
    make_skeleton(work)
    before = snapshot(work)
    _write(work, "Analysis/big.bin", b"x" * 2000)
    _write(work, "Analysis/a.md", b"x" * 600)
    _write(work, "Analysis/b.md", b"x" * 600)
    original = _write(work, "Data/new_data/original.csv", "a\n")
    os.link(original, work / "Data" / "new_data" / "linked.csv")

    outputs = collect_outputs(work, before, max_file_bytes=1000, max_total_bytes=1000)
    assert [item.relative for item in outputs.files] == ["Analysis/a.md"]
    ignored = "\n".join(outputs.ignored)
    assert "Analysis/big.bin (larger than" in ignored and "Analysis/b.md (the run's outputs are over" in ignored
    assert "Data/new_data/linked.csv (a hard link)" in ignored and "Data/new_data/original.csv (a hard link)" in ignored


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_links_made_by_the_agent_are_never_followed(tmp_path: Path) -> None:
    work = tmp_path / "work"
    make_skeleton(work)
    before = snapshot(work)
    secret = _write(tmp_path, "secret.txt", "token")
    os.symlink(secret, work / "Analysis" / "leak.txt")
    os.symlink(tmp_path, work / "Analysis" / "outside")
    outputs = collect_outputs(work, before)
    assert outputs.files == []
    assert "ignored: Analysis/leak.txt (a link)" in outputs.ignored and "ignored: Analysis/outside (a link)" in outputs.ignored
    with pytest.raises(Refused, match="link"):
        preflight_tree(work)


def test_a_run_folder_starts_empty(tmp_path: Path) -> None:
    folder = RunFolder(tmp_path / "runs" / "run-x")
    folder.create()
    _write(folder.work, "Analysis/x.md")
    folder.create()  # a leftover from a crash is replaced
    assert not any(folder.work.iterdir())
    assert folder.config.is_dir() and folder.sock.is_dir()


def test_a_file_carrying_a_claude_credential_is_never_uploaded(tmp_path: Path) -> None:
    """The login volume is readable in the agent's container; a copied token must not reach the lab."""

    from labnet.automation_runner.workcopy import collect_outputs, holds_credential, make_skeleton, redact_credentials

    work = tmp_path / "work"
    make_skeleton(work)
    token = "sk-ant-ort01-" + "Q" * 60
    (work / "Analysis" / "Analysis.md").write_text("# Results\nfine\n", encoding="utf-8")
    (work / "Data" / "new_data" / "creds.csv").write_text(f"a,b\n1,{token}\n", encoding="utf-8")
    big = work / "Analysis" / "outputs" / "big.txt"  # the token straddles a scan chunk
    big.write_bytes(b"x" * (1024 * 1024 - 10) + token.encode() + b"\n")
    outputs = collect_outputs(work, {})
    uploaded = {item.relative for item in outputs.files}
    assert "Analysis/Analysis.md" in uploaded
    assert "Data/new_data/creds.csv" not in uploaded and "Analysis/outputs/big.txt" not in uploaded
    assert any("creds.csv (it contains a Claude credential)" in note for note in outputs.ignored)
    assert holds_credential(big) and not holds_credential(work / "Analysis" / "Analysis.md")

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(f'{{"text": "read {token} from /claude"}}\n', encoding="utf-8")
    redacted = tmp_path / "out.jsonl"
    redact_credentials(transcript, redacted)
    assert token not in redacted.read_text(encoding="utf-8") and "sk-ant-…" in redacted.read_text(encoding="utf-8")
