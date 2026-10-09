# Changelog

All notable changes to LabNet. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The packages `labnet`,
`labnet-control-center`, `labnet-automation` and `labnet-sim` are released
together with the same version.

## [3.0.0] - unreleased

The first open-source release. LabNet 2.x ran one lab and was never
published; 3.0 makes device families pluggable and removes everything
specific to that lab. **2.x and 3.0 computers can't talk to each other:**
update a lab all at once (see "Migrating to 3.0" in the documentation).

### Breaking

- Device families are **driver packages**, found through the
  `labnet.devices` entry-point group. `labnet` itself ships none: the
  simulated instruments moved to `labnet-sim` (`from labnet_sim import
  DummyAOM`), and inventories import drivers from their package
  (`labnet_sim.simulated_wavemeter.physical`).
- The device API version is `labnet.v3`; a Lab PC refuses 2.x clients
  (`labnet.devices.v1`) with an API-version error. gRPC services of the
  simulated families are `labnet_sim.v1.*`.
- The Control Center is its own package, `labnet-control-center`
  (`labnet_control_center`), and ships the safe-state watchdog as
  `labnet-watchdog` (was `labnet-automation-watchdog`). The Automation
  runner is `labnet-automation` (`labnet_automation`).
- A Lab PC without `LABNET_DEVICE_INVENTORY` refuses to start; the gRPC
  clients need an explicit contract; `LabClient.acquire_dummy` is gone.
- The Control Center's Automation tab and Photo-Agent are **off by
  default**: set `LABNET_AUTOMATION_ENABLED=true` and
  `LABNET_PHOTO_AGENT_ENABLED=true` to keep them.
- The proposal block in Photo-Agent chats is `labnet-experiment`
  (`pqt-experiment` still renders in old chats). Browser settings move from
  `pqt-*` to `labnet-*` keys on the first visit.
- The Sacher laser driver left the repository; it is its own driver package.

### Added

- `labnet driver build` generates a driver package's bindings, contracts and
  typed facades from its protos, and `--check` fails on stale files.
  `labnet driver new NAME --service SERVICE` starts a package from a
  template that builds and passes its tests out of the box.
- `labnet.testing`: `check_driver` (would a Lab PC accept this device?) and
  `local_network` (a real Central and Lab PC in one process).
- Each device reports its driver (package, version, contract fingerprint).
  The Control Center flags a missing or mismatched driver and refuses to
  automate a device whose driver doesn't match.
- `SimulatedTunableLaser` in `labnet-sim`: a step-tuned laser whose moves
  take time.
- `labnet dev up / down / status / smoke / seed / restart`: the development
  network in Python, from any shell, with no windows.
- `labnet runner build / login / up / down / logs / self-check / status`: the
  Automation runner next to a dev network.
- `labnet bundle` builds a hash-recorded bundle for a lab (wheels with every
  dependency, the site folder, `install.ps1`); `labnet update` installs a role
  side by side, checks it (database backup and migrations; inventory and
  drivers; settings), then switches over, keeping earlier versions for rollback.
- `LABNET_SITE_NAME` names the lab in the Control Center's titles and
  generated scripts.
- `setup_logins.ps1 -Realm` names the login prompt.
- Documentation site, contribution guide, security policy and code of conduct.

### Changed

- The repository is a uv workspace of four packages under `packages/`.
- A lab's configuration moved from `site/` to `sites/<lab>/`.
- The bash scripts in `scripts/dev`, `scripts/release` and `scripts/deploy`
  are replaced by the `labnet` commands above.

### Fixed

- The Automation egress proxy closes a connection it refuses at its
  connection cap (it was left half-open).
- `labnet update --python` accepts an interpreter path with spaces.
