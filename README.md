# LabNet

LabNet is a lease-controlled device network for the PQT lab. Version 1 is a clean break from the former WebSocket RPC design: the Central Server manages identity, discovery, and leases over HTTP, while clients communicate directly with each Lab PC through typed gRPC.

## Architecture

| Surface | Default | Responsibility |
| --- | --- | --- |
| Central HTTP API | `127.0.0.1:8008` | API-key authentication, device registry, heartbeats, exclusive leases, and audit history |
| Lab-PC HTTP management | `127.0.0.1:9001` | Local liveness and readiness only |
| Lab-PC gRPC | `127.0.0.1:50051` | Typed `ReadValue`, `WriteValue`, and streamed measurements |

The control path is:

1. A Lab PC registers its stable manager ID, device inventory, and gRPC endpoint with Central.
2. A client discovers an available device and requests an exclusive lease from Central.
3. Central returns an opaque lease ID and that device's typed gRPC endpoint.
4. The client calls the Lab PC directly, carrying the lease ID in `x-labnet-lease-id` metadata.
5. The Lab PC validates the lease with Central before control and periodically during a long stream.

Central never proxies or stores measurement samples. Each physical device has a dedicated bounded worker, so its blocking driver calls remain ordered without blocking the asynchronous network servers.

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

Edit `lab_pc/device_inventory.py` to construct the physical adapters installed on that machine. Reusable device interfaces, execution, Central adapters, protobuf contracts, and gRPC services stay in the `labnet` package.

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

`LabClient` and `RemoteDummyDevice` provide the same explicit cleanup pattern
for synchronous programs:

```python
from labnet import LabClient, RemoteDummyDevice

central = LabClient("http://127.0.0.1:8008", "<researcher API key>")

try:
    device = RemoteDummyDevice(central, device_id="dummy-device-1")
    try:
        device.write_value(12.5, channel="phase")
        print(device.read_value(channel="phase"))
    finally:
        device.close()
finally:
    central.close()
```

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

## Development

Run the complete test suite:

```powershell
python -m pytest
```

After editing a `.proto` file, regenerate and verify the committed bindings:

```powershell
python tools/generate_protos.py
```

The schema/API details and administration commands are documented in `central_server/README.md`.

## Credential warning

Local `.env` files and bytecode caches are ignored. Credentials previously committed can still exist in Git history: rotate or revoke every exposed key before deployment. Removing a file in a later commit does not erase it from earlier commits.
