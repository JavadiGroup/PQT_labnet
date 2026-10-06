from __future__ import annotations

import asyncio
import re

import pytest

from labnet.control_center.script_runner import ScriptProcess
from labnet.control_center.scripting import ScriptError, validate_script


WAVEMETER = ["read_wavelength", "set_exposure", "stream_wavelength"]


@pytest.mark.parametrize(
    "code",
    [
        "device.set_exposure(50)\nprint(device.read_wavelength())",
        "readings = []\nfor i in range(3):\n    readings.append(device.read_wavelength())\nprint(mean(readings))",
        "x = {'a': 1}\nif x.get('a') == 1 and not False:\n    print(f'{x}')",
        "source = lease('dev01-dummy-a')\nsource.write_value(1.5)\nsleep(0.1)",
    ],
)
def test_lab_style_scripts_are_accepted(code: str) -> None:
    validate_script(code)


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("import os", "import is not allowed"),
        ("while True:\n    pass", "while loops is not allowed"),
        ("def f():\n    pass", "def is not allowed"),
        ("f = lambda: 1", "lambda is not allowed"),
        ("print(().__class__)", "Only method calls"),
        ("x = device.__class__()", "not a device method"),
        ("open('central.env')", "'open()' is not an allowed function"),
        ("__import__('os')", "'__import__()' is not an allowed function"),
        ("_secret = 1", "Names starting with '_'"),
        ("print('{0.__class__}'.format(1))", "'.format()' is not"),
        ("[x for x in range(3)]", "list comprehensions is not allowed"),
        ("device.read_wavelength", "Only method calls"),
        ("print(", "Syntax error"),
    ],
)
def test_everything_else_is_rejected_before_running(code: str, message: str) -> None:
    with pytest.raises(ScriptError, match=re.escape(message)):
        validate_script(code)


async def _run(code: str, *, timeout: float = 20.0, stop_after: float | None = None):
    """Run a script in a real worker process against fake device calls."""

    loop = asyncio.get_running_loop()
    events: list[dict] = []
    calls: list[tuple] = []
    finished: asyncio.Future = loop.create_future()

    async def on_call(handle, method, args, kwargs):
        calls.append((handle, method, args, kwargs))
        if method == "read_wavelength":
            return 780.0 + len(calls) * 0.001
        if method == "fail":
            raise RuntimeError("the instrument said no")
        return args[0] if args else None

    async def on_lease(device_id):
        return {"handle": device_id, "methods": ["write_value"]}

    async def on_finished(error):
        finished.set_result(error)

    process = ScriptProcess(
        code=code,
        devices={"device": {"label": "dev02-wavemeter", "methods": WAVEMETER + ["fail"]}},
        on_call=on_call,
        on_lease=on_lease,
        on_event=events.append,
        on_finished=on_finished,
        loop=loop,
        timeout_seconds=timeout,
    )
    process.start()
    if stop_after is not None:
        await asyncio.sleep(stop_after)
        process.stop()
    error = await asyncio.wait_for(finished, timeout=30)
    return error, [event["text"] for event in events if event["kind"] == "output"], calls


@pytest.mark.asyncio
async def test_script_calls_devices_and_prints() -> None:
    error, output, calls = await _run(
        "device.set_exposure(50)\n"
        "readings = []\n"
        "for i in range(3):\n"
        "    readings.append(device.read_wavelength())\n"
        "print('n', len(readings))\n"
        "source = lease('dev01-dummy-a')\n"
        "source.write_value(1.5)\n"
    )

    assert error is None
    assert output == ["n 3"]
    assert [call[1] for call in calls] == [
        "set_exposure", "read_wavelength", "read_wavelength", "read_wavelength", "write_value",
    ]
    assert calls[-1][0] == "dev01-dummy-a"


@pytest.mark.asyncio
async def test_errors_name_the_script_line() -> None:
    error, _output, _calls = await _run("x = 1\ndevice.fail()")
    assert error.startswith("Line 2:") and "the instrument said no" in error

    error, _output, _calls = await _run("x = 1\ny = x / 0")
    assert error == "Line 2: ZeroDivisionError: division by zero"


@pytest.mark.asyncio
async def test_stop_and_time_limit_end_a_busy_script() -> None:
    busy = "for i in range(10 ** 9):\n    x = i * i"

    error, _output, _calls = await _run(busy, stop_after=1.0)
    assert error == "Stopped by user."

    error, _output, _calls = await _run(busy, timeout=1.0)
    assert error == "Stopped: the script ran longer than 1 s."
