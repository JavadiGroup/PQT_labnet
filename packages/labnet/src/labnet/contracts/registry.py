"""Where LabNet code finds device contracts: by device type, service or RPC.

Driver packages register their contracts in the entry-point group
``labnet.devices``, one entry per device type::

    [project.entry-points."labnet.devices"]
    dummy_aom = "labnet_sim._generated.contracts:DUMMY_AOM_CONTRACT"

:func:`get` imports only the package that provides the requested type;
:func:`all`, :func:`by_service` and :func:`rpc` import every one. Two packages
declaring one type, or one that fails to import, become :func:`problems`, and
:func:`get` for that type raises saying so.

``all`` shadows the builtin inside this module; callers write ``registry.all()``.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from importlib.metadata import EntryPoint, PackageNotFoundError, entry_points, version
from types import MappingProxyType

from .types import DeviceContract, RpcSpec

GROUP = "labnet.devices"


class UnknownDeviceType(KeyError):
    """No contract is registered for a device type."""

    def __init__(self, device_type: object, reason: str = "") -> None:
        super().__init__(device_type)
        self.device_type = device_type
        self.reason = reason

    def __str__(self) -> str:
        message = f"No device contract is registered for device type {self.device_type!r}."
        return f"{message} {self.reason}" if self.reason else message


@dataclass(frozen=True, slots=True)
class RegistryProblem:
    """A contract that could not be registered, and why."""

    device_type: str
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class DriverInfo:
    """Which installed package provides a device type: what a Lab PC reports, and a Control Center compares."""

    package: str
    version: str
    fingerprint: str

    def as_dict(self) -> dict[str, str]:
        return {"package": self.package, "version": self.version, "fingerprint": self.fingerprint}


@dataclass(frozen=True, slots=True)
class _Tables:
    contracts: tuple[DeviceContract, ...]
    by_type: Mapping[str, DeviceContract]
    by_service: Mapping[str, DeviceContract]
    by_rpc: Mapping[str, RpcSpec]

    @classmethod
    def build(cls, contracts: Iterable[DeviceContract]) -> _Tables:
        ordered = tuple(sorted(contracts, key=lambda contract: contract.service_full_name))
        return cls(
            contracts=ordered,
            by_type=MappingProxyType({contract.device_type: contract for contract in ordered}),
            by_service=MappingProxyType({contract.service_full_name: contract for contract in ordered}),
            by_rpc=MappingProxyType(
                {method.rpc_full_name: method for contract in ordered for method in contract.methods}
            ),
        )


_lock = threading.RLock()
_loaded: dict[str, DeviceContract] = {}
_problems: dict[str, RegistryProblem] = {}
_tables_cache: _Tables | None = None
_override: _Tables | None = None


def _entry_points() -> dict[str, list[EntryPoint]]:
    """The registered entries by device type (a seam for tests)."""

    found: dict[str, list[EntryPoint]] = {}
    for entry in entry_points(group=GROUP):
        if not any(entry.value == other.value for other in found.get(entry.name, [])):
            found.setdefault(entry.name, []).append(entry)
    return found


def _source(entry: EntryPoint) -> str:
    dist = getattr(entry, "dist", None)
    return f"{dist.name} ({entry.value})" if dist is not None else entry.value


def _load(device_type: str, entries: list[EntryPoint]) -> DeviceContract | None:
    if device_type in _loaded:
        return _loaded[device_type]
    if device_type in _problems:
        return None
    if len(entries) > 1:
        _problems[device_type] = RegistryProblem(
            device_type,
            " and ".join(_source(entry) for entry in entries),
            f"{device_type!r} is declared by more than one package: "
            + ", ".join(_source(entry) for entry in entries)
            + ". Uninstall all but one.",
        )
        return None
    (entry,) = entries
    try:
        contract = entry.load()
    except Exception as exc:
        _problems[device_type] = RegistryProblem(
            device_type, _source(entry), f"{_source(entry)} failed to import: {type(exc).__name__}: {exc}"
        )
        return None
    if not isinstance(contract, DeviceContract) or contract.device_type != device_type:
        _problems[device_type] = RegistryProblem(
            device_type,
            _source(entry),
            f"{_source(entry)} is not the device contract for {device_type!r}.",
        )
        return None
    _loaded[device_type] = contract
    return contract


def _tables() -> _Tables:
    global _tables_cache
    if _override is not None:
        return _override
    with _lock:
        if _tables_cache is None:
            for device_type, entries in sorted(_entry_points().items()):
                _load(device_type, entries)
            _tables_cache = _Tables.build(_loaded.values())
        return _tables_cache


def _find(device_type: str) -> DeviceContract | None:
    if _override is not None:
        return _override.by_type.get(device_type)
    with _lock:
        if device_type in _loaded:
            return _loaded[device_type]
        entries = _entry_points().get(device_type)
        return _load(device_type, entries) if entries else None


def get(device_type: str) -> DeviceContract:
    """The contract for ``device_type``; :class:`UnknownDeviceType` (a KeyError) if none."""

    contract = _find(device_type) if isinstance(device_type, str) else None
    if contract is None:
        problem = _problems.get(device_type) if _override is None and isinstance(device_type, str) else None
        reason = problem.message if problem else "Is the package with its driver installed?"
        raise UnknownDeviceType(device_type, reason)
    return contract


def find(device_type: str | None) -> DeviceContract | None:
    """The contract for ``device_type``, or None if there is none (or no type)."""

    if not isinstance(device_type, str):
        return None
    return _find(device_type)


def all() -> tuple[DeviceContract, ...]:  # noqa: A001 - registry.all() reads as intended
    """Every registered contract, in a stable order."""

    return _tables().contracts


def by_service(service_full_name: str) -> DeviceContract | None:
    """The contract serving ``<package>.<Name>Service``, or None."""

    return _tables().by_service.get(service_full_name)


def rpc(rpc_full_name: str) -> RpcSpec | None:
    """The spec of one RPC by its full name, or None."""

    return _tables().by_rpc.get(rpc_full_name)


def problems() -> tuple[RegistryProblem, ...]:
    """Contracts that could not be registered: conflicting or failing packages."""

    if _override is not None:
        return ()
    _tables()
    return tuple(problem for _, problem in sorted(_problems.items()))


def driver_info(device_type: str) -> DriverInfo | None:
    """The package, version and fingerprint behind ``device_type`` here, or None."""

    contract = find(device_type)
    if contract is None:
        return None
    try:
        installed = version(contract.distribution) if contract.distribution else ""
    except PackageNotFoundError:
        installed = ""
    return DriverInfo(contract.distribution, installed, contract.fingerprint)


def refresh() -> None:
    """Forget what was loaded: look at the installed packages again on the next lookup."""

    global _tables_cache
    with _lock:
        _loaded.clear()
        _problems.clear()
        _tables_cache = None


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
    "DriverInfo",
    "GROUP",
    "RegistryProblem",
    "UnknownDeviceType",
    "all",
    "by_service",
    "driver_info",
    "find",
    "get",
    "override",
    "problems",
    "refresh",
    "rpc",
]
