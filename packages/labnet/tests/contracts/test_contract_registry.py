"""labnet.contracts.registry: contracts from the installed driver packages' entry points."""

from __future__ import annotations

import dataclasses
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from labnet.contracts import DeviceContract, registry
from labnet_sim._generated import contracts as sim


@pytest.fixture
def fresh_registry():
    registry.refresh()
    yield
    registry.refresh()


def test_it_serves_the_installed_driver_packages(fresh_registry) -> None:
    assert set(registry.all()) == set(sim.DEVICE_CONTRACTS)
    for contract in sim.DEVICE_CONTRACTS:
        assert registry.get(contract.device_type) is contract
        assert registry.find(contract.device_type) is contract
        assert registry.by_service(contract.service_full_name) is contract
        for method in contract.methods:
            assert registry.rpc(method.rpc_full_name) is method
    assert registry.problems() == ()


def test_each_contract_names_its_driver(fresh_registry) -> None:
    aom = registry.get("dummy_aom")
    assert isinstance(aom, DeviceContract)
    assert aom.service_full_name == "labnet_sim.v1.DummyAOMService"
    assert (aom.distribution, aom.facade) == ("labnet-sim", "labnet_sim:DummyAOM")
    assert aom.physical == "labnet_sim.dummy_aom.physical:PhysicalDummyAOM"
    info = registry.driver_info("dummy_aom")
    assert info.package == "labnet-sim" and info.version and len(info.fingerprint) == 64
    assert info.as_dict() == {"package": "labnet-sim", "version": info.version, "fingerprint": aom.fingerprint}
    assert registry.driver_info("no_such_device") is None


def test_unknown_lookups(fresh_registry) -> None:
    assert registry.find("no_such_device") is None
    assert registry.find(None) is None
    assert registry.by_service("labnet_sim.v1.NoSuchService") is None
    assert registry.rpc("labnet_sim.v1.NoSuchService.Read") is None
    with pytest.raises(registry.UnknownDeviceType) as caught:
        registry.get("no_such_device")
    assert str(caught.value) == (
        "No device contract is registered for device type 'no_such_device'. "
        "Is the package with its driver installed?"
    )
    assert isinstance(caught.value, KeyError)  # callers that caught the old dict's KeyError still do


def test_conflicting_and_broken_packages_are_problems(
    fresh_registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fake_rival.py").write_text(
        "import dataclasses\n"
        "from labnet_sim._generated.contracts import DUMMY_AOM_CONTRACT\n"
        "AOM = dataclasses.replace(DUMMY_AOM_CONTRACT, distribution='fake-rival')\n",
        encoding="utf-8",
    )
    (tmp_path / "fake_broken.py").write_text("import vendor_sdk_that_is_missing\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    real = registry._entry_points()

    def entries():
        return {
            **real,
            "dummy_aom": [*real["dummy_aom"], EntryPoint("dummy_aom", "fake_rival:AOM", registry.GROUP)],
            "broken_laser": [EntryPoint("broken_laser", "fake_broken:LASER", registry.GROUP)],
        }

    monkeypatch.setattr(registry, "_entry_points", entries)
    registry.refresh()

    with pytest.raises(registry.UnknownDeviceType, match="'dummy_aom' is declared by more than one package"):
        registry.get("dummy_aom")
    with pytest.raises(registry.UnknownDeviceType, match="failed to import: ModuleNotFoundError"):
        registry.get("broken_laser")
    assert {problem.device_type for problem in registry.problems()} == {"dummy_aom", "broken_laser"}
    # The others still load: one bad package doesn't hide the rest.
    assert registry.get("dummy_eom").distribution == "labnet-sim"
    assert "dummy_aom" not in {contract.device_type for contract in registry.all()}


def test_override_replaces_the_contracts_inside_the_block_only(fresh_registry) -> None:
    wavemeter = registry.get("simulated_wavemeter")
    with registry.override([wavemeter]):
        assert registry.all() == (wavemeter,)
        assert registry.find("dummy_device") is None
        with registry.override([]):
            assert registry.all() == ()
        assert registry.all() == (wavemeter,)
    assert set(registry.all()) == set(sim.DEVICE_CONTRACTS)
    assert dataclasses.is_dataclass(wavemeter)
