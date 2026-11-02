"""The Control Center panels in src/pqt_drivers/panels/ match the contracts they drive.

The panels are JavaScript, so these checks read them as text: each registers
itself under its device type, and every method it calls (or streams) exists in
that type's contract with the arguments it passes. A typo would otherwise only
show up as an error on the device page.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from labnet.contracts import registry


ROOT = Path(__file__).resolve().parents[1]
PANELS = ROOT / "src" / "pqt_drivers" / "panels"
STREAM = "RPC_PATTERN_POLLED_SCALAR_STREAM"
# session.call("method", { name: ..., ... }) - only the first argument name is checked.
CALL = re.compile(r'session\.call\(\s*"(?P<method>\w+)"(?:\s*,\s*\{\s*(?P<argument>\w+)\s*[:,}])?')
# A "[method, { name: ... }]" pair the Horiba panel collects and calls in a loop.
QUEUED = re.compile(r'\[\s*"(?P<method>\w+)"\s*,\s*\{\s*(?P<argument>\w+)\s*:')
# A "[button, method]" pair whose method is called with no arguments (WS7 start/stop).
PAIRED = re.compile(r'\[\s*\w+\s*,\s*"(?P<method>\w+)"\s*\]')
PLAYED = re.compile(r'stream:\s*\{\s*method:\s*"(?P<method>\w+)"')


def _entry_points(group: str) -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return project["entry-points"][group]


DEVICE_TYPES = sorted(_entry_points("labnet.devices"))


def test_every_device_type_has_a_panel() -> None:
    panels = _entry_points("labnet.control_center.panels")

    assert sorted(panels) == DEVICE_TYPES
    assert set(panels.values()) == {"pqt_drivers.panels"}
    assert sorted(path.stem for path in PANELS.glob("*.js")) == DEVICE_TYPES


@pytest.mark.parametrize("device_type", DEVICE_TYPES)
def test_the_panel_drives_only_what_its_contract_serves(device_type: str) -> None:
    source = (PANELS / f"{device_type}.js").read_text(encoding="utf-8")
    methods = {spec.python_name: spec for spec in registry.get(device_type).methods}

    assert f"window.LabNet.panels.{device_type} = mount;" in source
    calls = [*CALL.finditer(source), *QUEUED.finditer(source), *PAIRED.finditer(source)]
    assert calls, "the panel calls nothing"
    for match in calls:
        spec = methods.get(match["method"])
        assert spec is not None, f"{match['method']} is not a method of {device_type}"
        assert spec.pattern != STREAM, f"{match['method']} is a stream; the panel must Play it"
        if match.groupdict().get("argument"):
            fields = [field.name for field in spec.forwarded_field_specs]
            assert match["argument"] in fields, f"{match['method']} takes {fields}, not {match['argument']}"
    for match in PLAYED.finditer(source):
        assert methods[match["method"]].pattern == STREAM


def test_the_sacher_panel_never_serves_supervised_calibration_calls() -> None:
    source = (PANELS / "sacher_laser.js").read_text(encoding="utf-8")

    for name in ("jog_steps", "calibration_mode", "reference_wavelength_nm"):
        assert name not in source


def test_the_control_center_finds_the_panels() -> None:
    panels = pytest.importorskip("labnet_control_center.panels")
    if not hasattr(panels, "panel_for"):
        pytest.skip("this Control Center does not serve driver panels")
    panels._registered.cache_clear()

    for device_type in DEVICE_TYPES:
        assert panels.panel_for(device_type) == (device_type, f"/api/panels/{device_type}.js")
        assert f"LabNet.panels.{device_type} = mount" in panels.driver_panel(device_type)
