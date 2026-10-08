"""Paths inside an Automation project or the lab library: checked, never trusted.

Every path a browser (and later an agent) names is relative to a root. Before
it touches the disk it must:

* be clean: no ``..``, drive letters, UNC or ``\\\\?\\`` forms, ``:`` (alternate
  data streams), reserved device names, trailing dots or spaces, control or
  invisible characters, hidden (``.``-prefixed) names, 8.3 short names;
* stay inside its root: a component that is a link or junction is refused, and
  an opened file is checked by its handle, so nothing can point elsewhere;
* belong to a zone that allows the operation (see ``ZONES``).

Unicode is welcome: the library's titles contain en dashes and long names.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO


MAX_PATH_CHARACTERS = 240
MAX_DEPTH = 8
MAX_NAME_CHARACTERS = 160
_FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"}  # as experiment_schema.Text
_FORBIDDEN_CHARACTERS = set('<>:"/\\|?*')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
# PROGRA~1, ANALYS~1.MD: an 8.3 alias that Windows resolves to some longer name.
_SHORT_NAME = re.compile(r"[^\s.~]{1,6}~\d{1,6}(\.[^\s.]{1,3})?")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PathRefused(ValueError):
    """A path that must not be used; the message says why, for a person."""


class NotWritable(Exception):
    """People may not change this place (it belongs to the agent or the Control Center)."""


class WrongFileType(Exception):
    """This kind of file doesn't belong in this folder."""


# ---- names and relative paths -------------------------------------------------


def check_name(name: str) -> str:
    if not name or name in (".", ".."):
        raise PathRefused("A path has an empty part or a '.' / '..' part.")
    if len(name) > MAX_NAME_CHARACTERS:
        raise PathRefused(f"“{name[:40]}…” is longer than {MAX_NAME_CHARACTERS} characters.")
    if name.startswith("."):
        raise PathRefused(f"“{name}”: names starting with '.' are reserved.")
    if name != name.strip() or name.endswith("."):
        raise PathRefused(f"“{name}” starts or ends with a space, or ends with a dot.")
    for character in name:
        if character in _FORBIDDEN_CHARACTERS:
            raise PathRefused(f"“{name}” contains {character!r}, which file names can't contain.")
        if unicodedata.category(character) in _FORBIDDEN_CATEGORIES:
            raise PathRefused(f"“{name}” contains a control or invisible character (U+{ord(character):04X}).")
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        raise PathRefused(f"“{name}” is a reserved Windows device name.")
    if _SHORT_NAME.fullmatch(name):
        raise PathRefused(f"“{name}” looks like a Windows short name (~1); use the full name.")
    return name


def clean_relative(raw: str) -> PurePosixPath:
    """``raw`` as a clean relative path (forward slashes), or PathRefused."""

    if not isinstance(raw, str) or not raw:
        raise PathRefused("No path given.")
    if len(raw) > MAX_PATH_CHARACTERS:
        raise PathRefused(f"The path is longer than {MAX_PATH_CHARACTERS} characters.")
    if "\\" in raw or raw.startswith("/"):
        raise PathRefused("Use a relative path with forward slashes.")
    parts = raw.split("/")
    if len(parts) > MAX_DEPTH:
        raise PathRefused(f"The path is more than {MAX_DEPTH} folders deep.")
    for part in parts:
        check_name(part)
    return PurePosixPath(*parts)


def clean_upload_name(name: str) -> str:
    """A browser's file name made into one that passes ``check_name`` (Unicode kept)."""

    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(
        "_" if character in _FORBIDDEN_CHARACTERS or unicodedata.category(character) in _FORBIDDEN_CATEGORIES
        else character
        for character in base
    )
    cleaned = cleaned.strip(" .").lstrip(".") or "file"
    if _SHORT_NAME.fullmatch(cleaned):
        cleaned = cleaned.replace("~", "_")
    stem, dot, extension = cleaned.rpartition(".")
    if not dot:
        stem, extension = cleaned, ""
    limit = MAX_NAME_CHARACTERS - len(extension) - 1
    stem = stem[:limit].rstrip(" .") or "file"
    if stem.split(".")[0].upper() in _WINDOWS_RESERVED:
        stem = "_" + stem
    return check_name(f"{stem}.{extension.lower()}" if extension else stem)


# ---- staying inside a root ------------------------------------------------------


def _is_link(path: Path) -> bool:
    """True for symlinks, junctions and other reparse points."""

    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def resolve_inside(root: Path, relative: PurePosixPath) -> Path:
    """``root / relative``, refusing any existing component that is a link or junction."""

    base = root.resolve(strict=True)
    if _is_link(root):
        raise PathRefused("The folder itself is a link.")
    current = base
    for part in relative.parts:
        current = current / part
        try:
            if _is_link(current):
                raise PathRefused(f"“{part}” is a link or junction; links are not followed.")
        except FileNotFoundError:
            break  # the rest doesn't exist yet (a new file or folder)
    target = base.joinpath(*relative.parts)
    if not os.path.realpath(target).casefold().startswith(str(base).casefold() + os.sep.casefold()) and target != base:
        raise PathRefused("The path leads outside its folder.")
    return target


def _final_path(handle: int) -> str | None:
    """Where an open file really is (Windows), from its handle, not its name."""

    if sys.platform != "win32":
        return None
    import ctypes
    import msvcrt
    from ctypes import wintypes

    get_final = ctypes.windll.kernel32.GetFinalPathNameByHandleW
    get_final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_final.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_final(msvcrt.get_osfhandle(handle), buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        return None
    path = buffer.value
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path.removeprefix("\\\\?\\")


def check_handle_inside(handle: int, root: Path, path: Path) -> None:
    """An open file (by its descriptor) is a single-linked file inside ``root``."""

    info = os.fstat(handle)
    if not stat.S_ISREG(info.st_mode):
        raise FileNotFoundError(str(path))
    if info.st_nlink > 1:
        raise PathRefused("This file is a hard link; it is not used.")
    real = _final_path(handle) or os.path.realpath(path)
    base = str(root.resolve())
    if not real.casefold().startswith(base.casefold() + os.sep):
        raise PathRefused("The file lies outside its folder.")


def open_inside(root: Path, relative: PurePosixPath) -> tuple[BinaryIO, os.stat_result]:
    """Open a regular file for reading and prove, by its handle, that it is inside ``root``."""

    path = resolve_inside(root, relative)
    try:
        file = open(path, "rb")
    except (FileNotFoundError, IsADirectoryError, PermissionError, NotADirectoryError) as exc:
        raise FileNotFoundError(str(relative)) from exc
    try:
        check_handle_inside(file.fileno(), root, path)
        return file, os.fstat(file.fileno())
    except BaseException:
        file.close()
        raise


# ---- zones: who may write where -----------------------------------------------


USER, AGENT, MIRROR = "user", "agent", "mirror"

LITERATURE_EXTENSIONS = frozenset({".pdf", ".bib", ".ris", ".txt", ".md"})
DATA_EXTENSIONS = frozenset({
    ".csv", ".tsv", ".txt", ".dat", ".json", ".jsonl", ".md",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
})


@dataclass(frozen=True, slots=True)
class Zone:
    path: str  # as shown, e.g. "Data/example_data"
    owner: str  # user: people edit it; agent: written by the agent; mirror: by the Control Center
    label: str
    extensions: frozenset[str] = frozenset()  # what people may put here (user zones)
    exact: bool = False  # the zone is this one file, not a folder

    @property
    def prefix(self) -> tuple[str, ...]:
        return tuple(part.casefold() for part in self.path.split("/"))

    def matches(self, parts: tuple[str, ...]) -> bool:
        if self.exact:
            return parts == self.prefix
        return parts[: len(self.prefix)] == self.prefix

    def describe(self) -> dict[str, object]:
        return {"path": self.path, "owner": self.owner, "label": self.label,
                "extensions": sorted(self.extensions), "exact": self.exact}


# Most specific first. Folders a new project starts with are in SKELETON.
ZONES = (
    Zone("Experiment_layout/LAYOUT.md", USER, "Your notes on how the setups are wired",
         frozenset({".md"}), exact=True),
    Zone("Experiment_layout", MIRROR, "Copied from the Experiments tab by the Control Center"),
    Zone("Literature", USER, "Papers for this project only", LITERATURE_EXTENSIONS),
    Zone("Data/example_data", USER, "Example data you provide", DATA_EXTENSIONS),
    Zone("Data/new_data", AGENT, "Data the agent produces"),
    Zone("Tasks", USER, "What the agent should do, one file per task", frozenset({".md"})),
    Zone("Protocols", AGENT, "Methods and bounds the agent proposes for approval"),
    Zone("ScriptsUtilized", AGENT, "Every script the agent wrote or ran, for auditing"),
    Zone("Analysis", AGENT, "The agent's analysis and key figures"),
)

SKELETON = (
    "Literature",
    "Data/example_data",
    "Data/new_data",
    "Experiment_layout",
    "Tasks",
    "Protocols",
    "ScriptsUtilized/logs",
    "ScriptsUtilized/src",
    "ScriptsUtilized/analysis_scripts",
    "Analysis/outputs",
)


def zone_of(relative: PurePosixPath) -> Zone | None:
    parts = tuple(part.casefold() for part in relative.parts)
    for zone in ZONES:
        if zone.matches(parts):
            return zone
    return None


def check_user_write(relative: PurePosixPath, *, folder: bool = False) -> Zone:
    """The zone ``relative`` is in, if a person may create or change it there."""

    zone = zone_of(relative)
    if zone is None:
        raise NotWritable("Files go inside one of the project's folders (Literature, Data/example_data, Tasks, …).")
    if zone.owner == AGENT:
        raise NotWritable(f"{relative.parts[0]} is written by the agent; it is read-only here.")
    if zone.owner == MIRROR:
        raise NotWritable(
            "Experiment layouts are copied from the Experiments tab. Link an experiment instead; "
            "only LAYOUT.md is yours to edit here."
        )
    if folder:
        if zone.exact:
            raise NotWritable("That name is reserved for a file.")
        return zone
    if len(relative.parts) == len(zone.prefix) and not zone.exact:
        raise NotWritable("Name the file, not just its folder.")
    suffix = relative.suffix.lower()
    if suffix not in zone.extensions:
        allowed = ", ".join(sorted(zone.extensions))
        raise WrongFileType(f"{relative.name}: this folder takes {allowed} files.")
    if relative.name.casefold().startswith("claude") and suffix == ".md":
        raise NotWritable("CLAUDE*.md is reserved: Claude Code would treat it as instructions.")
    return zone


def is_instructions(name: str) -> bool:
    """CLAUDE.md, claude-notes.md, …: a file Claude Code may read as instructions."""

    folded = name.casefold()
    return folded.startswith("claude") and folded.endswith(".md")


def check_agent_write(relative: PurePosixPath, *, folder: bool = False) -> Zone:
    """The zone ``relative`` is in, if an agent (through its runner) may create or replace it there.

    The mirror image of ``check_user_write``: only the agent's folders, any
    file type, but never a hidden name (``.claude/``, ``.mcp.json``) or a
    ``CLAUDE*.md`` anywhere on the path, which a later run's Claude Code
    would take as instructions or settings.
    """

    for part in relative.parts:
        if part.startswith("."):
            raise NotWritable(f"“{part}”: names starting with '.' are reserved.")
        if is_instructions(part):
            raise NotWritable(f"“{part}”: CLAUDE*.md is reserved, since Claude Code would treat it as instructions.")
    zone = zone_of(relative)
    if zone is None or zone.owner != AGENT:
        raise NotWritable("The agent writes only to Data/new_data, Protocols, ScriptsUtilized and Analysis.")
    if not folder and is_zone_root(relative):  # Analysis, ScriptsUtilized/logs, …: always folders
        raise NotWritable("Name the file, not just its folder.")
    return zone


def is_zone_root(relative: PurePosixPath) -> bool:
    """Literature, Tasks, Data/example_data, …: folders every project keeps."""

    parts = tuple(part.casefold() for part in relative.parts)
    return any(parts == zone.prefix for zone in ZONES) or parts in {
        tuple(part.casefold() for part in PurePosixPath(folder).parts[:depth])
        for folder in SKELETON
        for depth in range(1, len(PurePosixPath(folder).parts) + 1)
    }


__all__ = [
    "AGENT",
    "DATA_EXTENSIONS",
    "LITERATURE_EXTENSIONS",
    "MAX_PATH_CHARACTERS",
    "MIRROR",
    "NotWritable",
    "PathRefused",
    "WrongFileType",
    "SKELETON",
    "USER",
    "ZONES",
    "Zone",
    "check_agent_write",
    "check_handle_inside",
    "check_name",
    "check_user_write",
    "clean_relative",
    "clean_upload_name",
    "is_instructions",
    "is_zone_root",
    "open_inside",
    "resolve_inside",
    "zone_of",
]
