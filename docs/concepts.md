# Concepts

## Roles and commands

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
| `labnet-control-center --env-file control-center.env` | Web interface ([Control Center](control-center/index.md#control-center)) | `C:\ProgramData\LabNet\control-center.env` |

For everyday development workflows (local network, change loop, release

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

## From physical device to network call

Every device family lives in a **driver package**, installed like any Python
package. [`labnet-sim`](https://github.com/mill0851/labnet/tree/main/packages/labnet-sim) is the one LabNet ships: a
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
