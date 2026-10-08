"""Site limits: per-device limits a Lab PC's inventory sets, enforced on that Lab PC.

A driver may publish hints in ``metadata()["hints"]`` (its own hard ranges and
choices). An inventory can narrow them for one instrument on one bench::

    from labnet.devices.core import site_limits

    def create_devices():
        return [
            site_limits(
                PhysicalSimulatedTunableLaser(device_id="lab01-laser"),
                limits={"go_to_wavelength_nm.wavelength_nm": (770.0, 790.0)},
                choices={"move_steps.velocity_mode": ["slow"]},
            ),
        ]

Keys are ``<method>.<argument>``, exactly as hints and Python clients name them
(``set_frequency.value``, ``stream_data.channel``). :func:`site_limits` returns
the same device object, so its type and contract are unchanged; the limits ride
on it as ``labnet_site_limits``.

The Lab PC then:

* refuses to start if a key names no method or argument of the device's
  contract, a range is inverted or not finite, a choice has the wrong type
  (:func:`site_limits` itself, when the inventory runs), or a site limit is not
  inside the driver's own published limit or choices (when the device has
  connected and published its hints): site limits may only narrow;
* refuses, before the driver is called, every remote call whose argument is
  outside the effective range or choices (the driver's and the site's together),
  so published hints are never merely advisory (:meth:`DeviceLimits.check`);
* publishes the effective values in ``metadata()["hints"]``, with
  ``hints["sources"][key]`` = ``"site"`` or ``"driver"``.

Limits apply to the physical method a call reaches. Two RPCs of one physical
method (``read_value`` and ``stream_data`` both call ``read_value``) share their
limits: a site choice for ``read_value.channel`` also limits ``stream_data``'s
channel, and both keys are published. A stream's own controls
(``sample_rate_hz``, ``sample_count``) are not arguments and are not limited
here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar


LIMITS_ATTRIBUTE = "labnet_site_limits"

_NUMBER_KINDS = frozenset({"float", "int"})
_CHOICE_KINDS = frozenset({"str", "bool"})
_MISSING = object()
D = TypeVar("D")


class DeviceLimitsError(ValueError):
    """Site limits, or a driver's published hints, can't be used as limits."""


@dataclass(frozen=True, slots=True)
class SiteLimits:
    """The limits an inventory set for one device (keys as ``<method>.<argument>``)."""

    limits: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    choices: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FieldRule:
    """The effective limit of one argument of one physical method."""

    method: str
    field: str
    kind: str
    low: float | None = None
    high: float | None = None
    choices: tuple[Any, ...] | None = None
    # The source of the binding limit: "site" when the inventory set one.
    source: str = "driver"


@dataclass(frozen=True, slots=True)
class DeviceLimits:
    """Effective limits of one device: what is enforced and what is published."""

    device_id: str
    # physical method -> argument -> kind ("float", "int", "str", "bool", or other)
    kinds: Mapping[str, Mapping[str, str]]
    # physical method -> argument -> rule, only for limited arguments
    rules: Mapping[str, Mapping[str, FieldRule]]
    hints: Mapping[str, Any]

    def check(self, method: str, arguments: Mapping[str, Any]) -> str | None:
        """Why this call must be refused, or None when it is within limits.

        ``arguments`` are the call's keyword arguments by name. An argument
        that has a site limit must be given: the driver's default for it may
        lie outside the site's limit.
        """

        kinds = self.kinds.get(method, {})
        rules = self.rules.get(method, {})
        for name, value in arguments.items():
            if kinds.get(name) in _NUMBER_KINDS and isinstance(value, bool):
                return (f"{self.device_id}.{method}: {name} is a number, and "
                        f"{_shown(value)} is not one.")
        for name, rule in rules.items():
            value = arguments.get(name, _MISSING)
            where = "site limit" if rule.source == "site" else "driver's limit"
            if value is _MISSING or value is None:
                if rule.source == "site":
                    return (f"{self.device_id}.{method}: {name} has a site limit "
                            f"({_rule_text(rule)}), so it must be sent explicitly; "
                            "the driver's default may lie outside it.")
                continue
            if rule.kind in _NUMBER_KINDS:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return (f"{self.device_id}.{method}: {name} must be a number, "
                            f"not {_shown(value)}.")
                if rule.kind == "int" and not isinstance(value, int):
                    return (f"{self.device_id}.{method}: {name} must be a whole "
                            f"number, not {_shown(value)}.")
                try:
                    number = float(value)
                except OverflowError:
                    number = math.inf
                if not math.isfinite(number):
                    return f"{self.device_id}.{method}: {name} must be finite."
                if rule.low is not None and number < rule.low or \
                        rule.high is not None and number > rule.high:
                    return (f"{self.device_id}.{method}: {name}={value:g} is outside "
                            f"the {where} {_rule_text(rule)}.")
                continue
            if rule.choices is not None:
                wanted = bool if rule.kind == "bool" else str
                if not isinstance(value, wanted) or value not in rule.choices:
                    allowed = "site choices" if rule.source == "site" else "driver's choices"
                    return (f"{self.device_id}.{method}: {name}={_shown(value)} is "
                            f"not one of the {allowed} ({_rule_text(rule)}).")
        return None

    def limits_method(self, method: str) -> bool:
        """True if calls of ``method`` need :meth:`check`."""

        return method in self.rules or any(
            kind in _NUMBER_KINDS for kind in self.kinds.get(method, {}).values()
        )


# ---- the inventory's API --------------------------------------------------------------------


def site_limits(
    device: D,
    *,
    limits: Mapping[str, Sequence[float]] | None = None,
    choices: Mapping[str, Sequence[Any]] | None = None,
) -> D:
    """Give ``device`` site limits and return the same device.

    ``limits`` maps ``"<method>.<argument>"`` of a number argument to
    ``(min, max)``; ``choices`` maps one of a text or true/false argument to
    the values allowed. Raises :class:`DeviceLimitsError` (a ``ValueError``)
    for anything that can't be a limit of this device's contract. Whether the
    limits lie inside the driver's own is checked when the device connects.
    """

    contract = _contract(device)
    device_id = _device_label(device)
    if getattr(device, LIMITS_ATTRIBUTE, None) is not None:
        raise DeviceLimitsError(f"{device_id}: site_limits() was already applied; give all its limits in one call.")
    if limits is not None and not isinstance(limits, Mapping):
        raise DeviceLimitsError(f"{device_id}: limits must be a dict of '<method>.<argument>': (min, max).")
    if choices is not None and not isinstance(choices, Mapping):
        raise DeviceLimitsError(f"{device_id}: choices must be a dict of '<method>.<argument>': [values].")
    if not limits and not choices:
        raise DeviceLimitsError(f"{device_id}: site_limits() needs limits or choices.")
    fields = _contract_fields(contract)
    clean_limits = {
        key: _range(key, value, fields, device_id, contract.device_type, what="site limit")
        for key, value in (limits or {}).items()
    }
    clean_choices = {
        key: _choice_list(key, value, fields, device_id, contract.device_type, what="site choices")
        for key, value in (choices or {}).items()
    }
    both = sorted(set(clean_limits) & set(clean_choices))
    if both:
        raise DeviceLimitsError(f"{device_id}: {both[0]} is in both limits and choices; give it once.")
    site = SiteLimits(limits=clean_limits, choices=clean_choices)
    # Several keys of one physical argument must leave something allowed.
    _merge(fields, {}, site, device_id)
    try:
        setattr(device, LIMITS_ATTRIBUTE, site)
    except (AttributeError, TypeError) as exc:
        raise DeviceLimitsError(
            f"{device_id}: site limits can't be attached to {type(device).__name__} "
            f"(it does not accept the attribute {LIMITS_ATTRIBUTE!r})."
        ) from exc
    return device


def get_site_limits(device: object) -> SiteLimits | None:
    """The site limits :func:`site_limits` gave ``device``, or None."""

    value = getattr(device, LIMITS_ATTRIBUTE, None)
    if value is None:
        return None
    if not isinstance(value, SiteLimits):
        raise DeviceLimitsError(
            f"{_device_label(device)}: {LIMITS_ATTRIBUTE} is not a SiteLimits; set it only with site_limits()."
        )
    return value


# ---- effective limits -----------------------------------------------------------------------


def resolve_limits(
    contract: Any,
    driver_hints: Any,
    site: SiteLimits | None,
    *,
    device_id: str,
) -> DeviceLimits:
    """Effective limits from the driver's published hints and the site's limits.

    Raises :class:`DeviceLimitsError` if the driver's hints are malformed or
    name no argument of the contract, or a site limit is not inside the
    driver's own limit or choices.
    """

    fields = _contract_fields(contract)
    driver = _driver_hints(driver_hints, fields, device_id, contract.device_type)
    merged = _merge(fields, driver, site, device_id)

    kinds: dict[str, dict[str, str]] = {}
    for target, value in fields.items():
        if isinstance(target, tuple):
            method, name = target
            kinds.setdefault(method, {})[name] = value[1]

    rules: dict[str, dict[str, FieldRule]] = {}
    for (method, name), rule in merged.items():
        rules.setdefault(method, {})[name] = rule

    hints: dict[str, Any] = {}
    if driver.get("channels"):
        hints["channels"] = list(driver["channels"])
    published_limits: dict[str, list[float]] = {}
    published_choices: dict[str, list[Any]] = {}
    sources: dict[str, str] = {}
    for spec in contract.methods:
        for item in spec.forwarded_field_specs:
            rule = merged.get((spec.physical_method, item.name))
            if rule is None:
                continue
            key = f"{spec.python_name}.{item.name}"
            if rule.kind in _NUMBER_KINDS:
                published_limits[key] = [float(rule.low), float(rule.high)]
            elif rule.choices is not None:
                only_channels = (
                    item.name == "channel" and rule.source == "driver"
                    and (spec.physical_method, item.name) not in driver["explicit_choices"]
                )
                if only_channels:
                    continue  # published once, as hints["channels"]
                published_choices[key] = list(rule.choices)
            else:
                continue
            sources[key] = rule.source
    if published_limits:
        hints["limits"] = published_limits
    if published_choices:
        hints["choices"] = published_choices
    if sources:
        hints["sources"] = sources
    return DeviceLimits(device_id=device_id, kinds=kinds, rules=rules, hints=hints)


# ---- helpers ----------------------------------------------------------------------------------


def _contract(device: object) -> Any:
    from labnet.grpc.generated.device_contracts import DEVICE_CONTRACT_BY_TYPE

    device_type = getattr(device, "device_type", None)
    contract = DEVICE_CONTRACT_BY_TYPE.get(device_type) if isinstance(device_type, str) else None
    if contract is None:
        raise DeviceLimitsError(
            f"{_device_label(device)}: device_type {device_type!r} has no generated contract, so its "
            "arguments can't be limited."
        )
    return contract


def _device_label(device: object) -> str:
    try:
        device_id = getattr(device, "device_id")
    except Exception:  # noqa: BLE001 - a label for an error message only
        device_id = None
    return device_id if isinstance(device_id, str) and device_id else type(device).__name__


def _contract_fields(contract: Any) -> dict[Any, Any]:
    """``{key: (physical method, argument, kind)}`` and ``{(physical method, argument): (key, kind)}``."""

    fields: dict[Any, Any] = {}
    for spec in contract.methods:
        for item in spec.forwarded_field_specs:
            kind = item.python_type.replace(" | None", "").strip()
            fields[f"{spec.python_name}.{item.name}"] = (spec.physical_method, item.name, kind)
            fields.setdefault((spec.physical_method, item.name), (f"{spec.python_name}.{item.name}", kind))
    return fields


def _resolve(key: Any, fields: dict[Any, Any], device_id: str, device_type: str, what: str) -> tuple[str, str, str]:
    if not isinstance(key, str) or key.count(".") != 1:
        raise DeviceLimitsError(f"{device_id}: {what} key {key!r} must be '<method>.<argument>'.")
    found = fields.get(key)
    if found is None:
        method, _, argument = key.partition(".")
        arguments = sorted(name.partition(".")[2] for name in fields
                           if isinstance(name, str) and name.partition(".")[0] == method)
        if arguments:
            raise DeviceLimitsError(
                f"{device_id}: {what} {key!r}: {device_type}.{method} has no argument {argument!r} "
                f"(its arguments: {', '.join(arguments)})."
            )
        methods = sorted({name.partition(".")[0] for name in fields if isinstance(name, str)})
        raise DeviceLimitsError(
            f"{device_id}: {what} {key!r}: {device_type} has no method {method!r} with arguments "
            f"(methods with arguments: {', '.join(methods) or 'none'})."
        )
    return found


def _range(key: Any, value: Any, fields: dict[Any, Any], device_id: str, device_type: str,
           what: str) -> tuple[float, float]:
    _method, _argument, kind = _resolve(key, fields, device_id, device_type, what)
    if kind not in _NUMBER_KINDS:
        raise DeviceLimitsError(
            f"{device_id}: {what} {key!r} is a range, but {key} is {kind}; give it as choices instead."
        )
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence) or len(value) != 2:
        raise DeviceLimitsError(f"{device_id}: {what} {key!r} must be (min, max), not {value!r}.")
    bounds = []
    for bound in value:
        if isinstance(bound, bool) or not isinstance(bound, (int, float)):
            raise DeviceLimitsError(f"{device_id}: {what} {key!r}: {bound!r} is not a number.")
        try:
            number = float(bound)
        except OverflowError:
            number = math.inf
        if not math.isfinite(number):
            raise DeviceLimitsError(f"{device_id}: {what} {key!r}: {bound!r} is not finite.")
        bounds.append(number)
    low, high = bounds
    if low > high:
        raise DeviceLimitsError(f"{device_id}: {what} {key!r} is inverted: min {low:g} is above max {high:g}.")
    return low, high


def _choice_list(key: Any, value: Any, fields: dict[Any, Any], device_id: str, device_type: str,
                 what: str) -> tuple[Any, ...]:
    _method, _argument, kind = _resolve(key, fields, device_id, device_type, what)
    if kind not in _CHOICE_KINDS:
        hint = "give it as a (min, max) limit instead" if kind in _NUMBER_KINDS else "it can't be limited"
        raise DeviceLimitsError(f"{device_id}: {what} {key!r} are choices, but {key} is {kind}; {hint}.")
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence) or not value:
        raise DeviceLimitsError(f"{device_id}: {what} {key!r} must be a non-empty list of values, not {value!r}.")
    clean: list[Any] = []
    for item in value:
        if kind == "bool" and not isinstance(item, bool):
            raise DeviceLimitsError(f"{device_id}: {what} {key!r}: {item!r} is not True or False.")
        if kind == "str" and (not isinstance(item, str) or not item):
            raise DeviceLimitsError(f"{device_id}: {what} {key!r}: {item!r} is not non-empty text.")
        if item in clean:
            raise DeviceLimitsError(f"{device_id}: {what} {key!r}: {item!r} is listed twice.")
        clean.append(item)
    return tuple(clean)


def _driver_hints(raw: Any, fields: dict[Any, Any], device_id: str, device_type: str) -> dict[str, Any]:
    """The driver's own limits by physical argument; malformed hints are refused."""

    driver: dict[str, Any] = {"channels": None, "limits": {}, "choices": {}, "explicit_choices": set()}
    if raw is None:
        return driver
    if not isinstance(raw, Mapping):
        raise DeviceLimitsError(f"{device_id}: the driver's metadata()['hints'] must be a dict.")
    channels = raw.get("channels")
    if channels is not None:
        if isinstance(channels, (str, bytes)) or not isinstance(channels, Sequence) or not channels or \
                not all(isinstance(item, str) and item for item in channels):
            raise DeviceLimitsError(f"{device_id}: the driver's hints['channels'] must be a list of channel names.")
        driver["channels"] = tuple(dict.fromkeys(channels))
    limits = raw.get("limits")
    limits = {} if limits is None else limits
    if not isinstance(limits, Mapping):
        raise DeviceLimitsError(f"{device_id}: the driver's hints['limits'] must be a dict.")
    for key, value in limits.items():
        method, argument, _kind = _resolve(key, fields, device_id, device_type, "driver limit")
        low, high = _range(key, value, fields, device_id, device_type, "driver limit")
        current = driver["limits"].get((method, argument))
        if current is not None:
            low, high = max(low, current[0]), min(high, current[1])
            if low > high:
                raise DeviceLimitsError(f"{device_id}: the driver's limits for {method}.{argument} don't overlap.")
        driver["limits"][(method, argument)] = (low, high)
    choices = raw.get("choices")
    choices = {} if choices is None else choices
    if not isinstance(choices, Mapping):
        raise DeviceLimitsError(f"{device_id}: the driver's hints['choices'] must be a dict.")
    for key, value in choices.items():
        method, argument, _kind = _resolve(key, fields, device_id, device_type, "driver choices")
        options = _choice_list(key, value, fields, device_id, device_type, "driver choices")
        current = driver["choices"].get((method, argument))
        if current is not None:
            options = tuple(item for item in current if item in options)
            if not options:
                raise DeviceLimitsError(f"{device_id}: the driver's choices for {method}.{argument} don't overlap.")
        driver["choices"][(method, argument)] = options
        driver["explicit_choices"].add((method, argument))
    if driver["channels"]:
        for target in [name for name in fields if isinstance(name, tuple) and name[1] == "channel"]:
            if fields[target][1] != "str":
                continue
            current = driver["choices"].get(target)
            options = driver["channels"] if current is None else tuple(
                item for item in current if item in driver["channels"])
            if not options:
                raise DeviceLimitsError(
                    f"{device_id}: the driver's choices for {target[0]}.channel name none of its channels."
                )
            driver["choices"][target] = options
    return driver


def _merge(fields: dict[Any, Any], driver: dict[str, Any], site: SiteLimits | None,
           device_id: str) -> dict[tuple[str, str], FieldRule]:
    """One rule per limited physical argument; site limits must lie inside the driver's."""

    site_ranges: dict[tuple[str, str], tuple[float, float]] = {}
    site_choices: dict[tuple[str, str], tuple[Any, ...]] = {}
    site_keys: dict[tuple[str, str], list[str]] = {}
    for key, (low, high) in (site.limits if site else {}).items():
        method, argument, _kind = fields[key]
        target = (method, argument)
        site_keys.setdefault(target, []).append(key)
        if target in site_ranges:
            low, high = max(low, site_ranges[target][0]), min(high, site_ranges[target][1])
            if low > high:
                raise DeviceLimitsError(f"{device_id}: the site limits {', '.join(site_keys[target])} don't "
                                        "overlap (they limit the same physical argument).")
        site_ranges[target] = (low, high)
    for key, options in (site.choices if site else {}).items():
        method, argument, _kind = fields[key]
        target = (method, argument)
        site_keys.setdefault(target, []).append(key)
        if target in site_choices:
            options = tuple(item for item in site_choices[target] if item in options)
            if not options:
                raise DeviceLimitsError(f"{device_id}: the site choices {', '.join(site_keys[target])} have no "
                                        "value in common (they limit the same physical argument).")
        site_choices[target] = options

    driver_ranges = driver.get("limits", {})
    driver_choices = driver.get("choices", {})
    merged: dict[tuple[str, str], FieldRule] = {}
    for target in set(site_ranges) | set(driver_ranges):
        kind = fields[target][1]
        mine = site_ranges.get(target)
        theirs = driver_ranges.get(target)
        if mine is not None and theirs is not None and not (theirs[0] <= mine[0] and mine[1] <= theirs[1]):
            raise DeviceLimitsError(
                f"{device_id}: site limit {', '.join(site_keys[target])} ({mine[0]:g} to {mine[1]:g}) is not "
                f"inside the driver's own limit ({theirs[0]:g} to {theirs[1]:g}); site limits may only narrow it."
            )
        low, high = mine if mine is not None else theirs
        merged[target] = FieldRule(method=target[0], field=target[1], kind=kind, low=low, high=high,
                                   source="site" if mine is not None else "driver")
    for target in set(site_choices) | set(driver_choices):
        kind = fields[target][1]
        mine = site_choices.get(target)
        theirs = driver_choices.get(target)
        if mine is not None and theirs is not None:
            outside = [item for item in mine if item not in theirs]
            if outside:
                raise DeviceLimitsError(
                    f"{device_id}: site choices {', '.join(site_keys[target])} include {outside[0]!r}, which the "
                    f"driver does not offer ({', '.join(map(repr, theirs))}); site choices may only narrow them."
                )
        merged[target] = FieldRule(method=target[0], field=target[1], kind=kind,
                                   choices=mine if mine is not None else theirs,
                                   source="site" if mine is not None else "driver")
    return merged


def _rule_text(rule: FieldRule) -> str:
    if rule.kind in _NUMBER_KINDS:
        return f"{rule.low:g} to {rule.high:g}"
    return ", ".join(_shown(item) for item in rule.choices or ())


def _shown(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return repr(value[:64])
    if value is None:
        return "None"
    return type(value).__name__ if not isinstance(value, (int, float)) else f"{value!r}"


__all__ = [
    "DeviceLimits",
    "DeviceLimitsError",
    "FieldRule",
    "LIMITS_ATTRIBUTE",
    "SiteLimits",
    "get_site_limits",
    "resolve_limits",
    "site_limits",
]
