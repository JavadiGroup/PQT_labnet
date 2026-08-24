# LabNet Central Server

The Central Server is the HTTP management plane for authentication, device
discovery, and exclusive leases. Measurement samples travel directly between
clients and Lab PCs over gRPC; they are never written to this database.

## Local SQLite setup

From the repository root, install the Central dependencies and create the
schema entirely through Python:

```powershell
python -m pip install -e ".[central]"
python -m central_server.manage db upgrade
python -m central_server.manage user create central-admin --role admin
python -m central_server.manage user create lab-pc-01 --role device_manager
python -m central_server.app.main
```

Central reads the process environment directly; `.env.example` is a reference,
not an automatically loaded file. In PowerShell, set an override before a
command with, for example, `$env:LABNET_DATABASE_URL = "sqlite:///D:/labnet/central.db"`.

The last two commands display a newly generated API key exactly once. Store it
in the appropriate process environment; the database stores only its SHA-256
hash. By default the server listens at `http://127.0.0.1:8008`. Its SQLite file
lives in the current user's writable application-data directory (for example,
`%LOCALAPPDATA%\LabNet\Central\labnet.db` on Windows), so an installed wheel
never attempts to write inside `site-packages`. Set `LABNET_DATABASE_URL` when
you want an explicit path.

Normal startup never creates or changes database tables. Run `db upgrade`
after installing a version that includes a schema migration. `/health/ready`
returns 503 until the expected Alembic revision is installed.

The built-in Uvicorn launcher refuses non-loopback bind addresses because it
serves plaintext HTTP. For remote access, run an HTTPS reverse proxy on the
same host, leave Central bound to loopback, and give Lab PCs and clients the
proxy's `https://` URL. Central does not expose WebSocket routes and does not
proxy gRPC measurement data.

## Legacy user import

The existing hashed JSON users can be imported without revealing their API
keys:

```powershell
python -m central_server.manage import-json-users --data-dir central_server/data
```

Legacy WebSocket device endpoints and leases are deliberately not imported.
Lab PCs register fresh gRPC endpoint descriptors after startup. The old JSON
files are not read by the Central runtime and should be archived after the
import is verified.

## PostgreSQL

The models and migrations are PostgreSQL-compatible. Install a PostgreSQL
driver (for example `psycopg[binary]`) and set `LABNET_DATABASE_URL` to a
`postgresql+psycopg://...` URL before running the same Alembic upgrade. SQLite
is recommended for a single Central process; PostgreSQL is the path for
multiple processes or replicas.

## Version 1 API

- `POST /api/v1/device-managers/register`
- `POST /api/v1/device-managers/heartbeat`
- `GET /api/v1/devices`
- `GET /api/v1/devices/{device_id}`
- `POST /api/v1/leases` (`Idempotency-Key` required)
- `POST /api/v1/leases/{lease_id}/renew`
- `DELETE /api/v1/leases/{lease_id}`
- `POST /api/v1/leases/{lease_id}/validate`
- `GET /api/v1/auth/me`
- `GET /health/live` and `GET /health/ready`

One active lease per device is enforced by a database constraint, not a Python
lock. Released, expired, and revoked leases remain in the database for audit.

## Renewable lease lifecycle

Lease creation defaults to a 60-second lifetime when `duration_seconds` is
omitted. A healthy client renews that short lifetime periodically; each renewal
advances `expires_at` and `last_renewed_at`, but deliberately does not count as
a device command. Normal client shutdown still sends `DELETE` for immediate
release. If the process, computer, or network fails, renewals stop and Central
expires the lease automatically.

Central independently enforces a 900-second command-idle policy, configured by
`LABNET_LEASE_DEFAULT_IDLE_TIMEOUT_SECONDS`. This is a server safety policy and
is not accepted in lease-create requests, so an ordinary client cannot disable
or lengthen it. Every successful Lab-PC validation immediately records
`last_activity_at`. Validation returns the earlier of the renewable lifetime
and the next command-idle deadline as its effective authorization deadline.

Configure the idle timeout longer than the Lab-PC stream revalidation interval
and the longest expected unary operation. With the shipped defaults, the
900-second idle policy is well above the 5-second stream revalidation interval
and 30-second unary-operation ceiling.

The maintenance process rechecks due lease candidates under the database lock,
marks them `expired`, clears their unique active slot, and writes the terminal
reason (`lease_duration_elapsed` or `command_idle_timeout_elapsed`) to both the
lease record and audit event. This is the crash-recovery backstop; renewal and
validation also perform the same expiration check transactionally.
