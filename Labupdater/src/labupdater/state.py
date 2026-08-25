from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class UpdaterState:
    active_version: str | None = None
    previous_version: str | None = None
    attempt_id: str | None = None
    phase: str = "idle"
    last_error: str | None = None


class StateStore:
    def __init__(self, release_root: Path) -> None:
        self.root = release_root.expanduser().resolve()
        self.state_path = self.root / "updater-state.json"
        self.pointer_path = self.root / "current.json"

    def load(self) -> UpdaterState:
        if not self.state_path.is_file():
            pointer = self.current_version()
            return UpdaterState(active_version=pointer)
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        return UpdaterState(
            active_version=raw.get("active_version"),
            previous_version=raw.get("previous_version"),
            attempt_id=raw.get("attempt_id"),
            phase=str(raw.get("phase", "idle")),
            last_error=raw.get("last_error"),
        )

    def save(self, state: UpdaterState) -> None:
        _atomic_json(self.state_path, asdict(state))

    def current_version(self) -> str | None:
        if not self.pointer_path.is_file():
            return None
        raw = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        version = raw.get("version")
        if not version:
            return None
        return _safe_version(str(version))

    def activate(self, version: str) -> None:
        version = _safe_version(version)
        runtime = (self.root / "runtimes" / version).resolve()
        marker = runtime / "runtime.json"
        if not marker.is_file():
            raise RuntimeError(f"Runtime {version!r} is not completely installed.")
        _atomic_json(
            self.pointer_path,
            {"version": version, "runtime": str(runtime)},
        )


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _safe_version(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", value) is None:
        raise RuntimeError("The active runtime version is not filesystem-safe.")
    return value


__all__ = ["StateStore", "UpdaterState"]
