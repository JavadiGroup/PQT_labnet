# Command reference

| Command | Where it runs | Purpose |
| --- | --- | --- |
| `labnet dev up / down / status / smoke` | dev machine | Start, stop, check, and exercise the local network |
| `labnet dev seed [--watchdog]` | dev machine | Wipe `.devnet/`; write `central.env`, run `labnet-central-admin setup`, write every component's settings |
| `labnet dev restart COMPONENT...` | dev machine | Restart only `central`, `control-center`, `watchdog`, or a Lab PC id, leaving the rest running |
| `labnet runner build / login / up / logs / self-check / down` | dev machine (Docker; WSL on Windows) | The [Automation runner](../automation/runner.md#the-automation-runner) against the dev Control Center |
| `labnet driver build [--core] [--check]` | dev machine | Generate protos (a driver package's, or LabNet's own with `--core`); `--check` fails on stale files |
| `labnet driver new NAME --service SERVICE` | dev machine | A new driver package from the template |
| `labnet bundle --site sites/<lab>` | development PC | Build a hash-recorded bundle: wheels, site, `MANIFEST.json` |
| `labnet update --from BUNDLE --role ROLE` | each runtime computer | Verify, install side by side, migrate or check the site, switch over |
| `labnet-certs init-ca / issue / show` | development PC | Create the private CA and server certificates |
| `labnet-central-admin setup / user …` | Central | Migrate, create users, write their settings files |
| `scripts/lab/setup_logins.ps1 [-Reset NAME] [-Realm TEXT] [-WhatIf]` | Central (PowerShell) | Control Center logins: Caddy snippet with each person's password hash, and the proxy secret in `control-center.env` |
| `labnet-control-center-admin runner add / list / revoke` | Central | Issue and revoke Automation runner tokens |
