# Client programming

Every client starts from one `LabClient` (or `AsyncLabClient`) per process:

```python
from labnet import LabClient, LabClientError
from labnet_sim import DummyDevice

central = None
device = None
try:
    central = LabClient.from_env()
    device = DummyDevice(client=central)          # first available; or device_id="..."
    device.write_value(12.5, channel="main")
    print(device.read_value(channel="main"))
except LabClientError as error:                   # any LabNet failure
    raise SystemExit(f"LabNet error: {error}")
finally:                                          # runs after errors and Ctrl+C too
    if device is not None:
        device.close()                            # releases the lease
    if central is not None:
        central.close()
```

Create every object inside `try` and close it in `finally`, newest first.
The `is not None` checks mean a failure halfway through setup closes only what
exists. `LabClientError` is the base class of every LabNet error: connection,
authentication, a device leased by someone else (`ResourceConflictError`), and
failed device calls.

`from_env()` reads `LABNET_SERVER_URL`, `LABNET_API_KEY`, and `LABNET_CA_FILE`.
It looks in the process environment, then the file named by
`LABNET_ENV_FILE`, then `./.env`. The same values can be passed directly:
`LabClient(url, key, ca_file=path)`. The CA file verifies Central's HTTPS
certificate, and every device leased through that client inherits it for
gRPC.

Omitting `client` drives the local physical implementation, with the same
methods:

```python
from labnet_sim import SimulatedWavemeter

meter = None
try:
    meter = SimulatedWavemeter(device_id="local-wavemeter")
    print(meter.read_wavelength())
finally:
    if meter is not None:
        meter.close()                             # disconnects the driver
```

Use `AsyncLabClient` to overlap independent instruments or consume streams:

```python
import asyncio

from labnet import AsyncLabClient, LabClientError
from labnet_sim import SimulatedWavemeter

async def main() -> None:
    central = None
    meter = None
    samples = None
    try:
        central = AsyncLabClient.from_env()
        meter = await central.acquire_device(SimulatedWavemeter.contract)
        samples = meter.stream_wavelength(sample_rate_hz=10, sample_count=20)
        async for sample in samples:
            print(sample.sequence, sample.value, sample.unit)
    except LabClientError as error:
        raise SystemExit(f"LabNet error: {error}")
    finally:
        if samples is not None:
            await samples.aclose()                # 1. the stream
        if meter is not None:
            await meter.aclose()                  # 2. the device and its lease
        if central is not None:
            await central.aclose()                # 3. Central

asyncio.run(main())
```

Asyncio lets unrelated operations overlap. It does not make one instrument
run several driver calls at once; that instrument's worker stays serialized.
The [`examples/`](https://github.com/JavadiGroup/labnet/blob/main/examples/README.md) directory has five complete scripts.
