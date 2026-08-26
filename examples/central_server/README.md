# Central Server example

The complete reusable Central API, database services, models, migrations, and
administration CLI are installed as `labnet.central`. This directory only shows
how a deployment imports, configures, and runs that installed application.

From `Labnet/`, install the Central dependencies:

```powershell
python -m pip install -e ".[central]"
```

Central reads its process environment directly. For a local SQLite example,
run these commands from the repository root:

```powershell
$env:LABNET_DATABASE_URL = "sqlite:///C:/ProgramData/LabNet/Central/labnet.db"
labnet-central-admin db upgrade
labnet-central-admin user create central-admin --role admin
labnet-central-admin user create lab-pc-dev-01 --role device_manager
labnet-central-admin user create researcher --role user
python -m examples.central_server
```

Each user command prints its API key once. The `device_manager` username must
exactly equal the corresponding Lab PC's `LABNET_LAB_PC_ID`.

The built-in server listens on loopback because it serves plaintext HTTP. Put
an HTTPS reverse proxy on the same host for multi-computer deployments. Schema
changes are explicit: run `labnet-central-admin db upgrade` after installing a
release that contains a migration.

The installed console command `labnet-central` runs the same reusable Central
application without this example wrapper.
