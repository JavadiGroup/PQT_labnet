# LabNet package and deployment guide

LabNet provides authenticated, lease-controlled access to laboratory
instruments over a network. It separates management traffic from instrument
traffic:

- Central uses HTTP to manage users, discovery, health, and exclusive leases.
- Lab PCs expose typed instrument operations over gRPC.
- Clients obtain authority from Central and then communicate directly with the
  Lab PC attached to the selected instrument.

The installable Python project is this `Labnet/` directory. Every role is an
installed command that reads one settings file:

| Command | Role | Typical settings file |
| --- | --- | --- |
| `labnet-central --env-file central.env` | Central server | `C:\ProgramData\LabNet\central.env` |
| `labnet-central-admin --env-file central.env …` | Central administration | same file |
| `labnet-lab-pc --env-file lab-pc.env` | Lab PC | `C:\ProgramData\LabNet\lab-pc.env` |
| `LabClient.from_env()` in your script | Client | `C:\ProgramData\LabNet\client.env` (via `LABNET_ENV_FILE`) |
| `labnet-certs …` | Certificate creation (development PC) | none |
| `labnet-control-center --env-file control-center.env` | Web interface ([Control Center](#control-center)) | `C:\ProgramData\LabNet\control-center.env` |

For everyday development workflows (local network, change loop, release
updates), start with the [repository README](../README.md).

## Contents

- [Architecture](#architecture)
- [Runtime safety model](#runtime-safety-model)
- [Installation](#installation)
- [Local setup](#local-setup)
- [Production deployment](#production-deployment)
- [Client programming](#client-programming)
- [Control Center](#control-center)
- [Lease and streaming behavior](#lease-and-streaming-behavior)
- [Central database and API](#central-database-and-api)
- [Configuration reference](#configuration-reference)
- [Adding laboratory instruments](#adding-laboratory-instruments)
- [Development and testing](#development-and-testing)
- [Operations and upgrades](#operations-and-upgrades)
- [Troubleshooting](#troubleshooting)

## Architecture

| Component | Default local endpoint | Responsibility |
| --- | --- | --- |
| Central API | `http://127.0.0.1:8008` | Authentication, discovery, manager heartbeats, leases, and audit records |
| Lab-PC health API | `http://127.0.0.1:9001` | Local liveness and readiness only |
| Lab-PC device API | `127.0.0.1:50051` | Typed gRPC control and streamed measurements |
| Client library | No listener | Central HTTP client, lease lifecycle, and direct gRPC channels |

The normal request path is:

1. A Lab PC constructs its physical devices and connects each driver.
2. It registers its stable ID, device metadata, and advertised gRPC endpoint
   with Central.
3. A client authenticates and requests an exclusive lease for a device.
4. Central returns the device endpoint and an opaque lease ID.
5. The client opens a direct gRPC channel and sends the lease ID in
   `x-labnet-lease-id` metadata.
6. The Lab PC validates the lease with Central before unary operations and
   periodically during streams.

Central never proxies or stores measurement samples. High-rate instrument
traffic therefore never becomes management-plane load, and each Lab PC stays
responsible for hardware timing and driver behavior.

## Runtime safety model

Each physical device owns a bounded worker queue and a dedicated worker
thread. VISA, serial, SDK, and similar blocking calls are therefore ordered
for that instrument without blocking the asynchronous HTTP or gRPC servers.
Independent instruments can still make progress concurrently.

Device contracts and physical implementations are deployment inputs:

- protobuf files are generated before building a release;
- generated files are committed and checked for staleness;
- physical implementations are installed from a versioned wheel;
- the Lab-PC inventory is constructed once at process startup; and
- servers run with source reload disabled.

There is no runtime proto generation, driver replacement, or device-object
hot reload. To change an instrument contract or implementation, build and
deploy a new release while the affected process is stopped.

## Installation

Python 3.11 or newer is required. One wheel serves every role; extras select
the additional dependencies:

| Install | Adds | Used on |
| --- | --- | --- |
| `pip install labnet` | Client library | Client computers |
| `pip install "labnet[lab-pc]"` | FastAPI, Uvicorn, gRPC health | Lab PCs |
| `pip install "labnet[central]"` | FastAPI, SQLAlchemy, Alembic, Uvicorn | Central |
| `pip install "labnet[central,postgres]"` | + psycopg | Central with PostgreSQL |
| `pip install "labnet[certs]"` | cryptography | The computer that issues certificates |
| `pip install "labnet[control-center]"` | FastAPI, Uvicorn | The Control Center (on the Central computer) |
| `pip install -e ".[dev,central,lab-pc]"` | Everything, plus protobuf tools and pytest | Development (`scripts/dev/setup.sh` does this) |

Production hosts install a reviewed wheel, never an editable checkout. In
practice `update_host.sh` creates each role's virtual environment and
installs the wheel for you.

## Local setup

From the repository root, in Git Bash:

```bash
scripts/dev/setup.sh && scripts/dev/seed.sh && scripts/dev/up.sh
export LABNET_ENV_FILE=.devnet/researcher.env
python examples/01_hello_device.py
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

## Production deployment

This walkthrough uses four Windows computers on one private LAN. All addresses
are static and all connections use raw IP addresses; no DNS service is assumed.

| Computer | Static IP | Runs | Required inbound traffic |
| --- | --- | --- | --- |
| `development_pc` | `192.168.50.40` | repository clone, `labnet-certs`, `build.sh` | only the chosen file-transfer method |
| `central_server_pc` | `192.168.50.10` | `labnet-central` behind Caddy | TCP 443 from Lab PCs and clients |
| `lab_pc_1` | `192.168.50.20` | `labnet-lab-pc` | TCP 50051 from approved clients |
| `client` | `192.168.50.30` | experiment scripts | none |

Exclude each address from DHCP, or reserve it for that computer. The
certificates contain the server IPs, so changing a server's IP requires a new
certificate. Each runtime computer needs Python 3.11 and Git for Windows,
whose Git Bash runs `update_host.sh`.

### 1. Prepare `development_pc`

Clone the repository and create the development environment:

```bash
git clone <repository-url> PQT_network && cd PQT_network
scripts/dev/setup.sh
```

#### The TLS model

TLS gives two things: encryption on the wire, and proof that a client
reached the intended server. LabNet uses the minimum set of certificates for
that:

- **One private CA.** Its private key `labnet-ca.key` signs certificates and
  never leaves the signing computer. Its public certificate `labnet-ca.pem` is
  copied everywhere something must be verified.
- **One server certificate per server:** `central.crt` with `central.key` for
  Central (read by Caddy), and `lab-pc-01.crt` with `lab-pc-01.key` for each
  Lab PC (read by `labnet-lab-pc`). Each lists, as its *subject alternative
  name*, the exact IP clients dial.
- **No client certificates.** Users and Lab PCs prove who they are with API
  keys, and leases decide what they may do. TLS only secures and
  authenticates the connection.

| Computer | Files it holds | Why |
| --- | --- | --- |
| `development_pc` (or protected offline storage) | `labnet-ca.key`, `labnet-ca.pem` | Issues certificates |
| Central | `central.crt`, `central.key` | Caddy presents Central's identity |
| Lab PC | `lab-pc.crt`, `lab-pc.key`, `labnet-ca.pem` | Presents its identity; trusts Central |
| Client | `labnet-ca.pem` | Trusts Central and every Lab PC |

`.pem` and `.crt` files are public. `.key` files are secret, and only the
server that uses a key should receive it. **Never copy `labnet-ca.key` to
Central, a Lab PC, a client, a release share, or the repository.**

#### Create the certificates

```powershell
labnet-certs init-ca --dir C:\LabNet-CA          # prompts for a passphrase for labnet-ca.key
labnet-certs issue central   --ip 192.168.50.10 --dir C:\LabNet-CA
labnet-certs issue lab-pc-01 --ip 192.168.50.20 --dir C:\LabNet-CA
labnet-certs show C:\LabNet-CA\lab-pc-01.crt --ca C:\LabNet-CA\labnet-ca.pem
```

`init-ca` creates a ten-year CA. Store its passphrase in the lab's secret
manager: losing it makes the CA unusable, and leaking it together with the key
lets anyone issue trusted certificates. `issue` signs a one-year server
certificate (`--days` changes this; repeat `--ip`/`--dns` for more names) and
refuses to overwrite existing files. `show` prints the names and validity
dates and verifies the signature. Use it before copying anything.

Stage the server files on encrypted removable media or another approved
transfer location (for example `E:\LabNet-TLS`). Remove the server keys from
there once deployed. Afterwards, keep `C:\LabNet-CA` in encrypted offline
storage and restore it only to issue or renew a certificate.

On each server, restrict its `.key` file: open **Properties → Security →
Advanced**, disable inheritance, and leave only Administrators plus the
account that runs Caddy or the Lab PC, with read access.

**Renewal:** issue a replacement with `labnet-certs issue <name> --ip … --force`,
deploy it, and restart Caddy or the Lab PC. The CA stays unchanged.
**Exposure:** if a server key may have leaked, issue a replacement at once.
This minimal CA has no revocation list, so an organizational PKI is the better
choice when revocation or many identities are needed. If the CA key leaks,
create a new CA and redistribute `labnet-ca.pem` before reissuing every server
certificate.

#### Build the release

```bash
# set version = "x.y.z" in Labnet/pyproject.toml and commit, then:
scripts/release/build.sh            # --wheelhouse also bundles every dependency for offline hosts
```

The release directory `PQT_network_releases/x.y.z/` holds the wheel, `site.zip`
(the lab's configuration from `site/`), `client-examples.zip`,
`update_host.sh`, and `RELEASE.TXT` with the SHA-256 of each file. Copy it to
a restricted, read-only share such as `\\192.168.50.40\LabNet-Releases\x.y.z`.
The hashes detect wrong or damaged files; controlling who can write to the
share is what keeps them trustworthy.

A wheel is the installable package archive. It contains the `labnet` package
(Central, Lab PC, client, device families, generated contracts, migrations).
It does **not** contain settings, keys, certificates, databases, the site
configuration, or third-party dependencies (pip fetches those, or takes them
from the `--wheelhouse` folder).

### 2. Set up `central_server_pc`

1. **Certificates.** Copy `central.crt` and `central.key` to
   `C:\ProgramData\LabNet\certs\`.
2. **Settings.** Create `C:\ProgramData\LabNet\central.env` from
   [`site/central.env.example`](../site/central.env.example):

   ```dotenv
   LABNET_DATABASE_URL=sqlite:///C:/ProgramData/LabNet/Central/labnet.db
   LABNET_CENTRAL_BIND_HOST=127.0.0.1
   LABNET_CENTRAL_BIND_PORT=8008
   ```

3. **Install.** In Git Bash, run `update_host.sh`. It verifies the release,
   creates `C:\ProgramData\LabNet\central-venv`, installs the wheel, and
   creates the database schema.

   ```bash
   bash //192.168.50.40/LabNet-Releases/x.y.z/update_host.sh central
   ```

4. **Users and their settings files**, in one command:

   ```powershell
   $admin = "C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe"
   & $admin --env-file C:\ProgramData\LabNet\central.env setup `
       --admin central-admin --user researcher --lab-pc lab-pc-01 `
       --write-env C:\LabNet-Keys `
       --server-url https://192.168.50.10 `
       --ca-file C:/ProgramData/LabNet/certs/labnet-ca.pem
   ```

   This writes `C:\LabNet-Keys\lab-pc-01.env` and `researcher.env` (and
   `central-admin.env`), each with its new API key, Central's URL, and the CA
   path. Central keeps only key hashes, so these files are the only copy:
   - move `lab-pc-01.env` to `lab_pc_1` and `researcher.env` to the client
     over the secure transfer;
   - keep `central-admin.env` for administration;
   - then delete `C:\LabNet-Keys`.

   Running `setup` again later only creates missing users.

5. **Start Central** by hand in its own PowerShell window, and leave that
   window open (it listens on loopback only):

   ```powershell
   C:\ProgramData\LabNet\central-venv\Scripts\labnet-central.exe --env-file C:\ProgramData\LabNet\central.env
   Invoke-RestMethod http://127.0.0.1:8008/health/ready
   ```

6. **HTTPS with Caddy.** Install Caddy and save
   `C:\ProgramData\LabNet\Caddyfile`:

   ```caddyfile
   https://192.168.50.10 {
       tls C:/ProgramData/LabNet/certs/central.crt C:/ProgramData/LabNet/certs/central.key
       reverse_proxy 127.0.0.1:8008
   }
   ```

   Validate it with `caddy validate --config C:\ProgramData\LabNet\Caddyfile`,
   then start it by hand in a second window with
   `caddy run --config C:\ProgramData\LabNet\Caddyfile`. Caddy loads the certificate and key with its
   [`tls` directive](https://caddyserver.com/docs/caddyfile/directives/tls)
   and forwards to Central with
   [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).
   Central's private key never touches the Python process.

7. **Firewall.** Allow inbound TCP 443 from the Lab PCs and approved clients.
   Never open port 8008.
8. **Control Center.** The web interface runs on this same computer; see
   [Control Center](#control-center) for its setup.

### 3. Set up each Lab PC

1. **Certificates.** Copy `lab-pc-01.crt` → `C:\ProgramData\LabNet\certs\lab-pc.crt`,
   `lab-pc-01.key` → `…\lab-pc.key`, and `labnet-ca.pem` → `…\labnet-ca.pem`.
2. **Settings.** Rename the transferred `lab-pc-01.env` to
   `C:\ProgramData\LabNet\lab-pc.env` and add the lines from
   [`site/lab-pc.env.example`](../site/lab-pc.env.example):

   ```dotenv
   # from setup --write-env
   LABNET_LAB_PC_ID=lab-pc-01
   LABNET_SERVER_URL=https://192.168.50.10
   LABNET_DEVICE_MANAGER_API_KEY=labnet_...
   LABNET_CA_FILE=C:/ProgramData/LabNet/certs/labnet-ca.pem
   # added for this computer
   LABNET_ENVIRONMENT=production
   LABNET_GRPC_ADVERTISED_TARGET=192.168.50.20:50051
   LABNET_GRPC_CERT_FILE=C:/ProgramData/LabNet/certs/lab-pc.crt
   LABNET_GRPC_KEY_FILE=C:/ProgramData/LabNet/certs/lab-pc.key
   LABNET_DEVICE_INVENTORY=C:/ProgramData/LabNet/site/inventories/lab_pc_01.py:create_devices
   ```

   The API key switches on Central registration and lease checks. The
   certificate and key switch on TLS, with gRPC listening on all interfaces.
   The advertised target must match the IP in the certificate.
3. **Drivers.** Install the vendor drivers or SDKs, and test each instrument
   locally (for example with `examples/05_local_hardware.py` adapted to its
   family). The inventory module comes from the repository's `site/` directory.
4. **Install.** This installs the wheel and `site/`, then loads `lab-pc.env`
   and imports the inventory to catch mistakes immediately.

   ```bash
   bash //192.168.50.40/LabNet-Releases/x.y.z/update_host.sh lab-pc
   ```

5. **Start the Lab PC** by hand in its own PowerShell window, and leave it open:

   ```powershell
   C:\ProgramData\LabNet\lab-pc-venv\Scripts\labnet-lab-pc.exe --env-file C:\ProgramData\LabNet\lab-pc.env
   Invoke-RestMethod http://127.0.0.1:9001/health/ready
   ```

   Readiness reports the gRPC listener, Central registration, and each device.
6. **Firewall.** Allow inbound TCP 50051 from approved clients only. Port 9001
   stays on loopback.

### 4. Set up a client

1. Copy `labnet-ca.pem` to `C:\ProgramData\LabNet\certs\`, and save the
   transferred `researcher.env` as `C:\ProgramData\LabNet\client.env`.
2. Install the library and the example scripts:

   ```bash
   bash //192.168.50.40/LabNet-Releases/x.y.z/update_host.sh client
   ```

3. Point clients at the settings file once (user environment variable), then
   run the first example:

   ```powershell
   [Environment]::SetEnvironmentVariable("LABNET_ENV_FILE", "C:\ProgramData\LabNet\client.env", "User")
   C:\ProgramData\LabNet\client-venv\Scripts\python.exe C:\ProgramData\LabNet\client-app\01_hello_device.py
   ```

   The script authenticates with Central at `192.168.50.10`, leases a device,
   and connects directly to `lab_pc_1` at `192.168.50.20:50051`. One CA file
   verifies both connections.

### Starting the lab by hand

Every process runs in its own PowerShell window that stays open. Start them
in this order; to stop one, press Ctrl+C in its window.

| Order | Computer | Window | Command |
| --- | --- | --- | --- |
| 1 | `central_server_pc` | Central | `C:\ProgramData\LabNet\central-venv\Scripts\labnet-central.exe --env-file C:\ProgramData\LabNet\central.env` |
| 2 | `central_server_pc` | Caddy | `caddy run --config C:\ProgramData\LabNet\Caddyfile` |
| 3 | `central_server_pc` | Control Center | `C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-control-center.exe --env-file C:\ProgramData\LabNet\control-center.env` |
| 4 | each Lab PC | Lab PC | `C:\ProgramData\LabNet\lab-pc-venv\Scripts\labnet-lab-pc.exe --env-file C:\ProgramData\LabNet\lab-pc.env` |
| 5 | clients | experiments | your scripts, with `LABNET_ENV_FILE` set |

Only Central has to come first; everything else recovers if it starts early.
Lab PCs keep retrying their registration, and the Control Center shows a
banner until Central answers. Before an update, stop the affected windows;
`update_host.sh` refuses to run while that role is still running.

### Network rules summary

- Central: allow TCP 443 from approved Lab PCs and client networks.
- Lab PC: allow TCP 50051 from approved client networks.
- Never expose Central's port 8008 or a Lab PC's port 9001.
- Clients need no inbound rule.
- Where practical, limit outbound traffic: Lab PCs need Central HTTPS; clients
  need Central HTTPS and the gRPC ports of authorized Lab PCs.

### Deployment verification

Verify in this order:

1. Central `/health/live` and `/health/ready` return `status: ok` through HTTPS
   (`curl.exe --cacert …\labnet-ca.pem https://192.168.50.10/health/ready`).
2. Central lists the expected Lab PC devices.
3. The Lab PC's loopback readiness reports gRPC serving and devices available.
4. A client authenticates and lists the expected devices
   (`02_choose_device.py`).
5. The client leases a device and completes one harmless read.
6. A second client cannot lease the same device at the same time.
7. Closing the first client releases the lease and frees the device.

## Client programming

Every client starts from one `LabClient` (or `AsyncLabClient`) per process:

```python
from labnet import DummyDevice, LabClient, LabClientError

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
from labnet import SimulatedWavemeter

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

from labnet import AsyncLabClient, LabClientError, SimulatedWavemeter


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
The [`examples/`](../examples/README.md) directory has five complete scripts.

## Control Center

The PQT Lab Control Center is the lab's web interface. It is a LabNet
client with its own Central user: the browser talks only to it, and it asks
Central for the network picture, so no API key ever reaches a browser.

| Path | Page |
| --- | --- |
| `/` | Redirects to `/status` |
| `/status` | **Network** tab: every Lab PC (online/offline, IP), its devices (available, leased, unavailable), and each device's callable methods from its generated contract |
| `/devices` | **Devices** tab: every device on the network in one list, with its Lab PC, state, and callable methods |
| `/<device-id>` | Device control page, e.g. `/dev02-wavemeter` (see below). The app's own paths (`status`, `devices`, `static`, `api`, `health`) can't be used as device ids for this page. |
| `/api/status` | The same picture as JSON; refreshed from Central at most every `LABNET_CONTROL_CENTER_REFRESH_SECONDS` |
| `/api/devices/<id>/events` | Live events for a device page: state, samples, script output |
| `/api/devices/<id>/play`, `/pause`, `/script`, `/script/stop` | Device page controls |
| `/api/agent`, `/api/agent/messages` | Photo-Agent status, and its chat (answers stream as server-sent events; see below) |

In development `scripts/dev/up.sh` starts it at `http://127.0.0.1:8080/status`.

### Device control pages

Each device page loads the control panel for its device type:
`static/js/panels/<device_type>.js`, or `panels/generic.js` (a script box
only) when a type has none yet. The simulated wavemeter's panel has three
parts:

1. **Live graph.**
   - **Play** leases the device, sends the parameters (exposure first), then
     starts the stream and plots it live.
   - **Pause** stops the stream and releases the lease, so the device is free
     for others.
   - **Play again** starts a fresh graph.
   - The time-window buttons only change how much is shown.
   - **Export CSV** saves every sample since Play.
2. **Parameters.** Exposure, sample rate, and channel are sent when Play is
   pressed, and locked while streaming.
3. **Script box.** Python-style commands that run on the Control Center
   with the page's lease (see below).

The Control Center holds the lease, not the browser. Everyone viewing a
device page shares one session, so anyone viewing it sees the same graph.
The session releases the device:
- when Pause is pressed;
- 15 s after the last viewer closes the page;
- after the maximum hold time (30 min); or
- if the device or lease fails.

If someone else already holds the device, Play reports that it is in use.

**Stopping a page left running.** The Network and Devices pages show who
holds each leased device:
- **Live in the Control Center · 12 min**, **Script running in the Control
  Center**, or **Used by a script on `<page>`**. These come with a **Pause**
  button and an **Open page** link. Pause stops the stream and any script and
  releases every lease that page holds (`POST /api/devices/<id>/release`).
- **Leased by another LabNet client**, for a Python program outside the
  Control Center. Only that program can release it.

Anyone using the Control Center can press Pause, from any computer. A
forgotten tab on another computer can be stopped by whoever notices it.

**Scripts** look like Python but are checked before they run. Allowed:
- variables, numbers, strings, lists, dicts, `if`, and `for`;
- `print`, `sleep`, `mean`, `stdev`, `range`, `len`, `min`, `max`, `round`,
  `abs`, and `sum`;
- the page's device as `device` (its contract methods, e.g.
  `device.set_exposure(50)`);
- other devices through `lease("device-id")`, released when the script ends.

Imports, `def`, `while`, `with`, other attributes, and names starting with
`_` are rejected. Each script runs in its own process, which holds no
credentials. The Control Center performs each device call with its lease,
and Stop or the 10-minute time limit kills the process. Stream methods
inside a script need a `sample_count` (at most 10 000) and return a list of
values.

To add a panel for another device type, copy
`static/js/panels/simulated_wavemeter.js` to `static/js/panels/<device_type>.js`
and register it as `PQT.panels["<device_type>"]`. The shared pieces are
`createChart`, `createScriptBox`, and the session (`session.play`,
`session.pause`, `session.runScript`). The page picks the new panel up
automatically.

### Photo-Agent

The chat sidebar on every page is answered by **Claude Code in headless
mode**. For each message, the Control Center runs `claude -p` and streams the
answer into the chat.
- **Conversations:** the Control Center picks each conversation's id
  (`--session-id`), and follow-ups continue it (`--resume`). The pencil icon
  starts a new chat.
  - If Claude Code no longer has a conversation, the answer starts a new one
    under the same id and says so.
- **Stopping:** the Stop button (the square that replaces Send), or closing
  the page, ends the `claude` process.

**The toolbar under the message box:**
- **Paperclip:** attach files. You can also paste a screenshot or drop files
  onto the box.
  - **Types:** images (PNG, JPG, GIF, WebP), which the model sees; PDFs; and
    text files such as CSV, TSV, TXT, JSON, Python or notebooks.
  - **Limits:** up to 5 files and 20 MB each per message.
  - **Not supported:** Excel and binary formats (HDF5, `.npy`). Export them
    to CSV first.
- **Model:** `--model`, from `LABNET_AGENT_MODELS`. The menu shows the
  version (for example "Sonnet 5.5") once Claude Code has used that model.
- **Effort:** `--effort` (Low, Medium, High, Extra high, Max), meaning how
  much the model thinks before answering. Haiku has no effort levels, so the
  menu is greyed out for it.

Each viewer's model and effort are remembered in their browser.

**Attached files** are saved in `uploads/<conversation id>/` in the
workspace.
- Only that conversation's answers can read them: each run gets `--add-dir`
  for its own folder only.
- They are deleted 14 days after the conversation was last used.

**Account.** It uses the Claude Code login of the Windows account that runs
the Control Center. No API key is used. Any `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN` or `CLAUDE_CODE_OAUTH_TOKEN` is removed from its
environment.
- Set up once, on that computer and as that account: install Claude Code,
  run `claude`, and use `/login`.
- The sidebar badge shows **Ready**, **Sign-in needed**, or **Not
  connected** (Claude Code not found). Hover over it for the reason.
- Anthropic's terms limit sharing one person's subscription with other
  people through an app. Before the whole lab uses it, confirm that your
  plan covers this (for example a Team plan).

**What it can do.** It can read, but not act:
- **Tools:** only Read, Glob and Grep. `--permission-mode dontAsk` denies
  everything else instead of asking. It has no shell, no file edits, no
  device control, none of the account's own MCP connectors, and no memory
  between chats (`CLAUDE_CODE_DISABLE_AUTO_MEMORY`).
- **Folders:**
  - its working folder `home/` in the workspace (`LABNET_AGENT_WORKSPACE`);
  - `LABNET_AGENT_SOURCES`, the LabNet code, docs and examples;
  - `LABNET_AGENT_LITERATURE_DIR`, papers, which it can read as PDFs;
  - the conversation's own uploads.
- **Blocked even inside those folders:** `*.env` files, `*.key` files,
  databases and `logs/`. The Control Center's own Central key is never in its
  environment.
- **Live network:** before every message the Control Center writes
  `home/live/network.md` in the workspace: Lab PCs, devices, states, who
  holds them, and methods. Questions about what is online are answered from
  it.
- **Briefing:** its role and the lab's script style (`from_env()`,
  try/except/finally, release in reverse order) are in
  [`agent_briefing.md`](labnet/control_center/agent_briefing.md).

It writes scripts for people to run. It never runs them.

In development, `scripts/dev/seed.sh` points it at this checkout's `Labnet/`
and `examples/`, and at `literature/` at the repository root (git-ignored;
drop papers there). Its workspace is `.devnet/agent`.

In the lab it runs on **`central_server_pc`**, next to Central and Caddy:
- **Central over loopback:** it reaches Central at `http://127.0.0.1:8008` on
  the same computer, so it needs no CA file and doesn't depend on Caddy.
- **Browsers through Caddy:** Caddy serves the page to the rest of the lab
  over HTTPS.

The load is small: one Central request every few seconds, however many
people have the page open.

1. **Central user.** On `central_server_pc`, create it and its settings file:

   ```powershell
   & $admin --env-file C:\ProgramData\LabNet\central.env setup --user control-center `
       --write-env C:\LabNet-Keys --server-url http://127.0.0.1:8008
   ```

   Move `control-center.env` to `C:\ProgramData\LabNet\control-center.env` and
   add `LABNET_CONTROL_CENTER_PORT=8080`, as in
   [`site/control-center.env.example`](../site/control-center.env.example).
2. **Install.** On the same computer, run
   `bash <release>/update_host.sh control-center`. It installs into
   `control-center-venv` and checks that the settings file loads.
3. **Address.** Choose how the lab reaches the page:

   | | `https://192.168.50.10:8443/status` | `https://pqt-control-center/status` |
   | --- | --- | --- |
   | Certificate | Reuse `central.crt` (it already covers 192.168.50.10) | On the development PC: `labnet-certs issue control-center --dns pqt-control-center --ip 192.168.50.10 --dir C:\LabNet-CA`, then copy the `.crt` and `.key` next to Central's |
   | Caddyfile site | `https://192.168.50.10:8443` with Central's `tls` line | `https://pqt-control-center` with the new `tls` line |
   | Firewall | Allow inbound TCP 8443 | Nothing new (443 is already open) |
   | Viewing computers | Nothing extra | Add `192.168.50.10  pqt-control-center` to `C:\Windows\System32\drivers\etc\hosts` (the lab has no DNS) |

   Either site block forwards to the Control Center:

   ```caddyfile
   https://pqt-control-center {          # or: https://192.168.50.10:8443
       tls C:/ProgramData/LabNet/certs/control-center.crt C:/ProgramData/LabNet/certs/control-center.key
       reverse_proxy 127.0.0.1:8080
   }
   ```

4. **Browser trust.** Python clients use `LABNET_CA_FILE`, but browsers use the
   Windows certificate store. Install the CA once on each viewing computer
   with `certutil -addstore Root labnet-ca.pem`, or the page shows a
   certificate warning.
5. **Start** it by hand in its own PowerShell window on `central_server_pc`:

   ```powershell
   C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-control-center.exe --env-file C:\ProgramData\LabNet\control-center.env
   ```

   If Central isn't running yet, the page shows a "can't reach Central"
   banner and recovers on its own once Central starts.
6. **Check it from a viewing computer**, not the server:
   `curl.exe https://pqt-control-center/health/ready`, which returns 200 when
   the Control Center is up *and* reaching Central.

Use hyphens in the name: `pqt_control_center` with underscores is not a
valid host name, and certificate tools reject it. The built-in server only
binds to loopback. Caddy makes it reachable, over HTTPS, so adding logins
later never sends keys in clear text.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | Central URL; keep the loopback default when it runs on the Central PC |
| `LABNET_API_KEY` | required | The Control Center's own Central key (role `user`) |
| `LABNET_CA_FILE` | system trust | Only needed if it ever runs on a different computer and reaches Central over HTTPS |
| `LABNET_CONTROL_CENTER_HOST` / `_PORT` | `127.0.0.1` / `8080` | Built-in HTTP listener; loopback only |
| `LABNET_CONTROL_CENTER_REFRESH_SECONDS` | `3` | How often the network picture is refreshed |
| `LABNET_CONTROL_CENTER_MAX_HOLD_MINUTES` | `30` | Play stops itself and releases the device after this long |
| `LABNET_CONTROL_CENTER_IDLE_RELEASE_SECONDS` | `15` | A device page releases its device this long after the last viewer leaves |
| `LABNET_CONTROL_CENTER_SCRIPT_TIMEOUT_SECONDS` | `600` | A script is stopped after this long |
| `LABNET_AGENT_COMMAND` | `claude` on `PATH`, then `~\.local\bin\claude.exe` | Claude Code for Photo-Agent; if it can't be found, the chat says so |
| `LABNET_AGENT_WORKSPACE` | `agent` next to the env file | Photo-Agent's folder: `briefing.md`, `home/` (where it runs) and `uploads/` |
| `LABNET_AGENT_SOURCES` | the installed `labnet` package | Folders it may read, separated by `;` on Windows. In the lab, list the package folder plus any folder where you unpacked the examples (`client-examples.zip`) or lab notes |
| `LABNET_AGENT_LITERATURE_DIR` | none | Folder of papers it may read |
| `LABNET_AGENT_MODELS` | `fable,opus,sonnet,haiku` | The chat's model menu: aliases for the latest of each model, or full ids such as `claude-opus-5-5`. List only models your plan includes |
| `LABNET_AGENT_MODEL` | `sonnet` | The model selected at first; one of `LABNET_AGENT_MODELS` |
| `LABNET_AGENT_EFFORT` | `high` | The effort selected at first: `low`, `medium`, `high`, `xhigh` or `max` |
| `LABNET_AGENT_TIMEOUT_SECONDS` | `300` | An answer is stopped after this long |
| `LABNET_AGENT_MAX_CONCURRENT` | `2` | Answers in progress at once; later messages wait their turn |

## Lease and streaming behavior

Clients use a 60-second lease by default and renew it every 20 seconds while
the device object is healthy. Closing a device releases its lease at once. If
a process crashes or loses connectivity, renewal stops and Central expires the
lease.

Central also expires a lease after 900 seconds without an authorized device
command. Renewal proves the client is alive but does not count as instrument
activity, so an abandoned program cannot hold an instrument forever. A live
stream counts as activity through its periodic lease revalidation. Keep the
idle timeout longer than both the stream revalidation interval and the
longest valid hardware operation.

Streams return scalar measurements. A stream can specify a channel, sample
rate, sample count, and duration; the first limit reached ends the stream.
Each sample has an operation ID, a monotonically increasing sequence number,
a Unix timestamp in nanoseconds, a double value, a unit, quality and status,
and an optional channel. The default 1 kHz ceiling suits telemetry and
supervisory control, not hard real-time feedback: keep stability-critical
loops on the Lab PC or dedicated hardware.

## Central database and API

Central stores users, API-key hashes, Lab PC and device registrations,
leases, idempotency records, and audit events. It never stores measurements.
SQLite suits one Central process; a database constraint enforces one active
lease per device. PostgreSQL uses the same models and migrations and suits
several workers or replicas.

Normal startup never creates or changes tables. Apply migrations explicitly
(`update_host.sh central` and `setup` both do):

```powershell
labnet-central-admin --env-file central.env db upgrade
```

| Task | Command (after `labnet-central-admin --env-file central.env`) |
| --- | --- |
| Migrate, create missing users, write settings files | `setup [--admin N] [--user N] [--lab-pc ID] [--write-env DIR --server-url URL [--ca-file PATH]]` |
| Show schema revision | `db current` |
| List users (never prints keys) | `user list` |
| Create one user and print its key | `user create NAME --role user\|admin\|device_manager` |
| Replace a key (the old one stops working at once) | `user rotate-key NAME` |

Important HTTP endpoints:

- `GET /health/live`, `GET /health/ready`
- `GET /api/v1/auth/me`
- `POST /api/v1/device-managers/register`, `POST /api/v1/device-managers/heartbeat`
- `GET /api/v1/devices`, `GET /api/v1/devices/{device_id}`
- `POST /api/v1/leases`, `POST /api/v1/leases/{lease_id}/renew`,
  `DELETE /api/v1/leases/{lease_id}`, `POST /api/v1/leases/{lease_id}/validate`

## Configuration reference

Every setting can be given in the settings file or the process environment.
The process environment wins, so a service manager can override any line.

### Central settings (`central.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_DATABASE_URL` | per-user SQLite file | SQLAlchemy database URL |
| `LABNET_CENTRAL_BIND_HOST` | `127.0.0.1` | HTTP bind; must be loopback |
| `LABNET_CENTRAL_BIND_PORT` | `8008` | HTTP port behind Caddy |
| `LABNET_LEASE_MIN_SECONDS` / `_MAX_SECONDS` | `5` / `86400` | Accepted lease durations |
| `LABNET_LEASE_DEFAULT_IDLE_TIMEOUT_SECONDS` | `900` | Lease ends after this long without a device command |
| `LABNET_HEARTBEAT_TIMEOUT_SECONDS` | `30` | A silent Lab PC is marked unhealthy after this |
| `LABNET_MAINTENANCE_INTERVAL_SECONDS` | `5` | How often expired leases and silent Lab PCs are swept |
| `LABNET_CREATE_SCHEMA_ON_STARTUP` | `false` | Test convenience only; use migrations instead |

### Lab-PC settings (`lab-pc.env`)

Two inputs decide how a Lab PC runs:

| You set | Effect |
| --- | --- |
| `LABNET_DEVICE_MANAGER_API_KEY` | Registers with Central and validates every lease. Without it the Lab PC is standalone: leases are not checked, so it is allowed only on loopback and never in `production`. |
| `LABNET_GRPC_CERT_FILE` + `LABNET_GRPC_KEY_FILE` | Serves gRPC over TLS and listens on all interfaces (`0.0.0.0`). Without them gRPC is plaintext and loopback-only. Setting only one is an error. |

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_ENVIRONMENT` | `development` | `production` additionally requires a key and an inventory |
| `LABNET_LAB_PC_ID` | the username that owns the key | Stable Central identity; looked up from Central at startup when omitted |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | Central URL; non-loopback must be `https://` |
| `LABNET_CA_FILE` | system trust | CA that verifies Central's HTTPS certificate |
| `LABNET_DEVICE_INVENTORY` | one simulated device (development only) | `package.module:function` or `C:/path/file.py:function` |
| `LABNET_GRPC_ADVERTISED_TARGET` | the bind address | `host:port` clients dial; required with TLS; must be in the certificate |
| `LABNET_GRPC_BIND_HOST` / `LABNET_GRPC_PORT` | see above / `50051` | gRPC listener |
| `LABNET_GRPC_TLS_SERVER_NAME` | none | Name clients verify, when it differs from the target host |
| `LABNET_LAB_PC_HOST` | host of the advertised target | Label reported to Central |
| `LABNET_HTTP_BIND_HOST` / `LABNET_HTTP_PORT` | `127.0.0.1` / `9001` | Health API; loopback only |

Relative paths in a settings file are relative to that file. Tuning settings
keep sensible defaults: `LABNET_HEARTBEAT_INTERVAL_SECONDS` (5),
`LABNET_DEVICE_QUEUE_CAPACITY` (128), `LABNET_DEVICE_START_TIMEOUT_SECONDS` (10),
`LABNET_DEFAULT_OPERATION_TIMEOUT_SECONDS` (30), `LABNET_STREAM_MAX_RATE_HZ`
(1000), `LABNET_STREAM_MAX_SAMPLES` (1000000), `LABNET_STREAM_MAX_DURATION_SECONDS`
(3600), `LABNET_STREAM_LEASE_REVALIDATION_SECONDS` (5),
`LABNET_GRPC_MAX_RECEIVE_BYTES` / `_SEND_BYTES` (4 MiB).

### Client settings (`client.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | Central URL |
| `LABNET_API_KEY` | required | A `user` or `admin` key |
| `LABNET_CA_FILE` | system trust | Verifies Central and every Lab PC |
| `LABNET_ENV_FILE` | none | Process environment only: which file `from_env()` loads |

### Removed in 2.0

Each removed setting now stops startup with a message naming its replacement.

| 1.x | 2.0 |
| --- | --- |
| `LABNET_CENTRAL_ENABLED`, `LABNET_LEASE_VALIDATION_MODE` | Set `LABNET_DEVICE_MANAGER_API_KEY`; omit it for standalone development |
| `LABNET_GRPC_TLS_MODE` | Set `LABNET_GRPC_CERT_FILE` and `LABNET_GRPC_KEY_FILE` |
| `LABNET_LAB_PC_ENV_FILE` | `labnet-lab-pc --env-file PATH` |
| `LABNET_GRPC_CA_FILE` (clients), `SSL_CERT_FILE` (for LabNet) | `LABNET_CA_FILE` |
| `python -m examples.central_server` / `examples.lab_pc` | `labnet-central` / `labnet-lab-pc` |
| `examples/lab_pc/device_inventory.py` | `site/inventories/<lab pc>.py` + `LABNET_DEVICE_INVENTORY` |
| `lab-pc-app.zip`, `sync-client-example.zip` | `site.zip`, `client-examples.zip` |
| Keys printed by `user create`, copied by hand | `setup --write-env` writes ready settings files |
| OpenSSL commands | `labnet-certs` |

## Adding laboratory instruments

### Another instrument of an existing family

No code generation is needed:

1. Install the vendor driver on the Lab PC and confirm the family's driver
   supports the model.
2. Add an instance with a stable, network-wide unique `device_id` to that Lab
   PC's inventory in `site/inventories/`.
3. Release, run `update_host.sh lab-pc`, and restart the Lab PC.
4. Confirm Central lists the device and a client can lease it.

### A new device family

`SimulatedWavemeter` is the reference. It has its own `.proto`, a driver, a
three-line facade, and exports. The steps are in the
[repository README](../README.md#adding-a-new-device-family). In short:

1. **Define the contract** in `protos/labnet/devices/v1/<family>.proto`. Every
   RPC declares its pattern, physical method, and default unit (use `"1"` for
   dimensionless values):

   | Pattern | RPC shape | Physical behavior |
   | --- | --- | --- |
   | `RPC_PATTERN_SCALAR_READ` | Unary | One physical read, returned as a scalar measurement |
   | `RPC_PATTERN_SCALAR_WRITE` | Unary | An absolute double setpoint, returned as a scalar measurement |
   | `RPC_PATTERN_UNARY_COMMAND` | Unary | Any driver method; its return value comes back as a typed `CommandResult` (none, bool, integer, float, text, or integer list) |
   | `RPC_PATTERN_POLLED_SCALAR_STREAM` | Server streaming | Repeated physical reads with scheduling, cancellation, lease revalidation, and backpressure |

2. **Generate** with `python tools/generate_protos.py` (or
   `scripts/dev/codegen.sh`). Never edit generated files.
3. **Implement `physical.py`** with a stable `device_id`, the generated
   `device_type`, `connect`, `disconnect`, `status`, `availability`, JSON-compatible
   `metadata`, and every physical method the contract names. Optional proto
   arguments need Python defaults. Extra required arguments are rejected
   when the Lab PC starts.
4. **Add the facade** (`device.py`), which binds the contract to the driver,
   and export it from `labnet/__init__.py` and `labnet/devices/__init__.py`.
   Do not write family-specific gRPC or transport code; dispatch,
   authorization, errors, leases, and streams are shared.
5. **Validate:** `python tools/generate_protos.py --check` and `python -m pytest`.

Deploy Lab PCs that serve a new RPC before clients that call it.

### Contract compatibility

Once a contract is deployed:

- never change the number or type of an existing protobuf field;
- add new optional fields with unused numbers;
- reserve removed field numbers and names;
- keep existing RPC semantics while older clients may call them; and
- introduce a new protobuf package for breaking changes.

## Development and testing

From `Labnet/`:

```cmd
python tools/generate_protos.py --check
python -m pytest
```

The suite covers Central storage, leases, and the admin CLI; client renewal
and settings; generated contracts and both device families; device workers;
TLS policy and real TLS handshakes with `labnet-certs` certificates; Lab PC
settings rules; and a full Central-to-Lab-PC-to-client stack. It also runs
every script in `examples/` against real servers.

`scripts/dev/codegen.sh` regenerates, checks, and tests in one step.
Generated files are pinned to LF line endings in `.gitattributes` because
`--check` compares bytes.

## Operations and upgrades

Start components in this order: Central (after any migration) and Caddy,
then Lab PCs, then clients. For an upgrade:

1. build and test a new release (`scripts/release/build.sh`);
2. keep the previously approved release directory;
3. stop the affected processes in reverse order;
4. run `update_host.sh <role>` on each computer, which installs the wheel
   and, on Central, backs up SQLite and migrates;
5. restart in dependency order; and
6. repeat the deployment verification checks.

Roll back code by stopping the process and running `update_host.sh` from the
previous release. Reinstalling old code does not reverse a schema migration;
restore the `labnet.db.before-<version>.<time>` backup for that.

## Troubleshooting

| Symptom | Likely cause | Check |
| --- | --- | --- |
| A command stops with "… was removed in LabNet 2.0" | A 1.x setting in a settings file | Apply the replacement in [Removed in 2.0](#removed-in-20) |
| Central readiness is 503 | Missing migration or unreachable database | `labnet-central-admin --env-file central.env db upgrade`; check `LABNET_DATABASE_URL` |
| Admin CLI says the schema is not ready | CLI and server read different databases | Pass the same `--env-file` to both |
| Lab PC cannot register | Wrong key, or Central's certificate not trusted | Recreate or rotate the device_manager key; check `LABNET_CA_FILE` |
| Lab PC refuses a LAN address | No certificate, or no API key | Set both TLS files and `LABNET_DEVICE_MANAGER_API_KEY` |
| Lab PC refuses to start in production | No inventory or no key | Set `LABNET_DEVICE_INVENTORY` and `LABNET_DEVICE_MANAGER_API_KEY` |
| Client sees no available devices | Lab PC down, device unhealthy, or every device leased | Both health endpoints and Central's device list |
| Client times out connecting to a TLS Lab PC | CA not configured, or IP not in the Lab PC certificate | `LABNET_CA_FILE`; compare `labnet-certs show` with `LABNET_GRPC_ADVERTISED_TARGET` |
| Proto check reports stale files | Generated bindings do not match the protos | `python tools/generate_protos.py`, then commit every generated change |
| Physical contract validation fails | Driver method missing or with an incompatible signature | Compare `physical_method` and forwarded fields with `physical.py` |
| Lease lost during an experiment | Renewal failure, lease deadline, or idle timeout | Client and Central logs; network stability; timeout policy |

## Credential handling

Settings files, private keys, caches, build outputs, and runtime databases are
ignored by Git. Keep each settings file only on the computer that uses it,
restrict its permissions, and rotate any exposed API key or private key
immediately.
