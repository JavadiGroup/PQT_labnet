"""Run every script in ``examples/`` against a real local network.

Central and one Lab PC run as Uvicorn servers in background threads, set up
the way an operator would: ``labnet-central-admin setup --write-env`` plus a
Lab PC env file. The examples then connect through ``LabClient.from_env()``.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import uvicorn

from labnet.central.config import Settings
from labnet.central.main import create_app as create_central_app
from labnet.central.manage import main as admin
from labnet.lab_pc.app import create_app as create_lab_pc_app
from labnet.lab_pc.config import LabPCSettings


EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
LAB_PC_ID = "lab-pc-test"


def load_example(name: str) -> ModuleType:
    path = next(EXAMPLES.glob(f"{name}_*.py"))
    spec = importlib.util.spec_from_file_location(f"example_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    inventory.write_text(
        "from labnet import PhysicalDummyDevice, PhysicalSimulatedWavemeter\n"
        "def create_devices():\n"
        "    return [PhysicalDummyDevice('test-dummy'),\n"
        "            PhysicalSimulatedWavemeter('test-wavemeter')]\n",
        encoding="utf-8",
    )

    with _restored_environment():
        for name in [key for key in os.environ if key.startswith("LABNET_")]:
            del os.environ[name]
        admin([
            "--env-file", str(central_env), "setup",
            "--user", "researcher", "--lab-pc", LAB_PC_ID,
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


def test_every_example_has_a_main() -> None:
    names = sorted(path.name for path in EXAMPLES.glob("0*_*.py"))
    assert names == [
        "01_hello_device.py",
        "02_choose_device.py",
        "03_stream_async.py",
        "04_scan_two_devices.py",
        "05_local_hardware.py",
    ]
    for path in EXAMPLES.glob("0*_*.py"):
        assert callable(load_example(path.name[:2]).main)


def test_01_hello_device(connected: None, capsys: pytest.CaptureFixture[str]) -> None:
    load_example("01").main()

    assert capsys.readouterr().out.strip() == "test-dummy 12.5"


def test_02_choose_device(connected: None, capsys: pytest.CaptureFixture[str]) -> None:
    load_example("02").main(["--lab-pc", LAB_PC_ID])

    output = capsys.readouterr().out
    assert "test-wavemeter" in output and "simulated_wavemeter" in output
    assert "Leased test-dummy" in output


def test_03_stream_async(connected: None, capsys: pytest.CaptureFixture[str]) -> None:
    load_example("03").main()

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 20 and all(line.endswith(" nm") for line in lines)


def test_04_scan_two_devices(
    connected: None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "scan.csv"

    load_example("04").main(["--output", str(output)])

    rows = output.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "setpoint,wavelength_nm,std_nm"
    assert len(rows) == 1 + 5
    assert "source: test-dummy   meter: test-wavemeter" in capsys.readouterr().out


def test_05_local_hardware(capsys: pytest.CaptureFixture[str]) -> None:
    load_example("05").main()

    output = capsys.readouterr().out
    assert "local dummy reads 2.5" in output
    assert output.count("local wavemeter") == 3
