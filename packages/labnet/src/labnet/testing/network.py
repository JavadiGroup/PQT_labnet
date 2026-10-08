"""``local_network``: a real Central and one Lab PC in this process, for tests and demos.

Both run as Uvicorn servers in background threads, set up the way an operator
would (``labnet-central-admin setup --write-env`` plus a Lab PC env file), so
client code connects to them exactly as it would in the lab: through a user's
env file (``LABNET_ENV_FILE``). Needs ``labnet[central,lab-pc]``.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from labnet.devices.core.protocol import PhysicalDevice


@dataclass(frozen=True)
class LocalNetwork:
    """Where a running :func:`local_network` is, and each user's env file."""

    root: Path
    central_url: str
    lab_pc_id: str
    env_files: dict[str, Path] = field(default_factory=dict)

    def env_file(self, user: str) -> Path:
        """The env file a client of ``user`` loads (``LABNET_ENV_FILE``)."""

        return self.env_files[user]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def _restored_environment() -> Iterator[None]:
    """Env files load into os.environ; keep them out of the caller's process."""

    saved = os.environ.copy()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class _ThreadedServer:
    def __init__(self, app: object, port: int) -> None:
        import uvicorn

        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.ready_url = f"http://127.0.0.1:{port}/health/ready"

    def start(self, timeout: float = 30.0) -> None:
        import httpx

        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.ready_url, timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"{self.ready_url} did not become ready.")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


@contextmanager
def local_network(
    devices: Callable[[], Iterable[PhysicalDevice]],
    root: Path,
    *,
    users: Sequence[str] = ("researcher",),
    lab_pc_id: str = "lab-pc-test",
) -> Iterator[LocalNetwork]:
    """Run Central and a Lab PC serving ``devices()`` until the block ends.

    ``root`` (an empty folder) receives the database and env files. Each name
    in ``users`` gets an API key and ``<root>/<user>.env``.
    """

    from labnet.central.config import Settings
    from labnet.central.main import create_app as create_central_app
    from labnet.central.manage import main as admin
    from labnet.lab_pc.app import create_app as create_lab_pc_app
    from labnet.lab_pc.config import LabPCSettings

    root = Path(root)
    central_port, grpc_port, health_port = _free_port(), _free_port(), _free_port()
    central_url = f"http://127.0.0.1:{central_port}"
    central_env = root / "central.env"
    central_env.write_text(
        f"LABNET_DATABASE_URL=sqlite:///{(root / 'central.db').as_posix()}\n"
        f"LABNET_CENTRAL_BIND_PORT={central_port}\n",
        encoding="utf-8",
    )

    with _restored_environment():
        for name in [key for key in os.environ if key.startswith("LABNET_")]:
            del os.environ[name]
        user_args = [argument for user in users for argument in ("--user", user)]
        admin([
            "--env-file", str(central_env), "setup", *user_args, "--lab-pc", lab_pc_id,
            "--write-env", str(root), "--server-url", central_url,
        ])
        central = _ThreadedServer(create_central_app(Settings.from_env(central_env)), central_port)
        with (root / f"{lab_pc_id}.env").open("a", encoding="utf-8") as lab_pc_env:
            lab_pc_env.write(f"LABNET_GRPC_PORT={grpc_port}\nLABNET_HTTP_PORT={health_port}\n")
        lab_pc_settings = LabPCSettings.from_env(root / f"{lab_pc_id}.env")

    lab_pc = _ThreadedServer(
        create_lab_pc_app(settings=lab_pc_settings, device_factory=lambda: list(devices())),
        health_port,
    )
    central.start()
    try:
        lab_pc.start()
        try:
            yield LocalNetwork(
                root=root,
                central_url=central_url,
                lab_pc_id=lab_pc_id,
                env_files={user: root / f"{user}.env" for user in users},
            )
        finally:
            lab_pc.stop()
    finally:
        central.stop()


__all__ = ["LocalNetwork", "local_network"]
