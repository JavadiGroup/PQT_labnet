"""labnet.contracts.registry: the one way to find a device contract."""

from __future__ import annotations

import pytest

from labnet.contracts import DeviceContract, registry
from labnet.grpc.generated import device_contracts as generated


def test_it_serves_exactly_the_generated_contracts() -> None:
    assert registry.all() == generated.DEVICE_CONTRACTS
    for contract in generated.DEVICE_CONTRACTS:
        assert registry.get(contract.device_type) is contract
        assert registry.find(contract.device_type) is contract
        assert registry.by_service(contract.service_full_name) is contract
        for method in contract.methods:
            assert registry.rpc(method.rpc_full_name) is method
    assert registry.problems() == ()


def test_the_types_are_the_ones_generated_code_uses() -> None:
    assert generated.DeviceContract is DeviceContract
    assert isinstance(registry.get("dummy_device"), DeviceContract)


def test_unknown_lookups() -> None:
    assert registry.find("no_such_device") is None
    assert registry.find(None) is None
    assert registry.by_service("labnet.devices.v1.NoSuchService") is None
    assert registry.rpc("labnet.devices.v1.NoSuchService.Read") is None
    with pytest.raises(registry.UnknownDeviceType) as caught:
        registry.get("no_such_device")
    assert str(caught.value) == "No device contract is registered for device type 'no_such_device'."
    # Callers that caught the old dict's KeyError still do.
    assert isinstance(caught.value, KeyError)


def test_override_replaces_the_contracts_inside_the_block_only() -> None:
    wavemeter = registry.get("simulated_wavemeter")
    with registry.override([wavemeter]):
        assert registry.all() == (wavemeter,)
        assert registry.find("dummy_device") is None
        with registry.override([]):
            assert registry.all() == ()
        assert registry.all() == (wavemeter,)
    assert registry.all() == generated.DEVICE_CONTRACTS
