"""Experiments on disk: one JSON file each, in the experiments folder.

* ids are a slug of the name plus six random characters (``aom-scan-k7q2mx``):
  lowercase, never a Windows device name, never a path;
* every save writes a temporary file and replaces the old one, so a crash
  never leaves half a file. Windows briefly locks files that indexers,
  antivirus or OneDrive have open, so the replace is retried;
* every save bumps ``version``; a save based on an older version is refused
  (VersionConflict), so two people editing at once never silently overwrite
  each other;
* deleting moves the file to ``.trash/`` (pruned after 30 days), because
  anyone using the Control Center can delete.

The folder is created on the first save. One lock serializes everything, which
is enough for the single Control Center process that owns the folder.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .experiment_schema import (
    EXPERIMENT_ID,
    MAX_FILE_BYTES,
    OPTIC_KINDS,
    SCHEMA_VERSION,
    Experiment,
    ExperimentContent,
    total_points,
)


_ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no 0/o or 1/l
_TRASH_DAYS = 30
_REPLACE_ATTEMPTS = 10


class ExperimentError(Exception):
    """Maps to an HTTP status; ``extra`` is added to the JSON reply."""

    def __init__(self, status_code: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.extra = extra


class VersionConflict(ExperimentError):
    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__(
            409,
            f"This experiment was changed elsewhere (now version {current['version']}). Reload to see it.",
            current=current,
        )


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:40].strip("-") or "experiment"


def summary(experiment: dict[str, Any]) -> dict[str, Any]:
    """What the list page shows for one experiment."""

    nodes = experiment.get("nodes", [])
    instruments = [node for node in nodes if node.get("kind") == "instrument"]
    optics: dict[str, int] = {}
    for node in nodes:
        if node.get("kind") in OPTIC_KINDS:
            optics[node["kind"]] = optics.get(node["kind"], 0) + 1
    content = ExperimentContent.model_validate(
        {key: experiment[key] for key in ("name", "description", "run", "nodes", "links") if key in experiment}
    )
    return {
        "id": experiment["id"],
        "name": experiment["name"],
        "description": experiment.get("description", ""),
        "created_by": experiment.get("created_by", ""),
        "updated_by": experiment.get("updated_by", ""),
        "created_at": experiment["created_at"],
        "updated_at": experiment["updated_at"],
        "version": experiment["version"],
        "devices": [
            {"node_id": node["id"], "device_id": node.get("device_id"), "device_type": node.get("device_type"),
             "label": node.get("label", "")}
            for node in instruments
        ],
        "optics": optics,
        "counts": {
            "components": len(nodes),
            "links": len(experiment.get("links", [])),
            "sweeps": sum(1 for node in instruments for setting in node.get("settings", []) if setting.get("sweep")),
            "records": sum(len(node.get("records", [])) for node in instruments),
        },
        "points": total_points(content),
    }


class ExperimentStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._summaries: dict[str, tuple[int, int, dict[str, Any] | None, str | None]] = {}

    # ---- paths ------------------------------------------------------------------

    def _path(self, experiment_id: str) -> Path:
        if EXPERIMENT_ID.fullmatch(experiment_id) is None:
            raise ExperimentError(404, "No such experiment.")
        path = self.root / f"{experiment_id}.json"
        if path.resolve().parent != self.root.resolve():
            raise ExperimentError(404, "No such experiment.")
        return path

    def _new_id(self, name: str) -> str:
        while True:
            candidate = f"{slug(name)}-{''.join(secrets.choice(_ID_ALPHABET) for _ in range(6))}"
            if not (self.root / f"{candidate}.json").exists():
                return candidate

    # ---- reading ------------------------------------------------------------------

    def _read(self, path: Path) -> dict[str, Any]:
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("the file is larger than an experiment can be")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        if data.get("schema_version", SCHEMA_VERSION) > SCHEMA_VERSION:
            raise ValueError("saved by a newer Control Center")
        experiment = Experiment.model_validate(data).model_dump(mode="json")
        if experiment["id"] != path.stem:
            raise ValueError("its id doesn't match its file name")
        return experiment

    def get(self, experiment_id: str) -> dict[str, Any]:
        path = self._path(experiment_id)
        with self._lock:
            if not path.is_file():
                raise ExperimentError(404, "No such experiment.")
            try:
                return self._read(path)
            except (OSError, ValueError) as exc:
                raise ExperimentError(500, f"{path.name} can't be read: {exc}") from exc

    def list(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(summaries, unreadable files), newest first. A bad file never hides the rest."""

        summaries: list[dict[str, Any]] = []
        unreadable: list[dict[str, Any]] = []
        with self._lock:
            try:
                entries = [entry for entry in os.scandir(self.root) if entry.is_file()]
            except FileNotFoundError:
                return [], []
            names = set()
            for entry in entries:
                if entry.name.startswith(".") or not entry.name.endswith(".json"):
                    continue
                names.add(entry.name)
                stat = entry.stat()
                cached = self._summaries.get(entry.name)
                if cached is None or cached[:2] != (stat.st_mtime_ns, stat.st_size):
                    try:
                        cached = (stat.st_mtime_ns, stat.st_size, summary(self._read(Path(entry.path))), None)
                    except (OSError, ValueError) as exc:
                        cached = (stat.st_mtime_ns, stat.st_size, None, str(exc))
                    self._summaries[entry.name] = cached
                if cached[2] is not None:
                    summaries.append(cached[2])
                else:
                    unreadable.append({"file": entry.name, "error": cached[3]})
            for stale in set(self._summaries) - names:
                del self._summaries[stale]
        summaries.sort(key=lambda item: item["updated_at"], reverse=True)
        return summaries, unreadable

    # ---- writing ------------------------------------------------------------------

    def create(self, content: ExperimentContent, *, author: str) -> dict[str, Any]:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            stamp = now()
            experiment = {
                "schema_version": SCHEMA_VERSION,
                "id": self._new_id(content.name),
                "version": 1,
                "created_at": stamp,
                "updated_at": stamp,
                "created_by": author,
                "updated_by": author,
                **content.model_dump(mode="json"),
            }
            self._write(experiment)
            return experiment

    def update(self, experiment_id: str, content: ExperimentContent, *, base_version: int, author: str) -> dict[str, Any]:
        with self._lock:
            current = self.get(experiment_id)
            if current["version"] != base_version:
                raise VersionConflict(current)
            experiment = {
                **current,
                **content.model_dump(mode="json"),
                "version": current["version"] + 1,
                "updated_at": now(),
                "updated_by": author or current.get("updated_by", ""),
            }
            self._write(experiment)
            return experiment

    def delete(self, experiment_id: str, *, version: int | None = None) -> None:
        with self._lock:
            current = self.get(experiment_id)
            if version is not None and current["version"] != version:
                raise VersionConflict(current)
            trash = self.root / ".trash"
            trash.mkdir(exist_ok=True)
            stamp = now().replace(":", "").replace("-", "")
            self._replace(self._path(experiment_id), trash / f"{experiment_id}.{stamp}.json")
            self._prune_trash(trash)

    def _write(self, experiment: dict[str, Any]) -> None:
        data = (json.dumps(experiment, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ExperimentError(413, "This experiment is too large to save.")
        handle, temporary = tempfile.mkstemp(dir=self.root, prefix=f".{experiment['id']}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            self._replace(Path(temporary), self._path(experiment["id"]))
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    @staticmethod
    def _replace(source: Path, target: Path) -> None:
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(source, target)
                return
            except PermissionError:  # Windows: another program has the file open
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise ExperimentError(503, "The experiment file is busy; try again in a moment.") from None
                time.sleep(0.05)

    @staticmethod
    def _prune_trash(trash: Path) -> None:
        cutoff = time.time() - _TRASH_DAYS * 24 * 3600
        for entry in trash.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                pass


__all__ = ["ExperimentError", "ExperimentStore", "VersionConflict", "now", "slug", "summary"]
