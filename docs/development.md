# Development

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
[`labnet/dev/network.py`](https://github.com/mill0851/labnet/blob/main/packages/labnet/src/labnet/dev/network.py), add an inventory under
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
[Device control pages](control-center/index.md#device-control-pages)).

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
[Automation](automation/index.md#automation). To bring it into use in the lab,
follow the [Automation go-live checklist](deploying/go-live.md#automation-go-live-checklist).

The **Photo-Agent** sidebar is Claude Code: ask it about LabNet, attach data
or papers, or ask it to set up an experiment. It proposes a diagram you can
apply to the canvas. See
[Experiments](control-center/index.md#experiments) and
[Photo-Agent](control-center/photo-agent.md).

The Control Center is just another LabNet client with its own Central user,
so the browser never sees an API key. In both development and the lab it
runs on the same computer as Central and reaches it over loopback. Only the
settings file's location and the address people open differ:

| | Development | Lab (on `central_server_pc`) |
| --- | --- | --- |
| Settings | `.devnet/control-center.env` (written by `labnet dev seed`) | `C:\ProgramData\LabNet\control-center.env` |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | `http://127.0.0.1:8008` (same computer as Central) |
| Started by | `labnet dev up` | you, in its own window (see [starting the lab by hand](deploying/index.md#starting-the-lab-by-hand)) |
| Address | `http://127.0.0.1:8080/status` | `https://lab-control-center/status` or `https://192.168.50.10:8443/status`, via Caddy |
| Logins | none | One per person, checked by Caddy (`scripts/lab/setup_logins.ps1`; see [Logins](control-center/logins.md#logins)) |

Use `lab-control-center`, with hyphens: underscores are not valid in host
names or certificates. Setup is in
[Control Center](control-center/index.md).

### Using the dev network from your own code

Point clients at the researcher file once, then run any script:

```bash
export LABNET_ENV_FILE=.devnet/researcher.env        # PowerShell: $env:LABNET_ENV_FILE = ".devnet\researcher.env"
python examples/01_hello_device.py
python examples/04_scan_two_devices.py               # leases devices on both Lab PCs
```

[`examples/README.md`](https://github.com/mill0851/labnet/blob/main/examples/README.md) describes all five examples.

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

## Running the pieces by hand

From the repository root:

```bash
uv sync
uv run labnet dev up
export LABNET_ENV_FILE=.devnet/researcher.env      # PowerShell: $env:LABNET_ENV_FILE = ".devnet\researcher.env"
uv run python examples/01_hello_device.py
```

This runs Central and two simulated Lab PCs on loopback. The equivalent
manual steps are:

```bash
labnet-central-admin --env-file central.env setup --user researcher --lab-pc lab-pc-dev-01 \
    --write-env . --server-url http://127.0.0.1:8008
labnet-central --env-file central.env                 # terminal 1
labnet-lab-pc --env-file lab-pc-dev-01.env            # terminal 2 (serves one simulated device)
LABNET_ENV_FILE=researcher.env python examples/01_hello_device.py
```

Here `central.env` contains a single line such as
`LABNET_DATABASE_URL=sqlite:///C:/tmp/labnet/central.db`.

## Tests and checks

From the repository root (`uv sync` installs every package and the tools):

```cmd
uv run labnet driver build --core --check --import-check
uv run lint-imports
uv run pytest
```

The suite covers Central storage, leases, and the admin CLI; client renewal
and settings; generated contracts and both device families; device workers;
TLS policy and real TLS handshakes with `labnet-certs` certificates; Lab PC
settings rules; and a full Central-to-Lab-PC-to-client stack. It also runs
every script in `examples/` against real servers.

`labnet driver build --core` regenerates LabNet's own protos (a driver
package runs `labnet driver build` in its folder). Generated files are pinned to LF line endings in `.gitattributes` because
`--check` compares bytes.

## Repository layout

| Path | Purpose |
| --- | --- |
| [`packages/`](https://github.com/mill0851/labnet/tree/main/packages) | The installable packages: `labnet` ([full guide](concepts.md)), `labnet-control-center`, `labnet-automation` and `labnet-sim` |
| [`packages/labnet/src/labnet/protos/`](https://github.com/mill0851/labnet/tree/main/packages/labnet/src/labnet/protos) | LabNet's shared protobuf messages and RPC options |
| [`packages/labnet/src/labnet/driver/`](https://github.com/mill0851/labnet/tree/main/packages/labnet/src/labnet/driver) | `labnet driver build` and `new`: contract validator, code generator, driver template |
| [`packages/labnet-sim/`](https://github.com/mill0851/labnet/tree/main/packages/labnet-sim) | The simulated device families: a complete driver package |
| [`packages/labnet/src/labnet/lab_pc/`](https://github.com/mill0851/labnet/tree/main/packages/labnet/src/labnet/lab_pc) | The `labnet-lab-pc` runtime and its settings rules |
| [`packages/labnet-control-center/src/labnet_control_center/`](https://github.com/mill0851/labnet/tree/main/packages/labnet-control-center/src/labnet_control_center) | The `labnet-control-center` web interface (backend + `static/` pages) |
| `packages/*/tests/`, [`tests/`](https://github.com/mill0851/labnet/tree/main/tests) | Each package's tests; full-stack and example tests |
| [`sites/pqt/`](deploying/sites.md) | A lab's configuration (here the PQT lab's): env templates, the Caddyfile template, and Lab PC inventories |
| [`examples/`](https://github.com/mill0851/labnet/blob/main/examples/README.md) | Experiment scripts 01–05 |
| [`scripts/lab/`](https://github.com/mill0851/labnet/tree/main/scripts/lab) | `setup_logins.ps1`, run on Central to manage Control Center logins |
