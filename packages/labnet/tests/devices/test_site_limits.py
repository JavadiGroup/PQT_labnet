"""Site limits: the inventory's API, its checks, and the effective limits a Lab PC enforces and publishes."""

from __future__ import annotations

import math

import pytest

from labnet_control_center.contracts import clean_hints, hint_lines
from labnet.devices.core import DeviceLimitsError, SiteLimits, get_site_limits, site_limits
from labnet.devices.core.limits import resolve_limits
from labnet_sim.dummy_aom.physical import PhysicalDummyAOM
from labnet_sim.dummy_awg.physical import PhysicalDummyAWG
from labnet_sim.dummy_device.physical import PhysicalDummyDevice
from labnet_sim.simulated_tunable_laser.physical import PhysicalSimulatedTunableLaser
from labnet.contracts import registry


def _effective(device, hints=None):
    contract = registry.get(device.device_type)
    driver = device.metadata().get("hints") if hints is None else hints
    return resolve_limits(contract, driver, get_site_limits(device), device_id=device.device_id)


# ---- the inventory's API ----------------------------------------------------------------------


def test_site_limits_returns_the_same_device_with_its_limits() -> None:
    aom = PhysicalDummyAOM("aom")
    limited = site_limits(aom, limits={"set_frequency.value": (65, 95)})
    assert limited is aom and type(limited) is PhysicalDummyAOM
    assert limited.device_type == "dummy_aom" and "set_frequency" in limited.allowed_methods()
    assert get_site_limits(aom) == SiteLimits(limits={"set_frequency.value": (65.0, 95.0)}, choices={})
    assert get_site_limits(PhysicalDummyAOM("plain")) is None


def test_the_lead_example_for_a_tunable_laser_is_accepted() -> None:
    laser = site_limits(
        PhysicalSimulatedTunableLaser(device_id="lab01-laser"),
        limits={"go_to_wavelength_nm.wavelength_nm": (770.0, 790.0)},
        choices={"move_steps.velocity_mode": ["slow"]},
    )
    assert get_site_limits(laser).choices == {"move_steps.velocity_mode": ("slow",)}


@pytest.mark.parametrize("kwargs, message", [
    ({"limits": {"go_to.wavelength_nm": (1, 2)}}, "simulated_tunable_laser has no method 'go_to'"),
    ({"limits": {"go_to_wavelength_nm.wl": (1, 2)}},
     "go_to_wavelength_nm has no argument 'wl' (its arguments: wavelength_nm)"),
    ({"limits": {"wavelength_nm": (1, 2)}}, "must be '<method>.<argument>'"),
    ({"limits": {"go_to_wavelength_nm.wavelength_nm": (790, 770)}}, "is inverted: min 790 is above max 770"),
    ({"limits": {"go_to_wavelength_nm.wavelength_nm": (770, math.inf)}}, "inf is not finite"),
    ({"limits": {"go_to_wavelength_nm.wavelength_nm": (math.nan, 790)}}, "nan is not finite"),
    ({"limits": {"go_to_wavelength_nm.wavelength_nm": (True, 790)}}, "True is not a number"),
    ({"limits": {"go_to_wavelength_nm.wavelength_nm": (770,)}}, "must be (min, max)"),
    ({"limits": {"move_steps.velocity_mode": (1, 2)}}, "is str; give it as choices instead"),
    ({"choices": {"go_to_wavelength_nm.wavelength_nm": ["770"]}}, "is float; give it as a (min, max) limit"),
    ({"choices": {"move_steps.velocity_mode": []}}, "must be a non-empty list"),
    ({"choices": {"move_steps.velocity_mode": "slow"}}, "must be a non-empty list"),
    ({"choices": {"move_steps.velocity_mode": ["slow", "slow"]}}, "'slow' is listed twice"),
    ({"choices": {"move_steps.velocity_mode": [1]}}, "1 is not non-empty text"),
    ({}, "needs limits or choices"),
])
def test_site_limits_refuses_what_cant_be_a_limit(kwargs, message: str) -> None:
    with pytest.raises(DeviceLimitsError) as caught:
        site_limits(PhysicalSimulatedTunableLaser(device_id="lab01-laser"), **kwargs)
    assert message in str(caught.value) and str(caught.value).startswith("lab01-laser: ")


def test_bool_choices_and_conflicting_keys() -> None:
    aom = site_limits(PhysicalDummyAOM("aom"), choices={"set_rf_enabled.enabled": [False]})
    assert get_site_limits(aom).choices == {"set_rf_enabled.enabled": (False,)}
    with pytest.raises(DeviceLimitsError, match="0 is not True or False"):
        site_limits(PhysicalDummyAOM("aom"), choices={"set_rf_enabled.enabled": [0]})
    with pytest.raises(DeviceLimitsError, match="already applied"):
        site_limits(aom, limits={"set_frequency.value": (65, 95)})
    # Two RPCs of one physical method (read_value, stream_data) must leave a value in common.
    with pytest.raises(DeviceLimitsError, match="have no value in common"):
        site_limits(PhysicalDummyDevice("dd"), choices={"read_value.channel": ["a"], "stream_data.channel": ["b"]})


def test_a_device_without_a_contract_cant_have_site_limits() -> None:
    class Legacy:
        device_type = "legacy_thing"
        device_id = "legacy"

    with pytest.raises(DeviceLimitsError, match="has no generated contract"):
        site_limits(Legacy(), limits={"set.value": (0, 1)})


# ---- the effective limits ------------------------------------------------------------------


def test_effective_hints_narrow_the_driver_and_say_where_each_limit_comes_from() -> None:
    aom = site_limits(PhysicalDummyAOM("aom"), limits={"set_frequency.value": (65, 95)},
                      choices={"set_rf_enabled.enabled": [False]})
    hints = _effective(aom).hints
    assert hints == {
        "limits": {"set_frequency.value": [65.0, 95.0], "set_rf_power.value": [0.0, 2.0]},
        "choices": {"set_rf_enabled.enabled": [False]},
        "sources": {"set_frequency.value": "site", "set_rf_power.value": "driver", "set_rf_enabled.enabled": "site"},
    }
    # The Control Center keeps all of it, the source map included.
    assert clean_hints({"hints": hints}) == hints
    assert "- Range of set_frequency.value: 65 to 95 (site limit)" in hint_lines(clean_hints({"hints": hints}))
    assert "- Choices for set_rf_enabled.enabled: false (site limit)" in hint_lines(clean_hints({"hints": hints}))


def test_a_driver_without_limits_gets_them_only_from_the_site_and_siblings_share_them() -> None:
    assert _effective(PhysicalDummyDevice("plain")).hints == {}
    dummy = site_limits(PhysicalDummyDevice("dd"), limits={"write_value.value": (-10, 10)},
                        choices={"read_value.channel": ["a", "b"]})
    hints = _effective(dummy).hints
    # stream_data reads with read_value, so its channel is limited (and published) too.
    assert hints["choices"] == {"read_value.channel": ["a", "b"], "stream_data.channel": ["a", "b"]}
    assert hints["limits"] == {"write_value.value": [-10.0, 10.0]}
    assert set(hints["sources"].values()) == {"site"}


def test_channels_stay_published_as_channels_and_narrowing_them_is_a_site_choice() -> None:
    awg = PhysicalDummyAWG("awg")
    hints = _effective(awg).hints
    assert hints["channels"] == ["CH1", "CH2"]
    assert not any(key.endswith(".channel") for key in hints.get("choices", {}))
    narrowed = site_limits(PhysicalDummyAWG("awg2"), choices={"set_amplitude.channel": ["CH2"]})
    hints = _effective(narrowed).hints
    assert hints["choices"]["set_amplitude.channel"] == ["CH2"] and hints["sources"]["set_amplitude.channel"] == "site"
    with pytest.raises(DeviceLimitsError, match="include 'CH3', which the driver does not offer"):
        _effective(site_limits(PhysicalDummyAWG("awg3"), choices={"set_amplitude.channel": ["CH3"]}))


@pytest.mark.parametrize("limits, message", [
    ({"set_frequency.value": (50, 95)}, "site limit set_frequency.value (50 to 95) is not inside the driver's own "
                                        "limit (60 to 100); site limits may only narrow it."),
    ({"set_frequency.value": (65, 101)}, "is not inside the driver's own limit (60 to 100)"),
])
def test_site_limits_may_only_narrow_the_driver(limits, message: str) -> None:
    with pytest.raises(DeviceLimitsError, match=None) as caught:
        _effective(site_limits(PhysicalDummyAOM("aom"), limits=limits))
    assert message in str(caught.value)


def test_site_choices_must_be_among_the_driver_choices() -> None:
    awg = site_limits(PhysicalDummyAWG("awg"), choices={"set_waveform.waveform": ["sine", "saw"]})
    with pytest.raises(DeviceLimitsError, match="include 'saw', which the driver does not offer"):
        _effective(awg)
    laser = site_limits(PhysicalSimulatedTunableLaser(device_id="lab01-laser"),
                        choices={"move_steps.velocity_mode": ["turbo"]})
    # The driver publishes "fast" and "slow".
    with pytest.raises(DeviceLimitsError, match="include 'turbo'"):
        _effective(laser)


@pytest.mark.parametrize("hints, message", [
    ({"limits": {"set_frequency.typo": [60, 100]}}, "has no argument 'typo'"),
    ({"limits": {"set_frequency.value": [100, 60]}}, "inverted"),
    ({"limits": {"set_frequency.value": [60, math.nan]}}, "not finite"),
    ({"limits": []}, "hints['limits'] must be a dict"),
    ({"choices": {"set_rf_enabled.enabled": ["yes"]}}, "is not True or False"),
    ({"channels": "CH1"}, "must be a list of channel names"),
    ("everything", "must be a dict"),
])
def test_malformed_driver_hints_are_refused(hints, message: str) -> None:
    with pytest.raises(DeviceLimitsError) as caught:
        _effective(PhysicalDummyAOM("aom"), hints)
    assert message in str(caught.value)


def test_every_shipped_driver_publishes_usable_hints() -> None:
    from labnet_sim.dummy_eom.physical import PhysicalDummyEOM
    from labnet_sim.dummy_laser.physical import PhysicalDummyLaser
    from labnet_sim.simulated_wavemeter.physical import PhysicalSimulatedWavemeter

    for device in (PhysicalDummyAOM("a"), PhysicalDummyAWG("b"), PhysicalDummyDevice("c"), PhysicalDummyEOM("d"),
                   PhysicalDummyLaser("e"), PhysicalSimulatedWavemeter("f")):
        _effective(device)


# ---- the check before every call ----------------------------------------------------------------


def test_check_refuses_outside_limits_bools_as_numbers_and_unknown_choices() -> None:
    aom = site_limits(PhysicalDummyAOM("aom"), limits={"set_frequency.value": (65, 95)})
    limits = _effective(aom)
    assert limits.check("set_frequency", {"value": 65.0}) is None
    assert limits.check("set_frequency", {"value": 95}) is None
    assert limits.check("set_frequency", {"value": 95.5}) == (
        "aom.set_frequency: value=95.5 is outside the site limit 65 to 95.")
    assert "is a number, and true is not one" in limits.check("set_frequency", {"value": True})
    assert "must be a number, not '80'" in limits.check("set_frequency", {"value": "80"})
    assert "must be finite" in limits.check("set_frequency", {"value": math.nan})
    assert "driver's limit 0 to 2" in limits.check("set_rf_power", {"value": 2.5})
    # A bool is never a number, even where nothing is limited.
    assert "true is not one" in _effective(PhysicalDummyDevice("dd")).check("write_value", {"value": True})

    awg = _effective(PhysicalDummyAWG("awg"))
    assert awg.check("set_waveform", {"waveform": "square", "channel": "CH2"}) is None
    assert "not one of the driver's choices" in awg.check("set_waveform", {"waveform": "Square"})
    assert "channel='CH3' is not one of the driver's choices ('CH1', 'CH2')" in awg.check(
        "set_amplitude", {"value": 1.0, "channel": "CH3"})
    # Left out, a driver-limited channel uses the driver's default, which is the driver's own business.
    assert awg.check("set_amplitude", {"value": 1.0}) is None

    rf = _effective(site_limits(PhysicalDummyAOM("aom2"), choices={"set_rf_enabled.enabled": [False]}))
    assert rf.check("set_rf_enabled", {"enabled": False}) is None
    assert "enabled=true is not one of the site choices (false)" in rf.check("set_rf_enabled", {"enabled": True})
    assert "not one of the site choices" in rf.check("set_rf_enabled", {"enabled": 0})


def test_a_site_limited_argument_must_be_sent() -> None:
    dummy = _effective(site_limits(PhysicalDummyDevice("dd"), choices={"read_value.channel": ["a"]}))
    assert "must be sent explicitly" in dummy.check("read_value", {})
    assert "must be sent explicitly" in dummy.check("read_value", {"channel": None})
    assert dummy.check("read_value", {"channel": "a"}) is None


def test_whole_numbers_must_be_integers() -> None:
    laser = site_limits(PhysicalSimulatedTunableLaser(device_id="lab01-laser"), limits={"move_steps.relative_steps": (-5, 5)})
    limits = _effective(laser, {})
    assert limits.check("move_steps", {"relative_steps": 3}) is None
    assert "must be a whole number, not 3.0" in limits.check("move_steps", {"relative_steps": 3.0})
    assert "outside the site limit -5 to 5" in limits.check("move_steps", {"relative_steps": 6})
