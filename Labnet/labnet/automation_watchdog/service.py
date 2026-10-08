"""``labnet-automation-watchdog --env-file watchdog.env``: the process, its health page and its client."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import threading
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from labnet.client import AsyncLabClient

from .config import WatchdogSettings
from .core import Watchdog


LOGGER = logging.getLogger("labnet.automation_watchdog")
SERVICE = "labnet-automation-watchdog"


class _HealthServer(ThreadingHTTPServer):
    # One watchdog per computer: its port can't be shared (Windows would allow a second bind
    # with SO_REUSEADDR, so ask for the port exclusively there).
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


def start_health_server(host: str, port: int, watchdog: Watchdog) -> ThreadingHTTPServer:
    """``/health/live`` (the process is up) and ``/health/ready`` (it scanned recently), on its own thread."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the http.server name
            if self.path == "/health/live":
                self._answer(200, {"status": "alive", "service": SERVICE})
            elif self.path == "/health/ready":
                ready = watchdog.ready()
                self._answer(200 if ready else 503,
                             {"status": "ok" if ready else "not_scanning", "service": SERVICE, **watchdog.status()})
            else:
                self._answer(404, {"detail": "Not found."})

        def _answer(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - quiet: polled every few seconds
            pass

    server = _HealthServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="watchdog-health", daemon=True).start()
    return server


async def serve(
    settings: WatchdogSettings,
    *,
    client_factory: Callable[[WatchdogSettings], Any] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Run the watchdog until ``stop`` is set (or the task is cancelled); leases it holds are released."""

    client = (client_factory or _client)(settings)
    watchdog = Watchdog(settings, client)
    try:
        server = start_health_server(settings.bind_host, settings.port, watchdog)
    except OSError as exc:
        await client.aclose()
        raise SystemExit(f"{SERVICE}: port {settings.port} is in use ({exc}). Is another watchdog running?") from None
    LOGGER.info("Health: http://%s:%d/health/ready", settings.bind_host, server.server_address[1])
    try:
        try:
            user = await asyncio.wait_for(client.authenticate(), 5.0)  # never delays watching for long
            LOGGER.info("Central user %s (role %s) at %s", user.user, user.role, settings.server_url)
            if user.role not in ("user", "admin"):
                LOGGER.error("Central user %s has role %s: it can't take leases. Give the watchdog a user-role key.",
                             user.user, user.role)
        except Exception as exc:  # noqa: BLE001 - Central may come up later; leases are asked for when needed
            LOGGER.warning("Central didn't confirm the watchdog's key yet (%s: %s); carrying on",
                           type(exc).__name__, exc)
        await watchdog.run(stop)
    finally:
        await watchdog.aclose()
        server.shutdown()
        server.server_close()
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Closing the Central client failed: %s", exc)


def _client(settings: WatchdogSettings) -> AsyncLabClient:
    return AsyncLabClient(settings.server_url, settings.api_key, ca_file=settings.ca_file)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog=SERVICE,
        description="Apply an Automation grant's safe state if the Control Center dies or hangs mid-grant.",
    )
    parser.add_argument("--env-file", type=Path, help="Settings file such as watchdog.env (process environment wins).")
    arguments = parser.parse_args(argv)
    try:
        settings = WatchdogSettings.from_env(arguments.env_file)
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"{SERVICE}: error: {exc}\n")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:
        pass


__all__ = ["main", "serve", "start_health_server"]
