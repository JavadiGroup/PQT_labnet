# LabNet

LabNet is a lease-controlled device network for the PQT lab. Version 1 is a clean break from the former WebSocket RPC design: the Central Server manages identity, discovery, and leases over HTTP, while clients communicate directly with each Lab PC through typed gRPC.

## Architecture

| Surface | Default | Responsibility |
| --- | --- | --- |
| Central HTTP API | `127.0.0.1:8008` | Authentication, device registry, leases, immutable release metadata/artifacts, deployments, and audit history |
| Lab-PC HTTP management | `127.0.0.1:9001` | Liveness, readiness, and token-authenticated loopback update lifecycle control |
| Lab-PC gRPC | `127.0.0.1:50051` | Typed `ReadValue`, `WriteValue`, and streamed measurements |
| Labupdater | separate OS service on each managed Lab PC | Versioned runtime staging, safe drain, process supervision, health verification, and rollback |

The control path is:

1. A Lab PC registers its stable manager ID, device inventory, and gRPC endpoint with Central.
2. A client discovers an available device and requests an exclusive lease from Central.
3. Central returns an opaque lease ID and that device's typed gRPC endpoint.
4. The client calls the Lab PC directly, carrying the lease ID in `x-labnet-lease-id` metadata.
5. The Lab PC validates the lease with Central before control and periodically during a long stream.

Central never proxies or stores measurement samples. Each physical device has a dedicated bounded worker, so its blocking driver calls remain ordered without blocking the asynchronous network servers.

Managed installations add a second, independent package at the repository's
top-level `Labupdater/` directory. It is deliberately excluded from the
`labnet` wheel. The updater can replace a LabNet runtime because its own Python
environment remains stable while the Lab-PC child process stops and starts.

## Getting started

Python 3.11 or newer is required. LabNet must be deployed and started in this
order:

1. **Central Server** -- creates identities, owns discovery and leases, and must
   report ready before a Lab PC starts.
2. **Lab PC** -- registers its device inventory with Central and must report
   ready before a client connects.
3. **Client** -- authenticates with a `user` key, acquires a lease from Central,
   and then calls the Lab PC directly over gRPC.

Do not continue to the next numbered role until the verification gate for the
current role succeeds. This order applies both to the single-host quick start
below and to the [managed deployment runbook](#required-managed-deployment-sequence).

### Local end-to-end quick start

The local defaults bind every service to loopback, so these instructions run
Central, the Lab PC, and the client on the **same Windows computer**. Run every
command from the repository root in PowerShell.

#### 0. Prepare the checkout

Create one development environment containing all three roles:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[lab-pc,central,dev]"
```

Activate that environment in each new PowerShell window with
`.\.venv\Scripts\Activate.ps1`. If the roles are being installed into separate
environments, use the smallest applicable install, in deployment order:

| Role | Development-checkout install |
| --- | --- |
| 1. Central Server | `python -m pip install -e ".[central]"` |
| 2. Lab PC | `python -m pip install -e ".[lab-pc]"` |
| 3. Client | `python -m pip install -e .` |

PostgreSQL Central deployments use `.[central,postgres]`. Editable (`-e`)
installs are for development; managed hosts install versioned artifacts as
described later.

#### 1. Deploy and start the Central Server

SQLite is built into Python, so the local Central Server needs no separate
database service. Its defaults use `127.0.0.1:8008` and a database in the
current user's application-data directory.

Review [`central_server/.env.example`](central_server/.env.example) if an
override is needed, but do **not** copy it to `.env`: Central does not load an
environment file. Set overrides such as `LABNET_DATABASE_URL` and
`LABNET_RELEASE_ROOT` in the PowerShell process or service environment before
running any migration, administration, or server command.

For a new local database, run the migration and create one principal for each
responsibility:

```powershell
python -m central_server.manage db upgrade
python -m central_server.manage user create central-admin --role admin
python -m central_server.manage user create lab-pc-dev-01 --role device_manager
python -m central_server.manage user create researcher --role user
```

Each `user create` command prints its API key exactly once. Save the three keys
separately. The `device_manager` username must exactly equal the Lab PC's
`LABNET_LAB_PC_ID`; the example uses `lab-pc-dev-01`. Give the client the
`researcher` key, not the admin or Device Manager key.

Start Central and leave this PowerShell window running:

```powershell
python -m central_server.app.main
# Installed console equivalent: labnet-central
```

In a second activated PowerShell window, verify both health endpoints:

```powershell
Invoke-RestMethod http://127.0.0.1:8008/health/live
Invoke-RestMethod http://127.0.0.1:8008/health/ready
```

**Gate 1:** both commands must return `status: ok`. Normal server startup never
creates or migrates tables; run `python -m central_server.manage db upgrade`
after every release that contains a migration.

#### 2. Configure and start the Lab PC

Central must still be running and ready. In another activated PowerShell
window, copy the local configuration template:

```powershell
Copy-Item .\lab_pc\.env.example .\lab_pc\.env
```

Before starting the Lab PC, perform both configuration tasks:

1. Edit [`lab_pc/device_inventory.py`](lab_pc/device_inventory.py) so
   `create_devices()` constructs the physical adapters attached to this
   computer. The checked-in function creates `dummy_device_1`, which is enough
   for this quick start.
2. Edit the untracked `lab_pc/.env` and set these values, using the Device
   Manager key printed in step 1:

```dotenv
LABNET_LAB_PC_ID=lab-pc-dev-01
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=http://127.0.0.1:8008
LABNET_DEVICE_MANAGER_API_KEY=<device-manager API key>
LABNET_LEASE_VALIDATION_MODE=central
```

The ID must still match Central's `device_manager` username exactly. Then start
the Lab PC and leave the window running:

```powershell
python -m lab_pc
# Installed console equivalent: labnet-lab-pc
```

In another activated PowerShell window, verify the Lab PC:

```powershell
Invoke-RestMethod http://127.0.0.1:9001/health/live
$labPc = Invoke-RestMethod http://127.0.0.1:9001/health/ready
$labPc | ConvertTo-Json -Depth 6
if (-not $labPc.ready -or -not $labPc.grpc.serving -or -not $labPc.central.registered) {
    throw "Lab PC is not ready, registered, and serving gRPC."
}
```

**Gate 2:** readiness must show `ready: true`, `grpc.serving: true`,
`central.registered: true`, and at least one device with `available: true`.

For a managed machine, inventory lives outside the release runtime and is
loaded through `LABNET_DEVICE_FACTORY=lab_inventory:create_devices`; do not use
the development inventory file as the machine-owned source of truth.

#### 3. Install and run the client

Central and the Lab PC must both remain running and ready. The shared
development environment already contains the client; a separate client-only
environment would use `python -m pip install -e .`.

Set the `user` key created in step 1 for this PowerShell session:

```powershell
$env:LABNET_CENTRAL_URL = "http://127.0.0.1:8008"
$env:LABNET_RESEARCHER_API_KEY = "<researcher API key>"
```

Save the following program as `client_smoke.py`. It verifies the complete path:
Central authentication and discovery, lease acquisition, direct gRPC write,
read and stream operations, lease release, and transport cleanup.

```python
import asyncio
import os

from labnet import AsyncLabClient, GrpcTlsConfig


def grpc_tls_config() -> GrpcTlsConfig | None:
    root = os.getenv("LABNET_GRPC_CA_FILE")
    private_key = os.getenv("LABNET_GRPC_CLIENT_KEY_FILE")
    certificate = os.getenv("LABNET_GRPC_CLIENT_CERT_FILE")
    if not any((root, private_key, certificate)):
        return None
    return GrpcTlsConfig.from_files(
        root_certificates=root,
        private_key=private_key,
        certificate_chain=certificate,
    )


async def main() -> None:
    central = AsyncLabClient(
        os.environ["LABNET_CENTRAL_URL"],
        os.environ["LABNET_RESEARCHER_API_KEY"],
    )

    try:
        await central.authenticate()
        devices = await central.list_available_devices("dummy_device")
        print([device.device_id for device in devices])
        if not devices:
            raise RuntimeError("Central has no available dummy_device.")

        device = await central.acquire_dummy(
            devices[0].device_id,
            duration_seconds=60,
            tls=grpc_tls_config(),
        )

        try:
            await device.write_value(384_230_000_000_000.0)
            measurement = await device.read_value()
            print(measurement.value, measurement.unit)

            samples = device.stream_data(
                sample_rate_hz=10,
                sample_count=20,
            )
            try:
                async for sample in samples:
                    print(sample.sequence, sample.value)
            finally:
                await samples.aclose()
        finally:
            await device.aclose()
    finally:
        await central.aclose()


asyncio.run(main())
```

Run it from the activated client environment:

```powershell
python .\client_smoke.py
```

**Gate 3:** the script must list a device, complete write/read/stream calls, and
exit without an exception. Passing authentication or discovery alone is not a
complete deployment test because device operations travel directly from the
client to the Lab PC rather than through Central.

This smoke program targets the checked-in `dummy_device` contract. When a Lab
PC exposes only another device family, replace the discovery type, acquisition,
and typed operations with that family's generated contract while preserving the
same authenticate -> discover -> acquire -> operate -> close sequence.

Synchronous programs use the public `DummyDevice` class for both local and
remote hardware. Omitting `client` selects the physical implementation in the
current process; passing a `LabClient` selects the leased gRPC implementation.
The experiment-facing method calls stay the same:

```python
import os

from labnet import DummyDevice, LabClient


def set_and_read(device: DummyDevice) -> float:
    device.write_value(12.5, channel="phase")
    return device.read_value(channel="phase")


local_device = DummyDevice(device_id="bench-dummy-1")
try:
    print(set_and_read(local_device))
finally:
    local_device.close()

central = LabClient(
    os.environ["LABNET_CENTRAL_URL"],
    os.environ["LABNET_RESEARCHER_API_KEY"],
)

try:
    device = DummyDevice(
        client=central,
    )
    try:
        print(set_and_read(device))
    finally:
        device.close()
finally:
    central.close()
```

Create one `LabClient` per user process and pass it to every remote device
handle. For example, `DummyDevice(device_id="dummy-a", client=central)` and
`DummyDevice(device_id="dummy-b", client=central)` can share the same Central
authentication and HTTP client; each handle still owns and releases its own
exclusive device lease. Close every device in `finally` before closing the
shared client.

##### Choosing `LabClient` or `AsyncLabClient`

Both clients use the same Central HTTP API, lease rules, and direct gRPC device
services. The difference is how the calling program waits for network work:

| Client workflow | Waiting model | Device client | Lease renewal | Best use case |
| --- | --- | --- | --- | --- |
| `LabClient` | Synchronous and blocking | `DummyDevice(client=central)` | Background thread | Sequential experiment, calibration, and one-off instrument scripts |
| `AsyncLabClient` | `asyncio` and non-blocking | Shared `AsyncContractDeviceClient` returned by `await acquire_dummy(...)` or `await acquire_device(...)` | Background asyncio task | Concurrent devices, continuous streams, automation services, and responsive monitoring applications |

Choose `LabClient` when an experiment naturally performs one operation after
another: acquire an instrument, configure it, collect a result, analyze it, and
release it. Its blocking calls keep this common laboratory workflow easy to
read and debug, while lease renewal still runs automatically in the background.

Choose `AsyncLabClient` when the program must make progress elsewhere while a
network operation is waiting. It can monitor several independent instruments,
consume a stream while controlling another device, or integrate into an
existing asyncio application without blocking its event loop. Independent
operations can be coordinated with tools such as `asyncio.gather()`.

Async operation does not make a single physical instrument execute multiple
commands simultaneously. LabNet deliberately serializes each device's hardware
operations to protect VISA, serial, and other stateful driver connections. For
most experiment scripts, start with `LabClient`; move to `AsyncLabClient` when
the application has a concrete concurrency or streaming requirement.

Stable idempotency keys should be supplied when an application may retry a
lease request after losing the HTTP response.

### Renewable lease lifecycle

Device clients use a 60-second lease by default and renew it every 20 seconds
while the object remains healthy. A healthy renewal therefore occurs at
one-third of the TTL and leaves about 40 seconds for transient recovery.
Calling `close()` still releases the lease immediately; if the process
crashes, loses power, or cannot reach Central, renewals stop and Central
expires the lease.

Central separately expires a lease after 900 seconds without an authorized
device command. A renewal updates client liveness but deliberately does not
update command activity, so a forgotten client cannot hold a device forever
merely because its renewal task is still running. Unary authorization counts
as activity, and a live `StreamData` call refreshes activity through periodic
lease validation.

The command-idle value is a Central safety policy rather than a client option.
This prevents a client from disabling or weakening the lab's cleanup policy.
Central rejects renewal at either the lease deadline or idle deadline. The
client then marks its authority lost, closes the gRPC transport, and refuses
new device calls. A physical operation already admitted before a deadline is
not forcibly interrupted; the per-device worker remains serialized until that
operation finishes.

Keep the Central idle timeout longer than the Lab-PC stream revalidation
interval and the longest expected unary device operation. The defaults are
comfortably separated: 900 seconds idle, 5 seconds between stream validations,
and a 30-second unary-operation ceiling.

## Database model

The SQL database is only the management-plane source of truth. It contains users and hashed API keys, Lab-PC identities and heartbeats, registered devices, leases, idempotency records, and audit events. It does not contain streamed experimental data.

SQLite is the practical default for one Central process. Lease mutations use database transactions, and a database uniqueness constraint—not an in-process Python lock—enforces one active lease per device. Terminal leases remain for audit. PostgreSQL uses the same SQLAlchemy models and Alembic migrations and is the upgrade path for multiple Central workers or replicas.

## Streaming and control semantics

`DummyDeviceService` exposes typed `ReadValue`, `WriteValue`, and server-streaming `StreamData`. A stream request can select a channel, rate, sample count, and duration; the first reached bound ends the stream. Each response carries a sequence number, Unix timestamp in nanoseconds, double-precision value, unit, quality, and status.

The default 1 kHz ceiling targets wavemeter-like telemetry and supervisory locking. Protobuf sends binary doubles incrementally with gRPC flow control, eliminating JSON/Base64 expansion and whole-acquisition buffering. Cameras and oscilloscopes should use a future chunked-array contract rather than forcing large arrays through this scalar stream.

Ethernet, Python, and a general-purpose operating system are not hard real-time. Keep a stability-critical inner servo on the Lab PC or dedicated controller; use LabNet for setpoints, coordination, and telemetry. `WriteValue` is an absolute-setpoint operation and therefore safe to repeat for this dummy contract. A future non-idempotent command must add durable operation reconciliation before automatic retry.

Failures use canonical gRPC status codes plus stable trailing metadata, including the operation ID and whether a timed-out physical operation may already have completed. Driver tracebacks remain in Lab-PC logs.

## TLS policy

Plaintext Central HTTP and gRPC are allowed only on loopback for development. A real multi-machine lab deployment needs:

- an HTTPS reverse proxy in front of the loopback-bound Central Server;
- `LABNET_GRPC_TLS_MODE=tls` for encrypted Lab-PC traffic and server authentication; or
- `LABNET_GRPC_TLS_MODE=mtls` when both Lab PCs and clients should present certificates.

TLS is the policy that defines which certificates are trusted, which endpoints must be encrypted, and whether one-way server authentication or mutual authentication is required. The code supports certificate files and mTLS now; certificate issuance, renewal, naming, and revocation remain deployment/PKI responsibilities.

## Managed release and fleet-update architecture

The update system is split across three ownership boundaries. Keeping these
boundaries explicit prevents the running LabNet interpreter from modifying its
own files and prevents a heartbeat from accidentally reopening lease admission
during an update.

```text
repository root
|-- Labnet/labnet/                    shared device, worker, gRPC, and client code
|   |-- device_manager/admission.py   active-RPC admission gate and drain counter
|   |-- device_manager/worker.py      queued/running operation quiescence
|   `-- release.py                    installed package version + contract digest
|-- lab_pc/
|   |-- main.py                       readiness and authenticated local update API
|   `-- update_control.py             coordinates RPC and physical-worker drain
|-- central_server/
|   |-- app/api/releases.py           immutable artifact publication API
|   |-- app/api/updates.py            assignments and updater-agent API
|   |-- app/services/releases.py      filesystem/DB artifact validation and index
|   |-- app/services/deployments.py   transactional lease admission and phases
|   `-- alembic/versions/...0003...   release/deployment/admission schema
|-- Labupdater/                        separate distribution and stable runtime
|   `-- src/labupdater/               download, verify, install, supervise, rollback
`-- tools/
    |-- build_release_bundle.py        hashed offline bundle construction
    `-- publish_release.py             authenticated streaming upload and promotion
```

### Required managed deployment sequence

For an initial multi-machine installation, and for every rollout that changes
more than one role, use the same order as the local quick start:

0. build immutable artifacts on a release workstation;
1. deploy or verify Central;
2. deploy the Lab PCs and complete a canary/device smoke test; and
3. deploy the client runtime for newly started experiments.

Artifact creation is step 0, not a deployed role. Never activate a new client
before every Lab PC it may reach supports that client's protobuf contract.

#### 0. Build the release artifacts

Use a clean development checkout on the same operating system, CPU architecture,
and Python minor version as the target Lab PCs. The commands below assume a
homogeneous Windows/Python 3.11 fleet. Central stores only one artifact per
version; a heterogeneous fleet therefore needs a distinct PEP 440 package
version and separately published artifact for each target platform. Never build
different platform bytes under the same version. Give every byte change a new
normalized version in `pyproject.toml`; an existing version is immutable.

Prepare the release workstation once, then build:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[lab-pc,central,dev]"
python tools\generate_protos.py --check
python -m pytest
python -m build --wheel
python -m build --wheel Labupdater

$version = python -c "import pathlib,tomllib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])"
$wheel = Get-Item ".\dist\labnet-$version-py3-none-any.whl"
$commit = git rev-parse HEAD
$contractDigest = python -c "from labnet.release import contract_digest; print(contract_digest())"
python tools\build_release_bundle.py `
  --wheel $wheel `
  --output-dir .\release-output `
  --git-commit $commit `
  --contract-digest $contractDigest `
  --role lab_pc `
  --role client
```

This produces four deployable files: the LabNet wheel in `dist/`, the Labupdater
wheel in `Labupdater/dist/`, and the versioned bundle plus sidecar manifest in
`release-output/`. The bundle builder needs package-index access unless
`--wheelhouse` points to a complete local wheelhouse. Stage these files without
renaming them; do not rebuild them between roles.

#### 1. Deploy or update Central

Central is always the first deployed role. If a release does not change
Central, confirm that the existing compatible Central is healthy and treat that
as completion of this step. If Central does change, stop its service, back up
both the SQL database and `LABNET_RELEASE_ROOT`, install the new wheel, migrate,
restart, and verify readiness before enabling any Labupdater assignment.

For a first Windows installation, stage the LabNet wheel on the Central host,
then create a stable environment outside the checkout:

```powershell
$version = "<version built in step 0>"
New-Item -ItemType Directory -Force `
  C:\ProgramData\LabNet\Central, `
  C:\ProgramData\LabNet\Central\release-store | Out-Null
py -3.11 -m venv C:\ProgramData\LabNet\central-venv
C:\ProgramData\LabNet\central-venv\Scripts\python.exe -m pip install --upgrade pip
C:\ProgramData\LabNet\central-venv\Scripts\python.exe -m pip install `
  "labnet[central] @ file:///C:/Staging/labnet-$version-py3-none-any.whl"
```

Configure at least these values in the Central service environment, using a
stable data volume outside the virtual environment:

```powershell
$env:LABNET_DATABASE_URL = "sqlite:///C:/ProgramData/LabNet/Central/labnet.db"
$env:LABNET_RELEASE_ROOT = "C:\ProgramData\LabNet\Central\release-store"
$env:LABNET_CENTRAL_BIND_HOST = "127.0.0.1"
$env:LABNET_CENTRAL_BIND_PORT = "8008"
```

These assignments configure only the current PowerShell process. Persist the
same values in the site's service wrapper before registering Central as a
service. PostgreSQL sites instead install `labnet[central,postgres]` and set a
`postgresql+psycopg://...` database URL.

Before migration or startup, grant the dedicated Central service account
modify access to `C:\ProgramData\LabNet\Central` and no broader access than the
site requires. The same account must be able to create/write the SQLite file
and the release store; running the migration as an administrator does not grant
those rights automatically.

Apply the schema before creating users. On the first installation, create one
admin, one `device_manager` per Lab PC, and at least one client `user`:

```powershell
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe db upgrade
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe user create central-admin --role admin
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe user create lab-pc-01 --role device_manager
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe user create researcher --role user
```

Save each printed key immediately in a separate access-controlled secret. For
additional Lab PCs, repeat only the `device_manager` command with that
machine's stable ID. The username, updater `manager_id`, and
`LABNET_LAB_PC_ID` must be identical.

On the release/administrator workstation, create
`C:\ProgramData\LabNet\secrets\release-admin.key` containing **only** the raw
API key printed for `central-admin` (the `labnet_...` token and no labels or
other CLI output). Do not copy this admin key to any Lab PC or client. Place
each Device Manager key only on its matching Lab PC and give researchers only
their `user` keys.

Start Central in the foreground for its first local verification:

```powershell
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central.exe
```

From a second PowerShell window, confirm
`http://127.0.0.1:8008/health/ready`, then stop the foreground process. Register
that same `labnet-central.exe` command with the site's Windows service wrapper,
persist the four environment values above, run it as the dedicated account,
and enable restart-on-failure. The repository does not bundle a Central service
wrapper, so wrapper installation, service registration, and stop/start commands
are explicit site-administration tasks. Start the registered service and verify
the loopback readiness endpoint again.

For an existing Central upgrade, use this exact order: stop that service; back
up the database and entire release store while Central is stopped; load the
**exact persisted environment used by that service** into the administration
shell; install the staged wheel into the existing `central-venv`; run the
migration; start the service; and verify readiness. Do not replace a custom or
PostgreSQL database URL with the first-install SQLite example. After loading
the service environment, use `central` for SQLite or `central,postgres` for
PostgreSQL:

```powershell
$version = "<version built in step 0>"
$centralExtras = "<central or central,postgres>"
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe db current
C:\ProgramData\LabNet\central-venv\Scripts\python.exe -m pip install `
  "labnet[$centralExtras] @ file:///C:/Staging/labnet-$version-py3-none-any.whl"
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe db upgrade
```

Do not rerun the first-install `user create` commands during an upgrade. Use
`labnet-central-admin user rotate-key <username>` only when deliberately
rotating a principal's credential.

The built-in Central launcher must stay on loopback. Before any remote Lab PC
or client connects, configure the site-managed HTTPS reverse proxy and give
those machines its `https://` URL. The proxy must preserve `Authorization`,
allow the API's streaming `PUT` upload, set its request-body limit at least as
high as `LABNET_RELEASE_MAX_UPLOAD_BYTES`, and use a timeout suitable for the
largest bundle. Install a private proxy CA in the calling host's trust store
when applicable. Verify both the local service and external proxy path:

```powershell
Invoke-RestMethod http://127.0.0.1:8008/health/ready
Invoke-RestMethod https://central.lab.example/health/ready
```

After Central is ready, upload and promote the exact bundle built in step 0
from the release workstation. Upload stores a candidate; promotion makes it
eligible for assignment but does not update a machine:

```powershell
$version = "<version built in step 0>"
python tools\publish_release.py `
  --central-url https://central.lab.example `
  --api-key-file C:\ProgramData\LabNet\secrets\release-admin.key `
  --manifest ".\release-output\labnet-$version-manifest.json" `
  --artifact ".\release-output\labnet-$version-bundle.zip" `
  --promote
```

When the reverse proxy uses a private CA that is not already trusted on the
release workstation, add
`--ca-file C:\ProgramData\LabNet\certs\central-ca.pem` to the publish command.

**Managed gate 1:** Central must report `status: ok` through the HTTPS URL, the
schema must be current, all role-specific keys must be stored, and the release
must be promoted before proceeding to Lab PCs.

#### 2. Deploy or update each Lab PC

Each managed Lab PC runs Labupdater as its **only** LabNet service. Labupdater
lives in a stable virtual environment, owns versioned LabNet runtimes, and
launches the selected `lab_pc` process itself. Do not also register
`labnet-lab-pc` as a service.

On a new Lab PC, create the external directories and copy the templates from a
staged checkout:

```powershell
New-Item -ItemType Directory -Force `
  C:\ProgramData\LabNet\config, `
  C:\ProgramData\LabNet\certs, `
  C:\ProgramData\LabNet\inventory, `
  C:\ProgramData\LabNet\releases, `
  C:\ProgramData\LabNet\secrets | Out-Null
Copy-Item .\Labupdater\config.example.toml C:\ProgramData\LabNet\config\updater.toml
Copy-Item .\lab_pc\.env.example C:\ProgramData\LabNet\config\lab-pc.env
Copy-Item .\deployment\examples\lab_inventory.py C:\ProgramData\LabNet\inventory\lab_inventory.py
```

Complete these machine-specific tasks before starting anything:

1. Put this Lab PC's Device Manager key in
   `C:\ProgramData\LabNet\secrets\device-manager.key` and also set it as
   `LABNET_DEVICE_MANAGER_API_KEY` in `lab-pc.env`.
2. Generate a random updater control token of at least 16 characters and put it
   in `C:\ProgramData\LabNet\secrets\updater-control.token`. Labupdater injects
   this token into the child process; do not add it to a wheel.
3. Install the Lab-PC server certificate chain and private key under `certs`.
   Restrict the private key, and confirm its SAN covers the DNS name in
   `LABNET_GRPC_ADVERTISED_TARGET`; startup fails when either TLS file is absent.
4. Edit `updater.toml`, `lab-pc.env`, and `lab_inventory.py`. Use the same stable
   manager ID everywhere and give every physical device a stable, globally
   unique ID.
5. Grant the service account read access to config, inventory, certificates,
   and secrets; read/execute access to the updater environment; and modify
   access only to `C:\ProgramData\LabNet\releases`. Also grant the account the
   OS/device-driver permissions required to control the attached hardware.

For the paths used above, the essential `updater.toml` values are:

```toml
manager_id = "lab-pc-01"
central_url = "https://central.lab.example"
api_key_file = "C:/ProgramData/LabNet/secrets/device-manager.key"
release_root = "C:/ProgramData/LabNet/releases"
local_control_token_file = "C:/ProgramData/LabNet/secrets/updater-control.token"
labpc_environment_file = "C:/ProgramData/LabNet/config/lab-pc.env"
labpc_pythonpath = ["C:/ProgramData/LabNet/inventory"]

labpc_host = "127.0.0.1"
labpc_port = 9001
```

Add `ca_file = "C:/ProgramData/LabNet/certs/lab-ca.pem"` when Central's HTTPS
proxy uses a private CA. The remaining polling and timeout defaults are shown
in [`Labupdater/config.example.toml`](Labupdater/config.example.toml).

The Lab-PC child also connects to Central independently of Labupdater. When
Central uses a private CA, add
`SSL_CERT_FILE=C:\ProgramData\LabNet\certs\lab-ca.pem` to `lab-pc.env` so both
registration and lease validation trust it; `ca_file` in `updater.toml` covers
only the updater's Central connection.

At minimum, the external `lab-pc.env` for a LAN host contains:

```dotenv
LABNET_ENVIRONMENT=production
LABNET_LAB_PC_ID=lab-pc-01
LABNET_LAB_PC_HOST=lab-pc-01.lab.example
LABNET_DEVICE_FACTORY=lab_inventory:create_devices
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=https://central.lab.example
LABNET_DEVICE_MANAGER_API_KEY=<lab-pc-01 Device Manager key>
LABNET_LEASE_VALIDATION_MODE=central
LABNET_GRPC_BIND_HOST=0.0.0.0
LABNET_GRPC_ADVERTISED_TARGET=lab-pc-01.lab.example:50051
LABNET_GRPC_TLS_MODE=tls
LABNET_GRPC_CERT_FILE=C:\ProgramData\LabNet\certs\lab-pc.crt
LABNET_GRPC_KEY_FILE=C:\ProgramData\LabNet\certs\lab-pc.key
LABNET_HTTP_BIND_HOST=127.0.0.1
```

The advertised target must be reachable by clients and match the server
certificate. Use `mtls` and set `LABNET_GRPC_CA_FILE` when clients must also
present certificates. Plaintext gRPC is allowed only for the single-host
loopback quick start; the HTTP management endpoint always remains loopback.
Configure DNS and the host firewall so approved clients can reach only the
advertised gRPC port (default TCP 50051). Never expose management port 9001.

Install Labupdater separately and bootstrap the known-good bundle:

```powershell
$version = "<version built in step 0>"
py -3.11 -m venv C:\ProgramData\LabNet\updater-venv
$updaterWheels = @(Get-ChildItem C:\Staging\labupdater-*-py3-none-any.whl)
if ($updaterWheels.Count -ne 1) {
    throw "Stage exactly one Labupdater wheel."
}
C:\ProgramData\LabNet\updater-venv\Scripts\python.exe -m pip install `
  ($updaterWheels[0].FullName)
C:\ProgramData\LabNet\updater-venv\Scripts\labupdater.exe `
  --config C:\ProgramData\LabNet\config\updater.toml `
  install-bundle `
  --bundle "C:\Staging\labnet-$version-bundle.zip" `
  --manifest "C:\Staging\labnet-$version-manifest.json" `
  --activate
C:\ProgramData\LabNet\updater-venv\Scripts\labupdater.exe `
  --config C:\ProgramData\LabNet\config\updater.toml status
```

Register the following command with the site's Windows service wrapper, using
[`deployment/windows/Labupdater.xml.example`](deployment/windows/Labupdater.xml.example)
as the supplied WinSW configuration pattern:

```powershell
C:\ProgramData\LabNet\updater-venv\Scripts\labupdater.exe `
  --config C:\ProgramData\LabNet\config\updater.toml run
```

The repository does not bundle a service-wrapper executable, so installing and
starting that wrapper is an explicit site-administration task. Configure it to
run under the restricted service account and restart Labupdater after failure.
Start the wrapper now; `install-bundle --activate` alone does not launch the Lab
PC. After it starts, verify the initial runtime from the Lab PC:

```powershell
$version = "<bootstrapped version>"
$manifest = Get-Content "C:\Staging\labnet-$version-manifest.json" -Raw | ConvertFrom-Json
C:\ProgramData\LabNet\updater-venv\Scripts\labupdater.exe `
  --config C:\ProgramData\LabNet\config\updater.toml status
$ready = Invoke-RestMethod http://127.0.0.1:9001/health/ready
$ready | ConvertTo-Json -Depth 6
if (
    $ready.package_version -ne $version -or
    $ready.contract_digest -ne $manifest.contract_digest -or
    -not $ready.grpc.serving -or
    -not $ready.central.registered -or
    @($ready.devices | Where-Object { $_.available }).Count -eq 0
) {
    throw "Managed Lab PC failed initial readiness verification."
}
```

Run a real device smoke test from a compatible test client. Repeat bootstrap,
service start, readiness verification, and the smoke test on every initial Lab
PC before proceeding to the production-client step.

If step 0 produced a newer Labupdater wheel, upgrade the stable updater service
**before** assigning any release whose manifest requires it. Stop the updater
service with the site's wrapper, stage exactly one new updater wheel, run the
following command, restart the wrapper, and reconfirm that the current Lab-PC
runtime remains ready:

```powershell
$updaterWheels = @(Get-ChildItem C:\Staging\labupdater-*-py3-none-any.whl)
if ($updaterWheels.Count -ne 1) {
    throw "Stage exactly one Labupdater wheel."
}
C:\ProgramData\LabNet\updater-venv\Scripts\python.exe -m pip install --upgrade `
  ($updaterWheels[0].FullName)
```

For subsequent updates, return to the administrator/release workstation and
assign the promoted version to one canary only. Never place the admin key used
by this command on a Lab PC:

```powershell
$version = "<promoted version>"
$headers = @{ Authorization = "Bearer $((Get-Content C:\ProgramData\LabNet\secrets\release-admin.key -Raw).Trim())" }
$body = @{ version = $version; allow_downgrade = $false } | ConvertTo-Json
Invoke-RestMethod `
  -Method Put `
  -Uri https://central.lab.example/api/v1/deployments/lab-pc-01 `
  -Headers $headers `
  -ContentType application/json `
  -Body $body
Invoke-RestMethod `
  -Uri https://central.lab.example/api/v1/deployments `
  -Headers $headers
```

Verify the local updater journal, Lab-PC readiness, Central deployment state,
and an actual device operation from a test client. Only then repeat the
assignment for the remaining Lab PCs:

```powershell
$version = "<promoted version>"
C:\ProgramData\LabNet\updater-venv\Scripts\labupdater.exe `
  --config C:\ProgramData\LabNet\config\updater.toml status
$ready = Invoke-RestMethod http://127.0.0.1:9001/health/ready
$ready | ConvertTo-Json -Depth 6
if (
    $ready.package_version -ne $version -or
    -not $ready.grpc.serving -or
    -not $ready.central.registered -or
    @($ready.devices | Where-Object { $_.available }).Count -eq 0
) {
    throw "Canary Lab PC did not reach the promoted version in a ready state."
}
```

**Managed gate 2:** every Lab PC that production clients may discover must be
registered, ready, serving gRPC with the intended version/contract digest, and
pass a real device smoke test before a new client runtime is activated.

#### 3. Deploy or update the client

Clients do not run Labupdater. After managed gate 2 succeeds, deliver both the
bundle and its sidecar manifest through a trusted deployment channel. Verify
the outer artifact before trusting the lock file inside it, then install into a
new side-by-side environment:

```powershell
$version = "<version deployed to all Lab PCs>"
$bundle = Get-Item "C:\Staging\labnet-$version-bundle.zip"
$manifestPath = "C:\Staging\labnet-$version-manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.version -ne $version -or @($manifest.roles) -notcontains "client") {
    throw "Manifest version or roles do not match this client deployment."
}
if ($bundle.Name -ne $manifest.artifact_filename -or $bundle.Length -ne [long]$manifest.artifact_size) {
    throw "Bundle filename or size does not match the manifest."
}
$actualHash = (Get-FileHash -LiteralPath $bundle.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$expectedHash = ([string]$manifest.artifact_sha256).ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
    throw "Bundle SHA-256 does not match the manifest."
}
Expand-Archive `
  -LiteralPath $bundle.FullName `
  -DestinationPath "C:\Staging\labnet-$version"
py -3.11 -m venv "C:\ProgramData\LabNet\client-runtimes\$version"
& "C:\ProgramData\LabNet\client-runtimes\$version\Scripts\python.exe" -m pip install `
  --no-index `
  --require-hashes `
  --find-links "C:\Staging\labnet-$version\wheelhouse" `
  -r "C:\Staging\labnet-$version\requirements.lock"
& "C:\ProgramData\LabNet\client-runtimes\$version\Scripts\python.exe" `
  -c "import importlib.metadata; print(importlib.metadata.version('labnet'))"
```

Save the quick-start `client_smoke.py` on the client host, then configure its
inputs and run it with the versioned interpreter:

```powershell
$env:LABNET_CENTRAL_URL = "https://central.lab.example"
$env:LABNET_RESEARCHER_API_KEY = "<client user API key>"

# Set this only when Central's HTTPS proxy uses a private CA bundle.
$env:SSL_CERT_FILE = "C:\ProgramData\LabNet\certs\central-ca.pem"

# Set this when the Lab-PC gRPC certificate uses a private CA.
$env:LABNET_GRPC_CA_FILE = "C:\ProgramData\LabNet\certs\lab-grpc-ca.pem"

# Set both of these only when the Lab PCs require mTLS client identity.
$env:LABNET_GRPC_CLIENT_KEY_FILE = "C:\ProgramData\LabNet\certs\client.key"
$env:LABNET_GRPC_CLIENT_CERT_FILE = "C:\ProgramData\LabNet\certs\client.crt"

& "C:\ProgramData\LabNet\client-runtimes\$version\Scripts\python.exe" `
  C:\ProgramData\LabNet\client_smoke.py
```

Remove the optional environment assignments when they do not apply. Central
HTTPS trust and direct gRPC trust are separate: `SSL_CERT_FILE` affects the
HTTPX connection to Central, while the three `LABNET_GRPC_*` inputs are read by
the smoke program to create `GrpcTlsConfig`. Certificates chaining to gRPC's
default trust roots need no gRPC CA override; mTLS requires both the client key
and certificate.

Once the full smoke path passes against a real device, point the site-specific
launcher/default environment at the new version for **newly started**
experiments. Do not mutate an environment under a running Python process, and
retain the previous client environment for rollback.

**Managed gate 3:** authentication, discovery, lease acquisition, a direct
gRPC device operation, and lease release must all succeed from the new client
environment. The Central -> Lab PC -> client rollout is then complete.

### What “idle” means

An update is not permitted merely because `physical.status()` says `idle` or a
worker queue is empty. Those observations can race a currently executing call.
The update barrier requires all of the following:

- Central has durably changed every device on the target manager from
  `accepting` to `draining`, so no new lease transaction can commit;
- Central reports zero active leases for every device on that manager;
- the Lab-PC gRPC admission gate has closed and its active unary/stream count is
  zero; and
- every physical worker has both zero queued calls and zero running calls.

Central changes admission in the same database locking path used by lease
creation. Therefore a concurrent lease either commits before the drain and is
waited on, or observes `draining` and is rejected. Existing leases remain valid
and renewable by default, so an experiment finishes rather than being cut off.

### Release and rollback storage

Central's `LABNET_RELEASE_ROOT` has this immutable layout:

```text
release-store/
|-- .staging/<release-id>/
`-- versions/<PEP440-version>/
    |-- manifest.json
    `-- labnet-<version>-bundle.zip
```

Each version is unique and is never overwritten. The database indexes release
state (`pending_upload`, `candidate`, or `promoted`), per-manager desired and
observed versions, update attempts, errors, and timestamps. Assigning an older
promoted version with `allow_downgrade=true` is the rollback operation; it uses
the exact retained wheel and dependency wheelhouse rather than resolving
today's dependencies.

Each Lab PC has its own `release_root/runtimes/<version>/venv`. `current.json`
is an atomically replaced pointer. The previous runtime is retained after a
successful switch, so failed startup can restore it without downloading or
installing anything.

### Update state machine and recovery

The normal state sequence is:

```text
assigned -> downloading -> staged -> draining -> quiescing
         -> switching -> verifying -> succeeded
```

Failures before process replacement leave the current runtime untouched and
resume local admission. Failures after replacement attempt to restart the
previous version and end as `rolled_back`. Central keeps device admission
closed during switching and verification. If an updater host is permanently
lost, an administrator can inspect the machine and call
`POST /api/v1/deployments/attempts/{attempt_id}/abort`; this is a recovery tool,
not a way to bypass checking that hardware activity has actually stopped. Its
request must set `reopen_admission=true` only after a healthy runtime and safe
hardware state have been verified; the safer default records failure while
leaving admission closed.

### Update API map

Administrator endpoints:

- `POST /api/v1/releases`, `PUT /api/v1/releases/{id}/artifact`, and
  `POST /api/v1/releases/{id}/promote` publish immutable releases;
- `GET /api/v1/releases` lists retained artifacts;
- `PUT /api/v1/deployments/{manager_id}` assigns or rolls back a Lab PC;
- `GET /api/v1/deployments` reports fleet state; and
- `POST /api/v1/deployments/attempts/{attempt_id}/abort` clears a verified
  stuck attempt (admission reopening is an explicit request field).

Device-Manager-authenticated updater endpoints:

- `GET /api/v1/updates/directive` returns only that manager's assignment;
- `GET /api/v1/updates/artifacts/{release_id}` streams its assigned bundle;
- phase, begin-drain, drain-status, complete, and abort endpoints journal the
  attempt and operate the Central admission barrier.

Token-authenticated loopback Lab-PC endpoints under `/internal/v1/update/`
expose status, drain, resume, and graceful stop. They are disabled unless
`LABNET_UPDATER_CONTROL_TOKEN` is configured and must remain loopback-only.

## Development

Run the complete test suite:

```powershell
python -m pytest
```

### Proto-driven device contracts

For every device family, the `.proto` file is the public API and
`physical.py` is the local hardware behavior. Generated contract metadata is
the runtime authority for the server mapping, client request construction,
physical-method signature, default unit, and remotely allowed method list.
This creates two deliberately different workflows.

If an operation's name, arguments, result shape, and unit are unchanged, edit
only that family's `physical.py`. For example, replacing a simulated
`read_frequency()` body with a vendor SDK call does not require protobuf
generation or a client change:

```python
def read_frequency(self, channel: str | None = None) -> float:
    return float(self._vendor_sdk.read_frequency(channel))
```

If an operation is added or removed, or if its arguments change within one of
the supported canonical pattern shapes, use this order:

1. Edit the family `.proto` file under
   `Labnet/protos/labnet/devices/v1/`.
2. Explicitly set `pattern`, `physical_method`, and `default_unit` on every
   RPC. Use the UCUM/SI symbol `"1"` for a dimensionless operation.
3. Run `python tools/generate_protos.py`.
4. Add or update the matching method in the family's `physical.py`.
5. Run `python tools/generate_protos.py --check` and `python -m pytest`.

Before the first deployment, this undeployed v1 contract may still be changed
freely. After clients are deployed, protobuf evolution must be additive:

- keep every existing field number and field type;
- add new optional fields with new numbers;
- never reuse a removed field number or name; mark both `reserved`;
- keep existing RPC semantics while older clients may still call them; and
- introduce a `v2` package/service for a breaking removal, rename, cardinality
  change, or incompatible argument/result change.

For an additive rollout, deploy Lab PCs that accept both old and new requests
before deploying clients that send the new fields or call the new RPC. Retire
the old surface only after all clients have migrated.

An RPC mapping looks like this:

```protobuf
rpc ReadFrequency(ReadFrequencyRequest) returns (ReadFrequencyResponse) {
  option (labnet.devices.v1.rpc) = {
    pattern: RPC_PATTERN_SCALAR_READ
    physical_method: "read_frequency"
    default_unit: "Hz"
  };
}
```

There are no inferred defaults for these three options. Generation fails when
an option is absent, empty, incompatible with the RPC streaming cardinality,
or uses a message shape the selected pattern cannot safely dispatch. The
declared `default_unit` is authoritative for local and remote calls; scalar
writes reject a different requested unit because implicit unit conversion is
not performed.

This release implements only these reusable runtime patterns:

| Pattern | RPC shape | Physical behavior |
| --- | --- | --- |
| `RPC_PATTERN_SCALAR_READ` | Unary | Calls one physical read and returns a `ScalarMeasurement` |
| `RPC_PATTERN_SCALAR_WRITE` | Unary | Passes an absolute `double value` to one physical write and returns a `ScalarMeasurement` |
| `RPC_PATTERN_POLLED_SCALAR_STREAM` | Server streaming | Repeatedly calls one physical read using LabNet's rate scheduling, cancellation, lease revalidation, and backpressure rules |

Other pattern names are reserved in `rpc_options.proto` for future shared
handlers. The generator rejects them in this release; declaring a reserved
pattern does not create a partially implemented RPC. A genuinely new result
kind, such as a camera frame or chunked ndarray, is a framework-level pattern
addition: implement and test its shared server/client handler first, then use
that pattern from individual device protos.

All supported pattern requests contain non-optional `string device_id` and
`string operation_id`, and responses contain `string operation_id`. Scalar
responses contain the canonical `ScalarMeasurement`. A scalar write also uses
non-optional `double value`, optional `string unit`, and optionally an
`optional string channel`. A polled scalar stream uses `optional string
channel`, non-optional `double sample_rate_hz`, optional `uint64 sample_count`,
and `google.protobuf.Duration duration`.

The generator writes protobuf bindings and `device_contracts.py` under
`Labnet/labnet/grpc/generated/`. Never edit those files by hand; the next
generation overwrites them. Commit the regenerated files with the proto
change. CI or a local check can detect stale output without modifying it:

```powershell
python tools/generate_protos.py
python tools/generate_protos.py --check
```

At Lab-PC startup, each physical object's `device_type` selects its generated
contract. Startup validates that every mapped physical method exists, accepts
the protobuf-forwarded arguments, does not add unmapped required arguments,
and gives protobuf-optional arguments Python defaults. The worker derives its
remote allowlist from that same contract, so a method cannot become remotely
callable merely because it exists on the driver.

The gRPC server registers every generated contract and routes it through the
shared pattern service. The client builds typed requests from the same
generated `RpcSpec` metadata. Consequently, a device family does not need a
handwritten `labnet/grpc/services/<device>.py`; family-specific code belongs
with the physical adapter, while lease, authorization, error translation,
stream scheduling, and transport dispatch remain shared framework behavior.

### Adding a device family or instrument

For the first implementation of a new family, use `DummyDevice` as the
reference layout:

1. Add a package under `Labnet/labnet/devices/<Family>/` with `physical.py`, a
   small `device.py` façade, and `__init__.py` exports. Package discovery
   includes new family directories automatically.
2. Add the family's annotated proto contract under
   `Labnet/protos/labnet/devices/v1/`. Name the service consistently; for
   example, `WavemeterService` derives the unique device type `wavemeter`.
3. Run the generator so the physical protocol, client metadata, service
   registration, and allowlist are created from that contract.
4. Implement the generated physical method signatures and the common physical
   lifecycle (`device_id`, `device_type`, `connect`, `disconnect`, `status`,
   `availability`, and JSON-compatible `metadata`).
5. Add each attached instrument instance to `lab_pc/device_inventory.py` with
   a stable, globally unique `device_id`.
6. Run generation in check mode and run the tests before starting the Lab PC.

The one-time public façade for a family is intentionally small; generated
contract methods are resolved by `ContractDeviceFacade` for both backends:

```python
from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import WAVEMETER_CONTRACT

from .physical import PhysicalWavemeter


class Wavemeter(ContractDeviceFacade):
    contract = WAVEMETER_CONTRACT
    physical_type = PhysicalWavemeter
```

After that family exists, adding or changing supported methods does not require
editing the façade. Regeneration changes its method surface through the
contract, and the shared remote lease backend dispatches the typed RPC.
Do not create a family-specific `remote.py` or `grpc/services/<family>.py`;
those lease, client-dispatch, and service-dispatch responsibilities are shared
framework code.

Async callers use the same generated contract with the one shared
`AsyncLabClient`; they do not add a family-specific async client class.
`acquire_dummy()` is only a convenience for the reference family. The generic
form for a new family is:

```python
device = await central.acquire_device(
    WAVEMETER_CONTRACT,
    device_id="wavemeter-1",
)
try:
    measurement = await device.read_frequency(channel="main")
finally:
    await device.aclose()
```

Adding another instrument instance of an existing family requires no proto or
generated-code change: construct another physical adapter in
`lab_pc/device_inventory.py`. Similarly, changing only how an existing driver
method talks to hardware requires only a `physical.py` edit.

The schema/API details and administration commands are documented in `central_server/README.md`.

## Credential warning

Local `.env` files and bytecode caches are ignored. Credentials previously committed can still exist in Git history: rotate or revoke every exposed key before deployment. Removing a file in a later commit does not erase it from earlier commits.
