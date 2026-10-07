"""The Control Center's runner API, as the runner sees it (synchronous httpx).

Every request carries ``Authorization: Bearer <runner token>``. The token is
never logged and never handed to the agent: only the supervisor and its tools
server hold this client.

Retries (with backoff) happen only when the Control Center could not answer:
a connection that failed to open, or 502/503/504 from Caddy. A 4xx is the
Control Center's decision and is raised at once as :class:`RunnerApiError`;
409 means the run isn't active any more (``run_status`` says what it is now,
e.g. ``cancelled``), which is how a runner learns that a person pressed Stop.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .config import RunnerSettings


LOGGER = logging.getLogger(__name__)
PREFIX = "/api/automation/runner"
USER_AGENT = "labnet-runner/2"

# The Control Center's limits on events (automation_runs / automation_runner_api).
MAX_EVENTS_PER_REQUEST = 100
MAX_EVENTS_REQUEST_BYTES = 256 * 1024
MAX_EVENT_TEXT_BYTES = 16 * 1024
# Leaves room for the JSON around the events.
_EVENTS_BODY_BUDGET = MAX_EVENTS_REQUEST_BYTES - 4 * 1024

RETRY_STATUSES = frozenset({502, 503, 504})
DEFAULT_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
CHUNK_BYTES = 256 * 1024

# Failures before the request could have reached the Control Center: always safe to retry.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# Failures after it may have been sent: retried only for requests that can be repeated.
_MAYBE_SENT = (httpx.ReadError, httpx.WriteError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError)
_REPEATABLE = frozenset({"GET", "PUT"})  # PUT replaces the same file again


class RunnerApiError(Exception):
    """The Control Center refused a request, or couldn't be reached.

    ``status_code`` is the HTTP status, or 0 when no answer came at all (after the
    retries). ``detail`` is the Control Center's message; ``run_status`` the run's
    status a 409 carries (``cancelled``, ``failed``, …), else None.
    """

    def __init__(self, status_code: int, detail: str, *, run_status: str | None = None) -> None:
        super().__init__(f"{status_code or 'no answer'}: {detail}")
        self.status_code = status_code
        self.detail = detail
        self.run_status = run_status


def truncate_text(text: str, limit: int = MAX_EVENT_TEXT_BYTES) -> str:
    """``text`` cut to at most ``limit`` UTF-8 bytes, marked when cut."""

    data = text.encode("utf-8", "replace")
    if len(data) <= limit:
        return text
    marker = " … (truncated)"
    cut = data[: limit - len(marker.encode("utf-8"))].decode("utf-8", "ignore")
    return cut + marker


def _segments(relative: str) -> str:
    parts = relative.split("/")
    if not relative or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Not a clean relative path: {relative!r}")
    return "/".join(quote(part, safe="") for part in parts)


def _run(run_id: str) -> str:
    return quote(run_id, safe="")


class ControlCenterClient:
    def __init__(
        self,
        settings: RunnerSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        retry_delays: Sequence[float] = DEFAULT_RETRY_DELAYS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._retry_delays = tuple(retry_delays)
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=settings.control_center_url,
            headers={"Authorization": f"Bearer {settings.token}", "User-Agent": USER_AGENT},
            verify=str(settings.ca_file) if settings.ca_file is not None else True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
            follow_redirects=False,
            trust_env=False,  # the runner's own proxy variables are not for the Control Center
        )

    # ---- plumbing ------------------------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ControlCenterClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _error(response: httpx.Response) -> RunnerApiError:
        detail = response.reason_phrase or "error"
        run_status = None
        try:
            body = response.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        if isinstance(body, dict):
            if isinstance(body.get("detail"), str):
                detail = body["detail"]
            elif body.get("detail") is not None:
                detail = json.dumps(body["detail"])[:500]
            if isinstance(body.get("status"), str):
                run_status = body["status"]
        elif response.text:
            detail = response.text[:500]
        return RunnerApiError(response.status_code, detail, run_status=run_status)

    def _send(self, method: str, url: str, *, stream: bool = False, body: Callable[[], dict[str, Any]] | None = None,
              **kwargs: Any) -> httpx.Response:
        """One request with retries; the response is open (``stream``) or read. Raises RunnerApiError."""

        attempt = 0
        while True:
            extra = body() if body is not None else {}
            try:
                request = self._http.build_request(method, url, **kwargs, **extra)
                response = self._http.send(request, stream=stream)
            except httpx.TransportError as exc:
                retry = isinstance(exc, _NOT_SENT) or (isinstance(exc, _MAYBE_SENT) and method in _REPEATABLE)
                if not retry or attempt >= len(self._retry_delays):
                    raise RunnerApiError(0, f"The Control Center didn't answer {method} {url}: {exc!r}") from exc
                LOGGER.warning("%s %s failed (%s); retrying", method, url, type(exc).__name__)
            else:
                if response.status_code in RETRY_STATUSES and attempt < len(self._retry_delays):
                    response.close()
                    LOGGER.warning("%s %s: %s; retrying", method, url, response.status_code)
                elif response.status_code >= 400:
                    if stream:
                        response.read()
                    response.close()
                    raise self._error(response)
                else:
                    return response
            self._sleep(self._retry_delays[attempt])
            attempt += 1

    def _json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self._send(method, url, **kwargs)
        try:
            data = response.json()
        except ValueError as exc:
            raise RunnerApiError(response.status_code, f"{method} {url} answered with something that isn't JSON") from exc
        if not isinstance(data, dict):
            raise RunnerApiError(response.status_code, f"{method} {url} answered with JSON that isn't an object")
        return data

    # ---- the runner API --------------------------------------------------------------------

    def whoami(self) -> str:
        return str(self._json("GET", f"{PREFIX}/whoami")["runner"])

    def claim(self) -> dict[str, Any] | None:
        """The oldest queued run, now this runner's; None when nothing is queued."""

        response = self._send("POST", f"{PREFIX}/claim")
        if response.status_code == 204:
            return None
        return response.json()["run"]

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._json("GET", f"{PREFIX}/runs/{_run(run_id)}")["run"]

    def download_inputs(self, run_id: str, dest: Path) -> None:
        """The run's inputs zip, streamed into ``dest`` (replaced if it exists)."""

        url = f"{PREFIX}/runs/{_run(run_id)}/inputs"
        attempt = 0
        while True:
            response = self._send("GET", url, stream=True, timeout=httpx.Timeout(120.0, connect=10.0))
            try:
                with open(dest, "wb") as file:
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        file.write(chunk)
                return
            except (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                if attempt >= len(self._retry_delays):
                    raise RunnerApiError(0, f"Downloading the inputs failed: {exc!r}") from exc
                LOGGER.warning("Downloading the inputs of %s broke off (%s); retrying", run_id, type(exc).__name__)
            finally:
                response.close()
            self._sleep(self._retry_delays[attempt])
            attempt += 1

    def upload(self, run_id: str, relative: str, file: Path) -> dict[str, Any]:
        """PUT ``file`` (streamed) to ``relative`` in the project's agent folders."""

        url = f"{PREFIX}/runs/{_run(run_id)}/files/{_segments(relative)}"
        size = file.stat().st_size

        def body() -> dict[str, Any]:  # a fresh stream for every attempt
            def chunks() -> Iterator[bytes]:
                with open(file, "rb") as handle:
                    while data := handle.read(CHUNK_BYTES):
                        yield data

            return {"content": chunks(), "headers": {"Content-Length": str(size),
                                                     "Content-Type": "application/octet-stream"}}

        return self._json("PUT", url, body=body, timeout=httpx.Timeout(300.0, connect=10.0))

    def events(self, run_id: str, events: list[dict[str, Any]]) -> str:
        """Send ``events`` ({kind, text}), in requests the Control Center accepts; the run's status."""

        url = f"{PREFIX}/runs/{_run(run_id)}/events"
        status = "active"
        batch: list[dict[str, str]] = []
        size = 0
        for event in events:
            item = {"kind": str(event["kind"]), "text": truncate_text(str(event["text"]))}
            item_size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 2
            if batch and (len(batch) >= MAX_EVENTS_PER_REQUEST or size + item_size > _EVENTS_BODY_BUDGET):
                status = self._json("POST", url, json={"events": batch})["status"]
                batch, size = [], 0
            batch.append(item)
            size += item_size
        if batch or not events:
            status = self._json("POST", url, json={"events": batch})["status"]
        return str(status)

    def heartbeat(self, run_id: str) -> str:
        return str(self._json("POST", f"{PREFIX}/runs/{_run(run_id)}/heartbeat", json={})["status"])

    def finish(self, run_id: str, status: str, summary: str) -> dict[str, Any]:
        summary = summary if len(summary) <= 4000 else summary[:3990] + " …"
        return self._json("POST", f"{PREFIX}/runs/{_run(run_id)}/finish",
                          json={"status": status, "summary": summary})["run"]

    def literature_search(self, run_id: str, query: str, k: int = 10, paper: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query, "k": k}
        if paper is not None:
            params["paper"] = paper
        return self._json("GET", f"{PREFIX}/runs/{_run(run_id)}/literature/search", params=params,
                          timeout=httpx.Timeout(90.0, connect=10.0))  # it may first refresh the index (≤ 30 s)

    def literature_papers(self, run_id: str) -> dict[str, Any]:
        return self._json("GET", f"{PREFIX}/runs/{_run(run_id)}/literature/papers",
                          timeout=httpx.Timeout(90.0, connect=10.0))

    def literature_pages(self, run_id: str, paper: str, first: int, last: int | None) -> dict[str, Any]:
        params: dict[str, Any] = {"paper": paper, "first": first}
        if last is not None:
            params["last"] = last
        return self._json("GET", f"{PREFIX}/runs/{_run(run_id)}/literature/pages", params=params,
                          timeout=httpx.Timeout(90.0, connect=10.0))


__all__ = ["ControlCenterClient", "MAX_EVENT_TEXT_BYTES", "RunnerApiError", "truncate_text"]
