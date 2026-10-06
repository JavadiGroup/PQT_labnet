"""Run every script in ``examples/`` against a real local network.

The network (Central plus one Lab PC with every simulated family) comes from
``conftest.py``; the examples connect through ``LabClient.from_env()``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
LAB_PC_ID = "lab-pc-test"  # as in conftest.py


def load_example(name: str) -> ModuleType:
    path = next(EXAMPLES.glob(f"{name}_*.py"))
    spec = importlib.util.spec_from_file_location(f"example_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
