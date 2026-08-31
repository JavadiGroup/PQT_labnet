# LabNet

LabNet is an open-source Python system for safely sharing laboratory
instruments across a network. A central HTTP service handles identity, device
discovery, health, and exclusive leases. Experiment programs then communicate
directly with the computer attached to an instrument through typed gRPC.

LabNet is designed for stateful hardware such as serial, VISA, vendor-SDK, and
Ethernet instruments. Each physical device receives an isolated, serialized
worker so concurrent network requests cannot call the same driver at the same
time.

## Highlights

- Typed protobuf contracts for instrument operations and streamed measurements.
- Direct client-to-instrument-computer data paths; Central does not proxy data.
- Exclusive, renewable leases with automatic expiry and command-idle limits.
- Synchronous and asyncio clients.
- A common facade for local and remote instruments.
- A minimal TLS model with one private CA and server certificates only.
- SQLite for a single Central process and PostgreSQL support for larger setups.
- Explicit pre-deployment contract generation with no runtime code replacement.

## How it works

```text
                  HTTPS management plane
Experiment client ─────────────────────────► Central Server
        │                                    identity, discovery,
        │                                    leases, heartbeats
        │
        └──────── typed gRPC + lease ───────► Lab PC ───► instrument driver
                    control and data
```

1. A Lab PC starts its physical-device inventory and registers it with Central.
2. A client authenticates with Central and requests an exclusive device lease.
3. Central returns the device's gRPC endpoint and an opaque lease identifier.
4. The client connects directly to the Lab PC and includes the lease identifier
   with every RPC.
5. The Lab PC validates the lease before executing hardware operations.

## Repository layout

| Path | Purpose |
| --- | --- |
| [`Labnet/`](Labnet/) | Self-contained installable Python project |
| [`Labnet/labnet/`](Labnet/labnet/) | Installed `labnet` package |
| [`Labnet/labnet/central/`](Labnet/labnet/central/) | Reusable Central API, services, storage, CLI, and migrations |
| [`Labnet/protos/`](Labnet/protos/) | Protobuf source contracts |
| [`Labnet/tools/`](Labnet/tools/) | Development-time protobuf generator |
| [`Labnet/tests/`](Labnet/tests/) | Unit and end-to-end tests |
| [`examples/central_server/`](examples/central_server/) | Central deployment example |
| [`examples/lab_pc/`](examples/lab_pc/) | Lab-PC configuration and device inventory example |
| [`examples/client_test/`](examples/client_test/) | Synchronous and asyncio client examples |

## Quick start

Python 3.11 or newer is required. The following commands create one local
development environment containing all three roles.

```powershell
git clone <repository-url> lab_network
cd lab_network
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\Labnet[dev,central,lab-pc]"
```

### 1. Initialize Central

In the activated shell:

```powershell
labnet-central-admin db upgrade
labnet-central-admin user create central-admin --role admin
labnet-central-admin user create lab-pc-dev-01 --role device_manager
labnet-central-admin user create researcher --role user
```

Save the API keys printed by the commands. Each key is shown once. The
`device_manager` username must exactly match the Lab PC ID used below.

Start Central and leave it running:

```powershell
python -m examples.central_server
```

Verify it from another shell:

```powershell
Invoke-RestMethod http://127.0.0.1:8008/health/ready
```

### 2. Start the example Lab PC

Copy the environment template:

```powershell
Copy-Item .\examples\lab_pc\.env.example .\examples\lab_pc\.env
```

Edit `.env` and set:

```dotenv
LABNET_LAB_PC_ID=lab-pc-dev-01
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=http://127.0.0.1:8008
LABNET_DEVICE_MANAGER_API_KEY=<device-manager API key>
LABNET_LEASE_VALIDATION_MODE=central
```

Start the Lab PC in a second activated shell:

```powershell
python -m examples.lab_pc
```

The checked-in inventory exposes one simulated `dummy_device`. Verify the Lab
PC locally:

```powershell
Invoke-RestMethod http://127.0.0.1:9001/health/ready
```

### 3. Run a client

In a third activated shell, use the researcher key:

```powershell
$env:LABNET_API_KEY = "<researcher API key>"
python .\examples\client_test\client.py
```

Or run the asyncio example:

```powershell
python .\examples\client_test\async_client.py
```

Both examples authenticate, discover an available device, acquire a lease,
perform direct gRPC operations, release the lease, and close their transports.

## Documentation

The complete guide in [`Labnet/README.md`](Labnet/README.md) covers:

- architecture and security principles;
- development and wheel-based installation;
- local and static-IP multi-system production deployment;
- static IP addresses, certificates, firewalls, and TLS;
- Central, Lab-PC, and client configuration;
- integrating an instrument from an existing family;
- defining a new protobuf-backed device family;
- lease, streaming, database, and operational behavior; and
- testing, release checks, and troubleshooting.

If Python packaging or private-CA certificates are new to you, start with
[building and staging the wheel](Labnet/README.md#stage-the-release-artifacts)
and then follow the complete
[certificate setup](Labnet/README.md#generate-the-minimum-tls-certificate-set).
Those sections explain what each file does, which computer receives it, and
which private-key files must never be copied.

Each directory under [`examples/`](examples/) also contains a focused README.

## Development and contributions

Run contract and test checks from the installable project directory:

```powershell
cd Labnet
python tools/generate_protos.py --check
python -m pytest
```

Changes to protobuf contracts must include the regenerated Python artifacts.
Changes to a physical adapter should include tests that exercise its contract
and failure behavior. Keep credentials, runtime databases, vendor binaries, and
instrument data out of commits.

## Security

The local quick start uses plaintext loopback connections only. LabNet rejects
plaintext Central or gRPC endpoints on non-loopback interfaces. Use HTTPS for
Central and TLS for every LAN-facing Lab-PC gRPC service.

Never commit API keys, private keys, site-specific certificate files, or
runtime databases. Rotate any credential that may have been exposed.

## License

LabNet is licensed under the [MIT License](LICENSE).
