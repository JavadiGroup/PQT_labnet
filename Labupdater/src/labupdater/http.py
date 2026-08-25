from __future__ import annotations

import json
import ssl
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class APIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class JSONClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        ca_file: Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.bearer_token = bearer_token
        self.timeout = timeout
        self.ssl_context = (
            ssl.create_default_context(cafile=str(ca_file))
            if ca_file is not None
            else ssl.create_default_context()
        )

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", path, body)

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = None
        headers = {"Accept": "application/json"}
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        headers.update(extra_headers or {})
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(
                request,
                timeout=self.timeout,
                context=self.ssl_context,
            ) as response:
                data = response.read()
        except HTTPError as exc:
            detail = _error_detail(exc)
            raise APIError(detail, status=exc.code) from exc
        except (OSError, URLError) as exc:
            raise APIError(f"Request to {request.full_url} failed: {exc}") from exc
        if not data:
            return {}
        try:
            decoded = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError("Server returned an invalid JSON response.") from exc
        if not isinstance(decoded, dict):
            raise APIError("Server returned a non-object JSON response.")
        return decoded

    def download(self, path: str, target: Path, *, maximum_bytes: int) -> None:
        headers = {}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            headers=headers,
            method="GET",
        )
        written = 0
        try:
            with urlopen(
                request,
                timeout=self.timeout,
                context=self.ssl_context,
            ) as response, target.open("xb") as stream:
                while chunk := response.read(1024 * 1024):
                    written += len(chunk)
                    if written > maximum_bytes:
                        raise APIError("Downloaded artifact exceeded its manifest size.")
                    stream.write(chunk)
                stream.flush()
        except HTTPError as exc:
            target.unlink(missing_ok=True)
            raise APIError(_error_detail(exc), status=exc.code) from exc
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        if written != maximum_bytes:
            target.unlink(missing_ok=True)
            raise APIError(
                f"Downloaded artifact size {written} did not match {maximum_bytes}."
            )


def _error_detail(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read())
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict):
                return str(detail.get("message") or detail)
            if detail:
                return str(detail)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return f"HTTP {error.code}: {error.reason}"


__all__ = ["APIError", "JSONClient"]
