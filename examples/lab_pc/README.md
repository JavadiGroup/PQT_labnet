# Lab-PC example

A Lab PC is the computer physically connected to one or more laboratory
instruments. This example composes the installed `labnet` device-manager and
gRPC components with machine-specific environment settings and a physical
device inventory.

The checked-in inventory creates one simulated `PhysicalDummyDevice`, so the
complete system can be evaluated without hardware.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | Compose physical workers, Central registration, gRPC, and health APIs |
| `config.py` | Parse and validate environment settings |
| `device_inventory.py` | Construct the physical devices attached to this computer |
| `.env.example` | Safe local defaults and all supported settings |
| `__main__.py` | Support `python -m examples.lab_pc` |

This example imports reusable functionality from the installed `labnet`
package. It is not included in the LabNet wheel because every laboratory needs
its own reviewed inventory and deployment configuration.

## Install

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\Labnet[lab-pc]"
```

## Local setup

Central must already be running and must have a `device_manager` user for this
Lab PC.

Copy the template:

```powershell
Copy-Item .\examples\lab_pc\.env.example .\examples\lab_pc\.env
```

Set these values:

```dotenv
LABNET_ENVIRONMENT=development
LABNET_LAB_PC_ID=lab-pc-dev-01
LABNET_LAB_PC_HOST=local-lab-pc
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=http://127.0.0.1:8008
LABNET_DEVICE_MANAGER_API_KEY=<device-manager API key>
LABNET_LEASE_VALIDATION_MODE=central

LABNET_GRPC_BIND_HOST=127.0.0.1
LABNET_GRPC_PORT=50051
LABNET_GRPC_ADVERTISED_TARGET=127.0.0.1:50051
LABNET_GRPC_TLS_MODE=disabled

LABNET_HTTP_BIND_HOST=127.0.0.1
LABNET_HTTP_PORT=9001
```

`LABNET_LAB_PC_ID` must exactly equal the Central `device_manager` username.
The API key must belong to that same identity.

Start from the repository root:

```powershell
python -m examples.lab_pc
```

Verify local health:

```powershell
Invoke-RestMethod http://127.0.0.1:9001/health/live
$ready = Invoke-RestMethod http://127.0.0.1:9001/health/ready
$ready | ConvertTo-Json -Depth 6
```

Readiness should show:

- the device manager is running;
- Central registration succeeded;
- the gRPC health service is serving; and
- at least one device is available.

## Configure attached instruments

Edit `device_inventory.py` before deployment. It must return one physical
adapter instance per attached instrument:

```python
from labnet.devices.DummyDevice.physical import PhysicalDummyDevice


def create_devices() -> list[PhysicalDummyDevice]:
    return [
        PhysicalDummyDevice(device_id="dummy-bench-a"),
        PhysicalDummyDevice(device_id="dummy-bench-b"),
    ]
```

Every `device_id` must be stable and globally unique. Do not derive IDs from a
temporary hostname or USB enumeration order. Prefer names that identify a
physical instrument or fixed rack position.

A physical adapter must provide:

- `device_id` and the generated contract's `device_type`;
- `connect()` and `disconnect()`;
- `status()` and `availability()`;
- JSON-compatible `metadata()`; and
- every operation declared by its protobuf contract.

LabNet validates the physical method signatures at startup and derives the
remote method allowlist from the generated contract. A helper method does not
become remotely callable merely because it exists on the Python object.

## Production TLS configuration

Plaintext gRPC is rejected on non-loopback addresses. A typical static-IP
configuration is:

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

LABNET_HTTP_BIND_HOST=127.0.0.1
LABNET_HTTP_PORT=9001
```

The advertised address must be reachable by clients and must match a subject
alternative name in the server certificate. When Central uses a private CA,
copy the public `labnet-ca.pem` to the Lab PC and set `SSL_CERT_FILE` in the
Lab-PC service environment. These files have distinct jobs:

```text
C:\ProgramData\LabNet\certs\lab-pc.crt    public Lab-PC server certificate
C:\ProgramData\LabNet\certs\lab-pc.key    secret Lab-PC private key
C:\ProgramData\LabNet\certs\labnet-ca.pem public CA certificate used to trust Central
```

The Lab-PC process reads `lab-pc.crt` and `lab-pc.key` to prove its identity to
gRPC clients. It uses the CA certificate separately when connecting to Central
over HTTPS. Never copy the CA private key to the Lab PC.

For mutual TLS, use:

```dotenv
LABNET_GRPC_TLS_MODE=mtls
LABNET_GRPC_CA_FILE=C:/ProgramData/LabNet/certs/labnet-ca.pem
```

The CA file must contain the issuer trusted for client certificates. It can be
the same CA used for server certificates in a small laboratory, as shown here,
or a separate client CA. The Lab PC receives the public CA certificate but
never a client's private key. See the main
[certificate guide](../../Labnet/README.md#certificates-and-trust) for the full
creation, signing, distribution, and renewal workflow.

## Deployment guidance

1. Generate and test contracts on a build computer.
2. Build and install a versioned LabNet wheel.
3. Copy the reviewed Lab-PC composition to a read-only application directory.
4. Install vendor drivers and verify OS-level instrument access.
5. Store API keys and private keys in the service environment or secret store.
6. Stop the Lab-PC process before changing its wheel or inventory.
7. Start Central first and wait for readiness.
8. Start the Lab PC and confirm registration before allowing clients.

The inventory is instantiated once at startup. Editing Python or proto files
does not modify the running device objects.

## Common startup failures

| Error | Resolution |
| --- | --- |
| Manager authentication fails | Match `LABNET_LAB_PC_ID`, Central username, and API key |
| Non-loopback Central URL is rejected | Use an `https://` URL and trusted certificate |
| Non-loopback gRPC bind is rejected | Configure `LABNET_GRPC_TLS_MODE=tls` or `mtls` |
| Development lease mode is rejected | Use `central` in production and on LAN binds |
| Advertised target is rejected | Use a client-reachable host or IP, never `0.0.0.0` or loopback for a LAN service |
| Physical contract validation fails | Regenerate contracts and match every physical method signature |
| Device is unavailable | Check vendor driver connection, `availability()`, and Lab-PC logs |

After the Lab PC is ready, continue with the
[client examples](../client_test/README.md).
