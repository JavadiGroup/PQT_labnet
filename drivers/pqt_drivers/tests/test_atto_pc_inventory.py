"""The atto-pc inventory builds its devices offline, with their IDs and the Sacher's site limits."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from labnet.devices.core import get_site_limits

INVENTORY = Path(__file__).resolve().parents[3] / "sites" / "pqt" / "inventories" / "atto_pc.py"


def _load_inventory():
    spec = importlib.util.spec_from_file_location("atto_pc_inventory", INVENTORY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_atto_pc_devices_and_site_limits() -> None:
    devices = _load_inventory().create_devices()  # passive: nothing connects
    by_id = {device.device_id: device for device in devices}
    assert set(by_id) == {"ws7", "sacher-vis", "horiba-spec-vis"}
    assert {device.device_type for device in devices} == {
        "ws7_wavemeter", "sacher_laser", "horiba_spectrometer",
    }
    limits = get_site_limits(by_id["sacher-vis"]).limits
    assert limits == {
        "move_to_wavelength_nm.wavelength_nm": (757.0, 782.0),
        "rebase_position_axis.measured_wavelength_nm": (757.0, 782.0),
    }
