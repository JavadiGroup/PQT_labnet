# Configuration reference

Every setting can be given in the settings file or the process environment.
The process environment wins, so a service manager can override any line.

## Central settings (`central.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_DATABASE_URL` | per-user SQLite file | SQLAlchemy database URL |
| `LABNET_CENTRAL_BIND_HOST` | `127.0.0.1` | HTTP bind; must be loopback |
| `LABNET_CENTRAL_BIND_PORT` | `8008` | HTTP port behind Caddy |
| `LABNET_LEASE_MIN_SECONDS` / `_MAX_SECONDS` | `5` / `86400` | Accepted lease durations |
| `LABNET_LEASE_DEFAULT_IDLE_TIMEOUT_SECONDS` | `900` | Lease ends after this long without a device command |
| `LABNET_HEARTBEAT_TIMEOUT_SECONDS` | `30` | A silent Lab PC is marked unhealthy after this |
| `LABNET_MAINTENANCE_INTERVAL_SECONDS` | `5` | How often expired leases and silent Lab PCs are swept |
| `LABNET_CREATE_SCHEMA_ON_STARTUP` | `false` | Test convenience only; use migrations instead |

## Lab-PC settings (`lab-pc.env`)

Two inputs decide how a Lab PC runs:

| You set | Effect |
| --- | --- |
| `LABNET_DEVICE_MANAGER_API_KEY` | Registers with Central and validates every lease. Without it the Lab PC is standalone: leases are not checked, so it is allowed only on loopback and never in `production`. |
| `LABNET_GRPC_CERT_FILE` + `LABNET_GRPC_KEY_FILE` | Serves gRPC over TLS and listens on all interfaces (`0.0.0.0`). Without them gRPC is plaintext and loopback-only. Setting only one is an error. |

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_ENVIRONMENT` | `development` | `production` additionally requires a key and an inventory |
| `LABNET_LAB_PC_ID` | the username that owns the key | Stable Central identity; looked up from Central at startup when omitted |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | Central URL; non-loopback must be `https://` |
| `LABNET_CA_FILE` | system trust | CA that verifies Central's HTTPS certificate |
| `LABNET_DEVICE_INVENTORY` | none: the Lab PC refuses to start | `package.module:function` or `C:/path/file.py:function` |
| `LABNET_GRPC_ADVERTISED_TARGET` | the bind address | `host:port` clients dial; required with TLS; must be in the certificate |
| `LABNET_GRPC_BIND_HOST` / `LABNET_GRPC_PORT` | see above / `50051` | gRPC listener |
| `LABNET_GRPC_TLS_SERVER_NAME` | none | Name clients verify, when it differs from the target host |
| `LABNET_LAB_PC_HOST` | host of the advertised target | Label reported to Central |
| `LABNET_HTTP_BIND_HOST` / `LABNET_HTTP_PORT` | `127.0.0.1` / `9001` | Health API; loopback only |

Relative paths in a settings file are relative to that file. Tuning settings
keep sensible defaults: `LABNET_HEARTBEAT_INTERVAL_SECONDS` (5),
`LABNET_DEVICE_QUEUE_CAPACITY` (128), `LABNET_DEVICE_START_TIMEOUT_SECONDS` (10),
`LABNET_DEFAULT_OPERATION_TIMEOUT_SECONDS` (30), `LABNET_STREAM_MAX_RATE_HZ`
(1000), `LABNET_STREAM_MAX_SAMPLES` (1000000), `LABNET_STREAM_MAX_DURATION_SECONDS`
(3600), `LABNET_STREAM_LEASE_REVALIDATION_SECONDS` (5),
`LABNET_GRPC_MAX_RECEIVE_BYTES` / `_SEND_BYTES` (4 MiB).

## Client settings (`client.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABNET_SERVER_URL` | `http://127.0.0.1:8008` | Central URL |
| `LABNET_API_KEY` | required | A `user` or `admin` key |
| `LABNET_CA_FILE` | system trust | Verifies Central and every Lab PC |
| `LABNET_ENV_FILE` | none | Process environment only: which file `from_env()` loads |

## Central database and API

Central stores users, API-key hashes, Lab PC and device registrations,
leases, idempotency records, and audit events. It never stores measurements.
SQLite suits one Central process; a database constraint enforces one active
lease per device. PostgreSQL uses the same models and migrations and suits
several workers or replicas.

Normal startup never creates or changes tables. Apply migrations explicitly
(`labnet update --role central` and `setup` both do):

```powershell
labnet-central-admin --env-file central.env db upgrade
```

| Task | Command (after `labnet-central-admin --env-file central.env`) |
| --- | --- |
| Migrate, create missing users, write settings files | `setup [--admin N] [--user N] [--lab-pc ID] [--write-env DIR --server-url URL [--ca-file PATH]]` |
| Show schema revision | `db current` |
| List users (never prints keys) | `user list` |
| Create one user and print its key | `user create NAME --role user\|admin\|device_manager` |
| Replace a key (the old one stops working at once) | `user rotate-key NAME` |

Important HTTP endpoints:

- `GET /health/live`, `GET /health/ready`
- `GET /api/v1/auth/me`
- `POST /api/v1/device-managers/register`, `POST /api/v1/device-managers/heartbeat`
- `GET /api/v1/devices`, `GET /api/v1/devices/{device_id}`
- `POST /api/v1/leases`, `POST /api/v1/leases/{lease_id}/renew`,
  `DELETE /api/v1/leases/{lease_id}`, `POST /api/v1/leases/{lease_id}/validate`

## Credential handling

Settings files, private keys, caches, build outputs, and runtime databases are
ignored by Git. Keep each settings file only on the computer that uses it,
restrict its permissions, and rotate any exposed API key or private key
immediately. The same goes for the Control Center's credentials:
- **Proxy secret:** delete the `LABNET_CONTROL_CENTER_PROXY_SECRET` line,
  run `setup_logins.ps1` again, and restart Caddy and the Control Center.
- **A login's password:** `setup_logins.ps1 -Reset <name>`, then restart Caddy.
- **A runner token:** `labnet-control-center-admin runner revoke <name>`,
  then `runner add <name>`.

## Network security

The local network uses plaintext only because everything is on loopback;
LabNet refuses plaintext Central or gRPC endpoints on any other address. Use
HTTPS for Central and TLS for every LAN-facing Lab PC.

In the lab, the Control Center answers only requests that come through
Caddy, where each person signs in with their own login; Caddy vouches for
them with a shared proxy secret. Automation runners use their own tokens
instead. Development on one computer has no logins. See
[Logins](../control-center/logins.md#logins).

Never commit API keys, private keys, `.env` files, site certificates, or
runtime databases. Rotate any credential that may have been exposed
(`labnet-central-admin --env-file central.env user rotate-key <user>`).
