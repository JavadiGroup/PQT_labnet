# Labupdater

`Labupdater` is a small Python distribution that is installed separately from
LabNet. It supervises the Lab-PC process and therefore never upgrades the
virtual environment from which it is running. This process boundary is
required on Windows, where an active interpreter and console-script executable
cannot be replaced reliably in place.

## Owned state

Given `release_root = "C:/ProgramData/LabNet/releases"`, the updater owns:

```text
releases/
|-- current.json                 atomic active-version pointer
|-- updater-state.json           resumable attempt journal
|-- downloads/                   verified temporary bundles
|-- work/                        temporary extraction directories
|-- logs/lab-pc.log              supervised Lab-PC output
`-- runtimes/
    |-- 0.2.0/venv/              immutable previous runtime
    `-- 0.3.0/venv/              immutable current runtime
```

Central has a separate artifact store. The updater does not mount or mutate
Central's files; it downloads an assigned artifact over authenticated HTTPS.

## Runtime sequence

For each assignment, the updater performs these operations:

1. Download the ZIP while the current Lab-PC runtime remains available.
2. Verify the outer size and SHA-256, reject unsafe ZIP members, verify every
   payload file, then create and smoke-test a new versioned virtual environment.
3. Ask Central to mark every device on this Lab PC as `draining`. This blocks a
   lease acquired after the drain transaction while allowing current leases to
   finish normally.
4. Wait until Central reports zero active leases, then call the authenticated
   loopback Lab-PC drain endpoint. The Lab PC rejects new gRPC work and waits
   for active RPCs, streams, queued work, and running physical calls to finish.
5. Stop the old process, update `current.json`, launch the new process, and
   require the new version's readiness response.
6. Report success so Central reopens device admission. If startup or readiness
   fails, restore the previous pointer, restart the prior runtime, verify it,
   report `rolled_back`, and then reopen admission.

An updater restart reads both its journal and Central's recorded phase. An
already installed runtime with the same artifact hash is reused; a version
directory with a different hash is rejected.

The default policy never revokes a valid lease and has no automatic drain
deadline. A continuously active experiment can therefore delay an update.
That is intentional: forced experiment interruption should be an explicit
operator action, not a side effect of package publication.

## Configuration

Copy `config.example.toml` to a stable, access-controlled location outside all
LabNet runtimes. Store the Device Manager API key and the local control token
in separate files. The token must also be at least 16 characters; Labupdater
injects it into the Lab-PC child process rather than storing it in a wheel.

`labpc_pythonpath` is where a machine-specific `lab_inventory.py` can live.
Set this in the external Lab-PC environment file:

```dotenv
LABNET_DEVICE_FACTORY=lab_inventory:create_devices
```

Only the updater should supervise the Lab-PC process. Do not configure a
second OS service that also starts `labnet-lab-pc`, or two processes may
contend for the same hardware and ports.

## Install and bootstrap on Windows

Build the updater wheel once from the repository root:

```powershell
python -m build --wheel Labupdater
```

Install it in a stable virtual environment that will not be replaced by a
LabNet release:

```powershell
py -3.11 -m venv C:\ProgramData\LabNet\updater-venv
C:\ProgramData\LabNet\updater-venv\Scripts\python.exe -m pip install .\Labupdater\dist\labupdater-1.0.0-py3-none-any.whl
```

Before the service runs for the first time, install one known-good LabNet
bundle and activate it:

```powershell
C:\ProgramData\LabNet\updater-venv\Scripts\labupdater.exe `
  --config C:\ProgramData\LabNet\config\updater.toml `
  install-bundle `
  --bundle C:\Staging\labnet-0.2.0-bundle.zip `
  --manifest C:\Staging\labnet-0.2.0-manifest.json `
  --activate
```

The long-running service command is:

```powershell
C:\ProgramData\LabNet\updater-venv\Scripts\labupdater.exe `
  --config C:\ProgramData\LabNet\config\updater.toml run
```

Run that command under the lab's Windows service wrapper (for example WinSW)
using a dedicated service account. Grant that account read access to config,
inventory, CA, and secret files and modify access only to `release_root`.
Configure service recovery to restart Labupdater. Labupdater itself launches
and monitors the selected Lab-PC runtime.

Use `labupdater --config <path> status` for the local journal. Fleet state is
also available to an administrator through Central's deployments endpoint.

## Bundle contract

A release ZIP contains only:

```text
requirements.lock
wheelhouse/
|-- labnet-<version>-py3-none-any.whl
`-- every exact transitive dependency wheel
```

`requirements.lock` pins every distribution and records its SHA-256. Runtime
installation uses `pip --no-index --require-hashes`, so rollback does not
silently resolve newer dependencies. The sidecar manifest records the ZIP
hash, LabNet wheel hash, every payload hash, Git commit, target roles, Python
version, platform tag, protobuf contract digest, and minimum updater version.

The current implementation authenticates manifests through the Central HTTPS
connection and verifies all declared hashes. The optional `signature` field is
reserved for a future site-specific signing integration; it is not currently
treated as a cryptographic signature.

