# Quickstart

Run a whole simulated lab on your computer: Central, two Lab PCs serving
simulated instruments, and the Control Center web interface. Everything binds
to `127.0.0.1`; nothing is reachable from the network.

You need Python 3.11 or newer. The commands are the same in PowerShell, cmd,
or a Unix shell.

## 1. Install

```bash
python -m venv labnet-try
labnet-try\Scripts\activate          # macOS/Linux: source labnet-try/bin/activate
pip install "labnet[central,lab-pc]" labnet-sim labnet-control-center
```

- `labnet[central,lab-pc]` is LabNet with what Central and a Lab PC need.
- `labnet-sim` is a driver package of simulated instruments: an optics bench
  (laser, AOM, EOM, AWG), a wavemeter, a tunable laser.
- `labnet-control-center` is the web interface.

## 2. Start the network

```bash
labnet dev up
```

The first run creates `.devnet/` in the current folder: a database, users,
and one settings file per component. Then it starts each process in the
background and waits until each is ready. Open
**<http://127.0.0.1:8080/status>** to see the Lab PCs and their instruments.

```bash
labnet dev status       # health of each process, and the devices clients see
labnet dev smoke        # lease every device, call it, release it
```

## 3. Use an instrument from Python

Point clients at the researcher's settings file, which `labnet dev` wrote:

```bash
export LABNET_ENV_FILE=.devnet/researcher.env        # PowerShell: $env:LABNET_ENV_FILE = ".devnet\researcher.env"
```

then run:

```python
from labnet import LabClient
from labnet_sim import SimulatedWavemeter

central = LabClient.from_env()
meter = SimulatedWavemeter(client=central)   # leases the first available wavemeter
try:
    print(meter.read_wavelength(), "nm")
finally:
    meter.close()                              # releases the lease
    central.close()
```

While the script holds the lease, the Control Center shows the wavemeter as
leased, and no one else can use it. The
[example scripts](https://github.com/JavadiGroup/labnet/tree/main/examples)
go further: choosing devices, streaming, and an experiment across two Lab PCs.

## 4. Stop

```bash
labnet dev down         # stop everything; .devnet/ is kept for next time
labnet dev seed         # (optional) start over with a fresh database
```

## Next

- [Concepts](concepts.md): Central, Lab PCs, leases, and what happens on one call.
- [Clients](clients.md): writing experiment scripts.
- [Writing a driver](drivers.md): your own instrument as a driver package.
- [Deploying a lab](deploying/index.md): real computers, certificates, and updates.
- [Development](development.md): working on LabNet itself from a checkout.
