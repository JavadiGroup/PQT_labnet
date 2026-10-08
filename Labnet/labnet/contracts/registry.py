"""Where LabNet code finds device contracts: by device type, service or RPC.

Every lookup goes through here, so where contracts come from can change
without touching the code that uses them. Today they come from the generated
``labnet.grpc.generated.device_contracts`` module, imported on first use.

``all`` shadows the builtin inside this module; callers write ``registry.all()``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Iterator, Mapping

from .types import DeviceContract, RpcSpec


class UnknownDeviceType(KeyError):
    """No contract is registered for a device type."""

    def __init__(self, device_type: object) -> None:
        super().__init__(device_type)
        self.device_type = device_type

    def __str__(self) -> str:
        return f"No device contract is registered for device type {self.device_type!r}."


@dataclass(frozen=True, slots=True)
class RegistryProblem:
    """A contract that could not be registered, and why."""

    source: str
    message: str


@dataclass(frozen=True, slots=True)
class _Tables:
    contracts: tuple[DeviceContract, ...]
    by_type: Mapping[str, DeviceContract]
    by_service: Mapping[str, DeviceContract]
    by_rpc: Mapping[str, RpcSpec]

    @classmethod
    def build(cls, contracts: Iterable[DeviceContract]) -> _Tables:
        contracts = tuple(contracts)
        return cls(
            contracts=contracts,
            by_type=MappingProxyType({contract.device_type: contract for contract in contracts}),
            by_service=MappingProxyType({contract.service_full_name: contract for contract in contracts}),
            by_rpc=MappingProxyType(
                {method.rpc_full_name: method for contract in contracts for method in contract.methods}
            ),
        )


_loaded: _Tables | None = None
_override: _Tables | None = None


def _tables() -> _Tables:
    global _loaded
    if _override is not None:
        return _override
    if _loaded is None:
        from labnet.grpc.generated import device_contracts as generated

        _loaded = _Tables.build(generated.DEVICE_CONTRACTS)
    return _loaded


def get(device_type: str) -> DeviceContract:
    """The contract for ``device_type``; :class:`UnknownDeviceType` (a KeyError) if none."""

    try:
        return _tables().by_type[device_type]
    except KeyError:
        raise UnknownDeviceType(device_type) from None


def find(device_type: str | None) -> DeviceContract | None:
    """The contract for ``device_type``, or None if there is none (or no type)."""

    if device_type is None:
        return None
    return _tables().by_type.get(device_type)


def all() -> tuple[DeviceContract, ...]:  # noqa: A001 - registry.all() reads as intended
    """Every registered contract, in a stable order."""

    return _tables().contracts


def by_service(service_full_name: str) -> DeviceContract | None:
    """The contract serving ``labnet.devices.v1.<Name>Service``, or None."""

    return _tables().by_service.get(service_full_name)


def rpc(rpc_full_name: str) -> RpcSpec | None:
    """The spec of one RPC by its full name, or None."""

    return _tables().by_rpc.get(rpc_full_name)


def problems() -> tuple[RegistryProblem, ...]:
    """Contracts that could not be registered. One generated module can't conflict, so none yet."""

    _tables()
    return ()


@contextlib.contextmanager
def override(contracts: Iterable[DeviceContract]) -> Iterator[None]:
    """Use exactly ``contracts`` inside the block (for tests)."""

    global _override
    previous = _override
    _override = _Tables.build(contracts)
    try:
        yield
    finally:
        _override = previous


__all__ = [
    "RegistryProblem",
    "UnknownDeviceType",
    "all",
    "by_service",
    "find",
    "get",
    "override",
    "problems",
    "rpc",
]
