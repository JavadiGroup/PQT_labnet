# PQT Network (LabNet)

LabNet lets experiment programs anywhere on the lab network use instruments
attached to other computers safely: one user at a time, through typed
operations, with the instrument's own computer staying in charge of the
hardware.

Each everyday workflow is a short sequence of scripts:

| I want to… | Run |
| --- | --- |
| Run the whole network on my laptop | `scripts/dev/setup.sh` → `seed.sh` → `up.sh` |
| Try or copy an experiment script | [`examples/`](examples/README.md) |
| Pick up code, proto, or device changes | `scripts/dev/redeploy.sh` |
| Ship a change to the real lab | `scripts/release/build.sh`, then `update_host.sh` on each computer |

First-time setup of the lab computers (static IPs, certificates, reverse
proxy, firewall) and the full settings reference are in
[`Labnet/README.md`](Labnet/README.md).

- [How the network fits together](#how-the-network-fits-together)
- [From physical device to network call](#from-physical-device-to-network-call)
- [Local development network](#local-development-network)
- [The change loop: down → update → generate → redeploy](#the-change-loop-down--update--generate--redeploy)
- [Updating the real lab network](#updating-the-real-lab-network)
- [Script reference](#script-reference)
- [Repository layout](#repository-layout)

## How the network fits together

There are three roles. In the lab each role runs on its own computer; for
development all three run on your laptop. Each role reads one settings file.

| Role | Command | Settings file | Listens on | Job |
| --- | --- | --- | --- | --- |
| **Central** | `labnet-central --env-file central.env` | `central.env` | HTTP `127.0.0.1:8008` (HTTPS `:443` via Caddy in the lab) | Users and API keys, the device directory, exclusive leases |
| **Lab PC** | `labnet-lab-pc --env-file lab-pc.env` | `lab-pc.env` | gRPC `:50051`; health `127.0.0.1:9001` | Owns the attached instruments and serves their operations |
| **Client** | your script, using `LabClient.from_env()` | `client.env` | nothing | Leases a device from Central, then talks directly to its Lab PC |

```text
                  ┌─────────────────────────────┐
                  │           Central           │
                  │ API keys · devices · leases │
                  └──▲──────────────────▲────┬──┘
1. register devices, │       2. log in, │    │ 3. Lab PC address
           heartbeat │        request a │    │    + lease id
      5. check lease │            lease │    │
                     │                  │    │
                     │                  │    ▼
              ┌──────┴──────┐         ┌─┴──────────┐
              │    Lab PC   │◄────────│   Client   │
              │  + drivers  │         │(your code) │
              └─────────────┘         └────────────┘
                      4. gRPC + lease id, direct
```

1. At startup a Lab PC connects its instruments and registers them, along with
   its gRPC address, with Central. It keeps sending heartbeats.
2. A client logs in to Central with an API key and requests a lease on one
   device.
3. Central grants the lease only if nobody else holds the device, and returns
   the Lab PC's address and a lease id.
4. The client connects straight to that Lab PC and sends the lease id with
   every call.
5. The Lab PC checks the lease with Central before touching the hardware.

Leases last 60 s and the client renews them automatically every 20 s. If a
client crashes, its lease expires and the device frees itself. Central also
expires a lease after 15 minutes with no device commands.

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
| [`examples/sacher_laser/`](examples/sacher_laser/) | Local calibration and leased Sacher laser examples |

## Quick start

Python 3.11 or newer is required. The following commands create one local
development environment containing all three roles.

```cmd
git clone <repository-url> lab_network
cd lab_network
py -3.11 -m venv .venv
call .\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".\Labnet[dev,central,lab-pc]"
```

### 1. Initialize Central

Central does not load `.env` automatically. If you choose a non-default
database location, set `LABNET_DATABASE_URL` with `set "..."` in the activated
Command Prompt before running both the CLI and Central; see the
[Central example](examples/central_server/README.md#use-an-explicit-database-location).

In the activated Command Prompt:

```cmd
labnet-central-admin db upgrade
labnet-central-admin user create central-admin --role admin
labnet-central-admin user create lab-pc-dev-01 --role device_manager
labnet-central-admin user create researcher --role user
```

Save the API keys printed by the commands. Each key is shown once. The
`device_manager` username must exactly match the Lab PC ID used below.

Start Central and leave it running:

```cmd
python -m examples.central_server
```

Verify it from another shell:

```cmd
curl.exe http://127.0.0.1:8008/health/ready
```

### 2. Start the example Lab PC

Copy the environment template:

```cmd
copy .\examples\lab_pc\.env.example .\examples\lab_pc\.env
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

```cmd
python -m examples.lab_pc
```

The checked-in inventory exposes one simulated `dummy_device` and can
optionally expose the Sacher laser when its Lab-PC environment flag is enabled.
Verify the Lab PC locally:

```cmd
curl.exe http://127.0.0.1:9001/health/ready
```

### 3. Run a client

In a third activated shell, use the researcher key:

```cmd
set "LABNET_API_KEY=<researcher API key>"
python .\examples\client_test\client.py
```

Or run the asyncio example:

```cmd
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

```cmd
cd Labnet
python tools/generate_protos.py --check
python -m pytest
```

Changes to protobuf contracts must include the regenerated Python artifacts.
Changes to a physical adapter should include tests that exercise its contract
and failure behavior. Keep credentials, runtime databases, and instrument data
out of commits. Commit vendor runtime binaries only when their redistribution
terms have been reviewed and the package explicitly requires them.

## Security

The local network uses plaintext only because everything is on loopback;
LabNet refuses plaintext Central or gRPC endpoints on any other address. Use
HTTPS for Central and TLS for every LAN-facing Lab PC.

Never commit API keys, private keys, `.env` files, site certificates, or
runtime databases. Rotate any credential that may have been exposed
(`labnet-central-admin --env-file central.env user rotate-key <user>`).

## License

LabNet is licensed under the [MIT License](LICENSE).
