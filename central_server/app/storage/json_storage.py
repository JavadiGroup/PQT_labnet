"""
This class is responsible for all json file writes
"""

from __future__ import annotations
import json
import os
import threading
from pathlib import Path
from typing import Any


class JsonStorageError(Exception):
    """Base error for JSON storage failures."""

class JsonFileCorruptError(JsonStorageError):
    """Raised when a JSON file cannot be decoded."""

class JsonStorage:
    """
    JSON-backed implementation of the central server StorageBackend.

    This class owns all direct file access for:
    - users.json
    - devices.json
    - leases.json
    - logs.jsonl
    """

    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)

        self.users_path = self.data_dir / "users.json"
        self.devices_path = self.data_dir / "devices.json"
        self.leases_path = self.data_dir / "leases.json"
        self.logs_path = self.data_dir / "logs.jsonl"

        self._lock = threading.RLock()

        self._ensure_storage_exists()

    def _ensure_storage_exists(self) -> None:
        """Create data directory and default storage files if missing."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._ensure_json_file(self.users_path)
        self._ensure_json_file(self.devices_path)
        self._ensure_json_file(self.leases_path)

        if not self.logs_path.exists():
            self.logs_path.touch()

    def _ensure_json_file(self, path: Path) -> None:
        """Create a missing JSON file with an empty object."""
        if not path.exists():
            self._save_json_atomic(path, {})

    def load_users(self) -> dict[str, Any]:
        """Load all user records from users.json."""
        return self._load_json(self.users_path)

    def save_users(self, users: dict[str, Any]) -> None:
        """Save all user records to users.json."""
        self._save_json_atomic(self.users_path, users)

    def load_devices(self) -> dict[str, Any]:
        """Load all device records from devices.json."""
        return self._load_json(self.devices_path)

    def save_devices(self, devices: dict[str, Any]) -> None:
        """Save all device records to devices.json."""
        self._save_json_atomic(self.devices_path, devices)

    def load_leases(self) -> dict[str, Any]:
        """Load all active leases from leases.json."""
        return self._load_json(self.leases_path)

    def save_leases(self, leases: dict[str, Any]) -> None:
        """Save all active leases to leases.json."""
        self._save_json_atomic(self.leases_path, leases)

    def append_log(self, event: dict[str, Any]) -> None:
        """Append one audit event to logs.jsonl."""
        line = json.dumps(event, separators=(",", ":"))

        with self._lock:
            with open(self.logs_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())

    def read_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Read the most recent log records from logs.jsonl."""
        if limit <= 0:
            return []

        with self._lock:
            if not self.logs_path.exists():
                return []

            with open(self.logs_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        records: list[dict[str, Any]] = []

        for line in lines[-limit:]:
            line = line.strip()

            if not line:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({
                    "event": "MALFORMED_LOG_LINE",
                    "raw": line,
                })

        return records

    def _load_json(self, path: Path) -> dict[str, Any]:
        """Load one JSON object from disk."""
        with self._lock:
            if not path.exists():
                return {}

            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()

            if not raw:
                return {}

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise JsonFileCorruptError(
                    f"Could not decode JSON file: {path}"
                ) from exc

            if not isinstance(data, dict):
                raise JsonStorageError(
                    f"Expected JSON object in {path}, got {type(data).__name__}"
                )

            return data

    def _save_json_atomic(self, path: Path, data: dict[str, Any]) -> None:
        """
        Save JSON atomically.

        Write sequence:
        1. Write to temporary file.
        2. Flush Python buffer.
        3. fsync to disk.
        4. Replace original file with temp file.
        """
        temp_path = path.with_suffix(path.suffix + ".tmp")

        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, path)