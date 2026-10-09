"""labnet driver new: the template builds, passes check_driver and works through its facade.

The full round trip (pip install, `labnet driver build`, the package's own
pytest) runs in CI's driver-template job.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from labnet import cli
from labnet.contracts import registry
from labnet.driver import ContractValidationError, build
from labnet.driver.build import target_from_project
from labnet.driver.cli import entry_point_problems
from labnet.driver.new import DriverNames, create
from labnet.testing import check_driver


def test_names_derive_from_the_package_and_the_service() -> None:
    names = DriverNames.of("acme-thing", "AcmeThingService")
    assert (names.package, names.service, names.device_type, names.constant) == (
        "acme_thing", "AcmeThingService", "acme_thing", "ACME_THING_CONTRACT"
    )
    for bad in (("Acme_Thing", "AcmeThing"), ("acme-thing", "acme_thing"), ("labnet", "Thing")):
        with pytest.raises(ContractValidationError):
            DriverNames.of(*bad)


def test_a_new_driver_builds_and_passes_the_lab_pcs_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "acme-thing"
    assert cli.main(["driver", "new", "acme-thing", "--service", "AcmeThing", "--directory", str(project)]) == 0
    assert (project / "protos" / "acme_thing" / "v1" / "acme_thing.proto").is_file()
    assert 'acme_thing = "acme_thing._generated.contracts:ACME_THING_CONTRACT"' in (
        project / "pyproject.toml"
    ).read_text(encoding="utf-8")

    target = target_from_project(project)
    specs = build(target)
    assert entry_point_problems(project, target.python_package, target.contracts_module, specs) == []

    monkeypatch.syspath_prepend(str(project / "src"))
    importlib.invalidate_caches()
    package = importlib.import_module("acme_thing")
    contract = importlib.import_module("acme_thing._generated.contracts").ACME_THING_CONTRACT
    assert (contract.distribution, contract.facade) == ("acme-thing", "acme_thing:AcmeThing")
    with registry.override([contract]):
        snapshot = check_driver(package.PhysicalAcmeThing("test-1"))
        assert snapshot.metadata["hints"]["limits"] == {"set_level.value": [0.0, 10.0]}
        device = package.AcmeThing(device_id="local-1")
        try:
            assert device.set_level(2.5) == 2.5 and device.read_level() == 2.5
        finally:
            device.close()


def test_new_refuses_a_folder_that_is_not_empty(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ContractValidationError, match="not empty"):
        create(tmp_path, DriverNames.of("acme-thing", "AcmeThing"))
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "mine"
