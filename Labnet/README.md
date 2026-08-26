# LabNet

LabNet is a lease-controlled device network for the PQT lab. Version 1 is a clean break from the former WebSocket RPC design: the Central Server manages identity, discovery, and leases over HTTP, while clients communicate directly with each Lab PC through typed gRPC.

## Features and working principles

| Surface | Default | Responsibility |
| --- | --- | --- |
| Central HTTP API | `127.0.0.1:8008` | API-key authentication, device registry, heartbeats, exclusive leases, and audit history |
| Lab-PC HTTP management | `127.0.0.1:9001` | Local liveness and readiness only |
| Lab-PC gRPC | `127.0.0.1:50051` | Typed `ReadValue`, `WriteValue`, and streamed measurements |

LabNet keeps the management and measurement paths separate:

- Central is the authority for API-key identity, device discovery, health,
  exclusive leases, and audit records.
- Clients use Central only to authenticate, discover a device, and obtain or
  renew a lease. Measurement and control traffic does not pass through Central.
- Each client connects directly to the selected Lab PC over a typed gRPC
  channel. The lease ID accompanies every device RPC.
- Each physical device has one bounded worker. Blocking VISA, serial, vendor
  SDK, and similar calls are ordered per instrument without blocking the
  asynchronous HTTP or gRPC servers.
- Device contracts are defined by protobuf and compiled into Python before a
  release is built. Generated metadata drives the server, client, physical
  method validation, and remote allowlist.
- A Lab PC creates its physical device inventory once during process startup.
  There is no runtime updater, proto generator, hot reload, or API for replacing
  device objects while the network is running.

The control path is:

1. A Lab PC registers its stable manager ID, device inventory, and gRPC endpoint with Central.
2. A client discovers an available device and requests an exclusive lease from Central.
3. Central returns an opaque lease ID and that device's typed gRPC endpoint.
4. The client calls the Lab PC directly, carrying the lease ID in `x-labnet-lease-id` metadata.
5. The Lab PC validates the lease with Central before control and periodically during a long stream.

Central never proxies or stores measurement samples. Physical implementations
and generated protobuf bindings are wheel inputs; the reviewed
`examples/lab_pc/device_inventory.py` composition is deployed alongside the
wheel. Change both in a development checkout, test them, and deploy them while
the affected process is stopped. A source or proto edit on an active host is
neither a supported update path nor loaded into the running process.

## Development installation

Python 3.11 or newer is required. Run these commands from the repository's
`Labnet/` directory in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[lab-pc,central,dev]"
```

The `.` means "install the project in this directory." Text inside brackets
selects optional dependency groups; it is not the Python module name. Separate
development environments can use the smallest suitable editable install:

```powershell
# Client workstation: base LabNet package and gRPC client
python -m pip install -e .

# Lab PC: base package plus FastAPI, health service, dotenv, and Uvicorn
python -m pip install -e ".[lab-pc]"

# Central host: base package plus FastAPI, SQLAlchemy, Alembic, and Uvicorn
python -m pip install -e ".[central]"
```

PostgreSQL deployments also install `.[central,postgres]`. Editable (`-e`) installs are convenient during development; build and install a versioned wheel for managed lab machines.

## Deployment process

LabNet uses an explicit build-then-deploy process. Production hosts do not run
a LabNet updater and should not use editable installs or a writable source
checkout.

1. Make device and contract changes on a development/build computer. Follow
   [How to add a new device to the network](#how-to-add-a-new-device-to-the-network)
   when adding a family or instrument.
2. If any `.proto` changed, generate and validate the committed bindings:

   ```powershell
   python tools/generate_protos.py
   python tools/generate_protos.py --check
   ```

3. Update the physical implementation and the reviewed
   `examples/lab_pc/device_inventory.py` composition, then run the full test
   suite:

   ```powershell
   python -m pytest
   ```

4. Assign a new project version in `pyproject.toml` and build one immutable
   wheel from the reviewed tree:

   ```powershell
   python -m pip wheel --no-deps . --wheel-dir dist
   ```

   The wheel intentionally contains the reusable `labnet` package, not the
   `examples/` directory. Treat the selected example composition as a separate
   version-controlled deployment input.

5. Copy that exact wheel to the Central computer, every Lab PC, and client
   computers. Copy the reviewed Lab-PC/client composition to the corresponding
   hosts. Stop the affected LabNet process, install the wheel into its virtual
   environment, and do not modify files inside the installed package or active
   composition.
6. On Central, run `labnet-central-admin db upgrade` before starting
   the new server. Start Central first, then Lab PCs, then clients. Confirm
   Central readiness, Lab-PC registration/heartbeats, device discovery, lease
   acquisition, and one test device RPC.

For a contract change, deploy compatible Lab PCs before clients that use the
new RPC or field. Existing protobuf field numbers and types remain stable; use
additive changes within `v1` and introduce a new API package for breaking
changes. Rollback is likewise manual and process-boundary based: stop the
process and reinstall the previously approved wheel. Database rollback needs a
separately reviewed migration plan.

## Three-computer static-IP deployment

This example places each role on a different computer on one trusted lab LAN:

| Computer | Static IP | Runs | Inbound access |
| --- | --- | --- | --- |
| Central server | `192.168.50.10` | Central on loopback plus an HTTPS reverse proxy | TCP 443 from Lab PCs and clients |
| Lab PC | `192.168.50.20` | Physical drivers, gRPC device server, loopback health API | TCP 50051 from approved clients |
| Client | `192.168.50.30` | Experiment scripts and the LabNet client library | No LabNet inbound port |

Reserve these addresses in DHCP or configure them statically, and verify that
the three hosts can route to one another. Use a lab CA or another trusted PKI.
The Central HTTPS certificate must contain `192.168.50.10` (or its stable DNS
name) in its subject alternative names; the Lab-PC gRPC certificate must
contain `192.168.50.20` (or its stable DNS name). Copy only the CA certificate,
not CA or server private keys, to the client and Lab PC.

### 1. Build and install the same release

Complete the deployment process above. Install the wheel with the appropriate
extras on each machine from its local staging directory:

```powershell
# Central computer
python -m pip install ".\labnet-0.2.0-py3-none-any.whl[central]"

# Lab PC
python -m pip install ".\labnet-0.2.0-py3-none-any.whl[lab-pc]"

# Client computer
python -m pip install .\labnet-0.2.0-py3-none-any.whl
```

Replace `0.2.0` with the release being deployed. Use a separate virtual
environment on each host and configure its service to run from that
environment. The Lab PC also needs a reviewed copy of `examples/lab_pc/` (or a
site-specific equivalent) outside `site-packages`; examples are deliberately
not embedded in the wheel.

### 2. Configure the Central computer

Keep the built-in Central server on loopback; it intentionally rejects a
plaintext LAN bind. Configure an HTTPS reverse proxy on the same computer to
listen on `192.168.50.10:443`, present the Central certificate, and proxy to
`http://127.0.0.1:8008`. Do not expose port 8008 in the host firewall.

Set Central's process environment, initialize the database, and create the Lab
PC and researcher identities:

```powershell
$env:LABNET_CENTRAL_BIND_HOST = "127.0.0.1"
$env:LABNET_CENTRAL_BIND_PORT = "8008"
$env:LABNET_DATABASE_URL = "sqlite:///C:/ProgramData/LabNet/Central/labnet.db"

labnet-central-admin db upgrade
labnet-central-admin user create lab-pc-01 --role device_manager
labnet-central-admin user create researcher --role user
labnet-central
```

Save each printed API key when it is created; Central stores only its hash. The
`device_manager` username must exactly match the Lab PC's
`LABNET_LAB_PC_ID`. Configure the reverse proxy and Central as OS services only
after the foreground configuration has passed readiness checks.

### 3. Configure the Lab PC

Edit the physical adapters and inventory on the build computer before building
the wheel. On the Lab PC, set the following values in the OS-service process
environment; dotenv syntax is shown for readability:

```dotenv
LABNET_ENVIRONMENT=production
LABNET_LAB_PC_ID=lab-pc-01
LABNET_LAB_PC_HOST=192.168.50.20
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=https://192.168.50.10
LABNET_DEVICE_MANAGER_API_KEY=<lab-pc-01 API key>
LABNET_LEASE_VALIDATION_MODE=central

LABNET_GRPC_BIND_HOST=0.0.0.0
LABNET_GRPC_PORT=50051
LABNET_GRPC_ADVERTISED_TARGET=192.168.50.20:50051
LABNET_GRPC_TLS_MODE=tls
LABNET_GRPC_CERT_FILE=C:/ProgramData/LabNet/certs/lab-pc.crt
LABNET_GRPC_KEY_FILE=C:/ProgramData/LabNet/certs/lab-pc.key
LABNET_GRPC_CA_FILE=

LABNET_HTTP_BIND_HOST=127.0.0.1
LABNET_HTTP_PORT=9001
```

If the Central certificate chains to a private CA, set `SSL_CERT_FILE` in the
Lab-PC service environment to the CA PEM path so its HTTPS registration and
lease-validation requests trust Central. Keep the gRPC private key readable
only by the Lab-PC service account. Start the checked-in composition from the
repository root with `python -m examples.lab_pc`, then verify on Central that `lab-pc-01` and
its devices are healthy. The loopback management port 9001 is not exposed.

Use `LABNET_GRPC_TLS_MODE=mtls` instead when clients must authenticate with
certificates. In that case set `LABNET_GRPC_CA_FILE` to the client-issuing CA
and give each client its own certificate and private key.

### 4. Configure the client computer

Trust the CA used by both HTTPS and gRPC. For a private CA, set
`SSL_CERT_FILE` for Central HTTPS and explicitly pass the same trust root to
the gRPC device connection:

```powershell
$env:SSL_CERT_FILE = "C:\ProgramData\LabNet\certs\lab-ca.pem"
```

```python
from labnet import DummyDevice, GrpcTlsConfig, LabClient

central = LabClient("https://192.168.50.10", "<researcher API key>")
tls = GrpcTlsConfig.from_files(
    root_certificates=r"C:\ProgramData\LabNet\certs\lab-ca.pem",
)

try:
    device = DummyDevice(
        device_id="dummy_device_1",
        client=central,
        tls=tls,
    )
    try:
        print(device.read_value())
    finally:
        device.close()
finally:
    central.close()
```

Open only the firewall paths shown in the table. The Central host never needs
an inbound gRPC rule, and the client does not connect to the Lab-PC HTTP health
port. If DNS names are used instead of the example IPs, advertise the same name
that appears in the gRPC certificate.

## Local development setup

### 1. Central Server

SQLite is built into Python, so a single-process Central Server needs no separate database service. Copy and review the example, apply the schema migration, and create principals:

```powershell
labnet-central-admin db upgrade
labnet-central-admin user create central-admin --role admin
labnet-central-admin user create lab-pc-dev-01 --role device_manager
labnet-central-admin user create researcher --role user
labnet-central
```

Each user command prints its new API key once. A `device_manager` username must exactly equal that Lab PC's `LABNET_LAB_PC_ID`; this binds registration and lease validation to the same principal. Central stores only API-key hashes.

Normal server startup does not create or mutate tables. Run `labnet-central-admin db upgrade` after each release containing a migration. `/health/ready` stays unavailable if the expected schema revision is missing.

### 2. Lab PC

```powershell
Copy-Item ..\examples\lab_pc\.env.example ..\examples\lab_pc\.env
```

For Central-backed operation, set these values in the untracked
`examples/lab_pc/.env`:

```dotenv
LABNET_LAB_PC_ID=lab-pc-dev-01
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=http://127.0.0.1:8008
LABNET_DEVICE_MANAGER_API_KEY=<the device-manager key>
LABNET_LEASE_VALIDATION_MODE=central
```

Then start the process:

```powershell
Set-Location ..
python -m examples.lab_pc
```

Edit `examples/lab_pc/device_inventory.py` to construct the physical adapters installed on that machine. Reusable device interfaces, execution, Central adapters, protobuf contracts, and gRPC services stay in the `labnet` package.

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

## Contract and device development

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
   `protos/labnet/devices/v1/`.
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
`labnet/grpc/generated/`. Never edit those files by hand; the next
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

## How to add a new device to the network

First decide whether this is another instance of an existing device family or
a new/changed family contract. Another instance with the same operations needs
no proto edit and no generation: add its physical adapter construction to
`examples/lab_pc/device_inventory.py`, give it a stable globally unique `device_id`,
test, and build a new wheel.

For the first implementation of a family, or when its network operations
change, use `DummyDevice` as the reference and work in this order:

1. Add or edit the annotated contract under
   `protos/labnet/devices/v1/`. Name the service consistently; for
   example, `WavemeterService` derives the unique device type `wavemeter`.
   Skip this step only when the existing operation names, arguments, result
   shapes, and units already describe the new hardware.
2. Run `python tools/generate_protos.py`. This creates the protobuf bindings,
   physical protocol, client metadata, service registration, and remote
   allowlist. Never edit files under `labnet/grpc/generated/` by hand.
3. Add or update `labnet/devices/<Family>/physical.py` and implement the
   generated method signatures plus the common lifecycle (`device_id`,
   `device_type`, `connect`, `disconnect`, `status`, `availability`, and
   JSON-compatible `metadata`). Hardware-only implementation changes start at
   this step and do not require regeneration.
4. For a new family, add its small `device.py` facade and `__init__.py` exports.
   Package discovery includes new family directories automatically.
5. Add each attached instrument instance to `examples/lab_pc/device_inventory.py` with
   a stable, globally unique `device_id`.
6. Run `python tools/generate_protos.py --check` and `python -m pytest`, update
   the package version, build the wheel, and deploy it only while the affected
   process is stopped.

The one-time public facade for a family is intentionally small; generated
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
editing the facade. Regeneration changes its method surface through the
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
`examples/lab_pc/device_inventory.py`. Similarly, changing only how an existing driver
method talks to hardware requires only a `physical.py` edit.

The example composition and administration commands are documented in
`../examples/central_server/README.md`.

## Credential warning

Local `.env` files and bytecode caches are ignored. Credentials previously committed can still exist in Git history: rotate or revoke every exposed key before deployment. Removing a file in a later commit does not erase it from earlier commits.
