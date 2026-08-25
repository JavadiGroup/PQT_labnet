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

## Installation

Python 3.11 or newer is required. For a development checkout, run this from the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[lab-pc,central,dev]"
```

The `.` means "install the project in this directory." Text inside brackets selects optional dependency groups; it is not the Python module name. Use the smallest suitable installation in deployment:

```powershell
# Client workstation: base LabNet package and gRPC client
python -m pip install -e .

# Lab PC: base package plus FastAPI, health service, dotenv, and Uvicorn
python -m pip install -e ".[lab-pc]"

# Central host: base package plus FastAPI, SQLAlchemy, Alembic, and Uvicorn
python -m pip install -e ".[central]"
```

PostgreSQL deployments also install `.[central,postgres]`. Editable (`-e`) installs are convenient during development; build and install a versioned wheel for managed lab machines.

## Bring up a local system

### 1. Central Server

SQLite is built into Python, so a single-process Central Server needs no separate database service. Copy and review the example, apply the schema migration, and create principals:

```powershell
python -m central_server.manage db upgrade
python -m central_server.manage user create central-admin --role admin
python -m central_server.manage user create lab-pc-dev-01 --role device_manager
python -m central_server.manage user create researcher --role user
python -m central_server.app.main
```

Each user command prints its new API key once. A `device_manager` username must exactly equal that Lab PC's `LABNET_LAB_PC_ID`; this binds registration and lease validation to the same principal. Central stores only API-key hashes.

Normal server startup does not create or mutate tables. Run `python -m central_server.manage db upgrade` after each release containing a migration. `/health/ready` stays unavailable if the expected schema revision is missing.

### 2. Lab PC

```powershell
Copy-Item lab_pc\.env.example lab_pc\.env
```

For Central-backed operation, set these values in the untracked `lab_pc/.env`:

```dotenv
LABNET_LAB_PC_ID=lab-pc-dev-01
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=http://127.0.0.1:8008
LABNET_DEVICE_MANAGER_API_KEY=<the device-manager key>
LABNET_LEASE_VALIDATION_MODE=central
```

Then start the process:

```powershell
python -m lab_pc
# Installed console equivalent: labnet-lab-pc
```

For development, edit `lab_pc/device_inventory.py` to construct the physical
adapters. For a managed machine, keep inventory outside the release runtime,
set `LABNET_DEVICE_FACTORY=lab_inventory:create_devices`, and make its parent
directory available through Labupdater's `labpc_pythonpath`. Reusable device
interfaces, execution, Central adapters, protobuf contracts, and gRPC services
stay in the versioned `labnet` package.

### 3. Client

The async API owns the Central lease and reusable gRPC channel. Use
`try`/`finally` so every exit path closes the stream, releases the device lease,
and closes the Central HTTP client:

```python
import asyncio

from labnet import AsyncLabClient


async def main() -> None:
    central = AsyncLabClient(
        "http://127.0.0.1:8008",
        "<researcher API key>",
    )

    try:
        await central.authenticate()
        devices = await central.list_available_devices("dummy_device")
        print([device.device_id for device in devices])

        device = await central.acquire_dummy(
            devices[0].device_id,
            duration_seconds=60,
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

Synchronous programs use the public `DummyDevice` class for both local and
remote hardware. Omitting `client` selects the physical implementation in the
current process; passing a `LabClient` selects the leased gRPC implementation.
The experiment-facing method calls stay the same:

```python
from labnet import DummyDevice, LabClient


def set_and_read(device: DummyDevice) -> float:
    device.write_value(12.5, channel="phase")
    return device.read_value(channel="phase")


local_device = DummyDevice(device_id="bench-dummy-1")
try:
    print(set_and_read(local_device))
finally:
    local_device.close()

central = LabClient("http://127.0.0.1:8008", "<researcher API key>")

try:
    device = DummyDevice(
        device_id="dummy-device-1",
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

#### Choosing `LabClient` or `AsyncLabClient`

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

### Build, publish, canary, and assign

Every code change, including a `physical.py`-only change, needs a new normalized
PEP 440 package version in `pyproject.toml`. Never publish different bytes under
an existing version. A protobuf method change additionally requires generated
bindings and must follow the additive rollout rules below.

From a clean development checkout:

```powershell
python tools\generate_protos.py --check
python -m pytest
python -m build --wheel

$wheel = Get-Item .\dist\labnet-0.3.0-py3-none-any.whl
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

The bundle builder resolves only binary dependencies, writes a fully hashed
lock, and creates a sidecar manifest. It needs package-index access unless a
complete wheelhouse is supplied with `--wheelhouse`.

Upload the candidate using an admin key stored in a file, then promote it:

```powershell
python tools\publish_release.py `
  --central-url https://central.lab.example `
  --api-key-file C:\ProgramData\LabNet\secrets\release-admin.key `
  --manifest .\release-output\labnet-0.3.0-manifest.json `
  --artifact .\release-output\labnet-0.3.0-bundle.zip `
  --promote
```

Promotion does not update a machine. Assign the promoted version to one canary
Lab PC first:

```powershell
$headers = @{ Authorization = "Bearer $((Get-Content C:\ProgramData\LabNet\secrets\release-admin.key -Raw).Trim())" }
$body = @{ version = "0.3.0"; allow_downgrade = $false } | ConvertTo-Json
Invoke-RestMethod `
  -Method Put `
  -Uri https://central.lab.example/api/v1/deployments/lab-pc-01 `
  -Headers $headers `
  -ContentType application/json `
  -Body $body
```

Observe `GET /api/v1/deployments`, the canary Lab-PC readiness response, and an
actual device smoke test before assigning the remaining Lab PCs. Publication
and assignment are intentionally separate, so a bad candidate never rolls out
merely because it was uploaded.

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

### Deployment responsibilities

Central is not self-updated by Labupdater. Updating Central can include a
database migration, so use this controlled order: stop Central, back up the
database and `LABNET_RELEASE_ROOT`, install the new `labnet[central]` wheel,
run `labnet-central-admin db upgrade`, start Central, and verify
`/health/ready`. Deploy the Central schema/API from this release before
enabling Labupdater agents.

For example, while the Central service is stopped:

```powershell
C:\ProgramData\LabNet\central-venv\Scripts\python.exe -m pip install `
  "labnet[central] @ file:///C:/Staging/labnet-0.3.0-py3-none-any.whl"
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe db upgrade
```

On each managed Lab PC, install `Labupdater` into its own stable virtual
environment, put its TOML, secrets, CA, Lab-PC environment file, and inventory
outside all versioned runtimes, bootstrap one known-good bundle, and run only
the `labupdater ... run` command as the OS service. Detailed commands and the
filesystem layout are in `Labupdater/README.md`.

Client workstations do not run the Lab-PC supervisor: arbitrary user Python
processes have no reliable global “idle” signal. Deploy client wheels into
side-by-side versioned environments with the same hashed bundle, then change
the lab's client launcher/default environment for new experiments. Already
running Python processes continue with their loaded old code. For additive
protobuf changes, update all Lab PCs before activating the new client runtime;
older clients remain compatible during the rollout. Central itself is updated
last only when a later application release specifically requires new Central
code; for this update system's initial introduction, Central is deployed first.

A client bundle can be installed without contacting a package index:

```powershell
Expand-Archive `
  -LiteralPath C:\Staging\labnet-0.3.0-bundle.zip `
  -DestinationPath C:\Staging\labnet-0.3.0
py -3.11 -m venv C:\ProgramData\LabNet\client-runtimes\0.3.0
C:\ProgramData\LabNet\client-runtimes\0.3.0\Scripts\python.exe -m pip install `
  --no-index `
  --require-hashes `
  --find-links C:\Staging\labnet-0.3.0\wheelhouse `
  -r C:\Staging\labnet-0.3.0\requirements.lock
```

Point the client launcher used for new experiments at that environment only
after the Lab-PC canary and fleet rollout are healthy. Retain the previous
client environment for rollback.

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
