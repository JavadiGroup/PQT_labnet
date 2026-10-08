"""Phase 3: protocols (validation, normalization, sha256), submission, approval, rejection, timeouts, ended_unsafe."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from labnet.control_center.app import create_app
from labnet.control_center.automation_grants import GrantCallState, check_call
from labnet.control_center.automation_protocol import (
    MAX_PROTOCOL_BYTES,
    build_review,
    canonical,
    parse_protocol,
    protocol_files,
    protocol_sha256,
    step_reader,
    validate_protocol,
)
from labnet.control_center.automation_store import AutomationError
from labnet.control_center.config import ControlCenterSettings
from labnet.grpc.generated.device_contracts import DEVICE_CONTRACT_BY_TYPE

from .test_automation import EXPERIMENT, FakeCentral, _settings, library  # noqa: F401  (library is a fixture)
from .test_automation_runner import RUNNER, SECRET, _bearer


GOOD: dict[str, Any] = {
    "schema_version": 1,
    "task": "Tasks/Task1.md",
    "title": "AOM efficiency vs RF frequency",
    "duration_minutes": 20,
    "devices": [
        {
            "device_id": "dev01-aom",
            "device_type": "dummy_aom",
            "methods": {
                "set_frequency": {"args": {"value": {"min": 70, "max": 90, "max_step": 2}}, "max_calls": 200,
                                  "max_rate_hz": 5},
                "set_rf_enabled": {"args": {"enabled": {"choices": [True, False]}}, "max_calls": 10, "max_rate_hz": 1},
                "read_diffraction_efficiency": {"max_calls": 500, "max_rate_hz": 10},
                "stream_diffraction_efficiency": {
                    "args": {"channel": {"choices": ["CH1"]}},
                    "stream": {"max_sample_rate_hz": 20, "max_samples": 1000},
                    "max_calls": 50, "max_rate_hz": 1,
                },
            },
        }
    ],
    "safe_state": [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False}}],
}
AOM_HINTS = {"limits": {"set_frequency.value": [60.0, 100.0], "set_rf_power.value": [0.0, 2.0]}}
AWG_HINTS = {"channels": ["CH1", "CH2"], "choices": {"set_waveform.waveform": ["sine", "square"]}}
PROJECT = {"allowed_devices": ["dev01-aom", "dev02-awg"]}
CANVAS = [{"device_id": "dev01-aom", "device_type": "dummy_aom"}, {"device_id": "dev02-awg", "device_type": "dummy_awg"},
          {"device_id": "dev03-laser", "device_type": "dummy_laser"}]
LIVE = {
    "dev01-aom": {"id": "dev01-aom", "type": "dummy_aom", "state": "available", "hints": AOM_HINTS},
    "dev02-awg": {"id": "dev02-awg", "type": "dummy_awg", "state": "available", "hints": AWG_HINTS},
    "dev03-laser": {"id": "dev03-laser", "type": "dummy_laser", "state": "available", "hints": {}},
    "dev09-free": {"id": "dev09-free", "type": "dummy_aom", "state": "available", "hints": {}},
}


def _check(protocol: Any, **overrides) -> tuple[dict | None, list[dict[str, str]]]:
    options = dict(project=PROJECT, live_devices=LIVE, canvas_devices=CANVAS, max_minutes=120, task="Tasks/Task1.md")
    options.update(overrides)
    return validate_protocol(protocol, **options)


def _good() -> dict[str, Any]:
    return copy.deepcopy(GOOD)


def _aom(protocol: dict) -> dict:
    return protocol["devices"][0]["methods"]


def _awg(methods: dict) -> dict:
    return {"device_id": "dev02-awg", "device_type": "dummy_awg", "methods": methods}


# ---- the good protocol, normalized --------------------------------------------------------------


def test_the_contract_example_passes_and_is_normalized() -> None:
    normalized, problems = _check(_good())
    assert problems == []
    assert normalized["duration_minutes"] == 20.0 and isinstance(normalized["duration_minutes"], float)
    frequency = normalized["devices"][0]["methods"]["set_frequency"]
    assert frequency == {"args": {"value": {"min": 70.0, "max": 90.0, "max_step": 2.0}}, "max_calls": 200,
                         "max_rate_hz": 5.0}
    assert all(isinstance(frequency["args"]["value"][key], float) for key in ("min", "max", "max_step"))
    assert isinstance(frequency["max_calls"], int) and isinstance(frequency["max_rate_hz"], float)
    read = normalized["devices"][0]["methods"]["read_diffraction_efficiency"]
    assert read == {"args": {}, "max_calls": 500, "max_rate_hz": 10.0}  # defaults filled, no stream
    stream = normalized["devices"][0]["methods"]["stream_diffraction_efficiency"]
    assert stream["stream"] == {"max_sample_rate_hz": 20.0, "max_samples": 1000}
    assert normalized["safe_state"] == [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False}}]

    no_safe = _good()
    del no_safe["safe_state"]
    assert _check(no_safe)[0]["safe_state"] == []
    no_step = _good()
    del _aom(no_step)["set_frequency"]["args"]["value"]["max_step"]
    assert _check(no_step)[0]["devices"][0]["methods"]["set_frequency"]["args"]["value"]["max_step"] is None


def test_canonical_text_and_sha256_are_stable() -> None:
    normalized, _ = _check(_good())
    text = canonical(normalized)
    assert " " not in text.replace("AOM efficiency vs RF frequency", "") and text.startswith('{"devices":')
    assert protocol_sha256(normalized) == hashlib.sha256(text.encode("utf-8")).hexdigest()

    # The same protocol written differently: key order, whitespace, 200 vs 200.0, max_step null.
    shuffled = json.loads(json.dumps(_good()), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
    _aom(shuffled)["set_frequency"]["max_calls"] = 200.0
    _aom(shuffled)["set_frequency"]["args"]["value"]["min"] = 70.0
    raw = json.dumps(shuffled, indent=4).encode("utf-8")
    parsed, problems = parse_protocol(raw)
    assert problems == []
    again, _ = _check(parsed)
    assert canonical(again) == text and protocol_sha256(again) == protocol_sha256(normalized)

    # Normalizing the normalized copy changes nothing (approval re-checks it).
    assert protocol_sha256(_check(copy.deepcopy(normalized))[0]) == protocol_sha256(normalized)
    # A real change changes the hash.
    changed = _good()
    _aom(changed)["set_frequency"]["args"]["value"]["max"] = 89
    assert protocol_sha256(_check(changed)[0]) != protocol_sha256(normalized)
    # Non-ASCII text stays as itself.
    titled = _good()
    titled["title"] = "Effizienz über Frequenz – AOM"
    assert "über" in canonical(_check(titled)[0])


# ---- reading the file -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b'{"a": 1, "a": 2}', "appears more than once"),
        (b'{"schema_version": NaN}', "NaN"),
        (b'{"schema_version": Infinity}', "Infinity"),
        (b"{'single': 1}", "isn't valid JSON"),
        (b'{"a": 1,}', "isn't valid JSON"),
        (b'{"a": 1} // comment', "isn't valid JSON"),
        (b"\xff\xfe{}", "isn't UTF-8"),
        (b"[" * 30000 + b"]" * 30000, "nested too deeply"),
        (b" " * (MAX_PROTOCOL_BYTES + 1), "at most 64 KiB"),
        (b"[]", "must be a JSON object"),
        (b'"text"', "must be a JSON object"),
    ],
    ids=["repeated-key", "nan", "infinity", "single-quotes", "trailing-comma", "comment", "not-utf8", "deep",
         "too-large", "list", "string"],
)
def test_files_that_are_not_protocols_are_refused(raw: bytes, expected: str) -> None:
    parsed, problems = parse_protocol(raw)
    if not problems:
        _normalized, problems = _check(parsed)
    assert problems and any(expected in problem["message"] for problem in problems), problems


def test_repeated_keys_are_refused_where_they_are() -> None:
    text = json.dumps(_good()).replace('"set_rf_enabled": {', '"set_frequency": {"max_calls": 1, "max_rate_hz": 1}, '
                                                             '"set_rf_enabled": {', 1)
    parsed, problems = parse_protocol(text.encode())
    assert problems == []
    _normalized, problems = _check(parsed)
    assert {"path": "devices[0].methods.set_frequency",
            "message": "The key 'set_frequency' appears more than once in this object; give it once."} in problems

    nan = json.dumps(_good()).replace('"max": 90', '"max": NaN')
    _normalized, problems = _check(parse_protocol(nan.encode())[0])
    assert problems == [{"path": "devices[0].methods.set_frequency.args.value.max",
                         "message": "max is NaN; write a finite number."}]
    huge = json.dumps(_good()).replace('"max": 90', '"max": 1e400')
    _normalized, problems = _check(parse_protocol(huge.encode())[0])
    assert problems[0]["path"].endswith("args.value.max") and "finite" in problems[0]["message"]


# ---- the rules, one by one --------------------------------------------------------------------


def _set(path: str, value: Any):
    """A change to GOOD: set (or, with value ..., delete) the item at a dotted path."""

    def change(protocol: dict) -> None:
        parts = path.split(".")
        target: Any = protocol
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        last = parts[-1]
        if value is ...:
            del target[int(last) if isinstance(target, list) else last]
        elif isinstance(target, list):
            target[int(last)] = value
        else:
            target[last] = value

    return change


M = "devices.0.methods"
FREQ = f"{M}.set_frequency"
STREAM = f"{M}.stream_diffraction_efficiency"


@pytest.mark.parametrize(
    "change, path, message",
    [
        # top level
        (_set("schema_version", 2), "schema_version", "must be 1"),
        (_set("schema_version", True), "schema_version", "must be 1"),
        (_set("extra", 1), "extra", "Unknown key 'extra'"),
        (_set("task", "Tasks/Task2.md"), "task", "must be 'Tasks/Task1.md'"),
        (_set("title", ""), "title", "must not be empty"),
        (_set("title", "x" * 121), "title", "at most 120"),
        (_set("title", "bad‮title"), "title", "invisible character"),
        (_set("title", ...), "title", "needs 'title'"),
        (_set("duration_minutes", 0), "duration_minutes", "from 1 to 120"),
        (_set("duration_minutes", 121), "duration_minutes", "from 1 to 120"),
        (_set("duration_minutes", "20"), "duration_minutes", "must be a number, not text"),
        (_set("duration_minutes", True), "duration_minutes", "must be a number, not true"),
        (_set("devices", []), "devices", "1 to 16 items"),
        (_set("devices", {}), "devices", "must be a list"),
        (_set("safe_state", [GOOD["safe_state"][0]] * 17), "safe_state", "0 to 16 items"),
        # devices
        (_set("devices.0.label", "AOM"), "devices[0].label", "Unknown key 'label'"),
        (_set("devices.0.device_id", "dev09-free"), "devices[0].device_id", "isn't on any experiment linked"),
        (_set("devices.0.device_id", "dev03-laser"), "devices[0].device_id", "isn't allowed for automation"),
        (_set("devices.0.device_type", "dummy_awg"), "devices[0].device_type", "is a dummy_aom on the network"),
        (_set("devices.0.methods", {}), "devices[0].methods", "1 to 64 methods"),
        (_set(f"{M}.set_power", {"max_calls": 1, "max_rate_hz": 1}), "devices[0].methods.set_power",
         "has no method 'set_power'"),
        # methods
        (_set(f"{FREQ}.max_calls", ...), "devices[0].methods.set_frequency.max_calls", "needs 'max_calls'"),
        (_set(f"{FREQ}.max_rate_hz", ...), "devices[0].methods.set_frequency.max_rate_hz", "needs 'max_rate_hz'"),
        (_set(f"{FREQ}.max_calls", 0), "set_frequency.max_calls", "whole number from 1 to 100000"),
        (_set(f"{FREQ}.max_calls", 100001), "set_frequency.max_calls", "whole number from 1 to 100000"),
        (_set(f"{FREQ}.max_calls", 1.5), "set_frequency.max_calls", "whole number from 1 to 100000"),
        (_set(f"{FREQ}.max_calls", True), "set_frequency.max_calls", "must be a number, not true"),
        (_set(f"{FREQ}.max_rate_hz", 0), "set_frequency.max_rate_hz", "more than 0 and at most 100"),
        (_set(f"{FREQ}.max_rate_hz", 100.5), "set_frequency.max_rate_hz", "more than 0 and at most 100"),
        (_set(f"{FREQ}.retries", 3), "set_frequency.retries", "Unknown key 'retries'"),
        (_set(f"{FREQ}.stream", {"max_sample_rate_hz": 1, "max_samples": 1}), "set_frequency.stream",
         "Only streams take stream limits"),
        # bounds of a number
        (_set(f"{FREQ}.args", ...), "set_frequency.args.value", "always sends value, so it needs bounds"),
        (_set(f"{FREQ}.args.value.min", True), "args.value.min", "must be a number, not true"),
        (_set(f"{FREQ}.args.value.min", "70"), "args.value.min", "must be a number, not text"),
        (_set(f"{FREQ}.args.value.min", 95), "args.value.min", "min (95) is larger than max (90)"),
        (_set(f"{FREQ}.args.value.max_step", 0), "args.value.max_step", "more than 0"),
        (_set(f"{FREQ}.args.value.max_step", -1), "args.value.max_step", "more than 0"),
        (_set(f"{FREQ}.args.value.max", ...), "args.value.max", "needs 'max'"),
        (_set(f"{FREQ}.args.value.choices", [70]), "args.value.choices", "Unknown key 'choices'"),
        (_set(f"{FREQ}.args.value.min", 59.9), "args.value.min", "below the driver's limit for set_frequency.value "
                                                                  "(60 to 100 MHz)"),
        (_set(f"{FREQ}.args.value.max", 100.1), "args.value.max", "above the driver's limit"),
        (_set(f"{FREQ}.args.unit", {"choices": ["Hz"]}), "set_frequency.args.unit", "unit can't be bounded or sent"),
        (_set(f"{FREQ}.args.channel", {"choices": ["CH1"]}), "set_frequency.args.channel", "has no argument 'channel'"),
        (_set(f"{M}.read_diffraction_efficiency.args", {"x": {"min": 0, "max": 1}}), "read_diffraction_efficiency.args.x",
         "takes no arguments"),
        # bounds of a bool or a string
        (_set(f"{M}.set_rf_enabled.args.enabled", {"choices": [1, 0]}), "args.enabled.choices[0]", "true or false"),
        (_set(f"{M}.set_rf_enabled.args.enabled", {"choices": [True, True]}), "args.enabled.choices[1]", "listed twice"),
        (_set(f"{M}.set_rf_enabled.args.enabled", {"choices": [True, False, True]}), "args.enabled.choices", "1 to 2"),
        (_set(f"{M}.set_rf_enabled.args.enabled", {"min": 0, "max": 1}), "args.enabled.min", "Unknown key 'min'"),
        (_set(f"{M}.set_rf_enabled.args.enabled", {"choices": []}), "args.enabled.choices", "1 to 2 items"),
        (_set(f"{STREAM}.args.channel", {"choices": ["CH1", "CH1"]}), "channel.choices[1]", "listed twice"),
        (_set(f"{STREAM}.args.channel", {"choices": [""]}), "channel.choices[0]", "must not be empty"),
        (_set(f"{STREAM}.args.channel", {"choices": ["x" * 65]}), "channel.choices[0]", "at most 64"),
        (_set(f"{STREAM}.args.channel", {"choices": [" CH1"]}), "channel.choices[0]", "start or end with spaces"),
        (_set(f"{STREAM}.args.channel", {"choices": [f"C{i}" for i in range(33)]}), "channel.choices", "1 to 32"),
        (_set(f"{STREAM}.args.channel", {"choices": [1]}), "channel.choices[0]", "is text"),
        # streams
        (_set(f"{STREAM}.stream", ...), "stream_diffraction_efficiency.stream", "needs 'stream'"),
        (_set(f"{STREAM}.stream.max_sample_rate_hz", 201), "stream.max_sample_rate_hz", "at most 200"),
        (_set(f"{STREAM}.stream.max_sample_rate_hz", 0), "stream.max_sample_rate_hz", "more than 0"),
        (_set(f"{STREAM}.stream.max_samples", 0), "stream.max_samples", "from 1 to 100000"),
        (_set(f"{STREAM}.stream.max_samples", 100001), "stream.max_samples", "from 1 to 100000"),
        (_set(f"{STREAM}.stream.max_samples", 2.5), "stream.max_samples", "whole number"),
        (_set(f"{STREAM}.stream.duration_seconds", 5), "stream.duration_seconds", "Unknown key"),
        (_set(f"{STREAM}.args.sample_rate_hz", {"min": 1, "max": 2}), "args.sample_rate_hz", "is a stream control"),
        # the safe state
        (_set("safe_state.0.args.enabled", 0), "safe_state[0].args.enabled", "isn't one of the protocol's choices"),
        (_set("safe_state.0.args", {}), "safe_state[0].args.enabled", "always sends enabled"),
        (_set("safe_state.0.method", "set_rf_power"), "safe_state[0].method", "isn't listed under devices[0].methods"),
        (_set("safe_state.0.device_id", "dev02-awg"), "safe_state[0].device_id", "isn't in devices"),
        (_set("safe_state.0", {"device_id": "dev01-aom", "method": "set_frequency", "args": {"value": 95}}),
         "safe_state[0].args.value", "95 is outside the protocol's bounds for value (70 to 90)"),
        (_set("safe_state.0", {"device_id": "dev01-aom", "method": "set_frequency", "args": {"value": True}}),
         "safe_state[0].args.value", "must be a number, not true"),
        (_set("safe_state.0", {"device_id": "dev01-aom", "method": "stream_diffraction_efficiency"}),
         "safe_state[0].method", "is a stream"),
        (_set("safe_state.0", {"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": False},
                               "delay": 1}), "safe_state[0].delay", "Unknown key"),
        (_set("safe_state.0", {"device_id": "dev01-aom", "method": "read_diffraction_efficiency",
                               "args": {"channel": "CH1"}}), "safe_state[0].args.channel", "isn't bounded"),
    ],
)
def test_each_rule_refuses_with_a_path_and_an_actionable_message(change, path: str, message: str) -> None:
    protocol = _good()
    change(protocol)
    normalized, problems = _check(protocol)
    assert normalized is None
    assert any(path in problem["path"] and message in problem["message"] for problem in problems), problems


def test_duplicate_devices_unknown_types_and_devices_off_the_network() -> None:
    twice = _good()
    twice["devices"].append(copy.deepcopy(twice["devices"][0]))
    problems = _check(twice)[1]
    assert problems == [{"path": "devices[1].device_id", "message": "dev01-aom is already listed as devices[0]; list "
                                                                  "each device once, with all its methods under it."}]

    offline = {key: value for key, value in LIVE.items() if key != "dev01-aom"}
    problems = _check(_good(), live_devices=offline)[1]
    assert any("isn't on the network now" in problem["message"] for problem in problems)

    swapped = {**LIVE, "dev01-aom": {**LIVE["dev01-aom"], "type": "dummy_eom"}}
    problems = _check(_good(), live_devices=swapped)[1]
    messages = " ".join(problem["message"] for problem in problems)
    assert "is a dummy_eom on the network" in messages and "a person must fix the experiment" in messages

    unknown = {**LIVE, "dev01-aom": {**LIVE["dev01-aom"], "type": "flux_capacitor"}}
    canvas = [{"device_id": "dev01-aom", "device_type": "flux_capacitor"}]
    protocol = _good()
    protocol["devices"][0]["device_type"] = "flux_capacitor"
    problems = _check(protocol, live_devices=unknown, canvas_devices=canvas)[1]
    assert any("no contract for flux_capacitor" in problem["message"] for problem in problems)

    many = _good()
    many["devices"] = [copy.deepcopy(many["devices"][0]) for _ in range(17)]
    assert _check(many)[1][0]["message"] == "devices must have 1 to 16 items; it has 17."


def test_optional_arguments_may_be_left_out_and_then_never_sent() -> None:
    protocol = _good()
    del _aom(protocol)["stream_diffraction_efficiency"]["args"]
    normalized, problems = _check(protocol)
    assert problems == [] and normalized["devices"][0]["methods"]["stream_diffraction_efficiency"]["args"] == {}
    review = build_review(normalized, LIVE)
    stream = next(item for item in review["devices"][0]["methods"] if item["name"] == "stream_diffraction_efficiency")
    assert stream["never_sent"] == ["channel"] and stream["args"] == []


def test_driver_hints_are_hard_limits_channels_and_choices_included() -> None:
    exactly = _good()
    _aom(exactly)["set_frequency"]["args"]["value"] = {"min": 60, "max": 100}
    assert _check(exactly)[1] == []

    awg = {
        "set_waveform": {"args": {"waveform": {"choices": ["sine"]}, "channel": {"choices": ["CH1", "CH2"]}},
                         "max_calls": 5, "max_rate_hz": 1},
        "set_amplitude": {"args": {"value": {"min": 0, "max": 1}}, "max_calls": 5, "max_rate_hz": 1},
    }
    protocol = _good()
    protocol["devices"].append(_awg(awg))
    normalized, problems = _check(protocol)
    assert problems == [] and normalized["devices"][1]["methods"]["set_waveform"]["args"]["channel"] == {
        "choices": ["CH1", "CH2"]}

    awg["set_waveform"]["args"]["waveform"]["choices"] = ["sine", "triangle"]
    awg["set_waveform"]["args"]["channel"]["choices"] = ["CH3"]
    problems = _check(protocol)[1]
    assert {"path": "devices[1].methods.set_waveform.args.waveform.choices[1]",
            "message": "'triangle' isn't one of the driver's choices for set_waveform.waveform: sine, square."} in problems
    assert {"path": "devices[1].methods.set_waveform.args.channel.choices[0]",
            "message": "'CH3' isn't one of this device's channels: CH1, CH2."} in problems

    # Without published channels or limits, any choice and range within the general rules is fine.
    free = _good()
    _aom(free)["stream_diffraction_efficiency"]["args"]["channel"]["choices"] = ["anything", "B"]
    assert _check(free)[1] == []


def test_the_longest_grant_is_a_setting_and_numbers_must_be_sane() -> None:
    protocol = _good()
    protocol["duration_minutes"] = 31
    assert _check(protocol, max_minutes=30)[1][0]["message"].startswith("duration_minutes must be from 1 to 30")
    protocol["duration_minutes"] = 30
    assert _check(protocol, max_minutes=30)[1] == []
    _aom(protocol)["set_frequency"]["args"]["value"] = {"min": -1e16, "max": 90}
    assert "no larger than" in _check(protocol)[1][0]["message"]


def test_problems_are_capped() -> None:
    protocol = _good()
    protocol.update({f"junk{i}": i for i in range(150)})
    problems = _check(protocol)[1]
    assert len(problems) == 101 and problems[-1]["message"].startswith("More problems")


def test_the_review_is_built_from_the_normalized_copy_and_the_contracts() -> None:
    normalized, _ = _check(_good())
    review = build_review(normalized, LIVE)
    assert review["title"] == GOOD["title"] and review["duration_minutes"] == 20.0
    device = review["devices"][0]
    assert (device["device_id"], device["type_label"], device["state"]) == ("dev01-aom", "Dummy AOM", "available")
    frequency = next(item for item in device["methods"] if item["name"] == "set_frequency")
    assert frequency["kind"] == "write" and frequency["unit"] == "MHz" and frequency["max_calls"] == 200
    assert frequency["args"] == [{"name": "value", "type": "float", "optional": False, "always_sent": True,
                                  "min": 70.0, "max": 90.0, "max_step": 2.0, "unit": "MHz",
                                  "step_from": "the device's reading before the first change",
                                  "driver_limit": [60.0, 100.0]}]
    assert review["safe_state"][0]["text"] == "dev01-aom.set_rf_enabled(enabled=false)"
    assert not any("max_step" in warning for warning in review["warnings"])

    bare = _good()
    del bare["safe_state"]
    del _aom(bare)["set_frequency"]["args"]["value"]["max_step"]
    warnings = build_review(_check(bare)[0], None)["warnings"]
    assert any("No safe state" in warning for warning in warnings)
    assert any("no max_step" in warning for warning in warnings)
    assert build_review(_check(bare)[0], None)["devices"][0]["state"] == "not on the network"


# ---- the security review's findings ----------------------------------------------------------------
# Each test asserts the safe behaviour; each failed before the fix.

MORE_LIVE = {
    **LIVE,
    "dev05-sacher": {"id": "dev05-sacher", "type": "sacher_laser", "state": "available", "hints": {}},
    "dev06-dummy": {"id": "dev06-dummy", "type": "dummy_device", "state": "available", "hints": {}},
    "dev07-wavemeter": {"id": "dev07-wavemeter", "type": "simulated_wavemeter", "state": "available", "hints": {}},
}
MORE_CANVAS = CANVAS + [{"device_id": device_id, "device_type": MORE_LIVE[device_id]["type"]}
                        for device_id in ("dev05-sacher", "dev06-dummy", "dev07-wavemeter")]
MORE_PROJECT = {"allowed_devices": ["dev01-aom", "dev02-awg", "dev05-sacher", "dev06-dummy", "dev07-wavemeter"]}
AWG_CH2 = _awg({
    "set_amplitude": {"args": {"value": {"min": 0, "max": 1, "max_step": 0.1}, "channel": {"choices": ["CH2"]}},
                      "max_calls": 100, "max_rate_hz": 5},
    "set_output_enabled": {"args": {"enabled": {"choices": [True, False]}, "channel": {"choices": ["CH2"]}},
                           "max_calls": 10, "max_rate_hz": 1},
})


def _more(devices: list[dict], safe_state: list[dict] | None = None) -> dict[str, Any]:
    protocol = {key: GOOD[key] for key in ("schema_version", "task", "title", "duration_minutes")}
    return {**protocol, "devices": copy.deepcopy(devices), "safe_state": copy.deepcopy(safe_state or [])}


def _check_more(protocol: Any) -> tuple[dict | None, list[dict[str, str]]]:
    return _check(protocol, project=MORE_PROJECT, live_devices=MORE_LIVE, canvas_devices=MORE_CANVAS)


def test_a_bounded_optional_argument_is_always_sent_the_safe_state_included() -> None:
    # Approved: CH2 only. A safe state leaving channel out would switch off the driver's default (CH1).
    protocol = _more([AWG_CH2], [{"device_id": "dev02-awg", "method": "set_output_enabled", "args": {"enabled": False}}])
    normalized, problems = _check_more(protocol)
    assert normalized is None and len(problems) == 1
    assert problems[0]["path"] == "safe_state[0].args.channel"
    assert "every call sends it" in problems[0]["message"] and "driver's default" in problems[0]["message"]

    protocol["safe_state"][0]["args"]["channel"] = "CH2"
    normalized, problems = _check_more(protocol)
    assert problems == []
    review = build_review(normalized, MORE_LIVE)
    amplitude = next(item for item in review["devices"][0]["methods"] if item["name"] == "set_amplitude")
    assert [arg["name"] for arg in amplitude["args"]] == ["value", "channel"]
    assert all(arg["always_sent"] for arg in amplitude["args"]) and amplitude["never_sent"] == []
    assert review["safe_state"][0]["text"] == 'dev02-awg.set_output_enabled(enabled=false, channel="CH2")'

    # The gateway refuses a call that leaves the bounded channel out (the driver would use CH1).
    known = GrantCallState(last_values={"dev02-awg.set_amplitude.value|channel=CH2": (0.5, 0.5),
                                        "dev02-awg.set_amplitude.value": (0.5, 0.5)})
    left_out = {"device_id": "dev02-awg", "method": "set_amplitude", "args": {"value": 0.5}}
    assert "every set_amplitude call must send it" in check_call(normalized, known, left_out, now=0.0,
                                                                   seconds_left=100.0)
    sent = {"device_id": "dev02-awg", "method": "set_amplitude", "args": {"value": 0.5, "channel": "CH2"}}
    assert check_call(normalized, known, sent, now=0.0, seconds_left=100.0) is None
    # Reads and streams too: GOOD bounds the stream's channel, so it must be sent.
    good, _ = _check(_good())
    stream = {"device_id": "dev01-aom", "method": "stream_diffraction_efficiency", "args": {},
              "sample_rate_hz": 10.0, "sample_count": 5}
    assert "must send it" in check_call(good, GrantCallState(), stream, now=0.0, seconds_left=100.0)
    stream["args"]["channel"] = "CH1"
    assert check_call(good, GrantCallState(), stream, now=0.0, seconds_left=100.0) is None


def test_the_setters_whose_position_the_gateway_can_read() -> None:
    readable = set()
    for device_type, contract in DEVICE_CONTRACT_BY_TYPE.items():
        for spec in contract.methods:
            for item in spec.forwarded_field_specs:
                if item.python_type in ("float", "int") and step_reader(contract, spec.python_name, item.name)[0]:
                    readable.add(f"{device_type}.{spec.python_name}")
    assert readable == {
        "dummy_aom.set_frequency", "dummy_aom.set_rf_power",
        "dummy_awg.set_amplitude", "dummy_awg.set_frequency", "dummy_awg.set_offset",
        "dummy_eom.set_modulation_frequency", "dummy_eom.set_rf_amplitude",
        "dummy_laser.set_current", "dummy_laser.set_wavelength",
    }
    reader, _ = step_reader(DEVICE_CONTRACT_BY_TYPE["dummy_awg"], "set_amplitude", "value")
    assert reader.python_name == "read_amplitude" and [item.name for item in reader.forwarded_field_specs] == ["channel"]


@pytest.mark.parametrize("device_id, method, field", [
    ("dev06-dummy", "write_value", "value"),
    ("dev05-sacher", "set_velocity_nm_s", "velocity_nm_s"),
    ("dev05-sacher", "go_to_wavelength_nm", "wavelength_nm"),
    ("dev05-sacher", "jog_steps", "relative_steps"),
    ("dev07-wavemeter", "set_exposure", "value"),
])
def test_max_step_needs_a_reading_of_where_the_field_is_now(device_id: str, method: str, field: str) -> None:
    device_type = MORE_LIVE[device_id]["type"]
    device = {"device_id": device_id, "device_type": device_type,
              "methods": {method: {"args": {field: {"min": 1, "max": 2, "max_step": 1}}, "max_calls": 5,
                                   "max_rate_hz": 1}}}
    normalized, problems = _check_more(_more([device]))
    assert normalized is None and len(problems) == 1, problems
    assert problems[0]["path"] == f"devices[0].methods.{method}.args.{field}.max_step"
    message = problems[0]["message"]
    assert message.startswith(f"{device_type} can't report where {field} of {method} is now")
    assert "so a step limit can't be enforced; drop max_step (any value in range is then allowed)" in message
    del device["methods"][method]["args"][field]["max_step"]
    assert _check_more(_more([device]))[1] == []


def test_a_whole_number_in_the_safe_state_stays_a_whole_number_and_passes_the_gateway() -> None:
    sacher = {"device_id": "dev05-sacher", "device_type": "sacher_laser", "methods": {
        "jog_steps": {"args": {"relative_steps": {"min": -10, "max": 10}}, "max_calls": 5, "max_rate_hz": 1}}}
    protocol = _more([sacher], [{"device_id": "dev05-sacher", "method": "jog_steps", "args": {"relative_steps": 0}}])
    normalized, problems = _check_more(protocol)
    assert problems == []
    call = normalized["safe_state"][0]
    assert call["args"] == {"relative_steps": 0} and type(call["args"]["relative_steps"]) is int
    assert check_call(normalized, GrantCallState(), call, now=0.0, seconds_left=1e9, steps=False) is None
    # Written as 0.0 it is the same whole number; normalizing the normalized copy changes nothing.
    protocol["safe_state"][0]["args"]["relative_steps"] = 0.0
    assert protocol_sha256(_check_more(protocol)[0]) == protocol_sha256(normalized)
    again, problems = _check_more(json.loads(canonical(normalized)))
    assert problems == [] and protocol_sha256(again) == protocol_sha256(normalized)


def test_stacked_combining_marks_are_refused_in_titles_and_choices() -> None:
    for title in ("A" + "̍" * 119, "A̍̍̍", "ok ⃝⃝⃝"):
        protocol = _good()
        protocol["title"] = title
        normalized, problems = _check(protocol)
        assert normalized is None and problems[0]["path"] == "title"
        assert "combining marks" in problems[0]["message"]
    fine = _good()
    fine["title"] = "Café, Việt (ệ), A̍̍"  # up to two marks on one character
    assert _check(fine)[1] == []
    choice = _good()
    _aom(choice)["stream_diffraction_efficiency"]["args"]["channel"]["choices"] = ["CH1́́́"]
    problems = _check(choice)[1]
    assert problems[0]["path"].endswith("channel.choices[0]") and "combining marks" in problems[0]["message"]


def test_the_review_title_is_contained_by_the_page_css() -> None:
    css = (Path(__file__).resolve().parents[2] / "labnet" / "control_center" / "static" / "css" / "app.css").read_text(
        encoding="utf-8")
    rule = re.search(r"\.review-title\s*\{([^}]*)\}", css).group(1)
    assert "overflow: hidden" in rule and "line-height" in rule


def test_protocol_files_follow_the_task_name() -> None:
    assert [path.as_posix() for path in protocol_files("Tasks/Task1.md")] == [
        "Protocols/Task1.protocol.json", "Protocols/Task1.protocol.md"]
    assert protocol_files("Tasks/sub/AOM scan.md")[0].as_posix() == "Protocols/AOM scan.protocol.json"


# ---- the app: submitting, approving, rejecting -------------------------------------------------------


class GrantRefused(Exception):
    """Shaped like automation_grants.GrantError, without depending on it."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.code, self.detail = status_code, code, detail


class FakeGrants:
    """Stands in for the GrantManager: records create() calls and reports the grant to the run."""

    def __init__(self, runs) -> None:
        self.runs = runs
        self.created: list[dict[str, Any]] = []
        self.ended: list[tuple[str, str]] = []
        self.refuse: Exception | None = None

    async def create(self, **kwargs) -> dict[str, Any]:
        if self.refuse is not None:
            raise self.refuse
        self.created.append(kwargs)
        grant = {"id": "grant-0123456789abcdef", "status": "active", "approved_by": kwargs["approved_by"],
                 "starts_at": "2026-10-07T12:00:00Z", "ends_at": "2026-10-07T12:20:00Z",
                 "devices": [device["device_id"] for device in kwargs["protocol"]["devices"]],
                 "calls": 0, "refused_calls": 0, "ended_reason": None, "ended_at": None}
        self.runs.set_grant(kwargs["project_id"], kwargs["run_id"], grant)
        return grant

    async def end_for_run(self, run_id: str, *, reason: str) -> None:
        self.ended.append((run_id, reason))

    def activity(self) -> dict:
        return {}

    def blocked(self, _device_id: str) -> bool:
        return False

    async def close(self) -> None:
        pass


def _client(tmp_path: Path, library: Path, **settings) -> TestClient:  # noqa: F811
    options = dict(agent_literature_dir=library, automation_max_file_mb=0.2, automation_run_quota_mb=2)
    options.update(settings)
    return TestClient(create_app(_settings(tmp_path, **options), client_factory=lambda _: FakeCentral()))


@pytest.fixture
def desk(tmp_path: Path, library: Path):  # noqa: F811
    """A Control Center with a project whose linked experiment has dev01-aom (allowed), a claimed run,
    and a fake GrantManager."""

    with _client(tmp_path, library) as client:
        _prepare(client)
        yield client


def _prepare(client: TestClient, headers: dict | None = None) -> None:
    headers = headers or {}
    real = client.app.state.grants
    client.app.state.grants = client.fake = FakeGrants(client.app.state.runs)
    client.real_grants = real
    client.tokens = {name: client.app.state.runners.add(name) for name in ("lab-laptop", "central-docker")}
    project = client.post("/api/automation/projects", json={"name": "AOM study"}, headers=headers).json()["project"]
    experiment = client.post("/api/experiments", json=EXPERIMENT, headers=headers).json()["experiment"]
    api = f"/api/automation/projects/{project['id']}"
    project = client.post(f"{api}/experiments", json={"experiment_id": experiment["id"]}, headers=headers).json()["project"]
    project = client.put(api, json={"version": project["version"], "name": project["name"],
                                    "allowed_devices": ["dev01-aom"]}, headers=headers).json()["project"]
    queued = client.post(f"{api}/runs", json={"task": "Tasks/Task1.md"}, headers=headers)
    assert queued.status_code == 201, queued.text
    run = client.post(f"{RUNNER}/claim", headers=_bearer(client.tokens["lab-laptop"])).json()["run"]
    assert run["phase"] == "planning"
    client.project, client.run, client.api = project, run, f"{api}/runs/{run['id']}"
    client.runner = f"{RUNNER}/runs/{run['id']}"
    client.token = _bearer(client.tokens["lab-laptop"])


def _upload(client: TestClient, protocol: Any = None, prose: str | None = "## Why\nTo map efficiency.") -> None:
    data = protocol if isinstance(protocol, bytes) else json.dumps(GOOD if protocol is None else protocol).encode()
    reply = client.put(f"{client.runner}/files/Protocols/Task1.protocol.json", content=data, headers=client.token)
    assert reply.status_code == 200, reply.text
    if prose is not None:
        reply = client.put(f"{client.runner}/files/Protocols/Task1.protocol.md", content=prose.encode(), headers=client.token)
        assert reply.status_code == 200, reply.text


def _submit(client: TestClient):
    return client.post(f"{client.runner}/protocol", json={}, headers=client.token)


def _run(client: TestClient) -> dict:
    return client.get(client.api).json()["run"]


def _sha(client: TestClient) -> str:
    return client.get(f"{client.api}/protocol").json()["sha256"]


def _approve(client: TestClient, sha256: str | None = None, minutes: float = 20, **extra):
    body = {"sha256": sha256 or _sha(client), "duration_minutes": minutes, "approved_by": "Rob", **extra}
    return client.post(f"{client.api}/protocol/approve", json=body, headers=extra.pop("headers", None) or {})


def _reject(client: TestClient, note: str = "Keep the frequency below 85 MHz.", **extra):
    return client.post(f"{client.api}/protocol/reject", json={"note": note, "rejected_by": "Rob", **extra})


def test_submitting_reads_the_uploaded_file_and_waits_for_approval(desk: TestClient, tmp_path: Path) -> None:
    missing = _submit(desk)
    assert missing.status_code == 422
    assert missing.json()["problems"] == [{"path": "", "message": "There is no Protocols/Task1.protocol.json. Write "
                                                                  "it, upload it, then submit again."}]

    bad = _good()
    _aom(bad)["set_frequency"]["args"]["value"]["max"] = 150
    _upload(desk, bad)
    refused = _submit(desk)
    assert refused.status_code == 422 and "1 problem" in refused.json()["detail"]
    assert refused.json()["problems"][0]["path"] == "devices[0].methods.set_frequency.args.value.max"
    run = _run(desk)
    assert run["phase"] == "planning" and run["protocol"] is None  # refusals don't count as submissions
    events = desk.get(desk.api).json()["events"]
    assert events[-1]["kind"] == "protocol" and "refused" in events[-1]["text"]

    _upload(desk, b'{"schema_version": 1, "schema_version": 1}')
    assert "more than once" in _submit(desk).json()["problems"][0]["message"]

    _upload(desk)
    accepted = _submit(desk)
    assert accepted.status_code == 200, accepted.text
    normalized, _ = _check(_good())
    expected = protocol_sha256(normalized)
    assert accepted.json() == {"phase": "awaiting_approval", "sha256": expected, "submission": 1}
    run = _run(desk)
    assert run["phase"] == "awaiting_approval"
    assert run["protocol"]["status"] == "submitted" and run["protocol"]["submissions"] == 1
    assert run["protocol"]["sha256"] == expected and run["protocol"]["title"] == GOOD["title"]
    assert run["protocol"]["duration_minutes"] == 20.0
    stored = tmp_path / "automation" / "state" / desk.project["id"] / "protocols" / f"{desk.run['id']}-1.json"
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == expected
    assert stored.read_text(encoding="utf-8") == canonical(normalized)
    assert stored.with_suffix(".md").read_text(encoding="utf-8") == "## Why\nTo map efficiency."

    # Waiting for approval: no second submission; the runner still sees the run.
    again = _submit(desk)
    assert again.status_code == 409 and again.json()["phase"] == "awaiting_approval"
    seen = desk.get(desk.runner, headers=desk.token).json()["run"]
    assert seen["phase"] == "awaiting_approval" and seen["protocol"]["sha256"] == expected


def test_the_runner_route_is_the_claiming_runners_and_needs_an_empty_body(desk: TestClient) -> None:
    _upload(desk)
    assert desk.post(f"{desk.runner}/protocol", json={}).status_code == 401
    other = desk.post(f"{desk.runner}/protocol", json={}, headers=_bearer(desk.tokens["central-docker"]))
    assert other.status_code == 404
    assert desk.post(f"{desk.runner}/protocol", json={"path": "x"}, headers=desk.token).status_code == 422
    browser = desk.post(f"{desk.runner}/protocol", json={}, headers={**desk.token, "Sec-Fetch-Site": "same-origin"})
    assert browser.status_code == 403
    assert _run(desk)["phase"] == "planning"


def test_a_protocol_is_never_checked_against_a_network_central_cant_confirm(
    desk: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upload(desk)
    status = desk.app.state.status

    async def stale() -> dict:
        return {"central": {"ok": False}, "summary": {}, "lab_pcs": []}

    monkeypatch.setattr(status, "snapshot", stale)
    reply = _submit(desk)
    assert reply.status_code == 503 and "Central" in reply.json()["detail"]
    assert _run(desk)["phase"] == "planning"


def test_at_most_three_submissions_per_run(desk: TestClient) -> None:
    for number in (1, 2, 3):
        _upload(desk)
        reply = _submit(desk)
        assert reply.status_code == 200 and reply.json()["submission"] == number
        assert _reject(desk).status_code == 200
    _upload(desk)
    fourth = _submit(desk)
    assert fourth.status_code == 409 and "already sent 3 protocols" in fourth.json()["detail"]
    assert _run(desk)["phase"] == "planning" and _run(desk)["protocol"]["submissions"] == 3


def test_people_see_the_review_and_the_agents_prose_apart(desk: TestClient) -> None:
    assert desk.get(f"{desk.api}/protocol").status_code == 404
    _upload(desk, prose="<script>alert('x')</script> **trust me**")
    _submit(desk)
    shown = desk.get(f"{desk.api}/protocol")
    assert shown.status_code == 200 and shown.headers["cache-control"] == "no-store"
    body = shown.json()
    assert body["status"] == "submitted" and body["phase"] == "awaiting_approval" and body["submission"] == 1
    assert body["prose"] == "<script>alert('x')</script> **trust me**"  # returned as text; the page sets textContent
    assert body["protocol"] == _check(_good())[0] and body["sha256"] == protocol_sha256(body["protocol"])
    assert body["review"]["devices"][0]["device_id"] == "dev01-aom" and body["max_minutes"] == 120
    assert body["review"] == build_review(body["protocol"], {
        "dev01-aom": {"type": "dummy_aom", "state": "available", "hints": AOM_HINTS}})


def test_a_tampered_stored_protocol_cannot_be_shown_or_approved(desk: TestClient, tmp_path: Path) -> None:
    _upload(desk)
    _submit(desk)
    sha = _sha(desk)
    stored = tmp_path / "automation" / "state" / desk.project["id"] / "protocols" / f"{desk.run['id']}-1.json"
    stored.write_text(stored.read_text(encoding="utf-8").replace('"max":90.0', '"max":100.0'), encoding="utf-8")
    assert desk.get(f"{desk.api}/protocol").status_code == 500
    assert _approve(desk, sha).status_code == 500
    assert desk.fake.created == []


def test_approving_checks_the_sha_the_duration_and_the_approver(desk: TestClient) -> None:
    _upload(desk)
    _submit(desk)
    sha = _sha(desk)
    wrong = _approve(desk, "0" * 64)
    assert wrong.status_code == 409 and wrong.json()["sha256"] == sha
    assert _approve(desk, sha, minutes=21).status_code == 422  # only shorter
    assert "never lengthen" in _approve(desk, sha, minutes=21).json()["detail"]
    for minutes in (0, 0.5, -5, True, "10", None):
        assert _approve(desk, sha, minutes=minutes).status_code == 422, minutes
    assert _approve(desk, "not-a-sha").status_code == 422
    nobody = desk.post(f"{desk.api}/protocol/approve", json={"sha256": sha, "duration_minutes": 10})
    assert nobody.status_code == 422 and "your name" in nobody.json()["detail"]
    assert desk.post(f"{desk.api}/protocol/approve", json={"sha256": sha, "duration_minutes": 10},
                     headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert desk.post(f"{desk.api}/protocol/approve", content=json.dumps({"sha256": sha, "duration_minutes": 10}),
                     headers={"content-type": "text/plain"}).status_code == 415
    assert desk.fake.created == [] and _run(desk)["phase"] == "awaiting_approval"

    approved = _approve(desk, sha, minutes=12.5)
    assert approved.status_code == 200, approved.text
    run = approved.json()["run"]
    assert run["phase"] == "executing" and run["status"] == "active"
    assert run["protocol"]["status"] == "approved" and run["protocol"]["reviewed_by"] == "Rob"
    assert run["protocol"]["approved_minutes"] == 12.5 and run["protocol"]["reviewed_at"]
    assert run["grant"]["id"] == "grant-0123456789abcdef" and run["grant"]["status"] == "active"
    [created] = desk.fake.created
    assert created == {"project_id": desk.project["id"], "run_id": desk.run["id"], "protocol": _check(_good())[0],
                       "sha256": sha, "duration_minutes": 12.5, "approved_by": "Rob"}
    # Done once: approving again, or rejecting now, is refused.
    assert _approve(desk, sha).status_code == 409
    assert _reject(desk).status_code == 409
    assert len(desk.fake.created) == 1
    events = desk.get(desk.api).json()["events"]
    assert any(event["kind"] == "approval" and "approved by Rob for 12.5 minutes" in event["text"] for event in events)


def test_a_huge_whole_number_duration_is_refused_not_a_server_error(desk: TestClient) -> None:
    _upload(desk)
    _submit(desk)
    sha = _sha(desk)
    for minutes in ("9" * 400, "-" + "9" * 400):
        body = '{"sha256": "%s", "duration_minutes": %s, "approved_by": "Rob"}' % (sha, minutes)
        reply = desk.post(f"{desk.api}/protocol/approve", content=body, headers={"content-type": "application/json"})
        assert reply.status_code == 422 and "duration_minutes" in reply.json()["detail"]
    assert desk.fake.created == [] and _run(desk)["phase"] == "awaiting_approval"


def test_approval_rechecks_the_protocol_against_the_project_and_network_now(desk: TestClient) -> None:
    _upload(desk)
    _submit(desk)
    sha = _sha(desk)
    project = desk.get(f"/api/automation/projects/{desk.project['id']}").json()["project"]
    desk.put(f"/api/automation/projects/{desk.project['id']}",
             json={"version": project["version"], "name": project["name"], "allowed_devices": []})
    changed = _approve(desk, sha)
    assert changed.status_code == 409 and "changed since" in changed.json()["detail"]
    assert any("isn't allowed for automation" in problem["message"] for problem in changed.json()["problems"])
    assert desk.fake.created == [] and _run(desk)["phase"] == "awaiting_approval"


def test_approval_needs_a_network_central_confirms(desk: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _upload(desk)
    _submit(desk)
    sha = _sha(desk)

    async def down() -> dict:
        raise ConnectionError("Central is down")

    monkeypatch.setattr(desk.app.state.status, "snapshot", down)
    assert _approve(desk, sha).status_code == 503
    assert desk.fake.created == [] and _run(desk)["phase"] == "awaiting_approval"


def test_a_busy_device_leaves_the_run_waiting_for_approval(desk: TestClient) -> None:
    _upload(desk)
    _submit(desk)
    sha = _sha(desk)
    desk.fake.refuse = GrantRefused(409, "conflict", "dev01-aom is held by another Automation grant.")
    busy = _approve(desk, sha)
    assert busy.status_code == 409 and busy.json() == {"detail": "dev01-aom is held by another Automation grant.",
                                                       "code": "conflict"}
    run = _run(desk)
    assert run["phase"] == "awaiting_approval" and run["protocol"]["status"] == "submitted" and run["grant"] is None

    grants = pytest.importorskip("labnet.control_center.automation_grants")
    desk.fake.refuse = grants.GrantError(409, "conflict", "The devices could not be leased: in use")
    real = _approve(desk, sha)
    assert real.status_code == 409 and real.json()["code"] == "conflict"
    assert _run(desk)["phase"] == "awaiting_approval"

    desk.fake.refuse = None
    assert _approve(desk, sha).status_code == 200


def test_the_real_grant_manager_refusing_to_lease_keeps_the_run_waiting(desk: TestClient) -> None:
    """FakeCentral can't lease anything, so the real GrantManager refuses; nothing changes."""

    pytest.importorskip("labnet.control_center.automation_grants")
    desk.app.state.grants = desk.real_grants
    _upload(desk)
    _submit(desk)
    refused = _approve(desk, _sha(desk))
    assert 400 <= refused.status_code < 500 and refused.json()["detail"]
    assert _run(desk)["phase"] == "awaiting_approval"
    desk.app.state.grants = desk.fake


def test_a_run_that_ends_while_its_grant_is_made_has_the_grant_ended(desk: TestClient) -> None:
    _upload(desk)
    _submit(desk)
    sha = _sha(desk)
    runs = desk.app.state.runs
    original = desk.fake.create

    async def cancelled_meanwhile(**kwargs):
        grant = await original(**kwargs)
        runs.cancel(kwargs["project_id"], kwargs["run_id"], author="Ann")
        return grant

    desk.fake.create = cancelled_meanwhile
    late = _approve(desk, sha)
    assert late.status_code == 409 and "too late" in late.json()["detail"]
    assert (desk.run["id"], "the run ended while it was being approved") in desk.fake.ended
    assert _run(desk)["status"] == "cancelled"


def test_with_logins_the_approver_is_the_signed_in_person(tmp_path: Path, library: Path) -> None:  # noqa: F811
    signed_in = {"X-LabNet-Proxy": SECRET, "X-LabNet-User": "alice"}
    with _client(tmp_path, library, proxy_secret=SECRET) as client:
        _prepare(client, signed_in)
        _upload(client)
        _submit(client)
        sha = client.get(f"{client.api}/protocol", headers=signed_in).json()["sha256"]
        reply = client.post(f"{client.api}/protocol/approve", headers=signed_in,
                            json={"sha256": sha, "duration_minutes": 5, "approved_by": "mallory"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["run"]["protocol"]["reviewed_by"] == "alice"
        assert client.fake.created[0]["approved_by"] == "alice"


def test_rejecting_sends_the_note_back_to_the_runner(desk: TestClient) -> None:
    assert _reject(desk).status_code == 409  # nothing waiting yet
    _upload(desk)
    _submit(desk)
    assert _reject(desk, note="  ").status_code == 422
    assert _reject(desk, note="x" * 2001).status_code == 422
    assert _reject(desk, sha256="0" * 64).status_code == 409
    rejected = _reject(desk, note="Keep the frequency below 85 MHz.\r\nAnd add a safe state for the stream.")
    assert rejected.status_code == 200
    run = rejected.json()["run"]
    assert run["phase"] == "planning" and run["protocol"]["status"] == "rejected"
    assert run["protocol"]["reviewed_by"] == "Rob"
    seen = desk.get(desk.runner, headers=desk.token).json()["run"]
    assert seen["phase"] == "planning"
    assert seen["protocol"]["note"] == "Keep the frequency below 85 MHz.\nAnd add a safe state for the stream."
    assert _approve(desk, run["protocol"]["sha256"]).status_code == 409  # a rejected protocol can't be approved

    _upload(desk)
    second = _submit(desk)
    assert second.status_code == 200 and second.json()["submission"] == 2
    assert _run(desk)["protocol"]["note"] is None and _run(desk)["protocol"]["status"] == "submitted"


# ---- timeouts, the end of a run, ended_unsafe -----------------------------------------------------------


def _later(client: TestClient, monkeypatch: pytest.MonkeyPatch, minutes: float) -> None:
    monkeypatch.setattr(client.app.state.runs, "_now", lambda: datetime.now(timezone.utc) + timedelta(minutes=minutes))


def test_a_protocol_waiting_too_long_for_approval_fails_the_run(desk: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ended = []
    runs = desk.app.state.runs
    runs.on_run_ended = lambda *args: ended.append(args)
    runs.approval_hours = 0.1  # six minutes, inside the runner's ten-minute heartbeat window
    _upload(desk)
    _submit(desk)
    _later(desk, monkeypatch, 5)
    assert runs.sweep() == []
    _later(desk, monkeypatch, 7)
    [failed] = runs.sweep()
    assert failed["status"] == "failed" and failed["summary"] == "Nobody approved or rejected the protocol within 0.1 hours."
    assert ended == [(desk.project["id"], desk.run["id"], "failed")]
    assert desk.get(desk.api).json()["events"][-1]["kind"] == "approval"
    assert _approve(desk, failed["protocol"]["sha256"]).status_code == 409
    assert desk.post(f"{desk.runner}/heartbeat", headers=desk.token).json()["status"] == "failed"


def test_approval_waits_do_not_count_once_approved(desk: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    runs = desk.app.state.runs
    runs.approval_hours = 0.1
    _upload(desk)
    _submit(desk)
    assert _approve(desk).status_code == 200
    _later(desk, monkeypatch, 7)
    assert runs.sweep() == []  # executing: only the heartbeat rule applies


def test_the_store_tells_the_grant_manager_when_a_run_ends(desk: TestClient, tmp_path: Path, library: Path,  # noqa: F811
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    ended = []
    runs = desk.app.state.runs
    runs.on_run_ended = lambda *args: ended.append(args)
    desk.post(f"{desk.runner}/finish", json={"status": "done", "summary": "ok"}, headers=desk.token)
    assert ended == [(desk.project["id"], desk.run["id"], "done")]

    # A cancel of an active run, and a runner gone quiet.
    for how in ("cancel", "quiet"):
        ended.clear()
        desk.app.state.grants = desk.fake
        project = desk.project
        desk.put(f"/api/automation/projects/{project['id']}/files/Tasks/{how}.md", content=b"# t")
        queued = desk.post(f"/api/automation/projects/{project['id']}/runs", json={"task": f"Tasks/{how}.md"}).json()["run"]
        desk.post(f"{RUNNER}/claim", headers=desk.token)
        if how == "cancel":
            desk.post(f"/api/automation/projects/{project['id']}/runs/{queued['id']}/cancel", json={})
            assert ended == [(project["id"], queued["id"], "cancelled")]
        else:
            _later(desk, monkeypatch, 11)
            runs.sweep()
            monkeypatch.undo()
            assert ended == [(project["id"], queued["id"], "failed")]

    # A hook that raises doesn't stop the run from ending.
    def broken(*_args):
        raise RuntimeError("boom")

    runs.on_run_ended = broken
    desk.put(f"/api/automation/projects/{desk.project['id']}/files/Tasks/last.md", content=b"# t")
    queued = desk.post(f"/api/automation/projects/{desk.project['id']}/runs", json={"task": "Tasks/last.md"}).json()["run"]
    assert desk.post(f"/api/automation/projects/{desk.project['id']}/runs/{queued['id']}/cancel", json={}).status_code == 200


UNSAFE = {"id": "grant-0123456789abcdef", "status": "ended_unsafe", "approved_by": "Rob",
          "starts_at": "2026-10-07T12:00:00Z", "ends_at": "2026-10-07T12:20:00Z", "devices": ["dev01-aom"],
          "calls": 12, "refused_calls": 1, "ended_reason": "expired", "ended_at": "2026-10-07T12:20:03Z"}


def test_a_run_whose_safe_state_failed_ends_unsafe(desk: TestClient) -> None:
    _upload(desk)
    _submit(desk)
    _approve(desk)
    runs = desk.app.state.runs
    run = runs.set_grant(desk.project["id"], desk.run["id"], UNSAFE)
    assert run["status"] == "active" and run["grant"]["status"] == "ended_unsafe"
    run = runs.set_phase(desk.project["id"], desk.run["id"], "analyzing")
    assert run["phase"] == "analyzing"
    finished = desk.post(f"{desk.runner}/finish", json={"status": "done", "summary": "Fit done."}, headers=desk.token)
    assert finished.status_code == 200
    run = finished.json()["run"]
    assert run["status"] == "ended_unsafe" and run["summary"] == "Fit done."
    assert any(event["kind"] == "grant" and "UNSAFE" in event["text"] for event in desk.get(desk.api).json()["events"])


@pytest.mark.parametrize("ending", ["done", "failed", "cancelled"])
def test_a_safe_state_failing_after_the_run_ended_still_marks_it_unsafe(desk: TestClient, ending: str) -> None:
    _upload(desk)
    _submit(desk)
    _approve(desk)
    if ending == "cancelled":
        desk.post(f"{desk.api}/cancel", json={})
    else:
        desk.post(f"{desk.runner}/finish", json={"status": ending}, headers=desk.token)
    run = desk.app.state.runs.set_grant(desk.project["id"], desk.run["id"], UNSAFE)
    assert run["status"] == "ended_unsafe"
    assert _run(desk)["status"] == "ended_unsafe"


def test_the_grant_record_and_phase_moves_are_checked(desk: TestClient) -> None:
    runs = desk.app.state.runs
    project_id, run_id = desk.project["id"], desk.run["id"]
    with pytest.raises(ValueError, match="grant record"):
        runs.set_grant(project_id, run_id, {**UNSAFE, "token": "lng_secret"})
    with pytest.raises(ValueError, match="grant record"):
        runs.set_grant(project_id, run_id, {**UNSAFE, "status": "paused"})
    with pytest.raises(ValueError, match="grant record"):
        runs.set_grant(project_id, run_id, {**UNSAFE, "calls": 1.5})
    with pytest.raises(ValueError, match="No such phase"):
        runs.set_phase(project_id, run_id, "dancing")
    with pytest.raises(AutomationError) as refused:
        runs.set_phase(project_id, run_id, "executing")  # planning can't jump to executing
    assert refused.value.status_code == 409
    assert runs.set_phase(project_id, run_id, "planning")["phase"] == "planning"  # staying put is fine
    desk.post(f"{desk.api}/cancel", json={})
    assert runs.set_phase(project_id, run_id, "analyzing")["phase"] == "planning"  # an ended run is left alone


def test_old_run_records_without_phases_still_load(desk: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "automation" / "state" / desk.project["id"] / "runs" / f"{desk.run['id']}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    for key in ("phase", "protocol", "grant"):
        record.pop(key, None)
    path.write_text(json.dumps(record), encoding="utf-8")
    run = _run(desk)
    assert run["phase"] is None and run["protocol"] is None and run["grant"] is None
    assert _submit(desk).status_code == 409  # no phase: not planning


# ---- the settings ---------------------------------------------------------------------------------------


def test_the_grant_and_approval_limits_are_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LABNET_API_KEY", "key")
    monkeypatch.setenv("LABNET_AUTOMATION_DIR", str(tmp_path / "automation"))
    monkeypatch.setenv("LABNET_CONTROL_CENTER_EXPERIMENTS_DIR", str(tmp_path / "experiments"))
    monkeypatch.setenv("LABNET_AGENT_WORKSPACE", str(tmp_path / "agent"))
    for name in ("LABNET_AUTOMATION_GRANT_MAX_MINUTES", "LABNET_AUTOMATION_APPROVAL_HOURS"):
        monkeypatch.delenv(name, raising=False)
    settings = ControlCenterSettings.from_env(search_cwd=False)
    assert (settings.automation_grant_max_minutes, settings.automation_approval_hours) == (120, 24)
    monkeypatch.setenv("LABNET_AUTOMATION_GRANT_MAX_MINUTES", "30")
    monkeypatch.setenv("LABNET_AUTOMATION_APPROVAL_HOURS", "2.5")
    settings = ControlCenterSettings.from_env(search_cwd=False)
    assert (settings.automation_grant_max_minutes, settings.automation_approval_hours) == (30, 2.5)
    for name, bad in (("LABNET_AUTOMATION_GRANT_MAX_MINUTES", "0.5"), ("LABNET_AUTOMATION_GRANT_MAX_MINUTES", "nan"),
                      ("LABNET_AUTOMATION_GRANT_MAX_MINUTES", "2000"), ("LABNET_AUTOMATION_APPROVAL_HOURS", "0"),
                      ("LABNET_AUTOMATION_APPROVAL_HOURS", "inf"), ("LABNET_AUTOMATION_APPROVAL_HOURS", "-1")):
        monkeypatch.setenv(name, bad)
        with pytest.raises(ValueError, match=name):
            ControlCenterSettings.from_env(search_cwd=False)
        monkeypatch.setenv(name, "10")


def test_the_app_passes_the_settings_to_the_run_store(tmp_path: Path, library: Path) -> None:  # noqa: F811
    with _client(tmp_path, library, automation_grant_max_minutes=30, automation_approval_hours=2) as client:
        runs = client.app.state.runs
        assert (runs.grant_max_minutes, runs.approval_hours) == (30, 2)
        _prepare(client)
        _upload(client)  # asks for 20 minutes: fine under 30
        assert _submit(client).status_code == 200
