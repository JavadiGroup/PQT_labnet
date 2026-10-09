# PQT Network (LabNet)

LabNet lets experiment programs anywhere on the lab network use instruments
attached to other computers safely: one user at a time, through typed
operations, with the instrument's own computer staying in charge of the
hardware.

Each everyday workflow is a short sequence of scripts:

| I want to… | Run |
| --- | --- |
| Run the whole network on my laptop | `scripts/dev/setup.sh` → `seed.sh` → `up.sh` |
| Try or copy an experiment script | [`examples/`](examples/README.md) |
| Pick up code, proto, or device changes | `scripts/dev/redeploy.sh` |
| Ship a change to the real lab | `scripts/release/build.sh`, then `update_host.sh` on each computer |

First-time setup of the lab computers (static IPs, certificates, reverse
proxy, firewall) and the full settings reference are in
[`packages/labnet/README.md`](packages/labnet/README.md).

- [How the network fits together](#how-the-network-fits-together)
- [From physical device to network call](#from-physical-device-to-network-call)
- [Local development network](#local-development-network)
- [The change loop: down → update → generate → redeploy](#the-change-loop-down--update--generate--redeploy)
- [Updating the real lab network](#updating-the-real-lab-network)
- [Script reference](#script-reference)
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
| Which instruments a Lab PC serves | `site/inventories/<lab pc>.py` | **You** |
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

The scripts run a complete network on one machine: Central, **two Lab
PCs**, and the **Control Center** web interface. Everything binds to
`127.0.0.1`.
- The first Lab PC serves two DummyDevices and a simulated optics bench: a
  laser, an AOM and an EOM.
- The second serves a DummyDevice, the SimulatedWavemeter and a two-channel
  AWG.

**Prerequisites:** Python 3.11+ and bash. On Windows, use **Git Bash** (it
comes with Git for Windows). From PowerShell, call it explicitly with
`& "C:\Program Files\Git\bin\bash.exe" scripts/dev/up.sh`, because a bare
`bash` may launch WSL instead.

```bash
scripts/dev/setup.sh    # once: create .venv, install LabNet editable with all extras
scripts/dev/seed.sh     # fresh database, users, and one settings file per user
scripts/dev/up.sh       # start Central, each Lab PC, then the Control Center; waits until ready
scripts/dev/status.sh   # health of each process + the device table clients see
scripts/dev/smoke.sh    # lease every device, call it over gRPC, release
scripts/dev/down.sh     # stop everything (data in .devnet/ is kept)
```

`status.sh` should show:

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

### What `seed.sh` creates

`seed.sh` writes `central.env`, then runs `labnet-central-admin setup`. That
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
| `logs/` | One log per process; `up.sh` prints the tail if a start fails |

A dev Lab PC file is six lines. Having an API key turns on Central
registration and lease checks; having no certificate keeps it on loopback
plaintext. `.devnet/` is git-ignored, and its keys are throwaway.

To change the topology (more Lab PCs, other ports), edit
[`scripts/dev/config.sh`](scripts/dev/config.sh), add an inventory under
`packages/labnet-sim/src/labnet_sim/inventories/`, then run `down.sh`, `seed.sh`, and `up.sh`.

### The Control Center

`up.sh` also starts the web interface at
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
| Settings | `.devnet/control-center.env` (written by `seed.sh`) | `C:\ProgramData\LabNet\control-center.env` |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | `http://127.0.0.1:8008` (same computer as Central) |
| Started by | `scripts/dev/up.sh` | you, in its own window (see [starting the lab by hand](packages/labnet/README.md#starting-the-lab-by-hand)) |
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
scripts/dev/down.sh                  # 1. take the network down
#                                       2. edit protos / physical.py / inventories
scripts/dev/codegen.sh               # 3. regenerate + check + run tests
scripts/dev/up.sh                    # 4. bring it back with the new code
scripts/dev/smoke.sh                 # 5. prove every device still answers
```

`scripts/dev/redeploy.sh` runs steps 1, 3, 4, and 5 in order and stops at the
first failure. Use `redeploy.sh --skip-tests` for a faster loop when you only
changed an inventory.

When the change touches only one component, restart just that one and leave
the rest running:

```bash
scripts/dev/restart.sh control-center      # Control Center Python or its .env
scripts/dev/restart.sh lab-pc-dev-02       # that Lab PC's inventory or .env
scripts/dev/restart.sh central             # Central code or central.env
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

Then run `scripts/dev/redeploy.sh --skip-tests`. Device ids must be unique
across **all** Lab PCs. A removed device stays in Central's table as `stopped`
and not leasable. Run `seed.sh` to clear it, or if you moved a device id to a
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
hash-checked release built on the development PC. Each computer keeps its
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

### 1. Build the release (development PC)

```bash
# bump version = "x.y.z" in every packages/*/pyproject.toml, then:
git commit -am "Release x.y.z"
scripts/release/build.sh                   # add --wheelhouse for offline hosts
```

`build.sh` refuses to run on uncommitted changes or to overwrite an existing
version. It checks generated files, runs the tests, and stages (git-ignored;
the release share is where releases are kept):

```text
PQT_network_releases/x.y.z/
  labnet-x.y.z-py3-none-any.whl   the package (all roles; extras pick dependencies)
  site.zip                        site/: env templates + every Lab PC inventory
  client-examples.zip             examples/: the 01–05 scripts
  update_host.sh                  the installer each runtime computer runs
  RELEASE.TXT                     git revision, version, SHA-256 of every file
```

Copy that directory to the release share (for example
`\\192.168.50.40\LabNet-Releases\x.y.z`).

### 2. Install it on each computer, in dependency order

Stop processes in reverse order (clients, then Lab PCs, then the Control
Center, Caddy, and Central). Then update and start them in this order:
**Central → Control Center → each Lab PC → clients**. On each computer, in
Git Bash:

```bash
R=//192.168.50.40/LabNet-Releases/x.y.z      # or a local copy of the directory
bash $R/update_host.sh central               # on central_server_pc
bash $R/update_host.sh control-center        # on central_server_pc too
bash $R/update_host.sh lab-pc                # on each Lab PC
bash $R/update_host.sh client                # on each client
```

`update_host.sh` handles the steps that are easy to get wrong:

| Step | What it does |
| --- | --- |
| Verify | Compares every file's SHA-256 with `RELEASE.TXT`; stops on any mismatch |
| Safety | Refuses to run while that role still answers on its loopback health port |
| Install | Creates `C:/ProgramData/LabNet/<role>-venv` if needed and installs the wheel with the right extra |
| Central | Backs up the SQLite file named in `central.env` (`labnet.db.before-x.y.z.<time>`), then applies migrations |
| Lab PC | Installs `site/` (the inventories), then loads `lab-pc.env` and imports its inventory, so mistakes appear now rather than at startup |
| Client | Installs the example scripts in `client-app/` |
| Record | Copies `RELEASE.TXT` to `C:/ProgramData/LabNet/<role>-RELEASE.TXT` |

Start the role again (the script prints the command) and check its
`/health/ready`. A Control Center installed before logins existed answers
400 through Caddy until you either set up
[logins](packages/labnet/README.md#logins) or add
`LABNET_CONTROL_CENTER_ALLOWED_HOSTS=pqt-control-center,192.168.50.10` to
`control-center.env`. Then run the
[deployment verification](packages/labnet/README.md#deployment-verification) checklist.

**Rollback:** rerun `update_host.sh` from the previous release directory. The
old `site/` and `client-app/` trees are also kept as `*.previous`. Reinstalling
old code does not undo a schema migration: restore the `labnet.db.before-*`
backup if you need to.

## Script reference

| Script / command | Where it runs | Purpose |
| --- | --- | --- |
| `scripts/dev/setup.sh` | dev machine | Create `.venv`, editable install with `dev,central,lab-pc` extras |
| `scripts/dev/seed.sh` | dev machine | Wipe `.devnet/`, write `central.env`, run `labnet-central-admin setup`, add dev ports and inventories |
| `scripts/dev/up.sh` | dev machine | Start `labnet-central`, every `labnet-lab-pc` in `config.sh`, then `labnet-control-center`; wait for readiness |
| `scripts/dev/status.sh` | dev machine | Component health + Central's device table |
| `scripts/dev/smoke.sh` | dev machine | Lease and read every available device end to end |
| `scripts/dev/runner.sh` (`build`, `login`, `up`, `logs`, `down`) | dev machine (Docker in WSL) | The [Automation runner](packages/labnet/README.md#the-automation-runner) against the dev Control Center |
| `scripts/dev/down.sh` | dev machine | Stop Lab PCs, then Central (found by port; only Python processes) |
| `scripts/dev/codegen.sh [--skip-tests]` | dev machine | Generate protos, `--check`, run pytest |
| `scripts/dev/redeploy.sh [--skip-tests]` | dev machine | `down` → `codegen` → `up` → `smoke` |
| `scripts/dev/restart.sh COMPONENT...` | dev machine | Restart only `central`, `control-center`, or a Lab PC id, leaving the rest running |
| `scripts/dev/config.sh` | — | Dev topology: Lab-PC ids, ports, inventories |
| `scripts/release/build.sh [--wheelhouse] [--output DIR]` | development PC | Build and stage a hash-recorded release |
| `update_host.sh <central\|lab-pc\|client\|control-center>` | each runtime computer | Verify, install, migrate or install site/examples, record |
| `labnet-certs init-ca / issue / show` | development PC | Create the private CA and server certificates |
| `labnet-central-admin setup / user …` | Central | Migrate, create users, write their settings files |
| `scripts/lab/setup_logins.ps1 [-Reset NAME] [-WhatIf]` | Central (PowerShell) | Control Center logins: Caddy snippet with each person's password hash, and the proxy secret in `control-center.env` |
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
| [`site/`](site/README.md) | The PQT lab's configuration: env templates, the Caddyfile template, and Lab PC inventories |
| [`examples/`](examples/README.md) | Experiment scripts 01–05 |
| [`scripts/`](scripts/) | Development, release, and host-update scripts |

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
