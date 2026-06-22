"""
This service owns all of the file read/write operations and atomic persistence

Available methods:
> load users
> save users
> load devices
> save devices
> load leases
> save leases
> append log

"""

import json
import os
import threading
from pathlib import Path
from datetime import datetime, timezone

class StorageBackend:
    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.users_path = self.data_dir / "users.json"
        self.devices_path = self.data_dir / "devices.json"
        self.leases_path = self.data_dir / "leases.json"
        self.logs_path = self.data_dir / "logs.jsonl"

        self._lock = threading.Lock()
        self._ensure_files_exist()

    def _ensure_files_exist(self) -> None:
        """Create missing MVP data files with safe defaults."""
        for path in [self.users_path, self.devices_path, self.leases_path]:
            if not path.exists():
                self._save_json_atomic(path, {})

        if not self.logs_path.exists():
            self.logs_path.touch()

    def _load_json(self, path: Path) -> dict:
        """Load a JSON object from disk."""
        with self._lock:
            if not path.exists():
                return {}

            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()

            if not content:
                return {}

            return json.loads(content)

    def _save_json_atomic(self, path: Path, data: dict) -> None:
        """Write JSON using temp-file replacement to avoid partial writes."""
        temp_path = path.with_suffix(path.suffix + ".tmp")

        with self._lock:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, path)

    def load_users(self) -> dict:
        """Return all user records from users.json."""
        return self._load_json(self.users_path)

    def save_users(self, users: dict) -> None:
        """Persist all user records to users.json."""
        self._save_json_atomic(self.users_path, users)

    def load_devices(self) -> dict:
        """Return all device records from devices.json."""
        return self._load_json(self.devices_path)

    def save_devices(self, devices: dict) -> None:
        """Persist all device records to devices.json."""
        self._save_json_atomic(self.devices_path, devices)

    def load_leases(self) -> dict:
        """Return all active leases from leases.json."""
        return self._load_json(self.leases_path)

    def save_leases(self, leases: dict) -> None:
        """Persist all active leases to leases.json."""
        self._save_json_atomic(self.leases_path, leases)

    def append_log(self, event: dict) -> None:
        """Append one audit event to logs.jsonl."""
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()

        line = json.dumps(event)

        with self._lock:
            with open(self.logs_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())