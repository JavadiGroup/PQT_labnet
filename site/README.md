# Site configuration

This directory is the PQT lab's own LabNet configuration. It is not example
code: every release ships it as `site.zip`, and `update_host.sh lab-pc`
installs it on each Lab PC as `C:\ProgramData\LabNet\site\`.

| File | Used on | Purpose |
| --- | --- | --- |
| `central.env.example` | Central | Template for `C:\ProgramData\LabNet\central.env` |
| `lab-pc.env.example` | each Lab PC | Template for `C:\ProgramData\LabNet\lab-pc.env` |
| `control-center.env.example` | Central | Template for `C:\ProgramData\LabNet\control-center.env` (the web interface) |
| `watchdog.env.example` | Central | Template for `C:\ProgramData\LabNet\watchdog.env` (the safe-state watchdog for Automation grants) |
| `Caddyfile.example` | Central | Template for `C:\ProgramData\LabNet\Caddyfile`: Central's HTTPS site, and the Control Center's with the logins that `scripts/lab/setup_logins.ps1` writes |
| `inventories/<lab pc>.py` | that Lab PC | Which instruments the Lab PC serves |

Real `.env` files hold API keys, and the Caddy logins snippet holds the
Control Center's proxy secret. They live only on their own computer, never
in this repository.

## Inventories

Each Lab PC has one inventory module with a `create_devices()` function that
returns one driver object per attached instrument:

```python
from labnet.devices.SimulatedWavemeter.physical import PhysicalSimulatedWavemeter


def create_devices():
    return [PhysicalSimulatedWavemeter(device_id="lab-pc-01-wavemeter")]
```

The Lab PC loads it through its `lab-pc.env`:

```dotenv
LABNET_DEVICE_INVENTORY=C:/ProgramData/LabNet/site/inventories/lab_pc_01.py:create_devices
```

Rules:

- `device_id` values must be unique across the whole network and must never
  change. Name them after the instrument or its fixed position, not a USB
  port or COM number.
- `create_devices()` should only construct objects. LabNet connects each
  device on its own worker thread at startup.
- Every device type must have a generated contract. A Lab PC refuses to
  start if a driver is missing a method its `.proto` declares.

## Adding a Lab PC

1. Copy `inventories/lab_pc_01.py` to `inventories/lab_pc_02.py` and list
   that computer's instruments.
2. On Central, create its identity:
   `labnet-central-admin --env-file central.env setup --lab-pc lab-pc-02 --write-env <dir> --server-url https://192.168.50.10 --ca-file C:/ProgramData/LabNet/certs/labnet-ca.pem`
3. Issue its certificate on the development PC:
   `labnet-certs issue lab-pc-02 --ip <its static IP> --dir C:\LabNet-CA`
4. Commit the inventory, build a release, and follow the Lab PC steps in
   [`Labnet/README.md`](../Labnet/README.md#3-set-up-each-lab-pc).
