"""A Lab PC reports which driver package, version and contract each device runs."""

from __future__ import annotations

from labnet.contracts import registry
from labnet.testing import check_driver
from labnet_sim import PhysicalDummyAOM


def test_each_device_reports_its_driver() -> None:
    snapshot = check_driver(PhysicalDummyAOM("aom-1"))
    driver = snapshot.metadata["driver"]
    assert driver == registry.driver_info("dummy_aom").as_dict()
    assert driver["package"] == "labnet-sim" and len(driver["fingerprint"]) == 64
