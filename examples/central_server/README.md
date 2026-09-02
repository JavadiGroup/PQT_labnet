# Central Server example

This directory demonstrates how to configure and launch the reusable Central
application installed by LabNet. The HTTP API, authentication, lease services,
database models, administration CLI, and Alembic migrations live in
`labnet.central`; the example only creates the application from environment
settings and runs Uvicorn.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | Import `Settings` and `create_app` from `labnet.central`, then run the app |
| `__main__.py` | Support `python -m examples.central_server` |
| `.env.example` | Reference list of Central environment variables |

Central does not automatically load `.env` or `.env.example`. Those files are
optional reference files unless your own launcher explicitly loads them. Set
the required variables in the Command Prompt that starts **both** Central and
`labnet-central-admin`, or configure them in the service manager, container,
or secrets manager. The CLI and Central must receive the same
`LABNET_DATABASE_URL` or they will use different databases.

## Install

From the repository root:

```cmd
py -3.11 -m venv .venv
call .\.venv\Scripts\activate.bat
python -m pip install -e ".\Labnet[central]"
```

For development and tests, install `dev` as well:

```cmd
python -m pip install -e ".\Labnet[central,dev]"
```

## Local SQLite setup

SQLite is included with Python and is the simplest option for one Central
process. If `LABNET_DATABASE_URL` is omitted, LabNet uses a database in the
current user's application-data directory.

Apply the schema before starting Central:

```cmd
labnet-central-admin db upgrade
```

Create one identity for each responsibility:

```cmd
labnet-central-admin user create central-admin --role admin
labnet-central-admin user create lab-pc-dev-01 --role device_manager
labnet-central-admin user create researcher --role user
```

Each command prints the generated API key exactly once. Save it immediately.
Central stores only a SHA-256 hash. The `device_manager` username must exactly
match the associated Lab PC's `LABNET_LAB_PC_ID`.

Start the example from the repository root:

```cmd
python -m examples.central_server
```

The installed command provides the same application without this wrapper:

```cmd
labnet-central
```

Verify the service:

```cmd
curl.exe http://127.0.0.1:8008/health/live
curl.exe http://127.0.0.1:8008/health/ready
```

Both should report `status: ok`. Readiness remains unavailable until the
database has the expected migration revision.

## Use an explicit database location

Set the database URL in the same Command Prompt before running the migration,
user-management CLI, and Central. A `.env` file alone has no effect unless a
launcher loads it:

```cmd
set "LABNET_DATABASE_URL=sqlite:///C:/ProgramData/LabNet/Central/labnet.db"
labnet-central-admin db upgrade
labnet-central-admin user create lab-pc-dev-01 --role device_manager
python -m examples.central_server
```

The target directory must be writable by the Central service account. LabNet
creates the SQLite parent directory during explicit schema setup, not during
normal server startup.

## PostgreSQL

Install the PostgreSQL extra and use a SQLAlchemy URL:

```cmd
python -m pip install -e ".\Labnet[central,postgres]"
set "LABNET_DATABASE_URL=postgresql+psycopg://labnet:<password>@db-host/labnet"
labnet-central-admin db upgrade
python -m examples.central_server
```

Use a secrets manager or service environment for the database password. Do not
commit it to this example.

## Production networking

The built-in Central server intentionally accepts plaintext HTTP only on
loopback. For a multi-computer deployment:

1. keep `LABNET_CENTRAL_BIND_HOST=127.0.0.1`;
2. run an HTTPS reverse proxy on the same computer;
3. install a certificate whose SAN contains the static IP `192.168.50.10`;
4. allow TCP 443 only from authorized Lab PCs and clients; and
5. do not expose port 8008 to the LAN.

Lab PCs and clients must use the proxy's `https://` URL. When using a private
CA, configure `SSL_CERT_FILE` in their process environments.

The certificate and key are read by the reverse proxy, not by the Central
Python process. For the example deployment, copy these files to Central:

```text
C:\ProgramData\LabNet\certs\central.crt  public server certificate
C:\ProgramData\LabNet\certs\central.key  secret Central private key
```

Copy the public CA certificate—not either of the files above—to every Lab PC
and client that must trust Central:

```text
C:\ProgramData\LabNet\certs\labnet-ca.pem
```

The certificate's subject alternative name must contain the exact raw IP used
in `LABNET_SERVER_URL`. The private-CA creation, signing, verification,
file-distribution, and renewal procedure is in the main
[certificate guide](../../Labnet/README.md#generate-the-minimum-tls-certificate-set).

## Important settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_DATABASE_URL` | User application-data SQLite file | SQLAlchemy database URL |
| `LABNET_CENTRAL_BIND_HOST` | `127.0.0.1` | Loopback HTTP bind |
| `LABNET_CENTRAL_BIND_PORT` | `8008` | HTTP port behind the proxy |
| `LABNET_HEARTBEAT_TIMEOUT_SECONDS` | `30` | Manager health timeout |
| `LABNET_LEASE_MIN_SECONDS` | `5` | Minimum lease duration |
| `LABNET_LEASE_MAX_SECONDS` | `86400` | Maximum lease duration |
| `LABNET_LEASE_DEFAULT_IDLE_TIMEOUT_SECONDS` | `900` | Maximum time without an authorized device command |
| `LABNET_MAINTENANCE_INTERVAL_SECONDS` | `5` | Lease/manager maintenance interval |

## Administration

Check the current migration revision:

```cmd
labnet-central-admin db current
```

Rotate a user's API key:

```cmd
labnet-central-admin user rotate-key researcher
```

The old key stops authenticating after rotation. Update the client secret
before its next connection.

## Next step

After Central is ready, configure the [Lab-PC example](../lab_pc/README.md),
then run a [client example](../client_test/README.md).
