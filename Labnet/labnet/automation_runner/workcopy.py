"""A run's working copy: unpacking its inputs, and finding what the agent produced.

    <work_dir>/runs/<run_id>/
      inputs.zip         as downloaded (deleted once unpacked)
      work/              the working copy: the agent's /work
      config/            run-settings.json, agents.json, mcp.json, briefing.md: the agent's /labnet (read-only)
      sock/              tools.sock, the supervisor's tools server: the agent's /run/labnet
      transcript.jsonl   Claude Code's stream-json, line by line
      runner.log         what the supervisor did

The zip comes from the Control Center, which already leaves out hidden names,
``CLAUDE*.md`` and links. It is checked again here (defence in depth): entries
are refused unless they are plain relative paths, links are refused, and the
number of entries and the bytes they unpack to are capped while unpacking, not
from the zip's own claims.

After the agent ends, the outputs are the regular files that are new or
changed (by sha256) and that ``check_agent_write`` allows. Anything else that
changed is reported, never uploaded; the Control Center checks every upload
again anyway.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from labnet.control_center.automation_paths import (
    AGENT,
    SKELETON,
    NotWritable,
    PathRefused,
    check_agent_write,
    clean_relative,
    is_instructions,
    zone_of,
)


MAX_ENTRIES = 20_000
MAX_UNPACKED_BYTES = 4 * 1024**3
MAX_RUN_JSON_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_FILE_BYTES = 100 * 1024**2  # the Control Center's default LABNET_AUTOMATION_MAX_FILE_MB
MAX_OUTPUT_BYTES = 500 * 1024**2  # its default LABNET_AUTOMATION_RUN_QUOTA_MB
MAX_OUTPUT_FILES = 5_000
AGENT_UID = AGENT_GID = 1000  # "node" in the agent image
_DRIVE = re.compile(r"^[A-Za-z]:")


class Refused(Exception):
    """The run can't start from these inputs; the message says why, for a person."""


@dataclass(frozen=True, slots=True)
class RunFolder:
    root: Path

    @property
    def inputs(self) -> Path:
        return self.root / "inputs.zip"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def sock(self) -> Path:
        return self.root / "sock"

    @property
    def devices(self) -> Path:
        """The device relay's folder (gateway.sock), created when a grant starts; mounted into run containers."""

        return self.root / "devices"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript.jsonl"

    @property
    def log(self) -> Path:
        return self.root / "runner.log"

    def create(self) -> None:
        if self.root.exists():
            remove_tree(self.root)
        for folder in (self.root, self.work, self.config, self.sock):
            folder.mkdir(parents=True, exist_ok=False)


def remove_tree(path: Path) -> None:
    def retry(function: Any, target: str, _exc: Any) -> None:  # read-only files (Windows)
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


# ---- unpacking ------------------------------------------------------------------------


def _entry_path(name: str) -> tuple[PurePosixPath, bool]:
    """(the entry as a relative path, is it a folder), or Refused for anything else."""

    if not name or "\\" in name or "\x00" in name:
        raise Refused(f"The inputs contain an entry with an unusable name: {name[:80]!r}.")
    if name.startswith("/") or _DRIVE.match(name):
        raise Refused(f"The inputs contain an absolute path: {name[:80]!r}.")
    folder = name.endswith("/")
    parts = name.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise Refused(f"The inputs contain a path that leaves its folder: {name[:80]!r}.")
    if any(":" in part for part in parts):
        raise Refused(f"The inputs contain a name with ':' in it: {name[:80]!r}.")
    return PurePosixPath(*parts), folder


def _is_link(info: zipfile.ZipInfo) -> bool:
    """A link, or any other special file (Unix mode bits); plain permission bits alone are fine."""

    kind = stat.S_IFMT(info.external_attr >> 16)
    return kind not in (0, stat.S_IFREG, stat.S_IFDIR)


def unpack_inputs(
    archive_path: Path,
    work: Path,
    *,
    max_entries: int = MAX_ENTRIES,
    max_bytes: int = MAX_UNPACKED_BYTES,
) -> dict[str, Any]:
    """Unpack the inputs zip into ``work`` (which must exist and be empty); returns run.json.

    run.json stays out of the working copy: it is the Control Center's, not the project's.
    """

    manifest: dict[str, Any] | None = None
    total = 0
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise Refused(f"The inputs aren't a readable zip: {exc}") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > max_entries:
            raise Refused(f"The inputs hold {len(entries)} entries; at most {max_entries} are unpacked.")
        seen: set[str] = set()
        for info in entries:
            # orig_filename: as stored (on Windows zipfile turns '\' into '/' and cuts at NUL).
            _entry_path(info.orig_filename)
            relative, is_folder = _entry_path(info.filename)
            if _is_link(info):
                raise Refused(f"The inputs contain a link: {relative}.")
            key = relative.as_posix().casefold()
            if key in seen and not is_folder:
                raise Refused(f"The inputs contain {relative} twice.")
            seen.add(key)
            if relative == PurePosixPath("run.json") and not is_folder:
                if info.file_size > MAX_RUN_JSON_BYTES:
                    raise Refused("run.json is too large.")
                with archive.open(info) as source:
                    raw = source.read(MAX_RUN_JSON_BYTES + 1)
                if len(raw) > MAX_RUN_JSON_BYTES:
                    raise Refused("run.json is too large.")
                try:
                    manifest = json.loads(raw.decode("utf-8"))
                except ValueError as exc:
                    raise Refused(f"run.json isn't valid JSON: {exc}") from exc
                continue
            preflight([relative])  # before anything is written
            target = work.joinpath(*relative.parts)
            if total + info.file_size > max_bytes:
                raise Refused(f"The inputs unpack to more than {max_bytes / 2**20:g} MB.")
            try:
                if is_folder:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "xb") as sink:  # "x": never through a link
                    written = 0
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        total += len(chunk)
                        if written > info.file_size or total > max_bytes:
                            raise Refused(f"{relative} unpacks to more than the zip says, or the inputs are too large.")
                        sink.write(chunk)
            except (OSError, zipfile.BadZipFile, EOFError) as exc:
                raise Refused(f"Unpacking {relative} failed: {exc}") from exc
    if not isinstance(manifest, dict):
        raise Refused("The inputs have no run.json.")
    return manifest


def make_skeleton(work: Path) -> None:
    """The project's fixed folders (the agent's ones aren't in the inputs)."""

    for folder in SKELETON:
        work.joinpath(*folder.split("/")).mkdir(parents=True, exist_ok=True)


# ---- preflight ----------------------------------------------------------------------------


def _refusal(relative: PurePosixPath) -> str | None:
    for part in relative.parts:
        if part.casefold() == ".claude":
            return f"{relative}: a .claude folder would configure Claude Code"
        if part.casefold() == ".mcp.json":
            return f"{relative}: .mcp.json would add MCP servers"
        if is_instructions(part):
            return f"{relative}: CLAUDE*.md would be read as instructions"
        if part.startswith("."):
            return f"{relative}: hidden names are not allowed in a project"
    return None


def preflight(paths: Iterable[PurePosixPath]) -> None:
    """Refused if any path would configure or instruct Claude Code (or is hidden)."""

    for relative in paths:
        reason = _refusal(relative)
        if reason is not None:
            raise Refused(f"The project can't be run: {reason}.")


def _walk(root: Path) -> Iterable[tuple[PurePosixPath, os.DirEntry[str]]]:
    """Every entry under ``root`` (links not followed), as (relative path, entry)."""

    stack: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    while stack:
        folder, prefix = stack.pop()
        folders = []
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                relative = (*prefix, entry.name)
                yield PurePosixPath(*relative), entry
                if entry.is_dir(follow_symlinks=False) and not _entry_is_link(entry):
                    folders.append((Path(entry.path), relative))
        stack.extend(reversed(folders))  # folders in name order


def _entry_is_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & 0x400)  # a junction or other reparse point (Windows)


def preflight_tree(work: Path) -> None:
    """``preflight`` over what is on disk, and no links anywhere."""

    for relative, entry in _walk(work):
        if _entry_is_link(entry):
            raise Refused(f"The project can't be run: {relative} is a link.")
        preflight([relative])


# ---- what changed ------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# A Claude credential (OAuth access or refresh token, API key). The login volume is readable in
# the agent's container; a file that carries one is never uploaded where lab members can read it.
CREDENTIAL = re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}")
_SCAN_CHUNK = 1024 * 1024


def holds_credential(path: Path) -> bool:
    """True if the file contains something shaped like a Claude credential (scanned in overlapping chunks)."""

    tail = b""
    with open(path, "rb") as file:
        while chunk := file.read(_SCAN_CHUNK):
            if CREDENTIAL.search(tail + chunk):
                return True
            tail = chunk[-256:]
    return False


def redact_credentials(source: Path, target: Path) -> None:
    """``source`` copied to ``target`` with every credential replaced (for the transcript and log)."""

    target.write_bytes(CREDENTIAL.sub("sk-ant-…".encode(), source.read_bytes()))


def snapshot(work: Path) -> dict[str, str]:
    """{relative path: sha256} of every regular file (links are not followed)."""

    files = {}
    for relative, entry in _walk(work):
        if entry.is_file(follow_symlinks=False) and not _entry_is_link(entry):
            files[relative.as_posix()] = _sha256(Path(entry.path))
    return files


@dataclass(frozen=True, slots=True)
class Output:
    relative: str  # e.g. Analysis/Analysis.md
    path: Path
    size: int


@dataclass(slots=True)
class Outputs:
    files: list[Output] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)  # "ignored: <path> (<why>)", for the run's events

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


def collect_outputs(
    work: Path,
    before: dict[str, str],
    *,
    max_file_bytes: int = MAX_OUTPUT_FILE_BYTES,
    max_total_bytes: int = MAX_OUTPUT_BYTES,
    max_files: int = MAX_OUTPUT_FILES,
) -> Outputs:
    """New or changed regular files in the agent's folders; everything else changed is only reported."""

    result = Outputs()
    seen: set[str] = set()
    total = 0
    for relative, entry in _walk(work):
        key = relative.as_posix()
        if _entry_is_link(entry):
            result.ignored.append(f"ignored: {key} (a link)")
            continue
        if entry.is_dir(follow_symlinks=False):
            continue
        if not entry.is_file(follow_symlinks=False):
            result.ignored.append(f"ignored: {key} (not a regular file)")
            continue
        seen.add(key)
        path = Path(entry.path)
        try:
            info = os.lstat(path)  # not entry.stat(): on Windows its st_nlink is always 0
            digest = _sha256(path)
        except OSError as exc:
            result.ignored.append(f"ignored: {key} (unreadable: {exc.strerror or exc})")
            continue
        if before.get(key) == digest:
            continue  # unchanged
        try:
            check_agent_write(clean_relative(key))
        except NotWritable as exc:
            zone = zone_of(relative)
            why = "not an agent folder" if zone is None or zone.owner != AGENT else str(exc)
            result.ignored.append(f"ignored: {key} ({why})")
            continue
        except PathRefused as exc:
            result.ignored.append(f"ignored: {key} ({exc})")
            continue
        if info.st_nlink > 1:
            result.ignored.append(f"ignored: {key} (a hard link)")
            continue
        if info.st_size > max_file_bytes:
            result.ignored.append(f"ignored: {key} (larger than {max_file_bytes / 2**20:g} MB)")
            continue
        try:
            if holds_credential(path):
                result.ignored.append(f"ignored: {key} (it contains a Claude credential)")
                continue
        except OSError as exc:
            result.ignored.append(f"ignored: {key} (unreadable: {exc.strerror or exc})")
            continue
        if total + info.st_size > max_total_bytes or len(result.files) >= max_files:
            result.ignored.append(f"ignored: {key} (the run's outputs are over their limit)")
            continue
        total += info.st_size
        result.files.append(Output(key, path, info.st_size))
    for key in sorted(set(before) - seen):
        result.ignored.append(f"ignored: deleting {key} (deletions are not uploaded)")
    return result


# ---- handing the folders to the agent's user ------------------------------------------------


def hand_over(folders: Iterable[Path], *, uid: int = AGENT_UID, gid: int = AGENT_GID) -> None:
    """chown ``folders`` (recursively) to the agent's user. A no-op on Windows and when not root."""

    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    for folder in folders:
        os.chown(folder, uid, gid, follow_symlinks=False)
        for _relative, entry in _walk(folder):
            os.chown(entry.path, uid, gid, follow_symlinks=False)


__all__ = [
    "Output",
    "Outputs",
    "Refused",
    "RunFolder",
    "collect_outputs",
    "hand_over",
    "make_skeleton",
    "preflight",
    "preflight_tree",
    "remove_tree",
    "snapshot",
    "unpack_inputs",
]
