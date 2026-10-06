"""Scripts exported from experiments run against a real local network.

The network comes from ``conftest.py`` (Central and a Lab PC serving every
simulated family). Each test generates a script exactly as "Export script"
does, loads it as a module and runs its ``main()``.
"""

from __future__ import annotations

import csv
import importlib.util
import math
from pathlib import Path
from types import ModuleType

import pytest

from labnet import DummyAOM, LabClient
from labnet.control_center.experiment_script import generate_script
from labnet.devices.DummyEOM.physical import bessel_j


def experiment() -> dict:
    """Laser on; AOM frequency (3 points) x AWG offset (2 points); four readings."""

    return {
        "id": "bench-scan-abcdef",
        "version": 1,
        "updated_at": "2026-10-06T12:00:00Z",
        "name": "Bench scan",
        "description": "",
        "run": {"settle_seconds": 0, "samples": 2, "sample_rate_hz": 100.0, "output_name": "bench_scan"},
        "nodes": [
            {"id": "laser", "kind": "instrument", "x": 0, "y": 0, "device_id": "test-laser", "device_type": "dummy_laser",
             "settings": [{"id": "s1", "method": "set_current", "args": {"value": 120}},
                          {"id": "s2", "method": "set_emission_enabled", "args": {"enabled": True}}],
             "records": [{"id": "r1", "method": "read_power", "samples": 1}]},
            {"id": "aom", "kind": "instrument", "x": 256, "y": 0, "device_id": "test-aom", "device_type": "dummy_aom",
             "settings": [{"id": "s1", "method": "set_rf_power", "args": {"value": 1.0}},
                          {"id": "s2", "method": "set_frequency", "sweep": {"start": 68, "stop": 92, "points": 3, "axis": 0}}],
             "records": [{"id": "r1", "method": "read_deflection_angle", "samples": 1},
                         {"id": "r2", "method": "read_diffraction_efficiency"}]},
            {"id": "awg", "kind": "instrument", "x": 256, "y": -160, "device_id": "test-awg", "device_type": "dummy_awg",
             "settings": [{"id": "s1", "method": "set_waveform", "args": {"waveform": "dc", "channel": "CH1"}},
                          {"id": "s2", "method": "set_output_enabled", "args": {"enabled": True, "channel": "CH1"}},
                          {"id": "s3", "method": "set_offset", "args": {"channel": "CH1"},
                           "sweep": {"start": -1, "stop": 1, "points": 2, "axis": 1}}],
             "records": [{"id": "r1", "method": "read_output_rms", "args": {"channel": "CH1"}, "samples": 1}]},
            {"id": "eom", "kind": "instrument", "x": 512, "y": 0, "device_id": "test-eom", "device_type": "dummy_eom",
             "records": [{"id": "r1", "method": "stream_sideband_ratio", "samples": 3}]},
            {"id": "m1", "kind": "mirror", "x": 384, "y": 0},
        ],
        "links": [
            {"id": "l1", "source": "laser", "target": "aom"},
            {"id": "l2", "source": "awg", "target": "aom", "type": "signal"},
            {"id": "l3", "source": "aom", "target": "m1"},
            {"id": "l4", "source": "m1", "target": "eom"},
        ],
    }


def load(script: str, folder: Path) -> ModuleType:
    path = folder / "bench_scan.py"
    path.write_text(script, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("bench_scan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def leased_ids(env_file: Path) -> set[str]:
    central = LabClient.from_env(env_file)
    try:
        return {device.device_id for device in central.list_devices() if device.leased}
    finally:
        central.close()


def test_an_exported_script_runs_the_experiment(connected: None, researcher_env: Path, tmp_path: Path) -> None:
    script = generate_script(experiment())
    output = tmp_path / "bench.csv"

    load(script, tmp_path).main(["--output", str(output)])

    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert len(rows) == 3 * 2
    # Variables (and columns) are named after the devices; setpoints in loop order.
    assert list(rows[0]) == [
        "point", "time_utc", "test_aom_frequency_MHz", "test_awg_offset_ch1_V", "test_laser_power_mW",
        "test_awg_output_rms_ch1_V", "test_aom_deflection_angle_mrad", "test_aom_diffraction_efficiency_mean",
        "test_aom_diffraction_efficiency_std", "test_eom_sideband_ratio_mean", "test_eom_sideband_ratio_std",
    ]
    rows = [{key.removeprefix("test_"): value for key, value in row.items()} for row in rows]
    assert [(float(row["aom_frequency_MHz"]), float(row["awg_offset_ch1_V"])) for row in rows] == [
        (68.0, -1.0), (68.0, 1.0), (80.0, -1.0), (80.0, 1.0), (92.0, -1.0), (92.0, 1.0),
    ]
    beta = math.pi * 1.0 / 5.0  # the EOM's defaults: 1 V on resonance
    for row in rows:
        frequency = float(row["aom_frequency_MHz"])
        assert float(row["laser_power_mW"]) == pytest.approx(0.8 * (120 - 40))
        assert float(row["aom_deflection_angle_mrad"]) == pytest.approx(780e-9 * frequency * 1e6 / 4200 * 1e3)
        assert float(row["aom_diffraction_efficiency_mean"]) == pytest.approx(0.85 * math.exp(-(((frequency - 80) / 12) ** 2)))
        assert float(row["awg_output_rms_ch1_V"]) == pytest.approx(abs(float(row["awg_offset_ch1_V"])))  # dc: |offset|
        assert float(row["eom_sideband_ratio_mean"]) == pytest.approx((bessel_j(1, beta) / bessel_j(0, beta)) ** 2)
    assert leased_ids(researcher_env) == set()  # every lease was released


def test_a_busy_device_stops_the_run_before_anything_changes(
    connected: None, researcher_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other_env = researcher_env.with_name("other-client.env")
    other = LabClient.from_env(other_env)
    held = DummyAOM(device_id="test-aom", client=other)  # someone else is using the AOM
    output = tmp_path / "bench.csv"
    try:
        with pytest.raises(SystemExit, match="LabNet error"):
            load(generate_script(experiment()), tmp_path).main(["--output", str(output)])
    finally:
        held.close()
        other.close()
    assert not output.exists()  # leasing comes first, so no file and no settings
    assert "test-laser" not in leased_ids(researcher_env)  # the laser, leased first, was released
