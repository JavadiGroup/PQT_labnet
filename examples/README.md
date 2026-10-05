# Example experiments

Short scripts to copy when writing an experiment. Each one adds one idea:

| Script | Shows | Devices it uses on the dev network |
| --- | --- | --- |
| [`01_hello_device.py`](01_hello_device.py) | Connect, lease a device, write, read, release | first available DummyDevice |
| [`02_choose_device.py`](02_choose_device.py) | List what Central offers; pick a device by id or Lab PC | any |
| [`03_stream_async.py`](03_stream_async.py) | Asyncio client and streaming samples | SimulatedWavemeter |
| [`04_scan_two_devices.py`](04_scan_two_devices.py) | An experiment across two Lab PCs: step a source, average a meter, save a CSV | DummyDevice + SimulatedWavemeter |
| [`05_local_hardware.py`](05_local_hardware.py) | The same classes with no network, for driver work on a Lab PC | none (local) |

## Running them

Start the local network, then point the examples at the researcher
settings that `seed.sh` wrote:

```bash
scripts/dev/seed.sh && scripts/dev/up.sh
export LABNET_ENV_FILE=.devnet/researcher.env      # PowerShell: $env:LABNET_ENV_FILE = ".devnet\researcher.env"
python examples/01_hello_device.py
```

`05_local_hardware.py` needs no network or settings.

## How they connect

Every networked example starts with:

```python
with LabClient.from_env() as central:
```

`from_env()` reads three settings, looking in the process environment first,
then the file named by `LABNET_ENV_FILE`, then `./.env`:

| Setting | Meaning |
| --- | --- |
| `LABNET_SERVER_URL` | Central's URL (`https://192.168.50.10` in the lab) |
| `LABNET_API_KEY` | Your Central API key |
| `LABNET_CA_FILE` | Lab only: the public `labnet-ca.pem`. It verifies Central and every Lab PC |

The same script therefore runs on the dev network and in the lab; only the
settings file changes. On a lab client computer that file is
`C:\ProgramData\LabNet\client.env`.

## Rules every experiment should follow

- **Release what you lease.** Use `with` blocks (or `ExitStack` for several
  devices, as in 04). A lease is released the moment its block ends.
  A crashed script's lease expires within about a minute.
- **Close in reverse order:** streams, then devices, then the Central client.
- **Expect exclusivity.** While you hold a device, nobody else can lease it.
  Leasing a device someone else holds raises `ResourceConflictError`.
- **Keep API keys out of scripts.** They belong in the settings file.

## Adapting to another device family

Each family is a class in `labnet` whose methods come from its `.proto`.
For example, `SimulatedWavemeter` has `read_wavelength()` and
`stream_wavelength()`:

- **Synchronous code:** `SimulatedWavemeter(client=central)`, or pass
  `device_id=` to pick a specific device.
- **Asyncio code:** `await central.acquire_device(SimulatedWavemeter.contract)`.

A new family appears the same way after its `.proto` is added and code
generation is run. See
[the change loop](../README.md#the-change-loop-down--update--generate--redeploy).
