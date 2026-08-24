# Labnet

Labnet is the device-control network for the PQT lab. The current implementation phase builds the Lab-PC host around typed gRPC services while retaining a small HTTP management plane. It is a clean break from the Lab-PC WebSocket RPC transport.

## Architecture

The Lab PC has two deliberately separate network surfaces:

| Plane | Default endpoint | Purpose |
| --- | --- | --- |
| gRPC control/data | `127.0.0.1:50051` | Typed device operations, lease-authorized control, and efficient server-streamed measurements |
| HTTP management | `127.0.0.1:9001` | Process liveness/readiness and Central Server registration/heartbeat management |

Reusable device interfaces, per-device execution, protobuf contracts, and gRPC services live under `Labnet/labnet`. The `lab_pc` package contains the deployment configuration, local device inventory, and process entry point. Each physical device is owned by a dedicated worker so blocking driver calls are serialized without blocking the async network server. One unavailable device does not prevent other configured devices from starting; duplicate IDs and invalid configuration remain fatal.

This phase covers the Lab-PC server only. The existing Central Server and test client still use the former WebSocket contract and are intentionally incompatible with this gRPC endpoint. They must be migrated before an end-to-end network deployment.

## Install and run

Python 3.11 or newer is required. From the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[lab-pc]"
Copy-Item lab_pc\.env.example lab_pc\.env
python -m lab_pc
# Equivalent after installation: labnet-lab-pc
```

The checked-in example configuration is safe for local development: both servers bind to loopback, gRPC is plaintext, Central integration is disabled, and lease validation uses the development validator. Edit the untracked `lab_pc/.env` for a real host.

## Device API and streaming

The versioned dummy-device service exposes `ReadValue`, `WriteValue`, and server-streaming `StreamData`. Device and operation IDs are explicit protobuf fields; the lease ID is sent as the `x-labnet-lease-id` gRPC metadata value.

`StreamData` is designed for wavemeter-like scalar feedback used by a control loop. A request selects a device and may include a channel, sample rate, sample count, and duration. If count and duration are both present, the first reached limit ends the stream. With neither present, it runs until cancellation or the RPC deadline. Each sample carries a sequence number, Unix timestamp in nanoseconds, scalar value, unit, quality, and status. The Lab-PC configuration bounds rate, count, and duration; the default maximum rate is 1,000 samples/s. Long streams periodically revalidate their lease rather than contacting the Central Server for every sample.

The default Lab-PC limits are 1,000,000 samples and one hour per bounded request. Scalar values are protobuf binary doubles rather than JSON/Base64 text, and responses are produced incrementally with gRPC flow control instead of materializing an entire acquisition in memory. Even at the 1 kHz ceiling, this wavemeter-style stream is normally well below 1 MB/s; camera and oscilloscope arrays should later receive a separate chunked-array contract.

For timing-sensitive acquisition, a physical adapter should return its hardware or driver acquisition timestamp with the value. A scalar-only adapter, including the simple dummy, uses server receipt time after the worker result is delivered.

This stream is appropriate for supervisory and wavemeter-rate feedback, but Ethernet, Python, and a general-purpose operating system are not hard-real-time components. A stability-critical or high-bandwidth servo should close its inner loop on the Lab PC or dedicated controller and expose setpoints plus telemetry over gRPC. Lease revocation is observed at the next periodic validation after any in-flight sample completes; with the default 5-second validation and sample timeouts, design for a worst case near 10 seconds.

`WriteValue` is an absolute-setpoint operation, so repeating the same value is idempotent. Clients should still give every call a stable operation ID. Deadline errors return machine-readable trailing metadata indicating whether a physical operation may already have completed; future non-idempotent RPCs must define their own reconciliation or durable deduplication policy rather than being retried blindly.

Failures use canonical gRPC status codes plus stable trailing metadata: `labnet-error-code`, `labnet-operation-id`, `labnet-retryable`, and, for device deadlines, `labnet-operation-may-have-completed`. Driver tracebacks stay in Lab-PC logs rather than crossing the network.

## Transport security policy

`LABNET_GRPC_TLS_MODE` supports three policies:

- `disabled`: permitted only when gRPC binds to a loopback address. This is for local development and tests.
- `tls`: required for a LAN-facing bind and requires a server certificate and private key. It encrypts traffic and lets clients authenticate the Lab PC.
- `mtls`: adds a trusted CA and requires client certificates, so the Lab PC and client authenticate each other.

The HTTP management plane is plaintext and therefore loopback-only. Put it behind an authenticated HTTPS reverse proxy before exposing it beyond the machine. Development lease bypass is also forbidden in production and on non-loopback gRPC binds.

Central Server API keys and lease-validation requests follow the same rule: plaintext HTTP is allowed only to a loopback Central endpoint. A remote Central Server URL must use HTTPS.

## Regenerate protobuf bindings

Install the development extra, then regenerate after changing a file under `Labnet/protos/labnet/devices/v1`:

```powershell
python -m pip install -e ".[lab-pc,dev]"
python tools/generate_protos.py
```

The checked-in script uses the pinned generator, repairs package-relative imports deterministically, and verifies that the resulting bindings import successfully. Commit the `.proto` source and regenerated Python bindings together.

Run the shared-package tests with:

```powershell
python -m pytest Labnet/tests
```

## Credential warning

Local `.env` files and bytecode caches are ignored and have been removed from the working tree. Credentials that were previously committed can still exist in Git history: rotate or revoke every exposed key before deployment. Removing a file in a later commit does not invalidate a secret or erase it from earlier commits.
