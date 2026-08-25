# Managed deployment map

This directory contains configuration examples, not secrets or generated
release artifacts. The authoritative workflow is the managed-release section
of the repository README and `Labupdater/README.md`.

## Central host

Central remains a separately managed service because upgrades may require an
Alembic database migration. Keep its database, release store, environment, and
admin keys outside its virtual environment. For each Central upgrade:

1. stop the service;
2. back up the SQL database and release store;
3. install the new `labnet[central]` wheel;
4. run `labnet-central-admin db upgrade`;
5. restart and verify `/health/ready`.

Do not point Labupdater at the Central process and do not automatically
downgrade a migrated Central database.

## Lab PC

Copy `examples/lab_inventory.py` and the Lab-PC/updater example configuration
into `C:\ProgramData\LabNet` (or the equivalent system-data directory). Install
Labupdater in its own stable virtual environment, bootstrap a LabNet bundle,
and register the `labupdater ... run` command as the only service for that Lab
PC. It launches the selected LabNet runtime itself.

## Client workstation

Client programs are not stopped by the Lab-PC updater. Install the hashed
release bundle into a new, versioned client virtual environment and update the
lab's launcher for newly started experiments only after the Lab-PC rollout is
complete. A Python process that is already running continues using the old
loaded modules, which is safer than mutating its environment underneath it.

