"""A real local network for scripts that use ``LabClient.from_env()``.

Central and one Lab PC run as Uvicorn servers in background threads, set up
the way an operator would: ``labnet-central-admin setup --write-env`` plus a
Lab PC env file. The examples and generated experiment scripts then connect
through ``LABNET_ENV_FILE``.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from labnet.central.config import Settings
from labnet.central.main import create_app as create_central_app
from labnet.central.manage import main as admin
from labnet.lab_pc.app import create_app as create_lab_pc_app
from labnet.lab_pc.config import LabPCSettings


LAB_PC_ID = "lab-pc-test"
# Every simulated family; noise off where the tests compare exact values.
INVENTORY = """\
from labnet import (
    PhysicalDummyAOM, PhysicalDummyAWG, PhysicalDummyDevice, PhysicalDummyEOM,
    PhysicalDummyLaser, PhysicalSimulatedWavemeter,
)

def create_devices():
    return [
        PhysicalDummyDevice('test-dummy'),
        PhysicalSimulatedWavemeter('test-wavemeter'),
        PhysicalDummyAOM('test-aom', noise=0.0),
        PhysicalDummyEOM('test-eom', noise=0.0),
        PhysicalDummyAWG('test-awg', noise=0.0),
        PhysicalDummyLaser('test-laser', noise_mw=0.0, noise_nm=0.0),
    ]
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def _restored_environment() -> Iterator[None]:
    """Env files load into os.environ; keep them out of the test process."""

    saved = os.environ.copy()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class _ThreadedServer:
    def __init__(self, app: object, port: int) -> None:
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.ready_url = f"http://127.0.0.1:{port}/health/ready"

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.ready_url, timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"{self.ready_url} did not become ready.")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture(scope="module")
def researcher_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("network")
    central_port, grpc_port, health_port = _free_port(), _free_port(), _free_port()
    central_env = root / "central.env"
    central_env.write_text(
        f"LABNET_DATABASE_URL=sqlite:///{(root / 'central.db').as_posix()}\n"
        f"LABNET_CENTRAL_BIND_PORT={central_port}\n",
        encoding="utf-8",
    )
    inventory = root / "inventory.py"
    inventory.write_text(INVENTORY, encoding="utf-8")

    with _restored_environment():
        for name in [key for key in os.environ if key.startswith("LABNET_")]:
            del os.environ[name]
        admin([
            "--env-file", str(central_env), "setup",
            "--user", "researcher", "--user", "other-client", "--lab-pc", LAB_PC_ID,
            "--write-env", str(root), "--server-url", f"http://127.0.0.1:{central_port}",
        ])
        central = _ThreadedServer(
            create_central_app(Settings.from_env(central_env)), central_port
        )
        with (root / f"{LAB_PC_ID}.env").open("a", encoding="utf-8") as lab_pc_env:
            lab_pc_env.write(
                f"LABNET_GRPC_PORT={grpc_port}\n"
                f"LABNET_HTTP_PORT={health_port}\n"
                f"LABNET_DEVICE_INVENTORY={inventory.as_posix()}:create_devices\n"
            )
        lab_pc_settings = LabPCSettings.from_env(root / f"{LAB_PC_ID}.env")

    lab_pc = _ThreadedServer(create_lab_pc_app(settings=lab_pc_settings), health_port)
    central.start()
    try:
        lab_pc.start()
        try:
            yield root / "researcher.env"
        finally:
            lab_pc.stop()
    finally:
        central.stop()


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch, researcher_env: Path) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in [key for key in os.environ if key.startswith("LABNET_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("LABNET_ENV_FILE", str(researcher_env))
