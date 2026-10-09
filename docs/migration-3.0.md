# Migrating to 3.0

For a lab running LabNet 2.x. **2.x and 3.0 computers can't talk to each
other** (a 3.0 Lab PC refuses a 2.x client with an API-version error), so the
whole lab moves in one window: plan an hour when nobody needs the
instruments. The database needs no conversion: 3.0 uses 2.x's schema.

## What changes for you

| In 2.x | In 3.0 |
| --- | --- |
| One `labnet` package with every device family | `labnet` plus one *driver package* per family; the simulated ones are `labnet-sim` |
| `from labnet import DummyDevice` | `from labnet_sim import DummyDevice` (any driver package: `from <package> import <Family>`) |
| Inventories import `labnet.devices.<Family>.physical` | They import from the driver package, e.g. `labnet_sim.simulated_wavemeter.physical` |
| `site/` in the repository | `sites/<lab>/` (the PQT lab's is `sites/pqt/`) |
| `scripts/release/build.sh`, then `update_host.sh <role>` in Git Bash | `labnet bundle --site sites/<lab>`, then the bundle's `install.ps1 -Role <role>` in PowerShell |
| `scripts/dev/*.sh` | `labnet dev …`, `labnet runner …`, `labnet driver build` |
| Automation tab and Photo-Agent always on | Off unless `control-center.env` turns them on |
| The Control Center is called "PQT Lab Control Center" | It is "`LABNET_SITE_NAME` Control Center" (default "LabNet") |
| `<role>-venv` is a folder | `<role>-venv` is a link to `venvs\<role>-<version>-<time>`; earlier versions stay for rollback |

## Before the window (development PC)

1. **Get 3.0** and set up the checkout: `git pull`, then `uv sync`.
2. **Drivers.** Every instrument family the lab uses must be a driver
   package. The simulated families are in `labnet-sim`; for your own (for
   example the Sacher laser), build each package with `labnet driver build`
   and check it with `labnet.testing.check_driver` ([Writing a
   driver](drivers.md)).
3. **Inventories.** In `sites/<lab>/inventories/`, change each driver import
   to its package. `labnet update --role lab-pc` checks every inventory
   against the installed drivers, so a mistake shows during the install, not
   at startup.
4. **Experiment scripts** that import a device class from `labnet` (your own,
   and those the Control Center saved in its experiments folder) change to
   the driver package: `from labnet_sim import DummyAOM`.
5. **Build the bundle**, with every driver package the lab uses:

    ```bash
    labnet bundle --site sites/pqt --driver packages/labnet-sim --driver ../sacher-driver
    ```

   Copy `dist/labnet-3.0.0-pqt/` to the release share.

## In the window

1. **Stop everything**, in reverse order: clients, Lab PCs, the Control
   Center, Caddy, Central.
2. **On each computer, set the 2.x environment aside.** `labnet update`
   won't replace a folder, and the old one is your way back:

    ```powershell
    Rename-Item C:\ProgramData\LabNet\central-venv central-venv-2.x     # and control-center-venv on Central,
                                                                        # lab-pc-venv on Lab PCs, client-venv on clients
    ```

3. **Central's settings** stay as they are. In `control-center.env`, add what
   2.x had without asking:

    ```dotenv
    LABNET_SITE_NAME=PQT Lab
    LABNET_AUTOMATION_ENABLED=true
    LABNET_PHOTO_AGENT_ENABLED=true
    ```

4. **Install, in order**: Central, the Control Center, each Lab PC, then
   clients ([Updating a lab](deploying/updating.md)):

    ```powershell
    powershell -ExecutionPolicy Bypass -File \\192.168.50.40\LabNet-Releases\3.0.0\install.ps1 -Role central
    ```

   Start each role after its install, in the same order, and check its
   `/health/ready`.
5. **Logins.** If you run `setup_logins.ps1` again, pass
   `-Realm 'PQT Lab Control Center'` to keep the login prompt's old name.
6. **Automation.** 3.0 adds the safe-state watchdog, which Automation grants
   rely on. If the lab uses Automation, work through the
   [go-live checklist](deploying/go-live.md) before the first run.
7. **Check** with the [deployment verification](deploying/index.md#deployment-verification)
   list. On the Network page, no device should show "Driver mismatch" or
   "No driver here".

## Going back to 2.x

Stop everything, then on each computer remove the 3.0 link and put the 2.x
folder back:

```powershell
cmd /c rmdir C:\ProgramData\LabNet\central-venv
Rename-Item C:\ProgramData\LabNet\central-venv-2.x central-venv
```

A Lab PC also keeps the previous site as `C:\ProgramData\LabNet\site.previous`.
The database needs nothing: its schema didn't change.
