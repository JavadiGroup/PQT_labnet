"""Driver status: whether a Lab PC's driver for a device is this Control Center's own."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from labnet.client import LabPCInfo
from labnet.contracts import registry
from labnet_control_center.contracts import driver_status

from .test_automation_protocol import LIVE, _check, _good
from .test_control_center import ENDPOINT, NOW, _app, _device

SIM = registry.driver_info("dummy_device").as_dict()


def _reporting(**driver) -> dict:
    return {"driver": {**SIM, **driver}}


def test_the_same_package_version_and_contract_is_ok() -> None:
    status = driver_status("dummy_device", _reporting())
    assert status["status"] == "ok" and status["reported"] == status["local"] == SIM
    assert status["detail"] == f"labnet-sim {SIM['version']}, the same as this Control Center."


@pytest.mark.parametrize("change, why", [
    ({"version": "9.9.9"}, "a different version"),
    ({"fingerprint": "0" * 64}, "a different contract"),
    ({"package": "acme-sim"}, "a different package"),
])
def test_any_difference_is_a_mismatch(change: dict, why: str) -> None:
    status = driver_status("dummy_device", _reporting(**change))
    assert status["status"] == "mismatch"
    assert f"({why}). Install the same release on both." in status["detail"]


def test_a_control_center_without_the_driver_says_what_to_install() -> None:
    with registry.override([]):
        status = driver_status("dummy_device", _reporting())
    assert status["status"] == "missing" and status["local"] is None
    assert f'pip install "labnet-sim>={SIM["version"]}"' in status["detail"]
    with registry.override([]):
        assert "install the package that provides it" in driver_status("dummy_device", {})["detail"]


@pytest.mark.parametrize("metadata", [{}, {"driver": "labnet-sim"}, _reporting(fingerprint="not hex"),
                                      _reporting(package="<script>"), _reporting(version="")])
def test_no_report_or_a_malformed_one_is_unknown(metadata: dict) -> None:
    status = driver_status("dummy_device", metadata)
    assert status["status"] == "unknown" and status["reported"] is None


class _Central:
    def __init__(self, *devices) -> None:
        self.network = [LabPCInfo(manager_id="lab-pc-01", host="192.168.50.20", endpoint=ENDPOINT, online=True,
                                  last_seen=NOW, devices=devices)]

    async def list_lab_pcs(self):
        return self.network

    async def aclose(self) -> None:
        pass


def _status(central: _Central) -> dict:
    with _app(central) as client:  # type: ignore[arg-type]
        devices = client.get("/api/status").json()["lab_pcs"][0]["devices"]
    return {device["id"]: device for device in devices}


def test_the_network_shows_each_devices_driver_status() -> None:
    central = _Central(
        replace(_device("same"), metadata=_reporting()),
        replace(_device("older"), metadata=_reporting(version="2.9.0")),
    )
    devices = _status(central)
    assert devices["same"]["driver"]["status"] == "ok"
    assert devices["older"]["driver"]["status"] == "mismatch"


def test_without_labnet_sim_the_device_still_shows_with_an_install_hint() -> None:
    central = _Central(replace(_device("aom", device_type="dummy_aom"), metadata=_reporting()))
    with registry.override([]):
        device = _status(central)["aom"]
    assert device["known_contract"] is False and device["methods"] == []
    assert device["driver"]["status"] == "missing"
    assert 'pip install "labnet-sim>=' in device["driver"]["detail"]


def test_automation_refuses_a_device_whose_driver_does_not_match() -> None:
    live = copy.deepcopy(LIVE)
    live["dev01-aom"]["driver"] = {"status": "mismatch", "detail": "Its Lab PC runs labnet-sim 2.9.0, ..."}
    normalized, problems = _check(_good(), live_devices=live)
    assert normalized is None
    assert any(
        problem["path"] == "devices[0].device_type" and "driver doesn't match" in problem["message"]
        for problem in problems
    ), problems
    live["dev01-aom"]["driver"] = {"status": "ok"}
    assert _check(_good(), live_devices=live)[1] == []
