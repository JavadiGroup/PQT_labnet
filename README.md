# PQT Network (LabNet)

LabNet lets experiment programs anywhere on the lab network use instruments
attached to other computers safely: one user at a time, through typed
operations, with the instrument's own computer staying in charge of the
hardware.

Each everyday workflow is one or two `labnet` commands:

| I want to… | Run |
| --- | --- |
| Run the whole network on my laptop | `uv sync`, then `labnet dev up` |
| Try or copy an experiment script | [`examples/`](examples/README.md) |
| Pick up code, proto, or device changes | `labnet dev down`, `labnet driver build`, `labnet dev up` |
| Ship a change to the real lab | `labnet bundle --site sites/pqt`, then `labnet update` on each computer |

First-time setup of the lab computers (static IPs, certificates, reverse
proxy, firewall) and the full settings reference are in
[`packages/labnet/README.md`](packages/labnet/README.md).

- [How the network fits together](#how-the-network-fits-together)
- [From physical device to network call](#from-physical-device-to-network-call)
- [Local development network](#local-development-network)
- [The change loop: down → update → generate → redeploy](#the-change-loop-down--update--generate--redeploy)
- [Updating the real lab network](#updating-the-real-lab-network)
- [Command reference](#command-reference)
- [Repository layout](#repository-layout)

## How the network fits together

There are three roles. In the lab each role runs on its own computer; for
development all three run on your laptop. Each role reads one settings file.

| Role | Command | Settings file | Listens on | Job |
| --- | --- | --- | --- | --- |
| **Central** | `labnet-central --env-file central.env` | `central.env` | HTTP `127.0.0.1:8008` (HTTPS `:443` via Caddy in the lab) | Users and API keys, the device directory, exclusive leases |
| **Lab PC** | `labnet-lab-pc --env-file lab-pc.env` | `lab-pc.env` | gRPC `:50051`; health `127.0.0.1:9001` | Owns the attached instruments and serves their operations |
| **Client** | your script, using `LabClient.from_env()` | `client.env` | nothing | Leases a device from Central, then talks directly to its Lab PC |
| **Control Center** | `labnet-control-center --env-file control-center.env` | `control-center.env` | HTTP `127.0.0.1:8080` (HTTPS via Caddy in the lab) | Web interface; a client of Central like any other |

```text
                  ┌─────────────────────────────┐
                  │           Central           │
                  │ API keys · devices · leases │
                  └──▲──────────────────▲────┬──┘
1. register devices, │       2. log in, │    │ 3. Lab PC address
           heartbeat │        request a │    │    + lease id
      5. check lease │            lease │    │
                     │                  │    │
                     │                  │    ▼
              ┌──────┴──────┐         ┌─┴──────────┐
              │    Lab PC   │◄────────│   Client   │
              │  + drivers  │         │(your code) │
              └─────────────┘         └────────────┘
                      4. gRPC + lease id, direct
```

1. At startup a Lab PC connects its instruments and registers them, along with
   its gRPC address, with Central. It keeps sending heartbeats.
2. A client logs in to Central with an API key and requests a lease on one
   device.
3. Central grants the lease only if nobody else holds the device, and returns
   the Lab PC's address and a lease id.
4. The client connects straight to that Lab PC and sends the lease id with
   every call.
5. The Lab PC checks the lease with Central before touching the hardware.

Leases last 60 s and the client renews them automatically every 20 s. If a
client crashes, its lease expires and the device frees itself. Central also
expires a lease after 15 minutes with no device commands.

## From physical device to network call

Every device family lives in a **driver package**, installed like any Python
package. [`labnet-sim`](packages/labnet-sim/) is the one LabNet ships: a
simulated optics bench (`DummyLaser`, `DummyAOM`, `DummyEOM`, `DummyAWG`),
`DummyDevice`, a `SimulatedTunableLaser` and a `SimulatedWavemeter`. You write
three small pieces; the rest is generated or shared framework code.

| Layer | File (in a driver package) | Who writes it |
| --- | --- | --- |
| Network contract | `protos/<package>/v1/<family>.proto` | **You** |
| Generated bindings, contracts and facades | `src/<package>/_generated/*` | `labnet driver build` |
| Driver wrapper | `src/<package>/<family>/physical.py` | **You** |
| Which instruments a Lab PC serves | `sites/<lab>/inventories/<lab pc>.py` | **You** |
| gRPC server, lease checks, worker threads, client stubs | `labnet` (`grpc`, `device_manager`, `client`) | Framework, do not edit per family |

### 1. The proto is the contract

Each RPC states which reusable pattern it follows and which Python method on
the driver it maps to:

```protobuf
rpc ReadWavelength(ReadWavelengthRequest) returns (ReadWavelengthResponse) {
  option (labnet.devices.v1.rpc) = {
    pattern: RPC_PATTERN_SCALAR_READ        // how LabNet executes it
    physical_method: "read_wavelength"      // method called in physical.py
    default_unit: "nm"
  };
}
```

The supported patterns are `RPC_PATTERN_SCALAR_READ`, `RPC_PATTERN_SCALAR_WRITE`,
`RPC_PATTERN_UNARY_COMMAND` and `RPC_PATTERN_POLLED_SCALAR_STREAM`. Other values
in `rpc_options.proto` are reserved, and the generator rejects them.

### 2. Code generation turns it into Python

`labnet driver build` (run in the driver package) runs `protoc`, validates
every RPC option, and writes `src/<package>/_generated/`:

- `<family>_pb2.py` / `_pb2_grpc.py`: protobuf messages, gRPC stub, and servicer;
- `contracts.py`: one `DeviceContract` per family. It records each RPC's
  pattern, the driver method it maps to, and the request fields forwarded as
  arguments, plus a `Protocol` describing the method signatures `physical.py`
  must have, and which package provides it (name, fingerprint, facade, driver);
- `facades.py` / `.pyi`: the class a client uses, e.g. `DummyAOM`.

The package registers each contract in the `labnet.devices` entry-point group,
and LabNet code finds it through `labnet.contracts.registry`
(`registry.get("dummy_aom")`). That generated contract is the single source of
truth. The server builds its handlers from it, the client builds its methods
from it, and only the methods it names can be called remotely; other helper
methods on a driver stay private. Generation happens before release and is
checked in. There is no code generation at runtime.

### 3. The driver wrapper does the hardware work

`physical.py` is ordinary Python around your vendor driver. It provides
`connect`, `disconnect`, `status`, `availability`, `metadata`, and each method
the proto names (for example `read_wavelength(self, channel=None)`). When a
Lab PC starts, it checks these signatures against the generated contract and
refuses to start if they don't match.

### 4. One call, end to end

Here is what happens when a client calls `device.write_value(12.5)`:

```text
CLIENT PROCESS
  DummyDevice.write_value(12.5)              devices/DummyDevice/device.py (facade)
   └─ ContractDeviceGrpcClient               client/core/grpc_client.py
        builds WriteValueRequest{device_id, operation_id, value}
        adds metadata  x-labnet-lease-id: <lease>
            │
            │  gRPC = HTTP/2 over one TCP connection (TLS outside loopback)
            ▼
LAB PC PROCESS (labnet-lab-pc)
  LabnetGrpcServer                           grpc/server.py
   └─ PatternDeviceGrpcService               grpc/services/patterns.py
        generic SCALAR_WRITE handler, built from the generated RpcSpec
   └─ DeviceExecutor                         device_manager/executor.py
        POST /api/v1/leases/<id>/validate to Central; checks device type and health
   └─ DeviceWorker (one thread + queue per device)   device_manager/worker.py
        method must be on the contract allow-list; calls run one at a time
   └─ PhysicalDummyDevice.write_value(value=12.5)    devices/DummyDevice/physical.py
        → vendor driver / hardware
```

The result travels back as a `ScalarMeasurement` (value, unit, timestamp,
quality). Because each device has its own worker thread and queue, calls to
one instrument never overlap, while different instruments work in parallel.
Streams poll the same physical read method at the requested rate and check
the lease with Central again every few seconds.

### 5. TCP, TLS, and who trusts whom

- **Transport.** gRPC runs over HTTP/2 on a single TCP connection per device
  object. Central's API is plain HTTP/1.1 JSON.
- **Encryption and server identity (TLS).** A Lab PC serves gRPC over TLS as
  soon as its `.env` names a certificate and key. Central sits behind Caddy
  for HTTPS. `labnet-certs` creates one private CA that signs both server
  certificates; each certificate contains the exact IP clients dial.
  Lab PCs and clients receive only the public `labnet-ca.pem`, named by
  `LABNET_CA_FILE`, which verifies Central and every Lab PC. There are no
  client certificates.
- **Authorization** is separate from TLS. API keys (stored hashed in Central)
  identify users and Lab PCs, and the lease id in gRPC metadata authorizes
  each device call.
- **Guard rails in code.** Plaintext is accepted only on loopback. A Lab PC
  with no API key skips lease checks, so it may run only on loopback and never
  in production. A Lab PC without a certificate refuses a LAN address. That
  is why the local network below can use plain HTTP and gRPC safely.

## Local development network

`labnet dev` runs a complete network on one machine: Central, **two Lab
PCs**, the **Control Center** web interface and its safe-state watchdog.
Everything binds to `127.0.0.1`.
- The first Lab PC serves two DummyDevices and a simulated optics bench: a
  laser, an AOM and an EOM.
- The second serves a DummyDevice, the SimulatedWavemeter and a two-channel
  AWG.

**Prerequisites:** Python 3.11+ and [uv](https://docs.astral.sh/uv/). The
commands work the same from PowerShell, cmd, or a Unix shell; processes start
in the background without opening windows.

```bash
uv sync                 # once, and after pulling: .venv with every package, editable
labnet dev up           # seed .devnet/ if needed, start everything, wait until ready
labnet dev status       # health of each process + the device table clients see
labnet dev smoke        # lease every device, call it over gRPC, release
labnet dev down         # stop everything (data in .devnet/ is kept)
labnet dev seed         # start over: a fresh database, users, and settings files
```

Run them from the repository root with the virtual environment active, or
prefix each with `uv run`. State lives in `.devnet/` under the current folder
(`--dir` picks another).

`labnet dev status` should show:

```text
DEVICE           TYPE                 LAB PC         gRPC TARGET      STATE  LEASABLE
dev01-aom        dummy_aom            lab-pc-dev-01  127.0.0.1:50051  ready  yes
dev01-dummy-a    dummy_device         lab-pc-dev-01  127.0.0.1:50051  ready  yes
dev01-dummy-b    dummy_device         lab-pc-dev-01  127.0.0.1:50051  ready  yes
dev01-eom        dummy_eom            lab-pc-dev-01  127.0.0.1:50051  ready  yes
dev01-laser      dummy_laser          lab-pc-dev-01  127.0.0.1:50051  ready  yes
dev02-awg        dummy_awg            lab-pc-dev-02  127.0.0.1:50052  ready  yes
dev02-dummy-a    dummy_device         lab-pc-dev-02  127.0.0.1:50052  ready  yes
dev02-wavemeter  simulated_wavemeter  lab-pc-dev-02  127.0.0.1:50052  ready  yes
```

### What `labnet dev seed` creates

Seeding writes `central.env`, then runs `labnet-central-admin setup`. That
creates the schema and every user, and writes each user's API key into its
own settings file:

| File in `.devnet/` | Contents |
| --- | --- |
| `central.env` | SQLite database at `.devnet/central.db`, port 8008 |
| `researcher.env` | Central URL + the `researcher` key, for clients and the examples |
| `dev-admin.env` | Central URL + the admin key |
| `lab-pc-dev-01.env` | ID + key, gRPC `:50051`, health `:9001`, inventory `labnet_sim.inventories.dev_lab_pc_01` |
| `lab-pc-dev-02.env` | ID + key, gRPC `:50052`, health `:9002`, inventory `labnet_sim.inventories.dev_lab_pc_02` |
| `control-center.env` | Central URL + the `control-center` key, port 8080, the experiments and automation folders (`experiments/` and `automation/` at the repository root, git-ignored, so re-seeding keeps them) and Photo-Agent's folders (papers in `literature/`) |
| `automation-watchdog.env` | The watchdog's Central key and port 8081 |
| `run/` | One pid file per process, so `down` stops exactly what `up` started |
| `logs/` | One log per process; `labnet dev up` prints the tail if a start fails |

A dev Lab PC file is six lines. Having an API key turns on Central
registration and lease checks; having no certificate keeps it on loopback
plaintext. `.devnet/` is git-ignored, and its keys are throwaway.

To change the topology (more Lab PCs, other ports), edit `DevNetwork` in
[`labnet/dev/network.py`](packages/labnet/src/labnet/dev/network.py), add an inventory under
`packages/labnet-sim/src/labnet_sim/inventories/`, then run `labnet dev down`,
`labnet dev seed`, and `labnet dev up`.

### The Control Center

`labnet dev up` also starts the web interface at
**http://127.0.0.1:8080/status**: every Lab PC, its devices, their state
(available, leased, unavailable), and each device's callable methods. The
**Network** tab is that status page, grouped by Lab PC. The **Devices** tab
lists every device in one list. On both, "Device control" opens the device's
own page at `/<device-id>` (e.g. `http://127.0.0.1:8080/dev02-wavemeter`).

The wavemeter's page has a live graph with Play/Pause, a time window, and
CSV export; parameters that are sent when Play is pressed; and a script box.
Other device types get a script box until they have their own panel (see
[Device control pages](packages/labnet/README.md#device-control-pages)).

The **Experiments** tab lists the lab's setup diagrams. **View Experiment**
opens one on a canvas:
- Drag devices and passive optics (mirrors, beamsplitters, filters…) onto the
  canvas, and connect them with beams or signal cables.
- Click a device to set each setting to a fixed value or a sweep, and to
  choose which readings to record.
- **Export script** turns the diagram into a Python script that runs it and
  writes a CSV.

The **Automation** tab holds projects an agent will work in. Each project is
a folder:
- **Literature:** the shared lab library is already there.
- **Data:** your example data, and the data the agent makes.
- **Experiment_layout:** snapshots of the canvases it uses.
- **Tasks:** one Markdown file per task.
- **ScriptsUtilized, Protocols and Analysis:** where the agent's work goes.

You upload, edit and link experiments from the page. Folders the agent writes
are read-only to people. Tasks are queued as runs: the agent plans, and any
device control waits for a person to approve a protocol whose bounds the
Control Center and the Lab PCs enforce. See
[Automation](packages/labnet/README.md#automation). To bring it into use in the lab,
follow the [Automation go-live checklist](packages/labnet/README.md#automation-go-live-checklist).

The **Photo-Agent** sidebar is Claude Code: ask it about LabNet, attach data
or papers, or ask it to set up an experiment. It proposes a diagram you can
apply to the canvas. See
[Experiments](packages/labnet/README.md#experiments) and
[Photo-Agent](packages/labnet/README.md#photo-agent).

The Control Center is just another LabNet client with its own Central user,
so the browser never sees an API key. In both development and the lab it
runs on the same computer as Central and reaches it over loopback. Only the
settings file's location and the address people open differ:

| | Development | Lab (on `central_server_pc`) |
| --- | --- | --- |
| Settings | `.devnet/control-center.env` (written by `labnet dev seed`) | `C:\ProgramData\LabNet\control-center.env` |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | `http://127.0.0.1:8008` (same computer as Central) |
| Started by | `labnet dev up` | you, in its own window (see [starting the lab by hand](packages/labnet/README.md#starting-the-lab-by-hand)) |
| Address | `http://127.0.0.1:8080/status` | `https://pqt-control-center/status` or `https://192.168.50.10:8443/status`, via Caddy |
| Logins | none | One per person, checked by Caddy (`scripts/lab/setup_logins.ps1`; see [Logins](packages/labnet/README.md#logins)) |

Use `pqt-control-center`, with hyphens: underscores are not valid in host
names or certificates. Setup is in
[`packages/labnet/README.md` → Control Center](packages/labnet/README.md#control-center).

### Using the dev network from your own code

Point clients at the researcher file once, then run any script:

```bash
export LABNET_ENV_FILE=.devnet/researcher.env        # PowerShell: $env:LABNET_ENV_FILE = ".devnet\researcher.env"
python examples/01_hello_device.py
python examples/04_scan_two_devices.py               # leases devices on both Lab PCs
```

[`examples/README.md`](examples/README.md) describes all five examples.

## The change loop: down → update → generate → redeploy

Running processes never reload code. Device objects are built once at
startup, so every change follows the same loop:

```bash
labnet dev down                      # 1. take the network down
#                                       2. edit protos / physical.py / inventories
labnet driver build --core           # 3. regenerate (core protos; in a driver package, labnet driver build)
pytest                               #    and run the tests
labnet dev up                        # 4. bring it back with the new code
labnet dev smoke                     # 5. prove every device still answers
```

Skip step 3 when you only changed an inventory or Python code.

When the change touches only one component, restart just that one and leave
the rest running:

```bash
labnet dev restart control-center    # Control Center Python or its .env
labnet dev restart lab-pc-dev-02     # that Lab PC's inventory or .env
labnet dev restart central           # Central code or central.env
```

Control Center page files (HTML, CSS, JS) need no restart: refresh the browser.

### Adding or removing instruments of an existing family

No code generation is needed. Edit the Lab PC's inventory, for example
`packages/labnet-sim/src/labnet_sim/inventories/dev_lab_pc_02.py`:

```python
def create_devices():
    return [
        PhysicalDummyDevice(device_id="dev02-dummy-a"),
        PhysicalSimulatedWavemeter(device_id="dev02-wavemeter"),
        PhysicalSimulatedWavemeter(device_id="dev02-wavemeter-b", center_nm=852.347),  # new
    ]
```

Then run `labnet dev restart lab-pc-dev-02`. Device ids must be unique
across **all** Lab PCs. A removed device stays in Central's table as `stopped`
and not leasable. Run `labnet dev seed` to clear it, or if you moved a device id to a
different Lab PC (Central rejects that as an ownership conflict).

### Adding a new device family (a driver package)

```
labnet driver new acme-thing --service AcmeThing     # a working package from the template
cd acme-thing
pip install -e ".[dev]"
labnet driver build                                  # protos -> src/acme_thing/_generated/
pytest                                               # check_driver + the facade, out of the box
```

1. Edit `protos/acme_thing/v1/acme_thing.proto`. Every RPC needs the
   `pattern`, `physical_method`, and `default_unit` options; its package is
   `acme_thing.v1` (`labnet.*` is LabNet's own). See
   [`labnet-sim`'s protos](packages/labnet-sim/protos/labnet_sim/v1/) for more.
2. `labnet driver build`. It fails with a precise message if an option is
   missing or doesn't fit the RPC's shape, and tells you the
   `[project.entry-points."labnet.devices"]` line for each family.
3. Implement `src/acme_thing/acme_thing/physical.py` around the vendor SDK.
   `labnet.testing.check_driver` runs a Lab PC's own startup checks on it.
4. Install the package on the Lab PC (and on clients and the Control Center),
   add instances to that Lab PC's inventory, and restart it. Clients use
   `from acme_thing import AcmeThing`.
5. Commit the proto **and** the generated files; CI runs `labnet driver build --check`.

After a contract is deployed, keep it backward compatible: never renumber or
retype fields, add new fields as optional, and reserve removed numbers. See
[contract compatibility](packages/labnet/README.md#contract-compatibility).

## Updating the real lab network

Runtime computers never run from a git checkout. They install a versioned,
hash-checked bundle built on the development PC. Each computer keeps its
settings in one file that updates never touch. You start each process by
hand in its own window, in the order listed in
[starting the lab by hand](packages/labnet/README.md#starting-the-lab-by-hand):

| Computer | Settings file | Start command |
| --- | --- | --- |
| Central | `C:\ProgramData\LabNet\central.env` | `central-venv\Scripts\labnet-central --env-file …\central.env` |
| Central (web interface) | `C:\ProgramData\LabNet\control-center.env` | `control-center-venv\Scripts\labnet-control-center --env-file …\control-center.env` |
| Lab PC | `C:\ProgramData\LabNet\lab-pc.env` | `lab-pc-venv\Scripts\labnet-lab-pc --env-file …\lab-pc.env` |
| Client | `C:\ProgramData\LabNet\client.env` | set `LABNET_ENV_FILE` to it, then run scripts |

First-time setup of a computer is in
[`packages/labnet/README.md` → Production deployment](packages/labnet/README.md#production-deployment).
After that, every update follows these steps:

### 1. Build the bundle (development PC)

```bash
# bump version = "x.y.z" in every packages/*/pyproject.toml, then:
pytest && labnet driver build --core --check
git commit -am "Release x.y.z"
labnet bundle --site sites/pqt             # add --driver <package> for each driver the lab uses
```

`labnet bundle` refuses to overwrite an existing bundle, or to pack a site
folder holding anything that looks like a secret (`*.env`, keys, logins). It
makes (in `dist/`, git-ignored):

```text
dist/labnet-x.y.z-pqt/
  MANIFEST.json      version, site, git revision, which wheel each role installs, SHA-256 of every file
  wheels/*.whl       LabNet, the Control Center, the drivers, and every dependency (for Windows, Python 3.11)
  site-pqt.zip       sites/pqt/: env templates, the Caddyfile template, every Lab PC inventory
```

`--no-dependencies` leaves out the dependencies, for computers that can
reach PyPI; `--python` and `--platform` choose another target. Copy the
folder to the release share (for example
`\\192.168.50.40\LabNet-Releases\x.y.z`).

### 2. Install it on each computer, in dependency order

Stop processes in reverse order (clients, then Lab PCs, then the Control
Center, Caddy, and Central). Then update and start them in this order:
**Central → Control Center → each Lab PC → clients**. On each computer, in
PowerShell:

```powershell
$B = '\\192.168.50.40\LabNet-Releases\x.y.z'     # or a local copy of the folder
# labnet itself, in a throwaway environment, to run the update with:
py -3.11 -m venv $env:TEMP\labnet-update
& $env:TEMP\labnet-update\Scripts\pip install --quiet --no-index --find-links $B\wheels labnet
& $env:TEMP\labnet-update\Scripts\labnet update --from $B --role central          # on central_server_pc
& $env:TEMP\labnet-update\Scripts\labnet update --from $B --role control-center   # on central_server_pc too
& $env:TEMP\labnet-update\Scripts\labnet update --from $B --role lab-pc           # on each Lab PC
& $env:TEMP\labnet-update\Scripts\labnet update --from $B --role client           # on each client
```

(For a `--no-dependencies` bundle, leave out `--no-index`.)

`labnet update` handles the steps that are easy to get wrong:

| Step | What it does |
| --- | --- |
| Verify | Compares every file's SHA-256 with `MANIFEST.json`; stops on any mismatch |
| Safety | Refuses to run while that role still answers on its loopback health port (for the Control Center, the watchdog's too) |
| Install | Builds a new environment, `C:\ProgramData\LabNet\venvs\<role>-x.y.z-<time>`, from the bundle's wheels; the current one is untouched |
| Central | Backs up the SQLite file named in `central.env` (`labnet.db.before-x.y.z.<time>`), then applies migrations |
| Lab PC | Installs the site as `C:\ProgramData\LabNet\site\`, then loads `lab-pc.env` and builds its inventory's devices (without connecting), so a missing driver shows now rather than at startup |
| Control Center | Loads `control-center.env` with the new code |
| Switch | Points `C:\ProgramData\LabNet\<role>-venv` (a junction) at the new environment, and records the bundle in `<role>-RELEASE.json` |

If any check fails, the new environment is deleted and nothing else has
changed. Start the role again and check its `/health/ready`. Then run the
[deployment verification](packages/labnet/README.md#deployment-verification) checklist.

**Rollback:** each role keeps its last three environments in
`C:\ProgramData\LabNet\venvs\`, and `<role>-RELEASE.json` names the
previous one. Point the junction back with
`cmd /c rmdir C:\ProgramData\LabNet\central-venv` then
`cmd /c mklink /J C:\ProgramData\LabNet\central-venv <previous environment>`.
The previous site is kept as `site.previous`. Reinstalling old code does not
undo a schema migration: restore the `labnet.db.before-*` backup if you need to.

## Command reference

| Command | Where it runs | Purpose |
| --- | --- | --- |
| `labnet dev up / down / status / smoke` | dev machine | Start, stop, check, and exercise the local network |
| `labnet dev seed [--watchdog]` | dev machine | Wipe `.devnet/`; write `central.env`, run `labnet-central-admin setup`, write every component's settings |
| `labnet dev restart COMPONENT...` | dev machine | Restart only `central`, `control-center`, `watchdog`, or a Lab PC id, leaving the rest running |
| `labnet runner build / login / up / logs / self-check / down` | dev machine (Docker; WSL on Windows) | The [Automation runner](packages/labnet/README.md#the-automation-runner) against the dev Control Center |
| `labnet driver build [--core] [--check]` | dev machine | Generate protos (a driver package's, or LabNet's own with `--core`); `--check` fails on stale files |
| `labnet driver new NAME --service SERVICE` | dev machine | A new driver package from the template |
| `labnet bundle --site sites/<lab>` | development PC | Build a hash-recorded bundle: wheels, site, `MANIFEST.json` |
| `labnet update --from BUNDLE --role ROLE` | each runtime computer | Verify, install side by side, migrate or check the site, switch over |
| `labnet-certs init-ca / issue / show` | development PC | Create the private CA and server certificates |
| `labnet-central-admin setup / user …` | Central | Migrate, create users, write their settings files |
| `scripts/lab/setup_logins.ps1 [-Reset NAME] [-Realm TEXT] [-WhatIf]` | Central (PowerShell) | Control Center logins: Caddy snippet with each person's password hash, and the proxy secret in `control-center.env` |
| `labnet-control-center-admin runner add / list / revoke` | Central | Issue and revoke Automation runner tokens |

## Repository layout

| Path | Purpose |
| --- | --- |
| [`packages/`](packages/) | The installable packages: `labnet` ([full guide](packages/labnet/README.md)), `labnet-control-center`, `labnet-automation` and `labnet-sim` |
| [`packages/labnet/src/labnet/protos/`](packages/labnet/src/labnet/protos/) | LabNet's shared protobuf messages and RPC options |
| [`packages/labnet/src/labnet/driver/`](packages/labnet/src/labnet/driver/) | `labnet driver build` and `new`: contract validator, code generator, driver template |
| [`packages/labnet-sim/`](packages/labnet-sim/) | The simulated device families: a complete driver package |
| [`packages/labnet/src/labnet/lab_pc/`](packages/labnet/src/labnet/lab_pc/) | The `labnet-lab-pc` runtime and its settings rules |
| [`packages/labnet-control-center/src/labnet_control_center/`](packages/labnet-control-center/src/labnet_control_center/) | The `labnet-control-center` web interface (backend + `static/` pages) |
| `packages/*/tests/`, [`tests/`](tests/) | Each package's tests; full-stack and example tests |
| [`sites/pqt/`](sites/pqt/README.md) | The PQT lab's configuration: env templates, the Caddyfile template, and Lab PC inventories |
| [`examples/`](examples/README.md) | Experiment scripts 01–05 |
| [`scripts/lab/`](scripts/lab/) | `setup_logins.ps1`, run on Central to manage Control Center logins |

## Security

The local network uses plaintext only because everything is on loopback;
LabNet refuses plaintext Central or gRPC endpoints on any other address. Use
HTTPS for Central and TLS for every LAN-facing Lab PC.

In the lab, the Control Center answers only requests that come through
Caddy, where each person signs in with their own login; Caddy vouches for
them with a shared proxy secret. Automation runners use their own tokens
instead. Development on one computer has no logins. See
[Logins](packages/labnet/README.md#logins).

Never commit API keys, private keys, `.env` files, site certificates, or
runtime databases. Rotate any credential that may have been exposed
(`labnet-central-admin --env-file central.env user rotate-key <user>`).

## License

LabNet is licensed under the [MIT License](LICENSE).
