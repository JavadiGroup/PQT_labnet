"""A protocol's devices and bounds as text the agent reads (the resume prompt, ``grant_status``).

The protocol is the one a person approved, as the Control Center normalized it
(it comes with the grant's token), never the agent's own file.

Standard library only: the MCP server imports this inside the agent container.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


# How the Control Center checks the bounds, said once under the devices.
RULES = (
    "Every argument shown for a method must be passed in every call (leaving one out is refused: the driver's "
    "default, e.g. another channel, was not approved); arguments not shown may never be passed.",
    "\"each change ≤ N\" is measured from the device's current reading (the Control Center reads it before the "
    "first change of that setting, per channel) and then from the last value set; after a call that failed, "
    "stay within N of both the old and the attempted value.",
    "A refused call is final: fix the script or end the experiment; never retry it or probe the limits.",
)


def protocol_sha256(protocol: Any) -> str:
    """The sha256 the Control Center gives a normalized protocol (its canonical JSON); "" if it can't be."""

    try:
        text = json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float) and math.isfinite(value) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:g}" if isinstance(value, float) else str(value)


def _bound(name: str, bound: Any) -> str:
    if not isinstance(bound, dict):
        return f"{name}: {json.dumps(bound, ensure_ascii=False)[:200]}"
    if "choices" in bound:
        choices = bound.get("choices") or []
        shown = ", ".join(json.dumps(choice, ensure_ascii=False) for choice in choices[:32])
        return f"{name}: one of {shown}"
    text = f"{name}: {_number(bound.get('min'))} … {_number(bound.get('max'))}"
    if bound.get("max_step") is not None:
        text += f", each change ≤ {_number(bound['max_step'])}"
    return text


def method_line(name: str, spec: Any) -> str:
    """``set_frequency(value: 70 … 90, each change ≤ 2): at most 200 calls, ≤ 5 calls/s``"""

    spec = spec if isinstance(spec, dict) else {}
    args = spec.get("args") if isinstance(spec.get("args"), dict) else {}
    parts = [_bound(arg, bound) for arg, bound in args.items()]
    stream = spec.get("stream") if isinstance(spec.get("stream"), dict) else None
    if stream:
        parts.append(f"sample_rate_hz ≤ {_number(stream.get('max_sample_rate_hz'))}")
        parts.append(f"sample_count ≤ {_number(stream.get('max_samples'))}")
    limits = []
    if spec.get("max_calls") is not None:
        limits.append(f"at most {_number(spec['max_calls'])} calls")
    if spec.get("max_rate_hz") is not None:
        limits.append(f"≤ {_number(spec['max_rate_hz'])} calls/s")
    return f"{name}({'; '.join(parts)})" + (f": {', '.join(limits)}" if limits else "")


def _devices(devices: Any) -> list[tuple[str, Any, dict[str, Any]]]:
    """(device_id, device_type, methods) from a protocol's list or a grant summary's mapping."""

    out: list[tuple[str, Any, dict[str, Any]]] = []
    if isinstance(devices, dict):
        for device_id, item in devices.items():
            item = item if isinstance(item, dict) else {}
            out.append((str(device_id), item.get("device_type"), item.get("methods") or {}))
    elif isinstance(devices, list):
        for item in devices:
            if isinstance(item, dict):
                out.append((str(item.get("device_id")), item.get("device_type"), item.get("methods") or {}))
            elif isinstance(item, str):
                out.append((item, None, {}))
    return out


def device_lines(devices: Any) -> list[str]:
    """One line per device and per approved method, then the rules (when any method is listed)."""

    lines: list[str] = []
    listed = False
    for device_id, device_type, methods in _devices(devices):
        lines.append(f"- `{device_id}`" + (f" ({device_type})" if device_type else ""))
        if not methods:
            lines.append("  - (its approved bounds weren't received; every call outside them is refused)")
        for name, spec in (methods.items() if isinstance(methods, dict) else []):
            lines.append(f"  - {method_line(str(name), spec)}")
            listed = True
    if listed:
        lines += ["", *(f"Note: {rule}" for rule in RULES)]
    return lines


def safe_state_lines(safe_state: Any) -> list[str]:
    lines = []
    for item in safe_state if isinstance(safe_state, list) else []:
        if isinstance(item, dict):
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            shown = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in args.items())
            lines.append(f"- {item.get('device_id')}.{item.get('method')}({shown})")
    return lines


__all__ = ["RULES", "device_lines", "method_line", "protocol_sha256", "safe_state_lines"]
