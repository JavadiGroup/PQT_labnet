# LabNet package and deployment guide

LabNet provides authenticated, lease-controlled access to laboratory
instruments over a network. It separates management traffic from instrument
traffic:

- Central uses HTTP to manage users, discovery, health, and exclusive leases.
- Lab PCs expose typed instrument operations over gRPC.
- Clients obtain authority from Central and then communicate directly with the
  Lab PC attached to the selected instrument.

The installable packages are in `packages/`: `labnet` (this one), `labnet-control-center`
and `labnet-automation`. Every role is an installed command that reads one settings file:

| Command | Role | Typical settings file |
| --- | --- | --- |
| `labnet-central --env-file central.env` | Central server | `C:\ProgramData\LabNet\central.env` |
| `labnet-central-admin --env-file central.env …` | Central administration | same file |
| `labnet-lab-pc --env-file lab-pc.env` | Lab PC | `C:\ProgramData\LabNet\lab-pc.env` |
| `LabClient.from_env()` in your script | Client | `C:\ProgramData\LabNet\client.env` (via `LABNET_ENV_FILE`) |
| `labnet-certs …` | Certificate creation (development PC) | none |
| `labnet-control-center --env-file control-center.env` | Web interface ([Control Center](#control-center)) | `C:\ProgramData\LabNet\control-center.env` |

For everyday development workflows (local network, change loop, release
updates), start with the [repository README](../../README.md).

## Contents

- [Architecture](#architecture)
- [Runtime safety model](#runtime-safety-model)
- [Installation](#installation)
- [Local setup](#local-setup)
- [Production deployment](#production-deployment)
- [Automation go-live checklist](#automation-go-live-checklist)
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
| `pip install "labnet[control-center]"` | FastAPI, Uvicorn, pypdf | The Control Center (on the Central computer) |
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
# set version = "x.y.z" in every packages/*/pyproject.toml and commit, then:
scripts/release/build.sh            # --wheelhouse also bundles every dependency for offline hosts
```

The release directory `PQT_network_releases/x.y.z/` (git-ignored) holds the wheel, `site.zip`
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
   [`site/central.env.example`](../../site/central.env.example):

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
   [`site/Caddyfile.example`](../../site/Caddyfile.example) is the whole file,
   with the Control Center's site and its logins added (see
   [Control Center](#control-center)).

7. **Firewall.** Allow inbound TCP 443 from the Lab PCs and approved clients.
   Never open port 8008.
8. **Control Center.** The web interface runs on this same computer; see
   [Control Center](#control-center) for its setup.
9. **Safe-state watchdog.** Applies an Automation grant's safe state if the
   Control Center dies mid-grant; see
   [The safe-state watchdog](#the-safe-state-watchdog) for its user,
   `watchdog.env` and how to start it.

### 3. Set up each Lab PC

1. **Certificates.** Copy `lab-pc-01.crt` → `C:\ProgramData\LabNet\certs\lab-pc.crt`,
   `lab-pc-01.key` → `…\lab-pc.key`, and `labnet-ca.pem` → `…\labnet-ca.pem`.
2. **Settings.** Rename the transferred `lab-pc-01.env` to
   `C:\ProgramData\LabNet\lab-pc.env` and add the lines from
   [`site/lab-pc.env.example`](../../site/lab-pc.env.example):

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
| 4 | `central_server_pc` | Safe-state watchdog | `C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-watchdog.exe --env-file C:\ProgramData\LabNet\watchdog.env` (or its [Task Scheduler task](#the-safe-state-watchdog)) |
| 5 | each Lab PC | Lab PC | `C:\ProgramData\LabNet\lab-pc-venv\Scripts\labnet-lab-pc.exe --env-file C:\ProgramData\LabNet\lab-pc.env` |
| 6 | clients | experiments | your scripts, with `LABNET_ENV_FILE` set |

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

## Automation go-live checklist

Use this to bring the [Automation](#automation) tab into use on the lab
network: the agent, its runner, approvals and device control. Work through
it in order. Each step links to the full instructions. Here the runner runs
in Docker inside WSL on `central_server_pc`. The steps for a dedicated Linux
runner PC are the same from step 5 on (see
[In the lab: the runner PC](#in-the-lab-the-runner-pc)).

Before you start, the network itself must already be deployed and verified
([Production deployment](#production-deployment)): Central, Caddy, the
Control Center and every Lab PC.

### 1. Decide first

- [ ] **Which instruments may ever be automated**, and the safe range of
      every number the agent may set on them. These become site limits in
      step 3. An instrument with no limit for a number can't be automated
      ("no limit, no automation").
- [ ] **Who approves.** Any signed-in lab member can approve a protocol.
      Agree who should, and that approvers read the review card's numbers,
      not the agent's description.
- [ ] **The Claude account** the runner signs in with is the lab's, and its
      plan allows automated use by the whole lab.
- [ ] **IT agrees** to WSL 2 and Docker Engine on `central_server_pc`, and
      virtualization is enabled in its firmware.
- [ ] **The first instrument** is one where a wrong value is harmless.

### 2. Control Center: logins and settings

On `central_server_pc`:

- [ ] Update the Control Center to this release (`update_host.sh
      control-center`; see [Operations and upgrades](#operations-and-upgrades)).
- [ ] In `C:\ProgramData\LabNet\control-center.env`, check:
      - `LABNET_AUTOMATION_DIR`, and that the folder is backed up;
      - `LABNET_AGENT_LITERATURE_DIR` (the lab library).

      The run limits keep their defaults unless you have a reason to change
      them ([Control Center](#control-center) settings table).
- [ ] **Logins:**
      1. List the lab members in `C:\ProgramData\LabNet\logins.txt`.
      2. Run `scripts/lab/setup_logins.ps1` and hand out the passwords it
         prints ([Logins](#logins)).
- [ ] The Caddyfile starts with `{ admin off }`, imports the logins snippet,
      and the Control Center's site block uses `import labnet_control_center`
      ([`site/Caddyfile.example`](../../site/Caddyfile.example)).
- [ ] Restart Caddy, then the Control Center.
- [ ] **Check:**
      - opening the Control Center from another computer asks for a login,
        and the top bar shows the signed-in name;
      - `curl.exe http://127.0.0.1:8080/status` on the server is refused
        (403).

### 3. Lab PCs: site limits

On each Lab PC whose instruments may be automated:

- [ ] In its inventory (`site/inventories/<lab-pc>.py`), wrap each such
      device in `site_limits(...)` with the ranges from step 1
      ([Site limits](#site-limits)). Narrow the driver's limits; never copy
      them unexamined.
- [ ] Restart the Lab PC. It refuses to start if a site limit is wider than
      what the driver allows, or names an unknown argument; fix the inventory
      and restart.
- [ ] **Check:**
      - on the Network page, the device's ranges show "(site limit)";
      - a client call just outside a limit is refused with
        `ARGUMENT_OUTSIDE_LIMITS`.

### 4. The safe-state watchdog

On `central_server_pc`, following
[The safe-state watchdog](#the-safe-state-watchdog):

- [ ] Create its Central user (`labnet-central-admin setup --user
      automation-watchdog …`).
- [ ] Put its settings in `C:\ProgramData\LabNet\watchdog.env`, as in
      [`site/watchdog.env.example`](../../site/watchdog.env.example).
- [ ] Start it next to the Control Center. Then register its Task Scheduler
      task, so it also starts at logon.
- [ ] **Check:** `http://127.0.0.1:8081/health/ready` answers 200 on the
      server.

### 5. The runner: Docker in WSL on the Central PC

The detailed steps are in [The Automation runner](#the-automation-runner).
On `central_server_pc`:

- [ ] Install WSL 2 with Ubuntu 24.04 (`wsl --install -d Ubuntu-24.04`).
      Then install Docker Engine in it
      ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)).
      Don't add people to the `docker` group: it is root-equivalent.
- [ ] Turn on **mirrored networking**, so the runner reaches the Control
      Center through Caddy at the Central PC's own address:
      1. Put `networkingMode=mirrored` under `[wsl2]` in
         `%UserProfile%\.wslconfig`.
      2. Run `wsl --shutdown`.
- [ ] **Keep WSL running.** Windows stops WSL, and with it Docker and the
      runner, about a minute after the last Windows program using WSL
      closes. Register a task that keeps one hidden WSL process open from
      logon:

      ```powershell
      $action  = New-ScheduledTaskAction -Execute "wsl.exe" `
          -Argument '-d Ubuntu-24.04 -e bash -c "exec -a labnet-runner-keepalive sleep infinity"'
      $trigger = New-ScheduledTaskTrigger -AtLogOn
      Register-ScheduledTask -TaskName "LabNet runner keep-alive" -Action $action -Trigger $trigger
      ```

- [ ] Inside WSL:
      1. Copy the repository to `/opt/labnet-src`.
      2. Copy `labnet-ca.pem` to `/etc/labnet/labnet-ca.pem`.
      3. Check that `pqt-control-center` resolves. WSL copies the Windows
         hosts file unless it has been told not to.
- [ ] **A runner token:** `labnet-control-center-admin … runner add
      central-runner` ([Runners and their tokens](#runners-and-their-tokens)).
- [ ] Write `/etc/labnet/runner.env`, owned by root with mode 600. It needs
      the HTTPS address, the token and the CA file
      ([In the lab: the runner PC](#in-the-lab-the-runner-pc), step 6).
      Don't set `LABNET_RUNNER_DEV`.
- [ ] Build, sign in, check, start. Run each in `/opt/labnet-src`, with
      `sudo docker compose -f packages/labnet-automation/docker/compose.yaml …` in front:
      1. `build`
      2. `--profile login run --rm login`: in Claude, `/login` with the lab
         account, then `/exit`.
      3. `run --rm --no-deps runner labnet-runner self-check`: every line
         must say `ok`.
      4. `up -d egress runner`
- [ ] Optional: limit the runner API to the runner's address with
      `setup_logins.ps1 -RunnerAddress <address>` ([Logins](#logins)). Check
      the address Caddy's log shows for runner requests first.
- [ ] **Check:** `sudo docker compose -f packages/labnet-automation/docker/compose.yaml logs
      runner` shows the self-check passing, then "polling".

### 6. A first supervised run

- [ ] On the Automation tab, press **Index library** and wait for the count.
      Note any papers flagged without a text layer
      ([Literature search](#literature-search)).
- [ ] **Create a test project:**
      - link an experiment canvas that holds the harmless instrument;
      - tick it under **Allowed devices**;
      - add a short task in `Tasks/`.
- [ ] Press **Run**. Watch the run's log on the project page while the agent
      plans and submits a protocol.
- [ ] **Review the protocol** on the approval card
      ([Protocols and approval](#protocols-and-approval)):
      - every device, method, range, step, rate and call limit, the duration
        and the safe state;
      - **Edit…** to narrow anything; **Reject** with a note if it's wrong.
- [ ] Approve. Then check:
      - the Network page shows the device as held by Automation, with a
        countdown and **Stop**;
      - Play on that device is refused.
- [ ] After the run:
      - check the results in `Analysis/`;
      - check the audit trail (`state/<project>/audit/<run>/`): every call is
        listed, the safe state was applied, and the leases were released.
- [ ] **Practise Stop:** start another run, approve it, and press **Stop**
      on the Network page mid-experiment. The device should go to its safe
      state at once.
- [ ] **Optional, the watchdog drill:** during a third approved run, end the
      Control Center's process in Task Manager. Within about two minutes the
      watchdog log (`C:\ProgramData\LabNet\logs\watchdog.log`) shows it
      applying the safe state. Then restart the Control Center.

### 7. Keep it running

- [ ] **Back up** these folders with the rest of LabNet's data:
      - `C:\ProgramData\LabNet\automation` (projects, runs, approvals, audit
        trails);
      - `experiments`;
      - `literature`.
- [ ] **After an update:** rebuild and restart the runner (`build`, then
      `up -d egress runner`), and restart the watchdog with the Control
      Center.
- [ ] **When an instrument is added or changed:** set its site limits before
      ticking it under Allowed devices.
- [ ] **When someone joins or leaves:** run `setup_logins.ps1` again, and
      restart Caddy.
- [ ] **If the lab's Claude login changes:** run the runner's `login` step
      again.

## Client programming

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
The [`examples/`](../../examples/README.md) directory has five complete scripts.

## Control Center

The PQT Lab Control Center is the lab's web interface. It is a LabNet
client with its own Central user: the browser talks only to it, and it asks
Central for the network picture, so no API key ever reaches a browser.

| Path | Page |
| --- | --- |
| `/` | Redirects to `/status` |
| `/status` | **Network** tab: every Lab PC (online/offline, IP), its devices (available, leased, unavailable), and each device's callable methods from its generated contract |
| `/devices` | **Devices** tab: every device on the network in one list, with its Lab PC, state, and callable methods |
| `/<device-id>` | Device control page, e.g. `/dev02-wavemeter` (see below). The app's own paths (`status`, `devices`, `experiments`, `automation`, `static`, `api`, `health`) can't be used as device ids for this page. |
| `/api/status` | The same picture as JSON; refreshed from Central at most every `LABNET_CONTROL_CENTER_REFRESH_SECONDS` |
| `/api/devices/<id>/events` | Live events for a device page: state, samples, script output |
| `/api/devices/<id>/play`, `/pause`, `/script`, `/script/stop` | Device page controls |
| `/experiments` | **Experiments** tab: every saved experiment, with its devices' live state |
| `/experiments/<id>` | The experiment's canvas: setup diagram, settings, Export script (see below) |
| `/api/experiments`, `/api/experiments/<id>`, `/validate`, `/meta`, `/<id>/script` | List, create, read, save (with the version it is based on), delete, check, describe the components, export |
| `/automation` | **Automation** tab: every project, the lab library, and what a project holds |
| `/automation/<id>` | One project: its files, the open file, and its tasks, linked experiments, allowed devices and library scope (see [Automation](#automation)) |
| `/api/automation/projects…` | Projects (list, create, read, save with the version it is based on, delete), their `tree`, `files/<path>` (read, PUT to upload or replace with `If-Match`, delete), `folders`, `move`, and linked `experiments` |
| `/api/automation/projects/<id>/runs…` | A project's runs: list, queue a task (POST), one run with its last 500 events, `cancel` (see [Runs](#runs)) |
| `/api/automation/library…` | The lab library's papers and reading lists, read-only |
| `/api/automation/runner/…` | The [runner API](#the-runner-api), for Automation runners with a runner token, never browsers |
| `/api/agent`, `/api/agent/messages` | Photo-Agent status, and its chat (answers stream as server-sent events; see below) |
| `/api/me` | Who is signed in: `{"user": "alice", "logins": true}`, or `{"user": null, "logins": false}` without [logins](#logins) |
| `/health/live`, `/health/ready` | Up, and up *and* reaching Central. No login needed |

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
credentials: it gets only a short list of environment variables (Windows'
own, `PATH`, the temp and user folders, Python's), never a `LABNET_*` key or
an Anthropic or other token. The Control Center performs each device call
with its lease, and Stop or the 10-minute time limit kills the process.
Lease ids, API keys and runner tokens are cut out of every error a script,
a page or a runner sees (`lease_…`); the Control Center's own log keeps
the full text. Stream methods
inside a script need a `sample_count` (at most 10 000) and return a list of
values.

To add a panel for another device type, copy
`static/js/panels/simulated_wavemeter.js` to `static/js/panels/<device_type>.js`
and register it as `PQT.panels["<device_type>"]`. The shared pieces are
`createChart`, `createScriptBox`, and the session (`session.play`,
`session.pause`, `session.runScript`). The page picks the new panel up
automatically.

### Experiments

The **Experiments** tab holds the lab's setup diagrams, one per experiment:
what is on the optical table, how it is connected, and how it runs.
- **The list:** each row shows the name, who created it, when it was saved
  and how many devices it uses. Expanding a row shows those devices with
  their live state, the optics, and the run (sweeps, readings, points).
- **New experiment** asks for a name. **View Experiment** opens the canvas.

**The canvas** (`/experiments/<id>`) has three parts:
- **Palette (left):** the network's devices and passive components (laser,
  mirror, beamsplitter, polarizing beamsplitter, lens, filter, waveplate,
  fiber, isolator, iris, sample, beam dump, photodetector, note).
  - Drag one onto the canvas, or click it.
  - A device can be on a diagram once; clicking a placed device shows it.
- **Diagram (middle):**
  - Drag blocks to move them; drag empty space to pan; scroll to zoom.
  - To connect two blocks, drag from a block's round handle onto the other.
    Device to device makes a **signal** cable (dashed blue); anything else
    makes a **beam** (orange). Hold Alt to choose the other.
  - Shift-drag selects several. Delete removes. Ctrl+Z / Ctrl+Y undo and redo.
  - Auto-layout arranges the blocks along the beams.
- **Inspector (right):** edits what is selected. For a device, the forms
  come from its contract and the hints its driver publishes:
  - **Settings** (writes) are Off, Fixed, or Sweep (start, stop, points and
    a loop number). Loops nest outermost first; sweeps in the same loop
    advance together.
  - **Commands** get one input per argument, with drop-downs where the driver
    lists choices (e.g. the AWG's waveform).
  - **Record** ticks readings to save at every point, with samples (and a
    rate for streams).
  - Channels (e.g. the AWG's CH1/CH2) can be set separately.
  - With nothing selected, it shows the experiment's name, description, run
    options (settle time, samples, stream rate, output file) and any problems.

**Saving and sharing.** The canvas saves itself about a second after each
change, with the version it was based on.
- If someone else saved in between, nothing is overwritten. A banner offers
  to reload or to save your version as a copy.
- Unsaved changes survive a closed tab: the canvas offers to restore them.
- With [logins](#logins), the signed-in person is recorded as the author.
  Without them (development), the name typed in *Created by* is.
- Saves must be JSON from the Control Center's own pages; another web site
  can't change an experiment through someone's open tab.
- Experiments are JSON files, one each, in the experiments folder
  (`LABNET_CONTROL_CENTER_EXPERIMENTS_DIR`). In development that is
  `experiments/` at the repository root (git-ignored); in the lab,
  `C:\ProgramData\LabNet\experiments`, which is worth backing up.
- Deleting moves a file to `.trash/` there for 30 days.

**Export script** generates a Python script from the saved experiment, in
the style of `examples/04_scan_two_devices.py`:
1. It leases every device it uses, so a busy device stops the run before
   anything changes.
2. It applies the fixed settings upstream first, following the beams and
   cables.
3. It steps through the sweeps, records each point to a CSV, and releases
   everything in `finally`.

Run it from any client with `LABNET_ENV_FILE` set. The generator follows
fixed rules; no AI writes the code.
- Method and argument names come from the device contracts.
- Values are Python literals.
- Free text (names, labels, descriptions) only appears in comments.

So a diagram cannot inject code into the script. Problems that would make
a bad script (a read used as a setting, a wrong argument type, a device
that changed type) are listed in the inspector and stop the export until
fixed.

**Photo-Agent can set up experiments.** On a canvas, the chat shows
"Photo-Agent sees: <experiment>" and sends it the saved experiment with
each message.
- Ask for an experiment in plain words. Photo-Agent answers with a proposal
  card that summarizes the devices, optics, links, sweeps and readings.
- **Apply to canvas** checks it, asks before replacing an existing diagram,
  and applies it as one undo step.
- Elsewhere, **Create experiment** saves it as a new one.
- Proposals are checked by the Control Center like any edit; nothing with
  errors is applied.

### Automation

The **Automation** tab holds projects an agent works in. People collect the
papers, data, setup and tasks; the agent's scripts, logs, data and analysis
are kept beside them, so every step can be checked. Tasks are queued as
[runs](#runs); the [Automation runner](#the-automation-runner) works through
them in Docker.

**A project is a folder** with fixed sections. Each section says whose it is:

| Folder | Whose | Holds |
| --- | --- | --- |
| `Literature/` | yours | Papers only this project needs. The shared **lab library** is shown here too (read-only) |
| `Data/example_data/` | yours | Example data: CSV, TSV, JSON, text and images |
| `Data/new_data/` | agent | Data the agent produces, with `DATA_SUMMARY.md` |
| `Experiment_layout/` | automatic | A snapshot of every linked experiment's canvas, and `DEVICES.md`: each instrument, how to call it, and whether automation may control it |
| `Experiment_layout/LAYOUT.md` | yours | How the setups connect, what may be automated, safety notes |
| `Tasks/` | yours | One Markdown file per task: goal, inputs, constraints, deliverables |
| `Protocols/` | agent | The method and bounds the agent proposes for approval |
| `ScriptsUtilized/` | agent | Every script the agent wrote or ran (`src/`, `analysis_scripts/`, `logs/`), for auditing |
| `Analysis/` | agent | `Analysis.md` and the key figures in `outputs/` |

**On the page:**
- **Left:** the file tree. Your folders take uploads (button or drag and drop) and new folders.
- **Middle:** the open file.
  - Markdown renders as a document. Your own files (tasks, `LAYOUT.md`) have View and Edit.
  - CSV shows as a table; images and PDFs show as themselves; code shows in a code block.
- **Right:** the project's panel.
  - Its tasks, with *Run* and *Run all*, and its [runs](#runs).
  - **Linked experiments:** *Link experiment…*, *Unlink*, and *Update copies* when a newer version is saved on the Experiments tab.
  - **Allowed devices:** only devices on a linked canvas, and only ticked ones, can ever be automated.
  - **Library scope:** topic folders or reading lists; nothing ticked means the whole library.

**Saving.**
- Replacing a file needs the version it was opened with (`If-Match`). If someone else saved in between, the page asks before overwriting.
- Uploads are limited to `LABNET_AUTOMATION_MAX_FILE_MB` per file. The limit is counted as the file streams in.
- Deleting moves a file to the project's trash for 30 days. Deleting a project moves the whole folder to `.trash/`.

**The lab library** is `LABNET_AGENT_LITERATURE_DIR`, the folder Photo-Agent already reads. Lay it out like this:
- **Topic folders** of PDFs.
- **Reading lists** in `collections/<name>.txt`: one library path per line, so a paper can be on several lists without copies.
- **Folders starting with `_`** (for example `_duplicates/`) are not shown.

**Where it lives.**
- `LABNET_AUTOMATION_DIR` holds two trees:
  - `projects/<id>/`: the folders above;
  - `state/<id>/`: the Control Center's own (`project.json`, experiment snapshots, trash, `runs/`);
  - `state/runners/`: one file per runner, holding a hash of its token;
  - `state/library/literature.sqlite` and `state/<id>/literature.sqlite`: the [search indexes](#literature-search).
- Whatever decides what an agent may do (allowed devices, approvals) lives in `state/`, never inside the folder an agent writes.
- In development this is `automation/` at the repository root (git-ignored). In the lab it's `C:\ProgramData\LabNet\automation`, which is worth backing up.

**Paths are checked, never trusted.** Every path is relative to its project or the library. Refused:
- `..`, drive or UNC forms, and `:`;
- hidden (`.`-prefixed) names, reserved device names, and control or invisible characters;
- links and junctions, which are never followed; an opened file is also checked by its handle.

Files are served inert:
- PDFs and images are shown inline, and text is served as plain text.
- An SVG is sandboxed.
- HTML and unknown types are download-only.
- Pages carry a Content-Security-Policy.
- Changes must be JSON (or a raw upload) from the Control Center's own pages.

#### Runs

A **run** is one task of one project (`Tasks/Task1.md`), worked through by a
[runner](#the-automation-runner). Until a runner is connected, queued runs
simply wait in the queue.

**On the page:**
- **Tasks panel:** each task has *Run*, which is disabled (with a tooltip
  saying why) while that task has a queued or active run. *Run all* queues
  every task without one, in name order (`Task2` before `Task10`), with the
  project's run settings.
- **Run with…** (the *…* beside *Run*) queues the task with its own model,
  effort, max turns and budget, for this run only. The dialog starts from the
  project's settings; *Default* there means the Control Center's own default.
  The project's settings don't change.
- **Runs list**, newest first: the status, the task, who queued it, the
  runner, when it started and finished, and how much it uploaded of its
  quota. A queued run says *Waiting for a runner*. Queued and active runs
  have *Stop*, which asks first.
- **Clicking a run** opens its log in the middle: the run's events (kind,
  text and the Control Center's time) and its summary, shown as plain text.
- **Refreshing:** an open log refreshes every 2 s while its run is queued or
  active, and the list every 5 s while any run is. Both pause while the page
  is hidden.

**Run settings** are part of the project (*Settings…*):

| Setting | Default | |
| --- | --- | --- |
| `run_model` | the Control Center's `LABNET_AGENT_MODEL` | One of `LABNET_AGENT_MODELS`; anything else is refused (422) |
| `run_effort` | the Control Center's `LABNET_AGENT_EFFORT` | `low` … `max`. Haiku has no effort levels, so its runs get none |
| `run_max_turns` | `60` | 1 to 200 |
| `run_budget_usd` | `5.0` | More than 0, at most 100 |

- A run copies the settings when it is queued, as `settings: {model, effort,
  max_turns, max_budget_usd}`. Changing the project afterwards changes only
  later runs.
- A save that leaves these fields out keeps them, so an older open page
  can't reset them. Projects saved before these settings existed get the
  defaults.
- **A run's own settings** (*Run with…*): `POST …/runs` may carry
  `"settings": {"model", "effort", "max_turns", "max_budget_usd"}`. Each one
  given replaces the project's for this run only; one left out or `null`
  keeps the project's. They are checked exactly like the project's (a model
  `LABNET_AGENT_MODELS` doesn't offer, an unknown effort, a range or an
  unknown key answers 422 naming `settings.<field>`), and Haiku still gets no
  effort. The run's `settings` record what was used.

Through the API:

| Request | Does |
| --- | --- |
| `POST /api/automation/projects/<id>/runs` `{"task": "Tasks/Task1.md", "settings"?}` | Queues the task (201). The task must be a Markdown file in `Tasks/`; one that is already queued or active is refused (409). `settings`: this run's own model, effort, max turns or budget |
| `GET …/projects/<id>/runs` | The project's runs, newest first |
| `GET …/projects/<id>/runs/<run_id>` | The run and its last 500 events |
| `POST …/projects/<id>/runs/<run_id>/cancel` `{}` | Cancels a queued or active run; the runner notices on its next request |

- A run is `queued`, then `active` once a runner claims it, and ends `done`,
  `failed`, `stopped` (the runner says which) or `cancelled` (a person did).
- With [logins](#logins), `queued_by` and `cancelled_by` are the signed-in
  person; without them, the optional name sent in the body.
- A runner claims the oldest queued run of any project.
- A project can't be deleted while one of its runs is queued or active.
- Events and the summary are written by the runner. Show them as plain
  text, never as HTML or Markdown.
- **Heartbeat and timeout.** Every runner request about an active run counts
  as a sign of life (`last_seen_at`), and so does an upload still streaming
  in. A runner with nothing else to say sends `POST runs/<run_id>/heartbeat`.
  If an active run hears nothing for `LABNET_AUTOMATION_RUN_STALE_MINUTES`
  (10), it is marked `failed` with the summary *The runner stopped reporting
  (nothing for 10 minutes).* and a `timeout` event. Its runner then gets 409.
  - The check runs whenever runs are read, and once a minute in the
    background.
  - After a Control Center restart, the clock starts again, so runners get a
    full window to reconnect.
  - Records from before heartbeats existed count from `claimed_at`.
- Records live in `state/<id>/runs/`: `<run_id>.json` and
  `<run_id>.events.jsonl`.

#### Runners and their tokens

A **runner** is the machine that works through the runs (see
[The Automation runner](#the-automation-runner)): the lab's Linux PC, or
Docker in WSL on a developer's laptop. It talks to the Control
Center only over the [runner API](#the-runner-api), with its own token,
never with a person's login. Manage runners on the Control Center PC:

```powershell
$ccadmin = "C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-control-center-admin.exe"
& $ccadmin --env-file C:\ProgramData\LabNet\control-center.env runner add lab-laptop
& $ccadmin --env-file C:\ProgramData\LabNet\control-center.env runner list
& $ccadmin --env-file C:\ProgramData\LabNet\control-center.env runner revoke lab-laptop
```

- **Names:** lowercase letters, digits and dashes, at most 32 characters.
- **`add`** prints the token (`lnr_…`) once. Put it in the runner's
  settings. The Control Center keeps only its sha256, in
  `state/runners/<name>.json`. A lost token can't be shown again: revoke the
  runner and add it again.
- **`revoke`** works at once: the runner's next request is refused.
- The Control Center doesn't need a restart for either.
- In development, `labnet-control-center-admin --env-file .devnet/control-center.env runner …`.
  On a checkout set up before this command existed, run `scripts/dev/setup.sh`
  again to install it, or use `python -m labnet_control_center.admin`.

#### The runner API

Every request carries `Authorization: Bearer lnr_…`. Paths start with
`/api/automation/runner/`.

| Request | Does |
| --- | --- |
| `GET whoami` | The runner's name |
| `POST claim` | Claims the oldest queued run (200 with the run), or 204 when nothing is queued |
| `GET runs/<run_id>` | The run's record, to notice a cancel |
| `GET runs/<run_id>/inputs` | A zip of the project's inputs, with `run.json` at its root |
| `GET runs/<run_id>/library/<path>` | One paper, if it is within the project's library scope |
| `GET runs/<run_id>/literature/search?q=&k=&paper=` | [Literature search](#literature-search) in the run's project |
| `GET runs/<run_id>/literature/papers` | The papers that search covers |
| `GET runs/<run_id>/literature/pages?paper=&first=&last=` | Up to 20 pages' text of one of them |
| `PUT runs/<run_id>/files/<path>` | Creates or replaces a file in the agent's folders. The body is the file |
| `POST runs/<run_id>/events` | `{"events": [{"kind": "progress", "text": "…"}, …]}`: progress and transcript lines |
| `POST runs/<run_id>/finish` | `{"status": "done" \| "failed" \| "stopped", "summary": "…"}` |
| `POST runs/<run_id>/heartbeat` | Still working on it: `{"status": "active"}` (see [Runs](#runs) for the timeout) |

- **Refused:** no token or a wrong one (401); any request a browser made,
  which says so in `Sec-Fetch-Site` (403); a run this runner didn't claim
  (404, as if it didn't exist); anything but `GET runs/<run_id>` on a run
  that isn't active any more (409, with its `status`, which is how a runner
  learns of a cancel).
- **The inputs zip** holds the folders people fill (`Literature`,
  `Data/example_data`, `Experiment_layout` with `DEVICES.md`, `Tasks`), never
  the agent's folders, hidden names, `CLAUDE*.md` or links. `run.json` holds
  the run, its `settings` (model, effort, max turns and budget, as copied
  when it was queued), the project's settings (allowed devices, linked experiments,
  library scope), the list of library papers in scope, and `truncated` when
  the project had more files than a listing holds. A project that names no
  topic folders or reading lists gets the whole library.
- **Events:** each is exactly a `kind` (lowercase letters, digits and `_`)
  and a `text` of at most 16 KiB; at most 100 per request, and 16 MiB per
  run. The summary is at most 4000 characters. The Control Center stamps the
  time and redacts both.

**What a runner may write.** Only the agent's folders: `Data/new_data`,
`Protocols`, `ScriptsUtilized` and `Analysis`. Uploads pass the same path
checks as a person's, plus:
- no hidden name (`.claude/`, `.mcp.json`) and no `CLAUDE*.md` anywhere on
  the path, which a later run's Claude Code would read as settings or
  instructions;
- a file name, never one of the fixed folders (`Analysis/outputs`,
  `ScriptsUtilized/logs`, …);
- each file at most `LABNET_AUTOMATION_MAX_FILE_MB`;
- the whole run at most `LABNET_AUTOMATION_RUN_QUOTA_MB` (500 MB). The quota
  is counted as bytes stream in, so two uploads at once can't pass it
  together, and replacing a file counts again. Past it, uploads get 413.

The Control Center, not the runner's machine, decides what the agent may
change.

#### The Automation runner

The runner (`labnet-runner`, in `labnet_automation`) works through
queued runs, one at a time. It runs in Docker: on a Linux PC in the lab, and
in WSL on a developer's laptop. Both use the same images and compose file
(`packages/labnet-automation/docker/`); only an address, a token and a CA file differ.

```text
Control Center (Windows) ◄── HTTPS, runner token ── runner container (trusted supervisor)
                                                     ├─ agent container per run: Claude Code + labnet tools
                                                     │     internal network; out only via the egress proxy
                                                     │     (Anthropic's servers); no token; tools via a
                                                     │     Unix socket in its run folder
                                                     ├─ run container per run_python call: the working
                                                     │     copy only, no network, CPU/memory/process limits;
                                                     │     during a device grant also the relay's socket
                                                     └─ device relay (in the runner): a script's device
                                                           calls → the gateway, with the grant's token
```

**A run, step by step.**
1. The runner claims the oldest queued run and downloads its inputs.
2. It unpacks them into a fresh working copy, refusing unsafe zip entries,
   links, `.claude/`, `.mcp.json` and `CLAUDE*.md`.
3. It starts Claude Code (`claude -p`) in an agent container, with the
   project's model, effort, maximum turns and budget. Its tools:
   - Read, Glob, Grep, and Edit and Write in the agent's folders only;
   - subagents (`literature-reviewer`, `data-analyst`, `experiment-runner`);
   - the labnet tools `search_literature`, `list_papers`, `read_paper_pages`
     (through the Control Center's [literature search](#literature-search)),
     `run_python`, and for experiments `submit_protocol`, `grant_status` and
     `end_experiment`.

   There is no shell, and no web access beyond Anthropic's servers.
4. **`run_python`** runs one script the agent wrote under `ScriptsUtilized/`.
   It runs in a new container: Python 3.11 with numpy, scipy, pandas and
   matplotlib, no network, a read-only system, 2 CPUs, 2 GB and 10 minutes by
   default. Its output is saved in `ScriptsUtilized/logs/<run_id>/`.
5. The run's progress streams to its log on the project page. The runner
   sends a heartbeat, and stops the agent at once if someone presses Stop.
   **Experiments on lab devices** (Phase 3) happen in the same conversation:
   - the agent writes `Protocols/<task>.protocol.json` (devices, methods,
     bounds, duration, safe state) and `.protocol.md`; `submit_protocol`
     uploads both and the Control Center validates them;
   - the agent ends its turn; the runner waits for a person's decision,
     heartbeating and polling the run every `LABNET_RUNNER_POLL_SECONDS`
     (Stop, the stale rule and the approval timeout end the wait);
   - **approved:** the runner takes the grant's token, starts its device
     relay (a Unix socket in the run folder) and resumes Claude Code
     (`--resume <session id>`) with the approved devices, bounds and end time,
     taken from the normalized protocol the Control Center sends with the
     token (what the gateway enforces), never from the agent's own file; the
     prompt and `grant_status` also say that every bounded argument is passed
     in every call, that steps start from the device's current reading, and
     that a refused call is final.
     While the grant lasts, `run_python` containers get the relay's socket at
     `/run/labnet-devices` and scripts use `labnet_devices`; the relay adds the
     token to each call and the Control Center's gateway checks every one;
   - **rejected:** the agent is resumed with the reviewer's note (at most 3
     reviews per run);
   - when that session ends, the runner ends the grant (the safe state) and,
     if `Analysis/Analysis.md` is missing, resumes the agent once more to
     write it. However a run ends, a grant whose token was taken is ended.
6. **At the end**, it uploads the new and changed files in the agent's
   folders, plus the transcript and its own log
   (`ScriptsUtilized/logs/<run_id>/`). Anything changed elsewhere is
   reported, never uploaded. It finishes the run `done`, `failed` or
   `stopped`.

**It refuses to start unless the isolation holds.** At start, and on
`labnet-runner self-check`, it checks each of these and logs what failed:
- the Control Center answers and knows its token;
- the images exist;
- the agent network is internal and has a Claude login;
- a script container can reach nothing and can't see the runner's folders;
- an agent container can reach `api.anthropic.com` through the proxy, but
  not `example.com`, and nothing directly;
- the installed Claude Code has the options the runner uses.

**What's trusted:**
- **The runner container** holds the token and the Docker socket. Agent and
  script containers never get either, nor a grant's token: the device relay
  adds it to each call itself.
- **The agent container** holds the Claude login (a Docker volume). Claude
  Code in it can read the login, but it can reach only Anthropic, and its
  uploads go only to the agent's folders.
- **The egress proxy** (`egress-allow.txt`) allows HTTPS to
  `api.anthropic.com`, `platform.claude.com` and `claude.ai` only. It refuses
  names that resolve to LAN addresses.

| Setting (`runner.env`) | Default | |
| --- | --- | --- |
| `LABNET_RUNNER_CONTROL_CENTER_URL` | required | `https://pqt-control-center` in the lab. Plain `http://` only with `LABNET_RUNNER_DEV=1` |
| `LABNET_RUNNER_TOKEN` (or `_TOKEN_FILE`) | required | From `labnet-control-center-admin runner add <name>` |
| `LABNET_RUNNER_CA_FILE` | system trust | `/etc/labnet/labnet-ca.pem` in the lab |
| `LABNET_RUNNER_WORK_DIR` | `/var/lib/labnet-runner` | Working copies; set by compose |
| `LABNET_RUNNER_POLL_SECONDS` | `10` | How often it asks for a run, and for a protocol's review decision |
| `LABNET_RUNNER_AGENT_TIMEOUT_MINUTES` | `120` | One agent session's time limit (waiting for approval doesn't count) |
| `LABNET_RUNNER_PYTHON_TIMEOUT_SECONDS` / `_CPUS` / `_MEMORY_MB` / `_PIDS` | `600` / `2` / `2048` / `256` | One `run_python` call's limits |
| `LABNET_RUNNER_KEEP_RUNS` | off | Keep each run's working copy after it ends (for debugging) |
| `LABNET_RUNNER_DEV` | off | Development: allows plain HTTP and marks every run "development only" |

##### In development: Docker in WSL on your laptop

Needs, once:
1. WSL 2 with a Linux distribution (`wsl --install -d Ubuntu-22.04`).
2. Docker Engine in that distribution
   ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)).
   Then, in WSL, `sudo usermod -aG docker $USER`.
3. **Mirrored networking**, so WSL reaches the laptop's `127.0.0.1:8080`.
   `%UserProfile%\.wslconfig` must contain:

   ```ini
   [wsl2]
   networkingMode=mirrored
   ```

   Then run `wsl --shutdown`.

Then, from Git Bash at the repository root, with the dev network up:

```bash
scripts/dev/runner.sh build        # the three images (again after changing labnet_automation)
scripts/dev/runner.sh login        # once: in Claude, type /login, open the link, then /exit
scripts/dev/runner.sh up           # issues a runner token into .devnet/runner.env, starts the runner
scripts/dev/runner.sh logs         # watch it check itself, then claim runs
scripts/dev/runner.sh self-check   # the checks alone
scripts/dev/runner.sh down
```

WSL stops a distribution, and its containers, about a minute after the last
Windows program attached to it exits. So `up` also starts a hidden
`wsl.exe … sleep` that keeps it running, as Docker Desktop does, and `down`
stops it. After a reboot, run `up` again.

The working copies live in WSL's own file system (`~/.labnet-runner`). Use a
different distribution with `LABNET_WSL_DISTRO=<name>`.

##### In the lab: the runner PC

The runner gets its own Linux PC (Ubuntu 24.04 LTS or Debian 12; about 8 GB
of RAM; wired to the lab network at a fixed address). It needs outbound
HTTPS to the Control Center and to Anthropic, and nothing inbound.

1. **Docker Engine.** Follow
   [docs.docker.com/engine/install](https://docs.docker.com/engine/install/)
   for the distribution, then `sudo systemctl enable --now docker`. Don't add
   people to the `docker` group on this PC: it is root-equivalent.
2. **The code.** Copy or clone the repository to `/opt/labnet-src`, and
   update it the same way.
3. **Trust and the address.**
   - Copy `labnet-ca.pem` to `/etc/labnet/labnet-ca.pem`.
   - Add `192.168.50.10  pqt-control-center` to `/etc/hosts`.
   - Check with `curl --cacert /etc/labnet/labnet-ca.pem https://pqt-control-center/health/live`.
4. **A token.** On the Control Center PC, `labnet-control-center-admin …
   runner add lab-runner` ([Runners and their tokens](#runners-and-their-tokens)).
5. **Optional: limit the runner API to this PC's address.** Run
   `setup_logins.ps1 -RunnerAddress <its IP>` there, then restart Caddy
   ([Logins](#logins)).
6. **Settings.** Create `/etc/labnet/runner.env`, owned by root, mode 600:

   ```ini
   LABNET_RUNNER_CONTROL_CENTER_URL=https://pqt-control-center
   LABNET_RUNNER_TOKEN=lnr_…
   LABNET_RUNNER_CA_FILE=/etc/labnet/labnet-ca.pem
   ```

7. **Build, sign in, start.** In `/opt/labnet-src`:

   ```bash
   sudo mkdir -p /var/lib/labnet-runner
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml build
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml --profile login run --rm login   # /login, then /exit
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml run --rm --no-deps runner labnet-runner self-check
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml up -d egress runner
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml logs -f runner
   ```

   The containers restart with Docker, so the runner is back after a reboot.

**Updating:** pull the new code into `/opt/labnet-src`, then `build` and
`up -d egress runner` again. A run in progress is stopped, keeps what it has
uploaded, and ends `stopped`; queue it again.

**On the Central PC instead:** the same steps work with Docker in WSL on
`central_server_pc`. There, set `LABNET_RUNNER_CONTROL_CENTER_URL` to the
Control Center's HTTPS address, as in the lab, and keep WSL running. The
[Automation go-live checklist](#automation-go-live-checklist) walks through
it. Keep in mind that the agent then shares a computer with Central.

**Claude login.** One login, saved in the `labnet-automation-claude-login`
volume, serves every run. Confirm that your Claude plan allows lab-wide
automated use. To change it, run the `login` step again. Removing the volume
signs the runner out.

#### Literature search

Papers are searchable by keyword, page by page, with citations like
`lasers_and_stabilization/…/An introduction to Pound–Drever–Hall laser frequency stabilization.pdf p.6`.
The project page's search box and the agent's `search_literature`,
`list_papers` and `read_paper_pages` tools use the same search.

**What is indexed:**
- **The lab library**, by the same rules as its tree: no `_`-folders, no
  `collections/`, no hidden names, links, junctions or hard links. PDFs page
  by page; `.txt` and `.md` files as one page.
- **Each project's own `Literature/`** folder, the same way.
- A PDF with (almost) no text layer, such as a scan, is flagged *no text
  layer* and can't be found by its words. An encrypted or broken one is
  flagged with a short reason. Neither stops the rest.

**Keeping it current:**
- The library is indexed when someone presses **Index library** on the
  Automation tab's *Lab library* card. The card shows how many papers are
  indexed, how many have no text layer and how many couldn't be read, with
  live progress while it runs. Only one indexing runs at a time; pressing
  again meanwhile gets 409.
- Indexing is incremental. A paper with the same size and modification time
  is skipped, one with the same bytes (sha256) only has its record updated,
  and a paper that is gone is dropped. *Index library* also tries papers
  that failed last time again.
  The lab's 62 papers take about two minutes the first time and under a
  second after that.
- A project's own papers are indexed just before it is searched. If new ones
  would take more than about 30 s, the search covers what is indexed and
  says so.

**What a search covers:** the project's [library scope](#automation) (its
topic folders and reading lists, or the whole library when it names neither,
exactly as the runner's library endpoint decides), plus the project's own
papers. `paper=<path>` narrows it to one of them.

**How a query is read:** words and `"quoted phrases"`. A page must have them
all; when no page does, any of them will do (the reply's `mode` says `all`
or `any`). Pages are ranked by BM25, and English endings are matched
(*stabilizing* finds *stabilization*). Everything typed is searched as
words: `AND`, `OR`, `NOT`, `NEAR`, `*`, `:` and parentheses mean nothing
special, and a query can't cause an error.

| Request | Does |
| --- | --- |
| `GET /api/automation/library/index` | The library index: `state` (`idle`, `running` or `failed`), `done` of `total`, the `current` paper, `papers`, `no_text`, `errors`, `message`, and `extractor` (false without pypdf) |
| `POST /api/automation/library/index` `{}` | Starts indexing (202); 409 while it runs; 503 without pypdf |
| `GET …/projects/<id>/literature/search?q=&k=&paper=` | Up to `k` pages (1 to 50, default 10) for `q` (at most 500 characters): `path`, `source` (`library` or `project`), `page`, `score`, `snippet` and `cite` |
| `GET …/projects/<id>/literature/papers` | Every paper the project's search covers, with its pages and status |

Runners have the same three, plus `pages`, under `runs/<run_id>/literature/`
(see [the runner API](#the-runner-api)). They work only while the run is
active.

- In a `snippet`, each hit is between the characters U+0002 and U+0003. The
  project page turns them into highlights without ever reading paper text as
  HTML.
- `pages` returns at most 20 pages per request, with `more` when the paper
  goes on.

**PDFs are read in a sandbox.** Papers come from the internet, and a PDF
parser is a large attack surface, so pypdf never runs inside the Control
Center:
- Each PDF is copied, hashed, and read by a separate process,
  `python -m labnet_control_center.literature_extract`.
- That process gets the same allow-list environment as experiment scripts
  (no `LABNET_*` settings, no credentials) and no console window.
- It has 60 s per PDF; a slower one keeps the pages read so far and is
  flagged.
- At most 3000 pages are read, 200 000 characters per page and 64 MB per
  paper.
- pypdf comes with the `control-center` extra (`pypdf>=5,<7`). Without it,
  the card says so and indexing is refused.

#### Protocols and approval

Before an agent may touch hardware, it writes a **protocol**: which devices,
which methods, the bounds of every argument, call and rate caps, how long,
and the safe state. A person reviews it and approves or rejects it
(`automation_protocol.py`). While a run is active, its `phase` says where it
is:

```
planning ──(submits a protocol)──► awaiting_approval ──approve──► executing ──(grant ends)──► analyzing
    ▲                                    │
    └──────────reject + note─────────────┘
```

A run that never needs devices goes from `planning` straight to its finish.

**The files.** For `Tasks/Task1.md` the agent writes
`Protocols/Task1.protocol.json` and, in its own words,
`Protocols/Task1.protocol.md`. The runner uploads both, then calls
`POST /api/automation/runner/runs/<run>/protocol` `{}`. The Control Center
reads the JSON from the project itself:

```json
{
  "schema_version": 1,
  "task": "Tasks/Task1.md",
  "title": "AOM efficiency vs RF frequency",
  "duration_minutes": 20,
  "devices": [
    {
      "device_id": "dev01-aom",
      "device_type": "dummy_aom",
      "methods": {
        "set_frequency": {"args": {"value": {"min": 70, "max": 90, "max_step": 2}}, "max_calls": 200, "max_rate_hz": 5},
        "set_rf_enabled": {"args": {"enabled": {"choices": [true, false]}}, "max_calls": 10, "max_rate_hz": 1},
        "read_diffraction_efficiency": {"max_calls": 500, "max_rate_hz": 10},
        "stream_diffraction_efficiency": {
          "args": {"channel": {"choices": ["CH1"]}},
          "stream": {"max_sample_rate_hz": 20, "max_samples": 1000},
          "max_calls": 50, "max_rate_hz": 1
        }
      }
    }
  ],
  "safe_state": [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": false}}]
}
```

**The checks.** Everything in the file is untrusted, so the checks are strict.
Each refusal names the place (`devices[0].methods.set_frequency.args.value.max`)
and says what to change:
- **The file:** UTF-8 JSON, at most 64 KiB. Unknown keys are refused at
  every level, and so are repeated keys, `NaN` and `Infinity`.
- **Top level:** `schema_version` 1. `task` is the run's own task. `title`
  has at most 120 characters (text never has control or invisible characters,
  nor more than 2 combining marks on one character, which could draw over the
  review). `duration_minutes` is from 1 to
  `LABNET_AUTOMATION_GRANT_MAX_MINUTES` (120). There are 1 to 16 devices,
  each listed once.
- **Devices:** each one must be on a canvas linked to the project, allowed
  for automation in the project's settings, and on the network now.
  `device_type` must be its live type, and the linked experiment must agree.
- **Methods:** each one must be in that type's contract, with `max_calls`
  (a whole number, 1 … 100 000) and `max_rate_hz` (above 0, at most 100).
  A stream also needs `stream: {max_sample_rate_hz (above 0 … 200),
  max_samples (1 … 100 000)}`; nothing else may have one.
- **Arguments:** `args` bounds every argument the method sends. An optional
  one may be left out, and then it may never be sent; one that is bounded is
  **sent in every call** (otherwise leaving out a `channel` limited to `CH2`
  would drive the driver's default, `CH1`). A write's `unit` is never allowed.

  | Argument type | Bounds |
  | --- | --- |
  | number (`float`, `int`) | `{"min", "max", "max_step"?}`: finite, `min ≤ max`, `max_step > 0`; whole numbers for an `int` |
  | `bool` | `{"choices": [true, false]}` (one or both) |
  | `str` | `{"choices": [...]}`: up to 32, each up to 64 characters |

  `true` is never a number, and `1` is never `true`.
- **`max_step` needs a reading.** Steps are measured from where the device
  is, so `max_step` is allowed only on a `set_<x>` that has a scalar
  `read_<x>` taking the same choice arguments (`set_amplitude(value,
  channel)` → `read_amplitude(channel)`; `automation_protocol.step_reader`).
  Today that is every setter of `dummy_aom`, `dummy_awg`, `dummy_eom` and
  `dummy_laser`; not `dummy_device.write_value`, the `simulated_tunable_laser` methods
  or `simulated_wavemeter.set_exposure`, where the check says to drop
  `max_step` (any value in range is then allowed) or narrow the range.
- **Published limits are hard limits.** A device publishes the driver's
  hints narrowed by its Lab PC's [site limits](#site-limits). `[min, max]`
  must lie within its `limits["<method>.<argument>"]`. Choices (text or
  true/false) must be among its `choices`, and a `channel` among its
  `channels`, whenever it publishes them. Each refusal says whether the limit
  is the site's or the driver's.
- **No limit, no automation.** Every number a protocol bounds needs a
  published limit. Without one the protocol is refused (a problem, not a
  warning): *"dev05-laser publishes no limit for
  go_to_wavelength_nm.wavelength_nm; set one in the Lab PC inventory with
  site_limits(...) before automating it."* A person sets that limit in the
  inventory and restarts the Lab PC. Text arguments without published choices
  still need explicit `choices` in the protocol.
- **The safe state:** up to 16 calls, each one the protocol itself allows:
  a listed method (not a stream), every argument it sends and every argument
  the protocol bounds (e.g. its `channel`), and values within the bounds.
  `max_step`, call counts and rates don't apply to it. A whole-number
  argument stays an integer (`0`, not `0.0`), so the gateway accepts it.

A protocol that passes is **normalized**: defaults filled in, bounds and
rates as floats, counts as integers. It is serialized canonically
(`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).
That text is stored as `state/<project>/protocols/<run>-<n>.json`, with the
prose beside it as `<run>-<n>.md`. Its sha256 is what a person approves.

**Submitting.**
- **200** `{"phase": "awaiting_approval", "sha256", "submission"}`: the run
  now waits for a person.
- **422** `{"detail", "problems": [{"path", "message"}]}`: the run stays
  `planning`. A refused file doesn't count as a submission; the agent fixes it
  and submits again.
- **409** when the run isn't `planning`, or has already sent 3 protocols for
  approval.
- **503** while Central can't be reached: a protocol is never checked against
  a stale picture of the network.

**Reviewing.** Clicking a run that waits for approval (*Review* in the runs
list) shows:
- **The approval card:** the protocol's sha256 prefix and the requested
  duration. The duration box can shorten the time but never lengthen it.
  *Approve* asks to confirm. *Reject* needs a note for the agent.
- **The review:** built by the server from the normalized copy and the
  device contracts, never from the agent's words. Each device shows its
  type and live state. Each method shows every argument's range or choices,
  its largest step (the first measured from the device's own reading) and
  the device's published limit with its source (`limit`, `limit_source`:
  `site` or `driver`; choices as `offered`), that each bounded argument is always sent, which
  optional arguments may never be sent, its call and rate caps, and its
  stream caps. Agent-written text (the title, choices) is clipped to its own
  line, so it can't draw over the rest. Then come the
  safe state and warnings: no safe state, no `max_step`, or a limit no
  longer published (the device went offline; approving checks again).
- **The agent's explanation:** the `.md`, in a box labelled as the agent's,
  shown as plain text only.

Once approved, the page shows the **grant** with a countdown and *Stop now*.
A run whose safe state failed ends `ended_unsafe`, with a red banner.

**Approving** sends the sha256 the page showed. The approver is the
signed-in person (with [logins](#logins)) or the name typed, and is
required. The Control Center then:
1. refuses a different sha256 (409), a run not waiting for approval (409),
   or a longer or unusable duration (422, also for an integer too large for a
   float);
2. checks the stored protocol again against the project and the live
   network. A device no longer allowed, gone or of another type answers 409
   with the problems, and the run keeps waiting: reject it so the agent can
   write a new one;
3. creates the [grant](#device-grants-and-the-gateway), taking every lease.
   A busy device answers 409, and nothing changes;
4. records the approval (`reviewed_by`, `approved_minutes`). The run is then
   `executing`.

One approval runs at a time. If the run ends while its grant is being
created, the grant is ended again.

**Editing before approving** (*Edit…* on the approval card). The approver
may change the protocol and approve their own version instead of rejecting
it:
- **What can be edited,** with plain fields: each argument's `min`, `max`
  and `max_step` (empty: any step) or its choices; `max_calls`,
  `max_rate_hz` and the stream caps; removing a method or a whole device; the
  duration it asks for; and the safe state (change a value, remove a call,
  move it earlier, or add a call to a method the protocol lists).
  *Advanced: edit as JSON* shows the whole edited protocol for anything else.
- **The same checks as the agent's.** The approver is trusted to widen or
  narrow anything, but only within the device's and the lab's limits:
  `validate_protocol` runs on the edit against the project and the live
  network, and a removed method still used by the safe state is refused (the
  page drops those calls itself and lists them). The duration may be raised
  up to `LABNET_AUTOMATION_GRANT_MAX_MINUTES`; the grant may then be as long
  as the edit asks.
- ***Review changes…*** sends the edit to `POST …/protocol/preview`
  `{"sha256", "protocol"}`, which checks it without approving and answers
  `{"normalized", "sha256", "problems", "changes", "review"}`. Problems are
  shown beside the fields. Otherwise a confirmation lists the changes the
  server found, one line each (`dev01-aom.set_frequency.value.max: 90 → 85`,
  `dev01-aom.read_diffraction_efficiency: removed (may not be called)`,
  `safe_state: added dev01-aom.set_frequency(value=75)`).
- **Approving** sends `{"sha256", "duration_minutes", "protocol",
  "edited_sha256"}`: `sha256` is the submission the edit started from (409 if
  another one is waiting now), `protocol` the previewed version and
  `edited_sha256` its sha256 (409 if the server's normalized copy differs).
  The server checks the edit again: one that no longer passes answers 422
  with `problems`, and nothing changes. An edit that changes nothing is a
  plain approval.
- **What is kept.** The edit is normalized and stored as
  `state/<project>/protocols/<run>-<n>-edited.json` (its canonical text, so
  the file's sha256 is its own); the agent's `<run>-<n>.json` stays as it
  was. The grant is created from the edit, under the edit's sha256, so
  `grant/token` hands the runner the edited protocol. The run's `protocol`
  record gains `approved_sha256` (the edit's), `edited_by` and `changes` (at
  most 60 lines, each clipped to 240 characters); the log says *approved …
  EDITED* with the changes.
- **The agent is told.** On resume the runner's prompt says the protocol was
  approved WITH EDITS, quotes the changes from the run record, and gives the
  edited bounds (the runner checks the protocol it got against
  `approved_sha256`). `grant_status` shows them too.
- Afterwards the run's page shows *Edited by … before approval* with the
  changes, the approved version's review, and the agent's submission under
  *As the agent submitted it*.

**Rejecting** (`{"note"}`, up to 2000 characters) returns the run to
`planning`. The note is in the run record (`protocol.note`), where the
runner reads it, and the agent may submit again, up to 3 protocols per run.

**Waiting.** A protocol not approved or rejected within
`LABNET_AUTOMATION_APPROVAL_HOURS` (24) fails the run. Meanwhile, the
runner keeps heartbeating; the 10-minute rule still applies.

**The run record** gains `phase` and `protocol`: `{submissions, status
(submitted | approved | rejected), sha256, title, duration_minutes,
submitted_at, reviewed_by, reviewed_at, approved_minutes, approved_sha256,
edited_by, changes, note}` for the latest submission. `approved_sha256` is
the version the grant enforces: `sha256` unless the approver edited it. It also gains `grant`, written by the GrantManager: `{id,
status (active | ending | ended | ended_unsafe | interrupted), approved_by,
starts_at, ends_at, devices, calls, refused_calls, ended_reason, ended_at}`.
- A run whose grant ended `ended_unsafe` ends `ended_unsafe` however it
  finishes (`done`, `failed`, `stopped` or `cancelled`), and keeps its
  summary.
- Run records from before Phase 3 load with no phase.

| Request | Does |
| --- | --- |
| `POST /api/automation/runner/runs/<run>/protocol` `{}` | Checks `Protocols/<task>.protocol.json` and sends it for approval (runner token) |
| `GET /api/automation/projects/<id>/runs/<run>/protocol` | `{"protocol" (normalized), "sha256", "review", "prose" (the agent's, untrusted), "status", "submission", "phase", "run_status", "max_minutes", "approved_sha256", "edited_by", "changes"}`; with an edit also `"approved_protocol"`, `"approved_review"` |
| `POST …/runs/<run>/protocol/preview` `{"sha256", "protocol"}` | Checks an approver's edit without approving: `{"normalized", "sha256", "problems", "changes", "review"}` |
| `POST …/runs/<run>/protocol/approve` `{"sha256", "duration_minutes", "approved_by"?, "protocol"?, "edited_sha256"?}` | Approves (the edit, if sent); starts the grant; `{"run", "grant"}` |
| `POST …/runs/<run>/protocol/reject` `{"note", "sha256"?, "rejected_by"?}` | Rejects; the run is planning again |

#### Device grants and the gateway

When a person approves a run's protocol, the Control Center creates a
**grant** (`automation_grants.py`). A grant holds the protocol's bounds, a
time limit (at most what the protocol asked for), and the leases on every
device it names. It is the only way an agent's script reaches hardware, and
every call is checked against it.

**Creating a grant.** Every lease is taken up front, and each device's live
type must match the protocol's `device_type`. If one device is busy or
mismatched, the leases already taken are released and approval answers 409.
If the Control Center starts shutting down while the leases are taken, they
are released and no grant is made. The grant's token (`lng_…`) exists only
in memory until the runner takes it once (`POST runs/<run>/grant/token`),
together with the normalized protocol the person approved, from which the
runner tells the agent its bounds. `state/<project>/grants/<id>.json` keeps
only its sha256.

**Every call** goes to `POST /api/automation/runner/gateway/calls` with
`Authorization: Bearer lng_…`:
- The body is at most 16 KiB of `application/json`:
  `{"device_id", "method", "args": {…}, "sample_rate_hz"?, "sample_count"?}`.
- A browser's request (`Sec-Fetch-Site`) is refused. The path is under the
  runner prefix, so it needs no Caddy login, and the grant token is its only
  credential.
- An allowed call answers `{"result", "calls_left", "seconds_left"}`. A
  stream's result is its list of values.
- Anything else answers 4xx `{"detail", "code"}`:

| Code | HTTP | Meaning |
| --- | --- | --- |
| `refused` | 422 (413/415 for the body, 403 for a browser) | Outside the approved protocol. Final: fix the script, don't retry. |
| `busy` | 409 | A call to that device is still running (one at a time per device). |
| `expired` | 410 | The grant's time is up. |
| `ended` | 410 (401 for an unknown token) | Stopped, ended by the agent, too many violations, or the run ended. |
| `device_error` | 424 | The device failed. The message is redacted: no lease ids, keys or tokens. |

**What the gateway checks.** Anything it does not expect is refused, and a
malformed protocol refuses rather than allows.
- The grant is active and has time left.
- The device is in the grant, and the method is listed for it.
- `args` is a JSON object of exactly the method's keyword arguments:
  - every required one is there;
  - every one the protocol bounds is there (a bounded `channel` left out
    would drive the driver's default channel);
  - an optional one only if the protocol bounds it;
  - never `unit`, and nothing else.
- Numbers:
  - they are JSON numbers (`true` is not a number), finite, and within
    `[min, max]`;
  - a whole-number argument takes only integers;
  - with `max_step`, a number is at most that far from where that setting
    may be now. "That setting" is the device, method, argument **and the
    call's choice arguments** (`dev02-awg.set_amplitude.value|channel=CH2`),
    so alternating channels can't double a step. Before the first change the
    gateway reads it with the matching reader and the same choices
    (`read_amplitude(channel="CH2")`): with the grant's lease, not counted
    against the protocol's calls, audited as a `reading` line. A reading that
    fails (or isn't a number) refuses the change (`device_error`, the change
    not sent). A call that succeeded sets the position to its value; one
    whose outcome is uncertain (a device error after sending, a timeout, the
    runner hanging up) widens it to `[min(old, sent), max(old, sent)]`, and
    the next value must be within `max_step` of both ends.
- Booleans and text must be one of the approved `choices`. A boolean must be
  a real JSON `true`/`false`.
- A stream sends `sample_rate_hz` (above 0, at most `max_sample_rate_hz`)
  and an integer `sample_count` (1 … `max_samples`), and
  `sample_count / sample_rate_hz` must fit in the time left. Other methods
  send neither.
- Each method keeps to its `max_calls` and to `max_rate_hz`: at least
  1/`max_rate_hz` seconds since that method's previous call.
- Each call is cut off when the grant's time runs out.

**Audit and violations.** Every call is one line in
`state/<project>/audit/<run>/grant-<id>.jsonl`, whether it was allowed or
not, including a body refused before it could be read (not JSON, too large,
the wrong media type, a number too long), which also counts as a refused
call once the token is known. A line has the arguments, the outcome and the
duration (and the result, or the number of samples). Ten refused calls in a
row end the grant. A `busy` reply or a device error during a call doesn't
count toward that; a change refused because its reading failed does.

**Lapsed leases.** The leases renew themselves while the grant holds them.
Central's idle timeout (15 minutes without a command) can still end one.
Then the gateway takes the lease again once. It retries the call only when
the call never left the Control Center, or when the call only reads. A write
or command the Lab PC refused is not sent twice. If the lease can't be taken
again, the grant ends.

**Ending.** A grant ends when:
- its time runs out;
- someone presses **Stop**;
- ten calls in a row are refused;
- the run is cancelled, fails, goes quiet or finishes;
- the agent ends it (`POST runs/<run>/grant/end {"reason"}`);
- the Control Center shuts down.

It always ends the same way:
1. New calls are refused at once (`ended`/`expired`).
2. Calls in flight are cancelled.
3. The protocol's `safe_state` calls are made. Each gets up to three tries,
   and a lost lease is taken again once.
4. The leases are released, and the end is written to the audit file.
5. The grant becomes `ended`. If any safe-state call failed, it becomes
   `ended_unsafe`, and the run is marked `ended_unsafe` with a red banner:
   check those devices by hand.
6. The run moves on to `analyzing`, unless it has already ended.

If the Control Center dies or hangs mid-grant, it can't do any of this
itself: its leases lapse about a minute later. The
[safe-state watchdog](#the-safe-state-watchdog), a separate process, then
applies the safe state. At the Control Center's next start, grants left
`active` are marked `interrupted`, unless the watchdog has already finished
with them; then its `ended`/`ended_unsafe` stands.

**While a grant holds a device:**
- The Network and Devices pages show *Automation: <project>/<task> · ends in
  N min*, with a **Stop** button anyone viewing can press, and a link to the
  project.
- The device's own page refuses Play and scripts with 409. So does a script
  on another page that tries to lease it.

| Request | Does |
| --- | --- |
| `POST /api/automation/runner/runs/<run>/grant/token` `{}` | The grant's token, `grant` and the approved `protocol`, once (runner token; 409 after that) |
| `POST /api/automation/runner/runs/<run>/grant/end` `{"reason"}` | Ends the grant now (safe state, release); `{"grant"}` |
| `POST /api/automation/runner/gateway/calls` | One device call, with the grant token (above) |
| `POST /api/automation/projects/<id>/runs/<run>/grant/stop` `{}` | Stop from the project page (anyone; same-origin JSON) |
| `POST /api/automation/grants/<grant_id>/stop` `{}` | Stop from the Network/Devices page (anyone; same-origin JSON) |

##### The safe-state watchdog

`labnet-watchdog` is a small separate process on
`central_server_pc` (`labnet_control_center/watchdog/`). If the Control Center
dies, or its event loop hangs, while a grant holds devices, the watchdog
applies that grant's safe state. It never fights a live Control Center:
Central lets only one of them hold a device's lease at a time.

**What the Control Center writes for it.** While a grant holds its leases
(`active` or `ending`), a task on the Control Center's event loop rewrites
`state/<project>/grants/<id>.json` every 5 s with a fresh `alive_at`, so a
hung loop stops the heartbeat. The record holds everything the watchdog
needs: the approved normalized protocol (devices, types, safe-state calls),
its sha256, `ends_at` and the status. If the heartbeat can't be written
three times in a row, the Control Center ends the grant itself, with its safe
state.

**When the watchdog acts.** It reads every grant record every 2 s:
- A grant is taken over when its heartbeat hasn't changed for 20 s (timed on
  the watchdog's own clock) and either:
  - the Control Center's `/health/live` hasn't answered for 20 s either (it
    died); or
  - it answers but the heartbeat stays stale for 40 s (its loop hung).
- A grant the Control Center marked `interrupted` at a restart is taken over
  at once: the process that held it is gone.
- Never:
  - an `ended` or `ended_unsafe` grant;
  - one it has already finished;
  - one with no heartbeat for more than an hour, which is only logged
    (`LABNET_WATCHDOG_MAX_AGE_MINUTES`; check those devices by hand).
- Expired grants (time ran out while the Control Center was dead) are safed
  like the others.

**Taking over.** The watchdog writes only its own claim file,
`<id>.watchdog.json` next to the record, and lines of `"kind": "watchdog"`
in the grant's audit file. Two processes never write the same file.
1. The claim becomes `claimed`.
2. It asks Central for each device the safe state needs, every 3 s for up to
   120 s. Central refuses while the Control Center's lease lives.
3. If the Control Center's heartbeat comes back before a lease is taken, or
   it ends the grant itself, the watchdog **stands down** (`stood_down`).
   The same happens when nothing was leased in 120 s and the Control Center
   still answers.
4. With the first lease, the claim becomes `safing`. From then on the grant
   is the watchdog's, and a Control Center that comes back hands it over: it
   refuses calls, cancels calls in flight and releases its leases without a
   safe state of its own.
5. The protocol's sha256 is checked against the approved one, and each
   safe-state call is checked against its bounds as the gateway checks it.
   Each call gets up to three tries, re-taking a lost lease once. Then every
   lease is released.
6. The claim ends `ended` (*applied by the watchdog: the Control Center stopped
   answering*) or `ended_unsafe` (a call failed, or a device couldn't be
   leased in time: check it by hand).
7. The Control Center copies the outcome into the grant's record
   (`safe_state_by: "watchdog"`) and into the run, whose red banner shows an
   `ended_unsafe`. If it is down, its next start does this.

A watchdog stopped mid-takeover releases what it holds. If its claim was
`safing`, the next watchdog finishes it.

**Set it up on `central_server_pc`:**
1. Its own Central user (role `user`, never the Control Center's key):

   ```powershell
   $admin = "C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe"
   & $admin --env-file C:\ProgramData\LabNet\central.env setup `
       --user automation-watchdog --write-env C:\LabNet-Keys --server-url http://127.0.0.1:8008
   ```

2. Move `C:\LabNet-Keys\automation-watchdog.env` to
   `C:\ProgramData\LabNet\watchdog.env`, then delete `C:\LabNet-Keys`. Add
   the lines from [`site/watchdog.env.example`](../../site/watchdog.env.example):
   at least `LABNET_AUTOMATION_DIR`, the Control Center's own (default
   `C:\ProgramData\LabNet\automation`), and `LABNET_CA_FILE` if
   `control-center.env` sets one.
3. Run it as the same Windows account as the Control Center, which owns that
   folder. The command is installed with the Control Center:

   ```powershell
   C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-watchdog.exe --env-file C:\ProgramData\LabNet\watchdog.env
   Invoke-RestMethod http://127.0.0.1:8081/health/ready
   ```

   Start it by hand in its own window, after the Control Center (see
   [Starting the lab by hand](#starting-the-lab-by-hand)). To start it
   automatically at logon instead, register a Task Scheduler task. It has no
   window, so it writes a log file:

   ```powershell
   New-Item -ItemType Directory -Force C:\ProgramData\LabNet\logs | Out-Null
   $exe = "C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-watchdog.exe"
   $action = New-ScheduledTaskAction -Execute "cmd.exe" `
       -Argument "/c $exe --env-file C:\ProgramData\LabNet\watchdog.env >> C:\ProgramData\LabNet\logs\watchdog.log 2>&1"
   $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
       -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
   Register-ScheduledTask -TaskName "LabNet safe-state watchdog" -Action $action -Trigger $trigger -Settings $settings
   ```

   `Start-ScheduledTask "LabNet safe-state watchdog"` starts it now, and
   `Stop-ScheduledTask` stops it.
   - Only one watchdog runs per computer: a second one can't take port 8081
     and exits.
   - It doesn't matter whether it starts before or after the Control Center.
     Stop it before an update, like the Control Center.

**In development,** `scripts/dev/seed.sh` creates the user and
`.devnet/automation-watchdog.env`. `scripts/dev/up.sh` starts the watchdog
after the Control Center (log: `.devnet/logs/automation-watchdog.log`,
health: `http://127.0.0.1:8081/health/ready`). `down.sh` stops it first,
`status.sh` shows it, and `restart.sh watchdog` restarts it. A network seeded
before the watchdog existed gets its user with `scripts/dev/seed.sh
--watchdog`, which deletes nothing.

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
- **An open Automation project:** the chat shows "Photo-Agent sees:
  <project>". With each message it gets:
  - a listing of the project's files;
  - a copy of its text files (up to 4 MB) in that conversation's uploads.

  It gets copies, not the folder itself, because an added folder would also
  load any Claude Code settings someone put in it.
- **Briefing:** its role and the lab's script style (`from_env()`,
  try/except/finally, release in reverse order) are in
  [`agent_briefing.md`](../labnet-control-center/src/labnet_control_center/agent_briefing.md).

It writes scripts for people to run. It never runs them.

In development, `scripts/dev/seed.sh` points it at this checkout's `packages/`
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
   [`site/control-center.env.example`](../../site/control-center.env.example).
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

   Either site block forwards to the Control Center through the logins
   snippet that step 4 writes
   ([`site/Caddyfile.example`](../../site/Caddyfile.example) has the whole file):

   ```caddyfile
   {
       admin off                                     # first block: see Logins
   }
   import C:/ProgramData/LabNet/caddy-logins.caddy

   https://pqt-control-center {          # or: https://192.168.50.10:8443
       tls C:/ProgramData/LabNet/certs/control-center.crt C:/ProgramData/LabNet/certs/control-center.key
       import labnet_control_center
   }
   ```

4. **Logins.** List the lab members in `C:\ProgramData\LabNet\logins.txt`
   and run `scripts/lab/setup_logins.ps1` (see [Logins](#logins)). It writes
   the snippet and sets `LABNET_CONTROL_CENTER_PROXY_SECRET` in
   `control-center.env`, and prints each person's password once.
5. **Browser trust.** Python clients use `LABNET_CA_FILE`, but browsers use the
   Windows certificate store. Install the CA once on each viewing computer
   with `certutil -addstore Root labnet-ca.pem`, or the page shows a
   certificate warning.
6. **Start** it by hand in its own PowerShell window on `central_server_pc`,
   and restart Caddy so it reads the snippet (stop it with Ctrl+C and run it
   again: with `admin off`, `caddy reload` and `caddy stop` don't work):

   ```powershell
   C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-control-center.exe --env-file C:\ProgramData\LabNet\control-center.env
   ```

   If Central isn't running yet, the page shows a "can't reach Central"
   banner and recovers on its own once Central starts.
7. **Check it from a viewing computer**, not the server:
   - `curl.exe https://pqt-control-center/health/ready` returns 200 when the
     Control Center is up *and* reaching Central. It needs no login.
   - Opening `https://pqt-control-center/status` asks for a login, and the
     top bar then shows "Signed in as <name>".
   - `curl.exe http://127.0.0.1:8080/status` on the server itself is refused
     (403): only requests through Caddy get in.

Use hyphens in the name: `pqt_control_center` with underscores is not a
valid host name, and certificate tools reject it. The built-in server only
binds to loopback. Caddy makes it reachable, over HTTPS, so passwords never
cross the network in clear text.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | Central URL; keep the loopback default when it runs on the Central PC |
| `LABNET_API_KEY` | required | The Control Center's own Central key (role `user`) |
| `LABNET_CA_FILE` | system trust | Only needed if it ever runs on a different computer and reaches Central over HTTPS |
| `LABNET_CONTROL_CENTER_HOST` / `_PORT` | `127.0.0.1` / `8080` | Built-in HTTP listener; loopback only |
| `LABNET_CONTROL_CENTER_PROXY_SECRET` | none (no logins) | Turns on [logins](#logins): only requests carrying it (from Caddy) get in. At least 32 printable ASCII characters, no spaces; `setup_logins.ps1` makes one |
| `LABNET_CONTROL_CENTER_ALLOWED_HOSTS` | `127.0.0.1,localhost`; any host with the secret | Host names the pages answer to, comma-separated, without ports or schemes (e.g. `pqt-control-center,192.168.50.10`); others get 400 |
| `LABNET_CONTROL_CENTER_REFRESH_SECONDS` | `3` | How often the network picture is refreshed |
| `LABNET_CONTROL_CENTER_MAX_HOLD_MINUTES` | `30` | Play stops itself and releases the device after this long |
| `LABNET_CONTROL_CENTER_IDLE_RELEASE_SECONDS` | `15` | A device page releases its device this long after the last viewer leaves |
| `LABNET_CONTROL_CENTER_SCRIPT_TIMEOUT_SECONDS` | `600` | A script is stopped after this long |
| `LABNET_CONTROL_CENTER_EXPERIMENTS_DIR` | `experiments` next to the env file | The Experiments tab's folder: one JSON file per experiment, created on the first save |
| `LABNET_AUTOMATION_DIR` | `automation` next to the env file | The Automation tab's folder: `projects/` and `state/` (see [Automation](#automation)) |
| `LABNET_AUTOMATION_MAX_FILE_MB` | `100` | The largest file people, or a runner, may upload to a project |
| `LABNET_AUTOMATION_RUN_QUOTA_MB` | `500` | How much one [run](#runs) may upload in all |
| `LABNET_AUTOMATION_RUN_STALE_MINUTES` | `10` | How long an active [run](#runs) may go without a word from its runner before it is marked failed |
| `LABNET_AGENT_COMMAND` | `claude` on `PATH`, then `~\.local\bin\claude.exe` | Claude Code for Photo-Agent; if it can't be found, the chat says so |
| `LABNET_AGENT_WORKSPACE` | `agent` next to the env file | Photo-Agent's folder: `briefing.md`, `home/` (where it runs) and `uploads/` |
| `LABNET_AGENT_SOURCES` | the installed `labnet` package | Folders it may read, separated by `;` on Windows. In the lab, list the package folder plus any folder where you unpacked the examples (`client-examples.zip`) or lab notes |
| `LABNET_AGENT_LITERATURE_DIR` | none | Folder of papers it may read |
| `LABNET_AGENT_MODELS` | `fable,opus,sonnet,haiku` | The chat's model menu: aliases for the latest of each model, or full ids such as `claude-opus-5-5`. List only models your plan includes |
| `LABNET_AGENT_MODEL` | `sonnet` | The model selected at first; one of `LABNET_AGENT_MODELS` |
| `LABNET_AGENT_EFFORT` | `high` | The effort selected at first: `low`, `medium`, `high`, `xhigh` or `max` |
| `LABNET_AGENT_TIMEOUT_SECONDS` | `300` | An answer is stopped after this long |
| `LABNET_AGENT_MAX_CONCURRENT` | `2` | Answers in progress at once; later messages wait their turn |

### Logins

In the lab, each person signs in with their own login. Caddy checks it
([`basic_auth`](https://caddyserver.com/docs/caddyfile/directives/basic_auth),
Caddy 2.8 or later) and adds two headers to every request it forwards:

| Header | Value |
| --- | --- |
| `X-LabNet-Proxy` | `LABNET_CONTROL_CENTER_PROXY_SECRET`, shared by Caddy and the Control Center |
| `X-LabNet-User` | The login name |

With the secret set, the Control Center refuses (403) any request without
it. A program on the server that dials `127.0.0.1:8080` directly gets
nowhere, so the user name can be trusted:
- the top bar shows "Signed in as <name>";
- it is the author of experiments and projects, and who queued or
  cancelled a run (the *Created by* box is hidden);
- later, it is who approved a protocol.

There is no sign-out button: the browser remembers the login until it is
closed.

**Setting up or changing logins.** On `central_server_pc`, as the Windows
account that runs Caddy and the Control Center (otherwise name theirs with
`-ServiceAccount`):

1. List the logins in `C:\ProgramData\LabNet\logins.txt`, one per line.
   Names use letters, digits, `.`, `_` and `-` (at most 64); `#` starts a
   comment. Names that differ only in case are refused.
2. Copy `scripts/lab/setup_logins.ps1` from the repository (the release
   doesn't include it) and run it:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup_logins.ps1 -WhatIf   # shows what would change
   powershell -ExecutionPolicy Bypass -File .\setup_logins.ps1
   ```

3. Hand each new password to its owner. They are shown only this once.
4. Restart Caddy. If the script made a new secret, restart the Control
   Center too, so both use it.

Run it again whenever someone joins or leaves, or forgets their password.
Existing logins keep their passwords; new ones get a random password
(20 characters, no look-alikes); logins no longer listed are removed.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `-UsersFile` | `C:\ProgramData\LabNet\logins.txt` | The logins file |
| `-CaddySnippet` | `C:\ProgramData\LabNet\caddy-logins.caddy` | The snippet it writes |
| `-EnvFile` | `C:\ProgramData\LabNet\control-center.env` | Where it sets `LABNET_CONTROL_CENTER_PROXY_SECRET`; every other line stays as it is |
| `-Caddy` | `caddy` on `PATH` | Path to `caddy.exe`, used for `caddy hash-password` |
| `-Reset` | none | Logins that get a new password, e.g. `-Reset alice,bob` |
| `-RunnerAddress` | any address, or what an earlier run was given | IP addresses or CIDR ranges allowed to use the runner API, e.g. the runner laptop's (full dotted form) |
| `-AnyRunnerAddress` | | Removes an earlier `-RunnerAddress` restriction |
| `-Upstream` | `127.0.0.1:8080`, or what an earlier run was given | Where Caddy forwards to |
| `-ServiceAccount` | none | Accounts that may read the two files besides yours, e.g. `'NT AUTHORITY\NetworkService'` if Caddy runs as a service |
| `-Caddyfile` | `C:\ProgramData\LabNet\Caddyfile` | Only checked: the script warns if it doesn't say `admin off` |
| `-WhatIf` | | Writes nothing and says what would change |

It checks that it can write both files before writing either, writes the
env file first, and prints new passwords as soon as their hashes are in
the snippet, so a failure halfway never loses a password. Passwords reach
`caddy hash-password` on its standard input, never on a command line.

The script keeps a secret already in the env file if it is at least 32
characters of `A–Z a–z 0–9 . _ ~ + / = -`; otherwise it makes a new one.

**The snippet** defines `(labnet_control_center)`. The Caddyfile imports the
file at the top and uses `import labnet_control_center` in the Control
Center's site block (step 3 above). It holds bcrypt hashes, never
passwords, and the proxy secret, which `control-center.env` holds too.
So the script restricts both files to Administrators, SYSTEM, the account
that ran it and any `-ServiceAccount`, and refuses either file if another
account owns it (anyone may create files in `C:\ProgramData`, and an owner
can always grant themselves access again). Inside the snippet, three
blocks, in order:

| Path | Login | Headers |
| --- | --- | --- |
| `/health/*` | none | Both `X-LabNet-*` headers removed |
| `/api/automation/runner/*` (from `-RunnerAddress` only, if given) | none | Both `X-LabNet-*` headers removed |
| everything else | `basic_auth` | `Authorization` removed; `X-LabNet-Proxy` and `X-LabNet-User` added |

**What is exempt, and why.**
- **Health checks**, so monitoring and the check step work without a login.
  They show only whether the Control Center is up and reaching Central.
- **The runner API**, because a runner is a program, not a person: it
  proves itself with a [runner token](#runners-and-their-tokens) instead.
  It refuses anything a browser sends. With `-RunnerAddress`, the runner
  API from any other address falls through to the login, which strips the
  token, so it can't be used from there.

The Control Center exempts the same two prefixes (lowercase, exactly). Caddy
removes the two headers there, so they can't be forged.

**Caddy's admin API must be off** (`admin off` in the Caddyfile's first
block, as in [`site/Caddyfile.example`](../../site/Caddyfile.example)). Caddy
otherwise answers on `localhost:2019` without a password: any program on the
server could read the running config, secret included, or load one without
logins.

**Whatever the mode,** a change (POST, PUT, PATCH, DELETE) that a browser
says came from another web site is refused (403). The Experiments and
Automation APIs also take changes only as JSON or a raw upload, which a
cross-site form can't send. A page elsewhere can't pause a device, start a
script, or edit an experiment through someone's open tab.

**Development has no logins.** Without `LABNET_CONTROL_CENTER_PROXY_SECRET`
anyone who can reach the port is trusted, the headers are ignored (anyone
could send them), and the *Created by* name typed on the page is recorded.
That is safe only because the server binds to loopback on a one-person
computer.

**The host check.** The pages answer only to the host names in
`LABNET_CONTROL_CENTER_ALLOWED_HOSTS`; any other `Host` gets 400 "Invalid
host header". This stops a web page from pointing its own name at
`127.0.0.1` and reading the Control Center through a browser.
- Without the secret, the default is `127.0.0.1,localhost`.
- With the secret, the default is any host: the secret already stops that
  trick, and Caddy forwards the name people typed.

**Upgrading a lab install without logins.** Caddy passes on the name people
typed (`pqt-control-center` or `192.168.50.10`), which the loopback-only
default refuses. Either set up logins, or add
`LABNET_CONTROL_CENTER_ALLOWED_HOSTS=pqt-control-center,192.168.50.10` to
`control-center.env` before restarting the Control Center.

| Symptom | Cause | Fix |
| --- | --- | --- |
| 400 "Invalid host header" | The name used isn't allowed | Set `LABNET_CONTROL_CENTER_ALLOWED_HOSTS`, or set up logins |
| 403 "Open the Control Center through its sign-in address (Caddy)." | No secret, or a different one: port 8080 opened directly, a site block with its own `reverse_proxy` instead of `import labnet_control_center`, or Caddy and the Control Center not both restarted after a new secret | Open it through Caddy; fix the site block; restart both |
| 403 "The sign-in proxy sent no usable user name." | A hand-edited Caddyfile sends the secret without a login | Use the snippet `setup_logins.ps1` writes, unchanged |
| The Control Center won't start: "…PROXY_SECRET must be at least 32…" | A short or hand-made secret | Run `setup_logins.ps1` again |

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
| `LABNET_DEVICE_INVENTORY` | none: the Lab PC refuses to start | `package.module:function` or `C:/path/file.py:function` |
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
   PC's inventory in `site/inventories/`, with any [site limits](#site-limits)
   this bench needs.
3. Release, run `update_host.sh lab-pc`, and restart the Lab PC.
4. Confirm Central lists the device and a client can lease it.

### A new device family

A device family is a driver package: `labnet driver new acme-thing --service
AcmeThing` creates a working one, and
[`labnet-sim`](../labnet-sim/) is a larger example. In short:

1. **Define the contract** in `protos/<package>/v1/<family>.proto` (package
   `<package>.v1`; `labnet.*` is reserved). It imports
   `labnet/devices/v1/common.proto` and `rpc_options.proto`. Every RPC
   declares its pattern, physical method, and default unit (use `"1"` for
   dimensionless values):

   | Pattern | RPC shape | Physical behavior |
   | --- | --- | --- |
   | `RPC_PATTERN_SCALAR_READ` | Unary | One physical read, returned as a scalar measurement |
   | `RPC_PATTERN_SCALAR_WRITE` | Unary | An absolute double setpoint, returned as a scalar measurement |
   | `RPC_PATTERN_UNARY_COMMAND` | Unary | Any driver method; its return value comes back as a typed `CommandResult` (none, bool, integer, float, text, or integer list) |
   | `RPC_PATTERN_POLLED_SCALAR_STREAM` | Server streaming | Repeated physical reads with scheduling, cancellation, lease revalidation, and backpressure |

2. **Generate** with `labnet driver build` (needs `labnet[driver-dev]`).
   Never edit generated files. It also checks the package's
   `[project.entry-points."labnet.devices"]`, one entry per device type.
3. **Implement `physical.py`** with a stable `device_id`, the generated
   `device_type`, `connect`, `disconnect`, `status`, `availability`, JSON-compatible
   `metadata`, and every physical method the contract names. Optional proto
   arguments need Python defaults. Extra required arguments are rejected
   when the Lab PC starts.
4. **The facade is generated** (`_generated/facades.py`), named after the
   service without "Service": `AcmeThingService` -> `AcmeThing`. Export it and
   the driver from the package's `__init__.py`. Export Script imports it from
   there. Do not write family-specific gRPC or transport code; dispatch,
   authorization, errors, leases, and streams are shared.
5. **Optionally publish hints** in `metadata()`. The Control Center's
   experiment forms and checks use them, and Photo-Agent sees them:

   ```python
   "hints": {"channels": ["CH1", "CH2"],
             "limits": {"set_frequency.value": [60, 100]},          # <method>.<argument>
             "choices": {"set_waveform.waveform": ["sine", "square"]}}
   ```

   The Lab PC enforces published limits and choices on every remote call
   (see [Site limits](#site-limits)), and a protocol can bound only numbers
   that have one. Drivers must still check their own ranges: local use of
   the driver skips the Lab PC.
6. **Validate:** `labnet driver build --check`, `pytest` (with `labnet.testing.check_driver`).

The four `Dummy*` optics-bench families (`labnet/devices/DummyLaser`,
`DummyAOM`, `DummyEOM`, `DummyAWG`) are small, complete examples of channels,
boolean and text commands, streams, hints and simple physics.
`SimulatedTunableLaser` is a motor-tuned laser whose moves take time: an
example of whole-number arguments, published choices, and a driver that
publishes no numeric limits (so its Lab PC must set site limits).

Deploy Lab PCs that serve a new RPC before clients that call it.

### Site limits

A driver's hints are the instrument's own limits. A bench is often narrower:
this AOM's mount clips beyond 95 MHz, or this laser may tune only around
780 nm. The Lab PC inventory says so with `site_limits` and returns the same
device, so its type and contract don't change:

```python
from labnet.devices.core import site_limits
from labnet_sim.simulated_tunable_laser.physical import PhysicalSimulatedTunableLaser

def create_devices():
    return [
        site_limits(
            PhysicalSimulatedTunableLaser(device_id="lab01-laser"),
            limits={"go_to_wavelength_nm.wavelength_nm": (770.0, 790.0),
                    "move_steps.relative_steps": (-2000, 2000)},
            choices={"move_steps.velocity_mode": ["slow"]},
        ),
    ]
```

- **Keys** are `"<method>.<argument>"`, exactly as in hints and Python
  clients. `limits` takes `(min, max)` for a number. `choices` takes a list
  for text or `True`/`False`. Living examples:
  `labnet_sim/inventories/dev_lab_pc_01.py` (the AOM narrowed to 65–95 MHz,
  the dummy devices' `write_value` to ±100).
- **The Lab PC refuses to start** with a clear error when:
  - a key names no method or argument of the device's contract;
  - a range is inverted or not finite;
  - a value has the wrong kind (a range for text, choices for a number);
  - a site limit isn't inside the driver's own published limit, or a choice
    isn't among the driver's choices or channels. Site limits may only narrow.
    This last check runs when the device connects and publishes its hints.
    Every device is then stopped again, and nothing is advertised to Central.

  A driver whose own hints are malformed also stops the Lab PC.
- **Every remote call is checked on the Lab PC** before the driver is
  called, at the one dispatch point all gRPC calls, streams and Control
  Center calls pass (`DeviceWorker.invoke`). The check uses the effective
  limits: the driver's published ranges, choices and channels, narrowed by
  the site's. So hints are never merely advisory.
  - A call outside them is refused with gRPC `INVALID_ARGUMENT` and
    `labnet-error-code: ARGUMENT_OUTSIDE_LIMITS`. Python clients raise
    `InvalidDeviceArgumentError` (`error_code == "ARGUMENT_OUTSIDE_LIMITS"`,
    not retryable), with a message such as
    `aom.set_frequency: value=96 is outside the site limit 65 to 95.`
  - `true` is never a number, and a whole-number argument takes only
    integers.
  - Choices match exactly: `"Square"` is refused where `"square"` is
    published.
  - An argument with a **site** limit must be sent. Leaving it out would
    use the driver's default, which may lie outside the limit.
  - Two RPCs of one physical method share limits. `read_value` and
    `stream_data` both call `read_value`, so a site choice for
    `read_value.channel` also limits `stream_data`'s channel.
  - A stream's own controls (`sample_rate_hz`, `sample_count`) aren't
    limited here.
  - A driver used locally, without a Lab PC, is not checked.
- **Published hints** are the effective values. `metadata()["hints"]` gains
  `sources`: `{"<method>.<argument>": "site" | "driver"}`. The Control
  Center names each limit's source in protocol checks, in `DEVICES.md`
  (`(site limit)`), and in the review (`limit_source`).

To change site limits, edit the inventory and restart that Lab PC
(`scripts/dev/restart.sh <lab-pc id>` on the dev network).

### Contract compatibility

Once a contract is deployed:

- never change the number or type of an existing protobuf field;
- add new optional fields with unused numbers;
- reserve removed field numbers and names;
- keep existing RPC semantics while older clients may call them; and
- introduce a new protobuf package for breaking changes.

## Development and testing

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
| Lab PC refuses to start | No inventory, or no key in production | Set `LABNET_DEVICE_INVENTORY` and `LABNET_DEVICE_MANAGER_API_KEY` |
| Client sees no available devices | Lab PC down, device unhealthy, or every device leased | Both health endpoints and Central's device list |
| Client times out connecting to a TLS Lab PC | CA not configured, or IP not in the Lab PC certificate | `LABNET_CA_FILE`; compare `labnet-certs show` with `LABNET_GRPC_ADVERTISED_TARGET` |
| Proto check reports stale files | Generated bindings do not match the protos | `labnet driver build --core`, then commit every generated change |
| A device shows **Driver mismatch** or **No driver here** | The Lab PC and the Control Center run different driver packages or versions for that device type, or the Control Center lacks it | Install the same release of the driver package (for example `labnet-sim`) on both; the device's details say which. Automation refuses a mismatched device |
| Physical contract validation fails | Driver method missing or with an incompatible signature | Compare `physical_method` and forwarded fields with `physical.py` |
| Lease lost during an experiment | Renewal failure, lease deadline, or idle timeout | Client and Central logs; network stability; timeout policy |

## Credential handling

Settings files, private keys, caches, build outputs, and runtime databases are
ignored by Git. Keep each settings file only on the computer that uses it,
restrict its permissions, and rotate any exposed API key or private key
immediately. The same goes for the Control Center's credentials:
- **Proxy secret:** delete the `LABNET_CONTROL_CENTER_PROXY_SECRET` line,
  run `setup_logins.ps1` again, and restart Caddy and the Control Center.
- **A login's password:** `setup_logins.ps1 -Reset <name>`, then restart Caddy.
- **A runner token:** `labnet-control-center-admin runner revoke <name>`,
  then `runner add <name>`.
