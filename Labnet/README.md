# LabNet package and deployment guide

LabNet provides authenticated, lease-controlled access to laboratory
instruments over a network. It separates management traffic from instrument
traffic:

- Central uses HTTP to manage users, discovery, health, and exclusive leases.
- Lab PCs expose typed instrument operations over gRPC.
- Clients obtain authority from Central and then communicate directly with the
  Lab PC attached to the selected instrument.

The installable Python project is this `Labnet/` directory. The sibling
`examples/` directory contains runnable compositions that import the installed
`labnet` package but are not included in its wheel.

## Features

- API-key authentication with stored key hashes.
- Stable Lab-PC identities and periodic health registration.
- Exclusive, renewable instrument leases.
- Automatic lease expiry after client or network failure.
- A server-owned command-idle timeout that clients cannot disable.
- Typed unary and server-streaming gRPC operations.
- Synchronous and asyncio experiment clients.
- Per-instrument serialized workers for blocking hardware drivers.
- Generated server, client, physical-protocol, and allowlist metadata.
- SQLite and PostgreSQL Central storage backends.
- TLS and optional mutual TLS for LAN deployments.
- Explicit schema migrations and reproducible wheel deployment.

## Architecture

| Component | Default local endpoint | Responsibility |
| --- | --- | --- |
| Central API | `http://127.0.0.1:8008` | Authentication, discovery, manager heartbeats, leases, and audit records |
| Lab-PC health API | `http://127.0.0.1:9001` | Local liveness and readiness only |
| Lab-PC device API | `127.0.0.1:50051` | Typed gRPC control and streamed measurements |
| Client library | No listener | Central HTTP client, lease lifecycle, and direct gRPC channels |

The normal request path is:

1. A Lab PC constructs its physical devices and connects each driver.
2. It registers its stable manager ID, device metadata, and advertised gRPC
   endpoint with Central.
3. A client authenticates and requests an exclusive lease for a device.
4. Central returns the device endpoint and an opaque lease ID.
5. The client opens a direct gRPC channel and sends the lease ID in
   `x-labnet-lease-id` metadata.
6. The Lab PC validates the lease with Central before unary operations and
   periodically during streams.

Central never proxies or stores measurement samples. This prevents high-rate
instrument traffic from becoming management-plane load and allows each Lab PC
to remain responsible for hardware timing and driver behavior.

## Runtime safety model

Each physical device owns a bounded worker queue and a dedicated worker thread.
VISA, serial, SDK, and similar blocking calls are therefore ordered for that
instrument without blocking the asynchronous HTTP or gRPC servers. Independent
instruments can still make progress concurrently.

Device contracts and physical implementations are deployment inputs:

- protobuf files are generated before building a release;
- generated files are committed and checked for staleness;
- physical implementations are installed from a versioned wheel;
- the Lab-PC inventory is constructed once at process startup; and
- Uvicorn runs with source reload disabled.

There is no runtime proto generation, driver replacement, or device-object hot
reload. To change an instrument contract or implementation, build and deploy a
new release while the affected process is stopped.

## Requirements

- Python 3.11 or newer.
- Network routes between clients, Central, and Lab PCs.
- A trusted certificate authority for non-loopback deployments.
- Vendor drivers and operating-system permissions required by each instrument.
- SQLite for a single Central process, or PostgreSQL for multiple Central
  workers or replicas.

The examples use PowerShell commands. Equivalent Python and pip commands work
on Linux and macOS after activating the appropriate virtual environment.

## Installation options

Run package commands from this `Labnet/` directory.

### Development installation

```powershell
py -3.11 -m venv ..\.venv
..\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,central,lab-pc]"
```

An editable install is useful during development because imports resolve to the
working source tree. Do not use editable installs on managed laboratory hosts.

### Role-specific installations

```powershell
# Client library
python -m pip install -e .

# Lab-PC dependencies
python -m pip install -e ".[lab-pc]"

# Central dependencies with SQLite
python -m pip install -e ".[central]"

# Central dependencies with PostgreSQL
python -m pip install -e ".[central,postgres]"
```

Extras add dependencies; they do not produce different LabNet source packages.
For example, the `central` extra adds FastAPI, SQLAlchemy, Alembic, and Uvicorn.

### Installed commands

The package creates these commands:

| Command | Purpose |
| --- | --- |
| `labnet-central` | Run the reusable Central application |
| `labnet-central-admin` | Apply migrations and manage Central users |

The Lab-PC composition is site-specific and remains under
`examples/lab_pc/`; run it with `python -m examples.lab_pc` from the repository
root or copy and adapt it in a deployment project.

## Local end-to-end setup

The local setup runs Central, one simulated Lab PC, and a client on the same
computer. Plaintext communication is permitted because every endpoint is on
loopback.

### 1. Install all roles

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\Labnet[dev,central,lab-pc]"
```

Activate this environment in every new terminal used below.

### 2. Initialize Central

Central reads its process environment directly. If
`LABNET_DATABASE_URL` is not set, it uses a SQLite database under the current
user's application-data directory.

```powershell
labnet-central-admin db upgrade
labnet-central-admin user create central-admin --role admin
labnet-central-admin user create lab-pc-dev-01 --role device_manager
labnet-central-admin user create researcher --role user
```

Each user command prints a new API key exactly once. Store the keys separately:

- the `device_manager` key belongs only to the corresponding Lab PC;
- the `researcher` key is used by experiment clients; and
- the `admin` key should be reserved for administration.

The `device_manager` username must exactly equal the Lab PC's configured
`LABNET_LAB_PC_ID`.

Start Central:

```powershell
python -m examples.central_server
```

Verify both health states from another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8008/health/live
Invoke-RestMethod http://127.0.0.1:8008/health/ready
```

Both responses should report `status: ok`.

### 3. Configure the Lab PC

```powershell
Copy-Item .\examples\lab_pc\.env.example .\examples\lab_pc\.env
```

Set at least these values in `examples/lab_pc/.env`:

```dotenv
LABNET_ENVIRONMENT=development
LABNET_LAB_PC_ID=lab-pc-dev-01
LABNET_LAB_PC_HOST=local-lab-pc
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=http://127.0.0.1:8008
LABNET_DEVICE_MANAGER_API_KEY=<device-manager API key>
LABNET_LEASE_VALIDATION_MODE=central

LABNET_GRPC_BIND_HOST=127.0.0.1
LABNET_GRPC_ADVERTISED_TARGET=127.0.0.1:50051
LABNET_GRPC_TLS_MODE=disabled
LABNET_HTTP_BIND_HOST=127.0.0.1
```

The checked-in `device_inventory.py` constructs one simulated
`PhysicalDummyDevice`. Start the Lab PC:

```powershell
python -m examples.lab_pc
```

Verify readiness:

```powershell
$ready = Invoke-RestMethod http://127.0.0.1:9001/health/ready
$ready | ConvertTo-Json -Depth 6
```

The response should show a serving gRPC endpoint, successful Central
registration, and at least one available device.

### 4. Run a synchronous client

```powershell
$env:LABNET_SERVER_URL = "http://127.0.0.1:8008"
$env:LABNET_API_KEY = "<researcher API key>"
python .\examples\client_test\client.py
```

### 5. Run an asyncio client

```powershell
python .\examples\client_test\async_client.py
```

The asyncio example performs discovery, acquisition, a write, a read, and a
short streamed acquisition. Both examples release the lease and close network
transports in `finally` blocks.

## Production deployment

Production hosts should install a reviewed wheel, not execute from a writable
clone. The examples are deliberately excluded from the wheel because Lab-PC
inventories and client programs are deployment-specific.

### 1. Prepare and test a release

On a development or build computer:

```powershell
cd Labnet
python -m pip install -e ".[dev,central,lab-pc]"
python tools/generate_protos.py --check
python -m pytest
```

If a proto changed, generate first and then rerun the checks:

```powershell
python tools/generate_protos.py
python tools/generate_protos.py --check
python -m pytest
```

Update `version` in `pyproject.toml`, then build one wheel:

```powershell
python -m pip wheel --no-deps . --wheel-dir dist
Get-FileHash .\dist\labnet-*.whl -Algorithm SHA256
```

Record the Git revision, package version, wheel filename, and SHA-256 hash.
Copy the exact same wheel to Central, Lab PCs, and client computers.

### 2. Example three-computer topology

| Role | Example static address | Required inbound traffic |
| --- | --- | --- |
| Central | `192.168.50.10` | TCP 443 from Lab PCs and clients |
| Lab PC | `192.168.50.20` | TCP 50051 from approved clients |
| Client | `192.168.50.30` | None for LabNet |

Assign addresses statically or reserve them in DHCP. Stable DNS names are
preferable when available. The names or IP addresses advertised by LabNet must
appear in the corresponding certificate subject alternative names.

Do not expose Central's loopback port 8008 or the Lab-PC health port 9001 to the
LAN. The public paths are Central HTTPS on 443 and Lab-PC gRPC TLS on 50051.

### 3. Certificates and trust

Provision certificates before starting services:

- Central needs a server certificate for `192.168.50.10` or its DNS name.
- Each Lab PC needs a server certificate for its advertised gRPC name/address.
- Lab PCs and clients need the CA certificate that issued Central's certificate.
- Clients need the CA certificate that issued Lab-PC certificates.
- In mTLS mode, each client additionally needs its own certificate and private
  key, and the Lab PC needs the client-issuing CA.

Keep private keys readable only by the relevant service account. Never copy a
CA private key to a runtime host.

### 4. Deploy Central

Create and activate a dedicated environment, then install the wheel:

```powershell
py -3.11 -m venv C:\ProgramData\LabNet\central-venv
C:\ProgramData\LabNet\central-venv\Scripts\python.exe -m pip install `
  ".\labnet-0.2.0-py3-none-any.whl[central]"
```

Set the Central service environment:

```powershell
$env:LABNET_DATABASE_URL = "sqlite:///C:/ProgramData/LabNet/Central/labnet.db"
$env:LABNET_CENTRAL_BIND_HOST = "127.0.0.1"
$env:LABNET_CENTRAL_BIND_PORT = "8008"
```

Apply migrations and create principals using the installed environment:

```powershell
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe db upgrade
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe user create `
  lab-pc-01 --role device_manager
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe user create `
  researcher --role user
```

Run Central on loopback:

```powershell
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central.exe
```

Place an HTTPS reverse proxy on the same host. Its responsibilities are:

1. listen on `192.168.50.10:443`;
2. present the Central server certificate;
3. accept only modern TLS connections;
4. proxy requests to `http://127.0.0.1:8008`; and
5. preserve request methods, headers, paths, and status codes.

For example, a Caddy configuration using an already issued certificate can be:

```caddyfile
https://192.168.50.10 {
    tls C:/ProgramData/LabNet/certs/central.crt C:/ProgramData/LabNet/certs/central.key
    reverse_proxy 127.0.0.1:8008
}
```

Run the proxy and Central under restricted service accounts and configure them
to start automatically after networking is available.

### 5. Deploy a Lab PC

Install the wheel with Lab-PC dependencies:

```powershell
py -3.11 -m venv C:\ProgramData\LabNet\lab-pc-venv
C:\ProgramData\LabNet\lab-pc-venv\Scripts\python.exe -m pip install `
  ".\labnet-0.2.0-py3-none-any.whl[lab-pc]"
```

Copy the reviewed `examples/` package, or an equivalent site-owned composition,
to a read-only application directory such as `C:\ProgramData\LabNet\app`.
Configure the service working directory as that application's parent so
`python -m examples.lab_pc` can import the composition.

Set the Lab-PC service environment or create the example's `.env` before
starting it:

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

When Central uses a private CA, also set the Lab-PC process variable:

```powershell
$env:SSL_CERT_FILE = "C:\ProgramData\LabNet\certs\lab-ca.pem"
```

Start from the application parent:

```powershell
C:\ProgramData\LabNet\lab-pc-venv\Scripts\python.exe -m examples.lab_pc
```

For mTLS, change `LABNET_GRPC_TLS_MODE` to `mtls` and set
`LABNET_GRPC_CA_FILE` to the CA that issues client certificates.

### 6. Deploy a client

Install the base wheel:

```powershell
py -3.11 -m venv C:\ProgramData\LabNet\client-venv
C:\ProgramData\LabNet\client-venv\Scripts\python.exe -m pip install `
  .\labnet-0.2.0-py3-none-any.whl
```

For a private CA, set HTTPS trust and construct gRPC trust explicitly:

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

For mTLS, also pass `private_key` and `certificate_chain` to
`GrpcTlsConfig.from_files()`.

### 7. Firewall rules

Restrict access by role:

- Central: allow TCP 443 from approved Lab PCs and client networks.
- Lab PC: allow TCP 50051 from approved client networks.
- Do not expose Central port 8008.
- Do not expose Lab-PC port 9001.
- Clients need no inbound LabNet rule.

Limit outbound traffic where practical: Lab PCs need Central HTTPS access;
clients need Central HTTPS and the gRPC ports of authorized Lab PCs.

### 8. Deployment verification

Verify in this order:

1. Central `/health/live` and `/health/ready` return `status: ok` through HTTPS.
2. Central reports the expected Lab-PC heartbeat and device inventory.
3. The Lab-PC loopback readiness endpoint reports gRPC serving and devices
   available.
4. A client authenticates and lists the expected device.
5. The client acquires a lease and completes one harmless read operation.
6. A second client cannot lease the same device concurrently.
7. Closing the first client releases the lease and makes the device available.

## Client programming

### Synchronous client

Create one `LabClient` per process and share it across remote device objects:

```python
from labnet import DummyDevice, LabClient

central = LabClient("http://127.0.0.1:8008", "<researcher API key>")
try:
    device = DummyDevice(device_id="dummy_device_1", client=central)
    try:
        device.write_value(12.5, channel="main")
        print(device.read_value(channel="main"))
    finally:
        device.close()
finally:
    central.close()
```

Omitting `client` creates the local physical implementation instead:

```python
from labnet import DummyDevice

device = DummyDevice(device_id="local-dummy")
try:
    device.write_value(2.5)
    print(device.read_value())
finally:
    device.close()
```

The experiment-facing methods remain the same for local and remote use.

### Asyncio client

Use `AsyncLabClient` when controlling independent instruments concurrently,
consuming streams, or integrating with an existing asyncio service:

```python
import asyncio

from labnet import AsyncLabClient


async def main() -> None:
    central = AsyncLabClient("http://127.0.0.1:8008", "<researcher API key>")
    try:
        devices = await central.list_available_devices("dummy_device")
        device = await central.acquire_dummy(devices[0].device_id)
        try:
            await device.write_value(12.5)
            measurement = await device.read_value()
            print(measurement.value, measurement.unit)

            samples = device.stream_data(sample_rate_hz=10, sample_count=20)
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

Asyncio allows unrelated operations to overlap, but it does not make one
physical instrument execute multiple driver calls simultaneously. The
per-device worker remains serialized.

## Lease behavior

Clients use a 60-second lease by default and renew it every 20 seconds while the
device object remains healthy. Closing a device releases its lease immediately.
If a process crashes or loses connectivity, renewal stops and Central expires
the lease.

Central also expires a lease after 900 seconds without an authorized device
command by default. Lease renewal proves client liveness but does not count as
instrument activity, so an abandoned program cannot hold an instrument forever
merely because its renewal task is running.

Unary authorization counts as activity. A live stream refreshes activity
through periodic lease revalidation. Keep the Central command-idle timeout
longer than both the Lab-PC stream revalidation interval and the longest valid
unary hardware operation.

## Streaming behavior

The example contract supports server-streamed scalar measurements. A stream can
specify a channel, sample rate, sample count, and duration. The first reached
limit ends the stream. Each sample includes:

- operation ID;
- monotonically increasing sequence number;
- Unix timestamp in nanoseconds;
- double-precision value;
- unit;
- quality and status; and
- optional channel.

The default 1 kHz ceiling is appropriate for scalar telemetry and supervisory
control, not hard real-time feedback. Keep stability-critical inner control
loops on the Lab PC or dedicated hardware. Cameras and oscilloscopes should use
a future chunked binary-array pattern rather than scalar streaming.

## Central database and API

Central stores users, API-key hashes, manager identities, device registrations,
leases, idempotency records, and audit events. It does not store instrument
measurements.

SQLite is recommended for one Central process. A database uniqueness constraint
enforces one active lease per device. PostgreSQL uses the same SQLAlchemy models
and Alembic migrations and is appropriate for multiple workers or replicas.

Normal startup never creates or modifies database tables. Apply migrations
explicitly:

```powershell
labnet-central-admin db upgrade
```

Important HTTP endpoints include:

- `GET /health/live`
- `GET /health/ready`
- `GET /api/v1/auth/me`
- `POST /api/v1/device-managers/register`
- `POST /api/v1/device-managers/heartbeat`
- `GET /api/v1/devices`
- `GET /api/v1/devices/{device_id}`
- `POST /api/v1/leases`
- `POST /api/v1/leases/{lease_id}/renew`
- `DELETE /api/v1/leases/{lease_id}`
- `POST /api/v1/leases/{lease_id}/validate`

## Configuration reference

### Central settings

Central reads environment variables from its process environment.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_DATABASE_URL` | User application-data SQLite file | SQLAlchemy database URL |
| `LABNET_CENTRAL_BIND_HOST` | `127.0.0.1` | Built-in HTTP bind; loopback only |
| `LABNET_CENTRAL_BIND_PORT` | `8008` | Built-in HTTP port |
| `LABNET_LEASE_MIN_SECONDS` | `5` | Minimum accepted lease duration |
| `LABNET_LEASE_MAX_SECONDS` | `86400` | Maximum accepted lease duration |
| `LABNET_LEASE_DEFAULT_IDLE_TIMEOUT_SECONDS` | `900` | Server-owned command-idle limit |
| `LABNET_HEARTBEAT_TIMEOUT_SECONDS` | `30` | Time before a manager is unhealthy |
| `LABNET_MAINTENANCE_INTERVAL_SECONDS` | `5` | Expiry maintenance interval |
| `LABNET_CREATE_SCHEMA_ON_STARTUP` | `false` | Test/development convenience only |

Do not enable automatic schema creation in production. Use Alembic migrations.

### Lab-PC settings

The example loads `examples/lab_pc/.env`; OS-service environments can provide
the same values directly.

| Variable | Purpose |
| --- | --- |
| `LABNET_ENVIRONMENT` | `development` or `production` policy mode |
| `LABNET_LAB_PC_ID` | Stable Central identity; must match the manager username |
| `LABNET_LAB_PC_HOST` | Human-readable or network host label |
| `LABNET_CENTRAL_ENABLED` | Enable registration and heartbeats |
| `LABNET_SERVER_URL` | Central HTTP(S) base URL |
| `LABNET_DEVICE_MANAGER_API_KEY` | Manager credential |
| `LABNET_LEASE_VALIDATION_MODE` | `central` or loopback-only `development` |
| `LABNET_GRPC_BIND_HOST` | gRPC listener address |
| `LABNET_GRPC_PORT` | gRPC listener port |
| `LABNET_GRPC_ADVERTISED_TARGET` | Client-reachable `host:port` registered with Central |
| `LABNET_GRPC_TLS_MODE` | `disabled`, `tls`, or `mtls` |
| `LABNET_GRPC_CERT_FILE` | Server certificate chain |
| `LABNET_GRPC_KEY_FILE` | Server private key |
| `LABNET_GRPC_CA_FILE` | Client CA required by mTLS |
| `LABNET_HTTP_BIND_HOST` | Local health API bind; loopback only |
| `LABNET_HTTP_PORT` | Local health API port |

The template documents queue, timeout, stream, and message-size controls in
`../examples/lab_pc/.env.example`.

## Adding laboratory instruments

There are two different workflows: adding another instance of an existing
family and defining a new network contract.

### Add another instrument from an existing family

No proto generation is needed when the instrument uses an existing contract.

1. Install the vendor driver or SDK on the Lab PC.
2. Confirm the existing physical adapter supports the instrument model.
3. Add another adapter instance to `examples/lab_pc/device_inventory.py`.
4. Assign a stable, globally unique `device_id`.
5. Test driver connection, status, availability, metadata, reads, and writes.
6. Stop the Lab-PC process, deploy the reviewed inventory, and restart it.
7. Confirm that Central reports the new device and that a client can lease it.

Example:

```python
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice


def create_devices() -> list[PhysicalDummyDevice]:
    return [
        PhysicalDummyDevice(device_id="dummy-bench-a"),
        PhysicalDummyDevice(device_id="dummy-bench-b"),
    ]
```

### Add a new device family

Use the checked-in `DummyDevice` as a reference and work in this order.

#### 1. Define the protobuf contract

Add a `.proto` file under `protos/labnet/devices/v1/`. Every RPC must declare
its reusable pattern, physical method, and default unit explicitly:

```protobuf
rpc ReadFrequency(ReadFrequencyRequest) returns (ReadFrequencyResponse) {
  option (labnet.devices.v1.rpc) = {
    pattern: RPC_PATTERN_SCALAR_READ
    physical_method: "read_frequency"
    default_unit: "Hz"
  };
}
```

Use the UCUM/SI symbol `"1"` for dimensionless values. Generation fails when an
option is absent, incompatible with the RPC cardinality, or paired with an
unsupported message shape.

Supported patterns are:

| Pattern | RPC shape | Physical behavior |
| --- | --- | --- |
| `RPC_PATTERN_SCALAR_READ` | Unary | Call one physical read and return a scalar measurement |
| `RPC_PATTERN_SCALAR_WRITE` | Unary | Pass an absolute double setpoint and return a scalar measurement |
| `RPC_PATTERN_POLLED_SCALAR_STREAM` | Server streaming | Repeatedly call one physical read with scheduling, cancellation, lease revalidation, and backpressure |

#### 2. Generate Python artifacts

From `Labnet/`:

```powershell
python tools/generate_protos.py
```

The generator writes protobuf bindings and contract metadata under
`labnet/grpc/generated/`. Never edit generated files manually.

#### 3. Implement the physical adapter

Create `labnet/devices/<Family>/physical.py`. The adapter must implement:

- stable `device_id` and generated `device_type`;
- `connect()` and `disconnect()`;
- `status()`;
- `availability()`;
- JSON-compatible `metadata()`; and
- every physical method declared by the generated contract.

At Lab-PC startup, LabNet validates method existence and signatures. Optional
protobuf arguments must have Python defaults, and extra required physical
arguments are rejected.

#### 4. Add the public facade

Create a small `device.py`:

```python
from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated.device_contracts import WAVEMETER_CONTRACT

from .physical import PhysicalWavemeter


class Wavemeter(ContractDeviceFacade):
    contract = WAVEMETER_CONTRACT
    physical_type = PhysicalWavemeter
```

Generated contract methods are resolved by `ContractDeviceFacade` for both
local and remote use. Do not create family-specific gRPC service or remote
transport modules; dispatch, authorization, errors, leases, and streams are
shared framework behavior.

#### 5. Add instrument instances

Import the physical adapter in `examples/lab_pc/device_inventory.py` and return
one instance for each attached instrument.

#### 6. Validate before deployment

```powershell
python tools/generate_protos.py --check
python -m pytest
python -m pip wheel --no-deps . --wheel-dir dist
```

Deploy compatible Lab PCs before clients that call a newly added RPC.

### Contract compatibility

Once a contract is deployed:

- never change the number or type of an existing protobuf field;
- add new optional fields with unused numbers;
- reserve removed field numbers and names;
- keep existing RPC semantics while older clients may call them; and
- introduce a new protobuf package for breaking changes.

## Development and testing

From `Labnet/`:

```powershell
python tools/generate_protos.py --check
python -m pytest
```

The suite covers Central storage and leases, client renewal, generated
contracts, device workers, TLS policy, gRPC status translation, all example
imports, and a complete Central-to-Lab-PC-to-client stack.

To deliberately regenerate after editing a proto:

```powershell
python tools/generate_protos.py
python tools/generate_protos.py --check
```

## Operations and upgrades

Start components in this order:

1. Central database migration.
2. Central Server and HTTPS proxy.
3. Lab PCs and physical devices.
4. Clients and experiment programs.

For an upgrade:

1. build and test a new wheel;
2. retain the previously approved wheel;
3. stop the affected process;
4. install the new wheel;
5. apply Central migrations when present;
6. restart in dependency order; and
7. repeat the deployment verification checks.

Rollback code by stopping the process and reinstalling the prior wheel.
Database rollback requires an explicitly reviewed migration or backup-restore
plan; do not assume reinstalling old Python code reverses a schema migration.

## Troubleshooting

| Symptom | Likely cause | Check |
| --- | --- | --- |
| Central readiness is 503 | Missing migration or unavailable database | Run `labnet-central-admin db upgrade`; verify `LABNET_DATABASE_URL` and permissions |
| Lab PC cannot register | Wrong manager identity/key or Central trust failure | Match `LABNET_LAB_PC_ID` to the manager username; verify `SSL_CERT_FILE` |
| Lab PC rejects startup on a LAN address | Plaintext gRPC or development lease bypass | Configure `tls`/`mtls` and `LABNET_LEASE_VALIDATION_MODE=central` |
| Client sees no available devices | Manager heartbeat missing, driver unavailable, or active lease | Check both health endpoints and Central device state |
| gRPC certificate verification fails | Advertised target does not match certificate SAN or CA is missing | Compare `LABNET_GRPC_ADVERTISED_TARGET`, certificate SANs, and `GrpcTlsConfig` |
| Proto check reports stale files | Generated bindings do not match source protos | Run `python tools/generate_protos.py` and commit all generated changes |
| Physical contract validation fails | Driver method missing or signature incompatible | Compare `physical_method` and forwarded proto fields with `physical.py` |
| Lease is lost during an experiment | Renewal failure, lease deadline, or command-idle timeout | Inspect client and Central logs; verify network stability and timeout policy |

## Credential handling

Local `.env` files, private keys, caches, build outputs, and Central runtime
databases are ignored by Git. Keep service credentials outside the source tree
where possible, restrict filesystem permissions, and rotate any exposed API
key or private key immediately.
