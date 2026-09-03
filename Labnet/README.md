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
- A minimal TLS model with one private CA and server certificates only.
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

The Windows examples use Command Prompt (`cmd.exe`) commands. Equivalent Python
and pip commands work on Linux and macOS after activating the appropriate
virtual environment.

## Installation options

Run package commands from this `Labnet/` directory.

### Development installation

```cmd
py -3.11 -m venv ..\.venv
call ..\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev,central,lab-pc]"
```

An editable install is useful during development because imports resolve to the
working source tree. Do not use editable installs on managed laboratory hosts.

### Role-specific installations

```cmd
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

```cmd
py -3.11 -m venv .venv
call .\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".\Labnet[dev,central,lab-pc]"
```

Activate this environment in every new terminal used below.

### 2. Initialize Central

Central reads its process environment directly; it does not automatically load
a `.env` file. The file is optional unless your own launcher loads it. Set
`LABNET_DATABASE_URL` in the Command Prompt that runs **both**
`labnet-central-admin` and Central. If it is not set, each process uses a
SQLite database under its current user's application-data directory.

```cmd
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

```cmd
python -m examples.central_server
```

Verify both health states from another terminal:

```cmd
curl.exe http://127.0.0.1:8008/health/live
curl.exe http://127.0.0.1:8008/health/ready
```

Both responses should report `status: ok`.

### 3. Configure the Lab PC

```cmd
copy .\examples\lab_pc\.env.example .\examples\lab_pc\.env
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
`PhysicalDummyDevice` and has an opt-in `PhysicalSacherLaser` entry. Start the
Lab PC:

```cmd
python -m examples.lab_pc
```

Verify readiness:

```cmd
curl.exe http://127.0.0.1:9001/health/ready
```

The response should show a serving gRPC endpoint, successful Central
registration, and at least one available device.

### 4. Run a synchronous client

```cmd
set "LABNET_SERVER_URL=http://127.0.0.1:8008"
set "LABNET_API_KEY=<researcher API key>"
python .\examples\client_test\client.py
```

### 5. Run an asyncio client

```cmd
python .\examples\client_test\async_client.py
```

The asyncio example performs discovery, acquisition, a write, a read, and a
short streamed acquisition. Both examples release the lease and close network
transports in `finally` blocks.

## Production deployment

Production hosts should install a reviewed wheel, not execute from a writable
clone. The examples are deliberately excluded from the wheel because Lab-PC
inventories and client programs are deployment-specific.

This walkthrough uses four Windows computers on one private LAN. All addresses
are static and all connections use raw IP addresses; no DNS service is assumed.

| Step | Computer | Static IP | Required inbound traffic |
| --- | --- | --- | --- |
| 1 | `development_pc` | `192.168.50.40` | None for LabNet; only the chosen file-transfer method |
| 2 | `central_server_pc` | `192.168.50.10` | TCP 443 from `lab_pc_1` and clients |
| 3 | `lab_pc_1` | `192.168.50.20` | TCP 50051 from approved clients |
| 4 | `client` | `192.168.50.30` | None for LabNet |

Use the same subnet mask and gateway required by the laboratory LAN. Confirm
that each address is excluded from DHCP or reserved for that computer. The
certificate SANs below contain the raw server IP addresses, so changing either
server IP requires a replacement certificate.

### 1. Set up `development_pc`

`development_pc` is the only computer that needs a repository clone, developer
dependencies, protobuf generation tools, or OpenSSL. Clone the repository and
create a development environment:

```cmd
git clone <repository-url> lab_network
cd /d .\lab_network
py -3.11 -m venv .venv
call .\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".\Labnet[dev,central,lab-pc]"
```

Run the release checks from the installable project directory:

```cmd
cd /d .\Labnet
python tools/generate_protos.py --check
python -m pytest
```

If a proto changed, generate first and then rerun the checks:

```cmd
python tools/generate_protos.py
python tools/generate_protos.py --check
python -m pytest
```

Update `version` in `pyproject.toml` and commit the reviewed release. Confirm
that `git status --short` is empty; the application ZIP commands below archive
`HEAD`, so building from uncommitted files could produce mismatched artifacts.
Then build one wheel:

```cmd
git status --short
python -m pip wheel --no-deps . --wheel-dir dist
certutil -hashfile .\dist\labnet-0.2.0-py3-none-any.whl SHA256
```

Record the Git revision, package version, wheel filename, and SHA-256 hash.
The later steps copy this exact wheel to the three runtime computers.

#### What the wheel is

A wheel is the installable release artifact produced from this repository. It
is a ZIP-format archive whose filename ends in `.whl`; it is not a running
server and should not be unpacked by hand. For example:

```text
labnet-0.2.0-py3-none-any.whl
```

That wheel contains the `labnet` Python package, including Central, database
migrations, physical-device adapters, protobuf definitions, and generated
protobuf Python modules. It does **not** contain:

- the site-specific programs under the repository's `examples/` directory;
- API keys, TLS certificates, `.env` files, or other configuration;
- an existing Central database; or
- LabNet's third-party Python dependencies.

Copying the wheel only transfers one file. Installing it with `pip` verifies
that it is a valid package, copies its contents into the selected virtual
environment, installs its declared dependencies, and creates commands such as
`labnet-central.exe`. Never copy the wheel directly into `site-packages` or
unzip it into the Python installation.

The extras in `[central]` and `[lab-pc]` select additional dependencies from
the same wheel. They are not different editions of the wheel:

```text
labnet-0.2.0-py3-none-any.whl            base client library
labnet-0.2.0-py3-none-any.whl[central]   same library plus Central dependencies
labnet-0.2.0-py3-none-any.whl[lab-pc]    same library plus Lab-PC dependencies
```

#### Stage the release artifacts

Create a versioned release directory on `development_pc` and put the reviewed
wheel in it:

```cmd
mkdir C:\LabNet-Releases\0.2.0
copy .\dist\labnet-0.2.0-py3-none-any.whl C:\LabNet-Releases\0.2.0\
```

The wheel deliberately excludes site compositions and examples. Create clean
archives directly from tracked Git files so an ignored development `.env` or
runtime database cannot be copied accidentally:

```cmd
git -C .. archive --format=zip ^
  --output=C:\LabNet-Releases\0.2.0\lab-pc-app.zip ^
  HEAD examples/__init__.py examples/lab_pc
git -C .. archive --format=zip ^
  --output=C:\LabNet-Releases\0.2.0\sync-client-example.zip ^
  HEAD examples/__init__.py examples/client_test
```

Each runtime step will copy from this release directory into its local
`C:\LabNet-Staging\0.2.0` directory. Use an approved transfer mechanism, such
as:

- an authenticated Windows file share;
- `scp` over SSH;
- an organization-managed software deployment system; or
- removable media when the instrument network is isolated.

The commands in steps 2-4 assume the read-only Windows share shown below. If
the lab uses `scp` or removable media instead, change only the source path; the
destination paths and hash checks stay the same.

For example, if `C:\LabNet-Releases` is exposed as a restricted, read-only
Windows share on `development_pc`, a destination can use the raw IP address:

```cmd
copy \\192.168.50.40\LabNet-Releases\0.2.0\labnet-0.2.0-py3-none-any.whl C:\LabNet-Staging\0.2.0\
```

The destination-specific steps repeat this transfer and calculate the hash of
the received file:

```cmd
certutil -hashfile C:\LabNet-Staging\0.2.0\labnet-0.2.0-py3-none-any.whl SHA256
```

Compare the full hash with the value recorded on the build computer. A
difference means that the file is not the reviewed artifact; do not install it.
The hash detects a wrong or damaged file, but it is not a substitute for
controlling who can write to the release share.

On a normal internet-connected computer, `pip` obtains any missing third-party
dependencies from the configured package index. Merely copying the LabNet
wheel does not copy those dependencies. For an offline instrument network,
prepare a wheelhouse on a connected build computer:

```cmd
mkdir .\dist\wheelhouse
python -m pip download --dest .\dist\wheelhouse ^
  ".\dist\labnet-0.2.0-py3-none-any.whl[central,lab-pc]"
```

Copy the entire `wheelhouse` directory to the isolated computer and install
without contacting an external package index:

```cmd
python -m pip install --no-index ^
  --find-links C:\LabNet-Staging\0.2.0\wheelhouse ^
  "C:\LabNet-Staging\0.2.0\wheelhouse\labnet-0.2.0-py3-none-any.whl[lab-pc]"
```

Build the wheelhouse on the same operating system, CPU architecture, and Python
version used by the destination computers, because some dependency wheels are
platform-specific. Keep the reviewed wheel and its hash after deployment; they
identify exactly what was installed and make a controlled rollback possible.

The two ZIP archives remain separate from the wheel because Lab-PC inventories
and experiment programs are deployment-specific.

#### Generate the minimum TLS certificate set

For this deployment, the complete minimum is one CA certificate, one Central
server certificate, and one Lab-PC server certificate. The client receives
only a copy of the public CA certificate. There are no client certificates and
no client private keys.

TLS provides two things: encryption while data crosses the network and proof
that the computer reached by a client is the intended server. gRPC uses the
same certificate model as HTTPS: the client verifies the server and the
connection is encrypted. See the
[gRPC authentication guide](https://grpc.io/docs/guides/auth/) for the
underlying model.

TLS identity and LabNet authorization are separate layers. A valid server
certificate does not grant a lease. The certificate secures and authenticates
the connection; LabNet's API keys, roles, and leases authenticate callers and
decide what they may do. Clients therefore do not need certificates or private
keys.

#### Certificate vocabulary

- A **private key** proves identity. It must remain secret and is used by the
  process that acts as that identity.
- A **certificate** is a public document that binds a public key to an identity,
  validity period, and one or more network names or IP addresses. This guide
  uses IP addresses only.
- A **certificate authority (CA)** signs certificates. Its private key issues
  identities; its public CA certificate is the trust anchor copied to machines
  that must verify those identities.
- A **certificate signing request (CSR)** contains a public key and requested
  identity. The CA signs it to create a certificate. The CSR is not secret.
- The **subject alternative name (SAN)** lists the addresses for which a server
  certificate is valid. Here the SAN is the server's static IP address. Modern
  clients check the SAN, not merely the certificate's common name.

The trust relationship is therefore:

```text
CA private key signs server certificate
              |
              +--> Central certificate  <-- trusted by Lab PCs and clients
              |
              +--> Lab-PC certificate   <-- trusted by clients
```

One private CA issues both kinds of server certificate. An organization with
an existing PKI can ask its administrator to issue them instead. The manual
OpenSSL process below is a small-laboratory bootstrap procedure; it does not
provide automatic renewal or revocation.

In this walkthrough, `development_pc` is also the signing computer. After
deployment, protect the CA files in encrypted offline storage and restore them
to `development_pc` only when issuing or renewing a server certificate.

#### Which files go on each computer

| Computer | Files retained or installed there | Why |
| --- | --- | --- |
| `development_pc` / protected CA storage | `labnet-ca.key`, `labnet-ca.pem`, CA serial and issue records | Issues certificates; the CA key never goes to a runtime computer |
| Central | `central.crt`, `central.key` | The HTTPS reverse proxy presents Central's identity |
| Lab PC | `lab-pc.crt`, `lab-pc.key`, `labnet-ca.pem` | The gRPC server presents its identity; the Lab-PC client trusts Central |
| Client | `labnet-ca.pem` | Trusts Central HTTPS and Lab-PC gRPC certificates |

`labnet-ca.pem`, `.crt` files, and `.csr` files contain public information and
may be copied. Files ending in `.key` contain private keys. Only the identity
that uses a key should receive it. In particular, **never copy
`labnet-ca.key` to Central, a Lab PC, a client, a release share, or the source
repository**.

The OpenSSL commands below create PEM-encoded text files. A certificate usually
starts with `-----BEGIN CERTIFICATE-----`; a private key starts with
`-----BEGIN PRIVATE KEY-----` or `-----BEGIN ENCRYPTED PRIVATE KEY-----`. The
filename suffix helps humans understand the file's purpose, but does not itself
provide security.

Central itself listens on loopback HTTP. The reverse proxy reads `central.crt`
and `central.key`, terminates HTTPS, and forwards the already decrypted request
to `127.0.0.1:8008`. The Lab-PC Python process terminates gRPC TLS directly, so
it reads `lab-pc.crt` and `lab-pc.key` itself.

#### Create a private CA

The following example uses OpenSSL 3.x on `development_pc`. It generates the CA
and both server identities in one place, so OpenSSL does not need to be
installed on the other computers.

OpenSSL is a separate command-line program and is not included with every
Windows installation. Install an organization-approved OpenSSL 3.x build on
`development_pc` and confirm that Windows Command Prompt can find it:

```cmd
openssl version
```

The output should begin with `OpenSSL 3.`. LabNet, Caddy, and gRPC do not need
the `openssl` command after certificate setup.

Create a working directory and an encrypted 4096-bit CA private key:

```cmd
if not exist "C:\LabNet-CA" mkdir "C:\LabNet-CA"
cd /d "C:\LabNet-CA"
openssl genpkey -algorithm RSA -aes-256-cbc -pkeyopt rsa_keygen_bits:4096 -out labnet-ca.key
```

OpenSSL prompts for a passphrase. Store it in the laboratory's approved secret
manager; losing it makes the CA unusable, while exposing it together with the
key lets an attacker issue trusted certificates.

Create a ten-year self-signed public CA certificate:

```cmd
openssl req -x509 -new -sha256 -days 3650 -key labnet-ca.key -out labnet-ca.pem -subj "/CN=LabNet Laboratory Root CA" -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" -addext "subjectKeyIdentifier=hash"
```

The `CA:TRUE` and certificate-signing key usage make this a CA certificate.
OpenSSL documents these fields in its
[X.509 extension configuration guide](https://docs.openssl.org/3.0/man5/x509v3_config/).
The long CA lifetime does not mean server certificates should be long-lived.

#### Issue the Central server certificate

Continue in `C:\LabNet-CA` on the administrative computer. Create Central's
private key and CSR:

```cmd
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out central.key
openssl req -new -key central.key -out central.csr -subj "/CN=192.168.50.10"
```

The server key is intentionally unencrypted so a restricted service account
can start the reverse proxy without an interactive passphrase. Its operating
system file permissions are therefore essential.

Create `central-ext.cnf` in `C:\LabNet-CA`:

```cmd
(
  echo basicConstraints=critical,CA:FALSE
  echo keyUsage=critical,digitalSignature,keyEncipherment
  echo extendedKeyUsage=serverAuth
  echo subjectAltName=IP:192.168.50.10
  echo subjectKeyIdentifier=hash
  echo authorityKeyIdentifier=keyid,issuer
) > central-ext.cnf
type central-ext.cnf
```

Sign a one-year certificate:

```cmd
openssl x509 -req -in central.csr -CA labnet-ca.pem -CAkey labnet-ca.key -CAcreateserial -out central.crt -days 365 -sha256 -extfile central-ext.cnf
```

`-CAcreateserial` creates `labnet-ca.srl`, which tracks the last certificate
serial number. Preserve it with the CA records.

#### Issue a Lab-PC server certificate

Still in `C:\LabNet-CA`, create a separate key and CSR for the Lab PC. Never
reuse Central's key:

```cmd
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out lab-pc-01.key
openssl req -new -key lab-pc-01.key -out lab-pc-01.csr -subj "/CN=192.168.50.20"
```

Create `lab-pc-01-ext.cnf` in the same directory:

```cmd
(
  echo basicConstraints=critical,CA:FALSE
  echo keyUsage=critical,digitalSignature,keyEncipherment
  echo extendedKeyUsage=serverAuth
  echo subjectAltName=IP:192.168.50.20
  echo subjectKeyIdentifier=hash
  echo authorityKeyIdentifier=keyid,issuer
) > lab-pc-01-ext.cnf
type lab-pc-01-ext.cnf
```

Then sign it:

```cmd
openssl x509 -req -in lab-pc-01.csr -CA labnet-ca.pem -CAkey labnet-ca.key -CAserial labnet-ca.srl -out lab-pc-01.crt -days 365 -sha256 -extfile lab-pc-01-ext.cnf
```

The IP SAN must match `LABNET_GRPC_ADVERTISED_TARGET`. In this walkthrough that
target is `192.168.50.20:50051`, so the certificate contains
`IP:192.168.50.20`.

#### Verify and stage certificates

Before copying anything, verify the signatures, names, purposes, and dates:

```cmd
openssl verify -CAfile labnet-ca.pem -purpose sslserver central.crt lab-pc-01.crt
openssl x509 -in central.crt -noout -subject -issuer -dates -ext subjectAltName,extendedKeyUsage
openssl x509 -in lab-pc-01.crt -noout -subject -issuer -dates -ext subjectAltName,extendedKeyUsage
```

The destination layout will be:

```text
Central C:\ProgramData\LabNet\certs\central.crt
Central C:\ProgramData\LabNet\certs\central.key

Lab PC C:\ProgramData\LabNet\certs\lab-pc.crt
Lab PC C:\ProgramData\LabNet\certs\lab-pc.key
Lab PC C:\ProgramData\LabNet\certs\labnet-ca.pem

Client C:\ProgramData\LabNet\certs\labnet-ca.pem
```

Stage the required files on encrypted removable media or another approved
transfer location. For example, if the media is mounted as `E:`:

```cmd
if not exist "E:\LabNet-TLS" mkdir "E:\LabNet-TLS"
copy /Y central.crt "E:\LabNet-TLS\central.crt"
copy /Y central.key "E:\LabNet-TLS\central.key"
copy /Y lab-pc-01.crt "E:\LabNet-TLS\lab-pc-01.crt"
copy /Y lab-pc-01.key "E:\LabNet-TLS\lab-pc-01.key"
copy /Y labnet-ca.pem "E:\LabNet-TLS\labnet-ca.pem"
```

For another Lab PC, repeat only the Lab-PC key, CSR, and signing block with its
own name and SANs. Clients still receive the same single `labnet-ca.pem` file.

This transfer includes two server private keys. After verifying the deployment,
remove `central.key` and `lab-pc-01.key` from the transfer location. Never put
`labnet-ca.key` there; keep it only in protected CA storage.

Restrict each deployed private key so that only administrators and the service
account running that identity can read it. The public CA certificate may be
broadly readable. On Windows, providing the CA explicitly avoids depending on
the machine's system trust store.

On each Windows host, open the private key's **Properties > Security >
Advanced** dialog. Disable inherited permissions, remove broad groups such as
`Users`, and retain only administrators plus the exact Windows account that
runs Caddy or the Lab PC. Grant the service account read access, not write
access. Perform this carefully while signed in as an administrator so the
service account is not accidentally locked out.

Copying a file does not activate it. The Caddy `tls` directive below tells the
proxy which Central certificate and key to present. The Lab-PC environment
variables tell gRPC which Lab-PC certificate and key to present. On clients,
`SSL_CERT_FILE` and `GrpcTlsConfig` tell the HTTPS and gRPC libraries which CA
to trust. This explicit configuration does not install the private CA into the
Windows system-wide trust store.

#### Renewal and incident handling

Record each server certificate's owner, SANs, serial number, issue date, and
expiry date. Renew it before expiry by generating a new private key and CSR,
signing a replacement, deploying it, and restarting the process that terminates
TLS. The CA certificate remains unchanged during an ordinary renewal.

If a server private key may have been copied or exposed, stop trusting that
identity and issue a replacement key and certificate. The simple commands
above do not implement certificate revocation. If immediate revocation,
automated renewal, or many identities are required, use the organization's PKI
or a managed private-CA system rather than operating this minimal manual CA.
If the CA private key is exposed, create a new CA and distribute its public
certificate to every relying computer before replacing all issued certificates.

### 2. Set up `central_server_pc`

Configure the Windows network adapter with static IP `192.168.50.10`, then
confirm that it can reach `development_pc`:

```cmd
powershell.exe -NoProfile -Command "Test-NetConnection 192.168.50.40 -Port 445"
```

Copy the wheel into a local staging directory and compare its SHA-256 hash with
the value recorded on `development_pc`:

```cmd
mkdir C:\LabNet-Staging\0.2.0
copy \\192.168.50.40\LabNet-Releases\0.2.0\labnet-0.2.0-py3-none-any.whl C:\LabNet-Staging\0.2.0\
certutil -hashfile C:\LabNet-Staging\0.2.0\labnet-0.2.0-py3-none-any.whl SHA256
```

Copy only Central's certificate and private key from the approved TLS transfer
location. This example assumes the encrypted media is mounted as `E:`:

```cmd
mkdir C:\ProgramData\LabNet\certs
copy E:\LabNet-TLS\central.crt C:\ProgramData\LabNet\certs\central.crt
copy E:\LabNet-TLS\central.key C:\ProgramData\LabNet\certs\central.key
```

Create and activate a dedicated environment, then install the wheel:

```cmd
py -3.11 -m venv C:\ProgramData\LabNet\central-venv
C:\ProgramData\LabNet\central-venv\Scripts\python.exe -m pip install ^
  "C:\LabNet-Staging\0.2.0\labnet-0.2.0-py3-none-any.whl[central]"
```

Set the Central service environment in this Command Prompt. A `.env` file is
not required and is not read automatically; the same variables must be present
when running the CLI below and when starting Central:

```cmd
set "LABNET_DATABASE_URL=sqlite:///C:/ProgramData/LabNet/Central/labnet.db"
set "LABNET_CENTRAL_BIND_HOST=127.0.0.1"
set "LABNET_CENTRAL_BIND_PORT=8008"
```

Apply migrations and create principals using the installed environment:

```cmd
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe db upgrade
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe user create ^
  lab-pc-01 --role device_manager
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe user create ^
  researcher --role user
```

Each creation command prints its API key once. Store both keys securely:

- the `lab-pc-01` key goes only into `lab_pc_1` configuration;
- the `researcher` key is used by the client in step 5.

Run Central on loopback and leave this Command Prompt open:

```cmd
C:\ProgramData\LabNet\central-venv\Scripts\labnet-central.exe
```

At this point Central is reachable only from `central_server_pc` at
`http://127.0.0.1:8008`. In a second Command Prompt, confirm the local application
before configuring the network-facing proxy:

```cmd
curl.exe http://127.0.0.1:8008/health/ready
```

Place an HTTPS reverse proxy on the same host. Its responsibilities are:

1. listen on `192.168.50.10:443`;
2. present the Central server certificate;
3. accept only modern TLS connections;
4. proxy requests to `http://127.0.0.1:8008`; and
5. preserve request methods, headers, paths, and status codes.

Install Caddy on `central_server_pc` and save the following as
`C:\ProgramData\LabNet\Caddyfile`:

```caddyfile
https://192.168.50.10 {
    tls C:/ProgramData/LabNet/certs/central.crt C:/ProgramData/LabNet/certs/central.key
    reverse_proxy 127.0.0.1:8008
}
```

Here Caddy's [`tls` directive](https://caddyserver.com/docs/caddyfile/directives/tls)
loads the public certificate and matching private key, while
[`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
forwards requests to Central's loopback HTTP listener.

Validate and run the proxy from another administrative Command Prompt during the
initial test:

```cmd
caddy validate --config C:\ProgramData\LabNet\Caddyfile
caddy run --config C:\ProgramData\LabNet\Caddyfile
```

Run the proxy and Central under restricted service accounts and configure them
to start automatically after networking is available. Allow inbound TCP 443
from `192.168.50.20` and approved client IPs. Do not create an inbound rule for
port 8008; Central must remain bound to loopback.

### 3. Set up `lab_pc_1`

Configure the Windows network adapter with static IP `192.168.50.20`. Confirm
that both the development and Central computers are reachable:

```cmd
powershell.exe -NoProfile -Command "Test-NetConnection 192.168.50.40 -Port 445"
powershell.exe -NoProfile -Command "Test-NetConnection 192.168.50.10 -Port 443"
```

Copy the wheel and reviewed Lab-PC composition, then verify the wheel hash:

```cmd
mkdir C:\LabNet-Staging\0.2.0
copy \\192.168.50.40\LabNet-Releases\0.2.0\labnet-0.2.0-py3-none-any.whl C:\LabNet-Staging\0.2.0\
copy \\192.168.50.40\LabNet-Releases\0.2.0\lab-pc-app.zip C:\LabNet-Staging\0.2.0\
certutil -hashfile C:\LabNet-Staging\0.2.0\labnet-0.2.0-py3-none-any.whl SHA256
```

Copy the Lab-PC server identity and the public CA certificate from the approved
TLS transfer location:

```cmd
mkdir C:\ProgramData\LabNet\certs
copy E:\LabNet-TLS\lab-pc-01.crt C:\ProgramData\LabNet\certs\lab-pc.crt
copy E:\LabNet-TLS\lab-pc-01.key C:\ProgramData\LabNet\certs\lab-pc.key
copy E:\LabNet-TLS\labnet-ca.pem C:\ProgramData\LabNet\certs\labnet-ca.pem
```

Install the wheel with Lab-PC dependencies:

```cmd
py -3.11 -m venv C:\ProgramData\LabNet\lab-pc-venv
C:\ProgramData\LabNet\lab-pc-venv\Scripts\python.exe -m pip install ^
  "C:\LabNet-Staging\0.2.0\labnet-0.2.0-py3-none-any.whl[lab-pc]"
```

Extract the reviewed composition into its application directory:

```cmd
mkdir C:\ProgramData\LabNet\app
tar -xf C:\LabNet-Staging\0.2.0\lab-pc-app.zip -C C:\ProgramData\LabNet\app
```

Install the vendor drivers required by the physical instruments and test each
instrument locally before starting LabNet. Edit
`C:\ProgramData\LabNet\app\examples\lab_pc\device_inventory.py` so it contains
only the instruments attached to this computer.

Create
`C:\ProgramData\LabNet\app\examples\lab_pc\.env` with the following contents.
Replace the API-key placeholder with the `lab-pc-01` key printed by the Central
administration command:

```dotenv
LABNET_ENVIRONMENT=production
LABNET_LAB_PC_ID=lab-pc-01
LABNET_LAB_PC_HOST=192.168.50.20
LABNET_CENTRAL_ENABLED=true
LABNET_SERVER_URL=https://192.168.50.10
LABNET_DEVICE_MANAGER_API_KEY=<lab-pc-01 API key>
LABNET_LEASE_VALIDATION_MODE=central
SSL_CERT_FILE=C:/ProgramData/LabNet/certs/labnet-ca.pem

LABNET_GRPC_BIND_HOST=0.0.0.0
LABNET_GRPC_PORT=50051
LABNET_GRPC_ADVERTISED_TARGET=192.168.50.20:50051
LABNET_GRPC_TLS_MODE=tls
LABNET_GRPC_CERT_FILE=C:/ProgramData/LabNet/certs/lab-pc.crt
LABNET_GRPC_KEY_FILE=C:/ProgramData/LabNet/certs/lab-pc.key

LABNET_HTTP_BIND_HOST=127.0.0.1
LABNET_HTTP_PORT=9001
```

Start from the application parent:

```cmd
cd /d C:\ProgramData\LabNet\app
C:\ProgramData\LabNet\lab-pc-venv\Scripts\python.exe -m examples.lab_pc
```

Leave the process running. In a second Command Prompt on `lab_pc_1`, check its
loopback-only readiness endpoint:

```cmd
curl.exe http://127.0.0.1:9001/health/ready
```

Allow inbound TCP 50051 from approved client IPs. Do not expose port 9001;
that health endpoint remains available only at `127.0.0.1:9001` on
`lab_pc_1`.

### 4. Set up `client`

Configure the Windows network adapter with static IP `192.168.50.30`, then
confirm that the release share, Central, and `lab_pc_1` are reachable:

```cmd
powershell.exe -NoProfile -Command "Test-NetConnection 192.168.50.40 -Port 445"
powershell.exe -NoProfile -Command "Test-NetConnection 192.168.50.10 -Port 443"
powershell.exe -NoProfile -Command "Test-NetConnection 192.168.50.20 -Port 50051"
```

Copy the wheel and synchronous client example, then verify the wheel hash:

```cmd
mkdir C:\LabNet-Staging\0.2.0
copy \\192.168.50.40\LabNet-Releases\0.2.0\labnet-0.2.0-py3-none-any.whl C:\LabNet-Staging\0.2.0\
copy \\192.168.50.40\LabNet-Releases\0.2.0\sync-client-example.zip C:\LabNet-Staging\0.2.0\
certutil -hashfile C:\LabNet-Staging\0.2.0\labnet-0.2.0-py3-none-any.whl SHA256
```

The client receives only the public CA certificate:

```cmd
mkdir C:\ProgramData\LabNet\certs
copy E:\LabNet-TLS\labnet-ca.pem C:\ProgramData\LabNet\certs\labnet-ca.pem
```

Before installing the client, confirm that Central's HTTPS proxy is reachable
and that its certificate validates against the copied CA:

```cmd
curl.exe --cacert C:\ProgramData\LabNet\certs\labnet-ca.pem ^
  https://192.168.50.10/health/ready
```

Install the base wheel:

```cmd
py -3.11 -m venv C:\ProgramData\LabNet\client-venv
C:\ProgramData\LabNet\client-venv\Scripts\python.exe -m pip install ^
  C:\LabNet-Staging\0.2.0\labnet-0.2.0-py3-none-any.whl
```

Extract the client example:

```cmd
mkdir C:\ProgramData\LabNet\client-app
tar -xf C:\LabNet-Staging\0.2.0\sync-client-example.zip -C C:\ProgramData\LabNet\client-app
```

No inbound firewall rule is required on the client.

### 5. Run the synchronous client example

Obtain the `researcher` API key created on `central_server_pc`. In the client
Command Prompt, configure the Central URL and the public CA file used by
both HTTPS and gRPC:

```cmd
set "LABNET_SERVER_URL=https://192.168.50.10"
set "LABNET_API_KEY=<researcher API key>"
set "SSL_CERT_FILE=C:\ProgramData\LabNet\certs\labnet-ca.pem"
set "LABNET_GRPC_CA_FILE=C:\ProgramData\LabNet\certs\labnet-ca.pem"
```

Run the packaged example:

```cmd
cd /d C:\ProgramData\LabNet\client-app
C:\ProgramData\LabNet\client-venv\Scripts\python.exe ^
  .\examples\client_test\client.py
```

The example authenticates with Central at `192.168.50.10`, discovers a device,
acquires its lease, and then connects directly to `lab_pc_1` at
`192.168.50.20:50051`.

The equivalent synchronous application pattern is:

```python
from labnet import DummyDevice, GrpcTlsConfig, LabClient

central = LabClient("https://192.168.50.10", "<researcher API key>")
tls = GrpcTlsConfig.from_files(
    root_certificates=r"C:\ProgramData\LabNet\certs\labnet-ca.pem",
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

### Network rules summary

Restrict access by role:

- Central: allow TCP 443 from approved Lab PCs and client networks.
- Lab PC: allow TCP 50051 from approved client networks.
- Do not expose Central port 8008.
- Do not expose Lab-PC port 9001.
- Clients need no inbound LabNet rule.

Limit outbound traffic where practical: Lab PCs need Central HTTPS access;
clients need Central HTTPS and the gRPC ports of authorized Lab PCs.

### Deployment verification

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

```cmd
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
| `LABNET_GRPC_TLS_MODE` | `disabled` for loopback development or `tls` for a LAN deployment |
| `LABNET_GRPC_CERT_FILE` | Server certificate chain |
| `LABNET_GRPC_KEY_FILE` | Server private key |
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
| `RPC_PATTERN_UNARY_COMMAND` | Unary | Forward typed arguments and return a lossless scalar, string, integer sequence, or no result |
| `RPC_PATTERN_POLLED_SCALAR_STREAM` | Server streaming | Repeatedly call one physical read with scheduling, cancellation, lease revalidation, and backpressure |

#### 2. Generate Python artifacts

From `Labnet/`:

```cmd
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

```cmd
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

```cmd
python tools/generate_protos.py --check
python -m pytest
```

The suite covers Central storage and leases, client renewal, generated
contracts, device workers, TLS policy, gRPC status translation, all example
imports, and a complete Central-to-Lab-PC-to-client stack.

To deliberately regenerate after editing a proto:

```cmd
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
| Lab PC rejects startup on a LAN address | Plaintext gRPC or development lease bypass | Configure `tls` and `LABNET_LEASE_VALIDATION_MODE=central` |
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
