"""Automation runners: the machines that work through a project's tasks, and their tokens.

A runner (the lab's Linux laptop, or Docker on the Central PC) talks to the
Control Center only over the runner API, with ``Authorization: Bearer lnr_…``.
Tokens are issued by ``labnet-control-center-admin runner add NAME`` and shown
once; the Control Center keeps only their sha256::

    state/runners/<name>.json   {"name", "sha256", "created_at"}

so a copy of ``state/`` doesn't hand out working tokens. Revoking a runner
deletes its file, and its next request is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from .automation_store import AutomationError, write_json_file
from .experiment_store import now


RUNNER_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
TOKEN_PREFIX = "lnr_"
# secrets.token_urlsafe(32): 43 characters of [A-Za-z0-9_-].
_TOKEN = re.compile(r"lnr_[A-Za-z0-9_-]{43}")
_MAX_RECORD_BYTES = 4096


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class RunnerRegistry:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()

    def _path(self, name: str) -> Path:
        if not isinstance(name, str) or RUNNER_NAME.fullmatch(name) is None:
            raise AutomationError(
                422, "A runner name is 1–32 lowercase letters, digits and dashes, starting with a letter or digit."
            )
        return self.folder / f"{name}.json"

    def add(self, name: str) -> str:
        """Register a runner; returns its token, which is never stored or shown again."""

        path = self._path(name)
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            if path.exists():
                raise AutomationError(409, f"A runner called {name} already exists. Revoke it first to replace it.")
            write_json_file(self.folder, path.name, {"name": name, "sha256": _digest(token), "created_at": now()})
        return token

    def _records(self) -> list[dict[str, Any]]:
        try:
            entries = sorted(os.scandir(self.folder), key=lambda entry: entry.name)
        except FileNotFoundError:
            return []
        records = []
        for entry in entries:
            name = entry.name.removesuffix(".json")
            if not entry.name.endswith(".json") or RUNNER_NAME.fullmatch(name) is None:
                continue
            try:
                if not entry.is_file(follow_symlinks=False) or entry.stat().st_size > _MAX_RECORD_BYTES:
                    continue
                record = json.loads(Path(entry.path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # an unreadable record authenticates nobody
            if (
                isinstance(record, dict) and record.get("name") == name
                and isinstance(record.get("sha256"), str) and len(record["sha256"]) == 64
            ):
                records.append(record)
        return records

    def list(self) -> list[dict[str, Any]]:
        return [{"name": record["name"], "created_at": record.get("created_at", "")} for record in self._records()]

    def revoke(self, name: str) -> None:
        path = self._path(name)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                raise AutomationError(404, f"There is no runner called {name}.") from None

    def authenticate(self, token: str) -> str | None:
        """The name of the runner ``token`` belongs to, or None."""

        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            return None
        digest = _digest(token).encode("ascii")
        found = None
        for record in self._records():  # compare against every record: no early exit to time
            if hmac.compare_digest(digest, record["sha256"].encode("ascii", "replace")):
                found = record["name"]
        return found


__all__ = ["RUNNER_NAME", "RunnerRegistry", "TOKEN_PREFIX"]
