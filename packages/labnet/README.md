# labnet

LabNet lets experiment programs anywhere on a lab network use instruments
attached to other computers safely: one user at a time, through typed
operations, with the instrument's own computer staying in charge of the
hardware.

This package holds Central (users, the device directory, leases), the Lab PC
runtime (serves instruments over gRPC), the client library, the driver
tooling, and the `labnet` command. Instruments come from driver packages,
such as [`labnet-sim`](https://pypi.org/project/labnet-sim/); the web
interface is [`labnet-control-center`](https://pypi.org/project/labnet-control-center/).

## Install

| Command | For |
| --- | --- |
| `pip install labnet` | Client computers: experiment scripts |
| `pip install "labnet[lab-pc]"` | Lab PCs |
| `pip install "labnet[central]"` | Central (add `postgres` for PostgreSQL) |
| `pip install "labnet[certs]"` | The computer that issues the lab's certificates |
| `pip install "labnet[driver-dev]"` | Writing a driver package (`labnet driver build`) |

A whole simulated lab on one computer:

```bash
pip install "labnet[central,lab-pc]" labnet-sim labnet-control-center
labnet dev up
```

## Use an instrument

```python
from labnet import LabClient
from labnet_sim import SimulatedWavemeter

central = LabClient.from_env()               # reads LABNET_ENV_FILE
meter = SimulatedWavemeter(client=central)   # leases the first available wavemeter
try:
    print(meter.read_wavelength(), "nm")
finally:
    meter.close()                              # releases the lease
    central.close()
```

## Documentation

<https://mill0851.github.io/labnet/>: the
[Quickstart](https://mill0851.github.io/labnet/quickstart/),
[writing a driver](https://mill0851.github.io/labnet/drivers/),
[deploying a lab](https://mill0851.github.io/labnet/deploying/), and the
[API reference](https://mill0851.github.io/labnet/reference/api/client/).

LabNet is MIT licensed. Source: <https://github.com/mill0851/labnet>.
