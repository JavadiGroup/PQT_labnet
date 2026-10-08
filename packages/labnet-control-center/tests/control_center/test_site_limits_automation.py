"""No limit, no automation: protocols may bound only numbers a device publishes a limit for, and the
review shows where each limit comes from (the Lab PC's site limits or the driver)."""

from __future__ import annotations

import copy

from labnet_control_center.automation_protocol import build_review
from labnet_control_center.contracts import clean_hints

from .test_automation_protocol import LIVE, _aom, _awg, _check, _good


AOM_SITE_HINTS = {"limits": {"set_frequency.value": [65.0, 95.0], "set_rf_power.value": [0.0, 2.0]},
                  "choices": {"set_rf_enabled.enabled": [False]},
                  "sources": {"set_frequency.value": "site", "set_rf_power.value": "driver",
                              "set_rf_enabled.enabled": "site"}}


def _live(device_id: str, hints: dict) -> dict:
    return {**LIVE, device_id: {**LIVE[device_id], "hints": hints}}


def test_a_number_without_a_published_limit_is_a_problem_not_a_warning() -> None:
    no_limits = _live("dev01-aom", {})
    normalized, problems = _check(_good(), live_devices=no_limits)
    assert normalized is None
    assert problems == [{
        "path": "devices[0].methods.set_frequency.args.value",
        "message": "dev01-aom publishes no limit for set_frequency.value; set one in the Lab PC inventory with "
                   "site_limits(...) before automating it.",
    }]
    # Every number counts, a read's or a command's too; text and true/false keep their own rules.
    laser = {"device_id": "dev03-laser", "device_type": "dummy_laser", "methods": {
        "set_current": {"args": {"value": {"min": 0, "max": 50}}, "max_calls": 5, "max_rate_hz": 1},
        "set_emission_enabled": {"args": {"enabled": {"choices": [False]}}, "max_calls": 5, "max_rate_hz": 1}}}
    protocol = _good()
    protocol["devices"].append(laser)
    problems = _check(protocol, project={"allowed_devices": ["dev01-aom", "dev03-laser"]})[1]
    assert [problem["path"] for problem in problems] == ["devices[1].methods.set_current.args.value"]
    assert "dev03-laser publishes no limit for set_current.value" in problems[0]["message"]


def test_site_limits_are_hard_limits_and_named_as_such() -> None:
    live = _live("dev01-aom", AOM_SITE_HINTS)
    within = _good()
    _aom(within)["set_frequency"]["args"]["value"] = {"min": 70, "max": 90, "max_step": 2}
    _aom(within)["set_rf_enabled"]["args"]["enabled"]["choices"] = [False]
    normalized, problems = _check(within, live_devices=live)
    assert problems == []

    wide = _good()
    _aom(wide)["set_frequency"]["args"]["value"] = {"min": 60, "max": 100}
    problems = _check(wide, live_devices=live)[1]
    assert {"path": "devices[0].methods.set_frequency.args.value.min",
            "message": "min (60) is below the site limit for set_frequency.value (65 to 95 MHz)."} in problems
    assert {"path": "devices[0].methods.set_frequency.args.value.max",
            "message": "max (100) is above the site limit for set_frequency.value (65 to 95 MHz)."} in problems
    # A site that allows only switching the RF off refuses a protocol that may switch it on.
    assert {"path": "devices[0].methods.set_rf_enabled.args.enabled.choices[0]",
            "message": "true isn't one of the site choices for set_rf_enabled.enabled: false."} in problems


def test_a_channel_must_be_among_the_published_channels_and_text_still_needs_choices() -> None:
    awg = {"set_waveform": {"args": {"waveform": {"choices": ["sine"]}, "channel": {"choices": ["CH9"]}},
                            "max_calls": 5, "max_rate_hz": 1}}
    protocol = _good()
    protocol["devices"].append(_awg(awg))
    problems = _check(protocol)[1]
    assert {"path": "devices[1].methods.set_waveform.args.channel.choices[0]",
            "message": "'CH9' isn't one of this device's channels: CH1, CH2."} in problems
    del awg["set_waveform"]["args"]["waveform"]
    protocol = _good()
    protocol["devices"].append(_awg(awg))
    problems = _check(protocol)[1]
    assert any(problem["path"] == "devices[1].methods.set_waveform.args.waveform"
               and "needs bounds" in problem["message"] for problem in problems)


def test_the_review_shows_where_each_limit_comes_from() -> None:
    live = _live("dev01-aom", AOM_SITE_HINTS)
    protocol = _good()
    _aom(protocol)["set_rf_enabled"]["args"]["enabled"]["choices"] = [False]
    _aom(protocol)["set_rf_power"] = {"args": {"value": {"min": 0, "max": 1}}, "max_calls": 5, "max_rate_hz": 1}
    normalized, problems = _check(protocol, live_devices=live)
    assert problems == []
    review = build_review(normalized, live)
    methods = {item["name"]: item for item in review["devices"][0]["methods"]}
    frequency = methods["set_frequency"]["args"][0]
    assert (frequency["limit"], frequency["limit_source"]) == ([65.0, 95.0], "site")
    assert frequency["site_limit"] == [65.0, 95.0] and frequency["driver_limit"] is None
    power = methods["set_rf_power"]["args"][0]
    assert (power["limit"], power["limit_source"], power["driver_limit"]) == ([0.0, 2.0], "driver", [0.0, 2.0])
    enabled = methods["set_rf_enabled"]["args"][0]
    assert (enabled["offered"], enabled["limit_source"]) == ([False], "site")
    stream = methods["stream_diffraction_efficiency"]["args"][0]
    assert "offered" not in stream  # the AOM publishes no channels
    assert not any("publishes no limit" in warning for warning in review["warnings"])

    # Offline at review time: no limit to show, and approval checks it again.
    offline = build_review(normalized, {})
    assert any("publishes no limit for set_frequency.value now" in warning for warning in offline["warnings"])


def test_clean_hints_keeps_a_valid_sources_map_and_true_false_choices() -> None:
    raw = copy.deepcopy(AOM_SITE_HINTS)
    raw["sources"].update({"ghost.value": "site", "set_rf_power.value": "somewhere"})
    raw["choices"]["set_mode.mode"] = [True, "x"]
    cleaned = clean_hints({"hints": raw})
    assert cleaned["sources"] == {"set_frequency.value": "site", "set_rf_enabled.enabled": "site"}
    assert cleaned["choices"] == {"set_rf_enabled.enabled": [False], "set_mode.mode": ["x"]}
    assert clean_hints({"hints": {"limits": {"a.b": [0, 1]}, "sources": "site"}}) == {"limits": {"a.b": [0.0, 1.0]}}
    # Hints without sources (an older Lab PC) are kept as they were.
    assert clean_hints({"hints": {"limits": {"a.b": [0, 1]}}}) == {"limits": {"a.b": [0.0, 1.0]}}
