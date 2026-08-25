from __future__ import annotations

from pathlib import Path
from typing import Any

from .http import JSONClient


class CentralUpdateClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        ca_file: Path | None = None,
    ) -> None:
        self.http = JSONClient(
            base_url,
            bearer_token=api_key,
            ca_file=ca_file,
            timeout=60.0,
        )

    def directive(self) -> dict[str, Any]:
        return self.http.get("/api/v1/updates/directive")

    def download(
        self,
        artifact_url: str,
        target: Path,
        *,
        expected_size: int,
    ) -> None:
        self.http.download(artifact_url, target, maximum_bytes=expected_size)

    def phase(
        self,
        attempt_id: str,
        state: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.http.post(
            f"/api/v1/updates/{attempt_id}/phase",
            {"state": state, "details": details or {}},
        )

    def begin_drain(self, attempt_id: str) -> dict[str, Any]:
        return self.http.post(f"/api/v1/updates/{attempt_id}/begin-drain")

    def drain_status(self, attempt_id: str) -> dict[str, Any]:
        return self.http.get(f"/api/v1/updates/{attempt_id}/drain")

    def complete(self, attempt_id: str, installed_version: str) -> dict[str, Any]:
        return self.http.post(
            f"/api/v1/updates/{attempt_id}/complete",
            {"installed_version": installed_version},
        )

    def abort(
        self,
        attempt_id: str,
        error: str,
        *,
        rolled_back: bool,
        observed_version: str | None,
        reopen_admission: bool,
    ) -> dict[str, Any]:
        return self.http.post(
            f"/api/v1/updates/{attempt_id}/abort",
            {
                "error": error[:8192],
                "rolled_back": rolled_back,
                "observed_version": observed_version,
                "reopen_admission": reopen_admission,
            },
        )


class LabPCControlClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.http = JSONClient(base_url, timeout=5.0)
        self.token = token

    def _request(self, method: str, path: str) -> dict[str, Any]:
        # The local endpoint deliberately uses a distinct header rather than a
        # Central bearer credential.
        return self.http.request(
            method,
            path,
            extra_headers={
                "Accept": "application/json",
                "X-LabNet-Updater-Token": self.token,
            },
        )

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/internal/v1/update/status")

    def drain(self) -> dict[str, Any]:
        return self._request("POST", "/internal/v1/update/drain")

    def resume(self) -> dict[str, Any]:
        return self._request("POST", "/internal/v1/update/resume")

    def stop(self) -> dict[str, Any]:
        return self._request("POST", "/internal/v1/update/stop")

    def ready(self) -> dict[str, Any]:
        return self.http.get("/health/ready")

    def live(self) -> dict[str, Any]:
        return self.http.get("/health/live")


__all__ = ["CentralUpdateClient", "LabPCControlClient"]
