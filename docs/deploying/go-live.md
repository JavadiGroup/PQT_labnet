# Automation go-live checklist

Use this to bring the [Automation](../automation/index.md#automation) tab into use on the lab
network: the agent, its runner, approvals and device control. Work through
it in order. Each step links to the full instructions. Here the runner runs
in Docker inside WSL on `central_server_pc`. The steps for a dedicated Linux
runner PC are the same from step 5 on (see
[In the lab: the runner PC](../automation/runner.md#in-the-lab-the-runner-pc)).

Before you start, the network itself must already be deployed and verified
([Production deployment](index.md#production-deployment)): Central, Caddy, the
Control Center and every Lab PC.

## 1. Decide first

- [ ] **Which instruments may ever be automated**, and the safe range of
      every number the agent may set on them. These become site limits in
      step 3. An instrument with no limit for a number can't be automated
      ("no limit, no automation").
- [ ] **Who approves.** Any signed-in lab member can approve a protocol.
      Agree who should, and that approvers read the review card's numbers,
      not the agent's description.
- [ ] **The Claude account** the runner signs in with is the lab's, and its
      plan allows automated use by the whole lab.
- [ ] **IT agrees** to WSL 2 and Docker Engine on `central_server_pc`, and
      virtualization is enabled in its firmware.
- [ ] **The first instrument** is one where a wrong value is harmless.

## 2. Control Center: logins and settings

On `central_server_pc`:

- [ ] Update the Control Center to this release (`labnet update --role
      control-center`; see [Operations and upgrades](updating.md#operations-and-upgrades)).
- [ ] In `C:\ProgramData\LabNet\control-center.env`, check:
      - `LABNET_AUTOMATION_DIR`, and that the folder is backed up;
      - `LABNET_AGENT_LITERATURE_DIR` (the lab library).

      The run limits keep their defaults unless you have a reason to change
      them ([Control Center](../control-center/index.md#control-center) settings table).
- [ ] **Logins:**
      1. List the lab members in `C:\ProgramData\LabNet\logins.txt`.
      2. Run `scripts/lab/setup_logins.ps1` and hand out the passwords it
         prints ([Logins](../control-center/logins.md#logins)).
- [ ] The Caddyfile starts with `{ admin off }`, imports the logins snippet,
      and the Control Center's site block uses `import labnet_control_center`
      ([`sites/pqt/Caddyfile.example`](https://github.com/mill0851/labnet/blob/main/sites/pqt/Caddyfile.example)).
- [ ] Restart Caddy, then the Control Center.
- [ ] **Check:**
      - opening the Control Center from another computer asks for a login,
        and the top bar shows the signed-in name;
      - `curl.exe http://127.0.0.1:8080/status` on the server is refused
        (403).

## 3. Lab PCs: site limits

On each Lab PC whose instruments may be automated:

- [ ] In its inventory (`sites/pqt/inventories/<lab-pc>.py`), wrap each such
      device in `site_limits(...)` with the ranges from step 1
      ([Site limits](../drivers.md#site-limits)). Narrow the driver's limits; never copy
      them unexamined.
- [ ] Restart the Lab PC. It refuses to start if a site limit is wider than
      what the driver allows, or names an unknown argument; fix the inventory
      and restart.
- [ ] **Check:**
      - on the Network page, the device's ranges show "(site limit)";
      - a client call just outside a limit is refused with
        `ARGUMENT_OUTSIDE_LIMITS`.

## 4. The safe-state watchdog

On `central_server_pc`, following
[The safe-state watchdog](../automation/security.md#the-safe-state-watchdog):

- [ ] Create its Central user (`labnet-central-admin setup --user
      automation-watchdog …`).
- [ ] Put its settings in `C:\ProgramData\LabNet\watchdog.env`, as in
      [`sites/pqt/watchdog.env.example`](https://github.com/mill0851/labnet/blob/main/sites/pqt/watchdog.env.example).
- [ ] Start it next to the Control Center. Then register its Task Scheduler
      task, so it also starts at logon.
- [ ] **Check:** `http://127.0.0.1:8081/health/ready` answers 200 on the
      server.

## 5. The runner: Docker in WSL on the Central PC

The detailed steps are in [The Automation runner](../automation/runner.md#the-automation-runner).
On `central_server_pc`:

- [ ] Install WSL 2 with Ubuntu 24.04 (`wsl --install -d Ubuntu-24.04`).
      Then install Docker Engine in it
      ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)).
      Don't add people to the `docker` group: it is root-equivalent.
- [ ] Turn on **mirrored networking**, so the runner reaches the Control
      Center through Caddy at the Central PC's own address:
      1. Put `networkingMode=mirrored` under `[wsl2]` in
         `%UserProfile%\.wslconfig`.
      2. Run `wsl --shutdown`.
- [ ] **Keep WSL running.** Windows stops WSL, and with it Docker and the
      runner, about a minute after the last Windows program using WSL
      closes. Register a task that keeps one hidden WSL process open from
      logon:

      ```powershell
      $action  = New-ScheduledTaskAction -Execute "wsl.exe" `
          -Argument '-d Ubuntu-24.04 -e bash -c "exec -a labnet-runner-keepalive sleep infinity"'
      $trigger = New-ScheduledTaskTrigger -AtLogOn
      Register-ScheduledTask -TaskName "LabNet runner keep-alive" -Action $action -Trigger $trigger
      ```

- [ ] Inside WSL:
      1. Copy the repository to `/opt/labnet-src`.
      2. Copy `labnet-ca.pem` to `/etc/labnet/labnet-ca.pem`.
      3. Check that `lab-control-center` resolves. WSL copies the Windows
         hosts file unless it has been told not to.
- [ ] **A runner token:** `labnet-control-center-admin … runner add
      central-runner` ([Runners and their tokens](../automation/runner.md#runners-and-their-tokens)).
- [ ] Write `/etc/labnet/runner.env`, owned by root with mode 600. It needs
      the HTTPS address, the token and the CA file
      ([In the lab: the runner PC](../automation/runner.md#in-the-lab-the-runner-pc), step 6).
      Don't set `LABNET_RUNNER_DEV`.
- [ ] Build, sign in, check, start. Run each in `/opt/labnet-src`, with
      `sudo docker compose -f packages/labnet-automation/docker/compose.yaml …` in front:
      1. `build`
      2. `--profile login run --rm login`: in Claude, `/login` with the lab
         account, then `/exit`.
      3. `run --rm --no-deps runner labnet-runner self-check`: every line
         must say `ok`.
      4. `up -d egress runner`
- [ ] Optional: limit the runner API to the runner's address with
      `setup_logins.ps1 -RunnerAddress <address>` ([Logins](../control-center/logins.md#logins)). Check
      the address Caddy's log shows for runner requests first.
- [ ] **Check:** `sudo docker compose -f packages/labnet-automation/docker/compose.yaml logs
      runner` shows the self-check passing, then "polling".

## 6. A first supervised run

- [ ] On the Automation tab, press **Index library** and wait for the count.
      Note any papers flagged without a text layer
      ([Literature search](../automation/index.md#literature-search)).
- [ ] **Create a test project:**
      - link an experiment canvas that holds the harmless instrument;
      - tick it under **Allowed devices**;
      - add a short task in `Tasks/`.
- [ ] Press **Run**. Watch the run's log on the project page while the agent
      plans and submits a protocol.
- [ ] **Review the protocol** on the approval card
      ([Protocols and approval](../automation/security.md#protocols-and-approval)):
      - every device, method, range, step, rate and call limit, the duration
        and the safe state;
      - **Edit…** to narrow anything; **Reject** with a note if it's wrong.
- [ ] Approve. Then check:
      - the Network page shows the device as held by Automation, with a
        countdown and **Stop**;
      - Play on that device is refused.
- [ ] After the run:
      - check the results in `Analysis/`;
      - check the audit trail (`state/<project>/audit/<run>/`): every call is
        listed, the safe state was applied, and the leases were released.
- [ ] **Practise Stop:** start another run, approve it, and press **Stop**
      on the Network page mid-experiment. The device should go to its safe
      state at once.
- [ ] **Optional, the watchdog drill:** during a third approved run, end the
      Control Center's process in Task Manager. Within about two minutes the
      watchdog log (`C:\ProgramData\LabNet\logs\watchdog.log`) shows it
      applying the safe state. Then restart the Control Center.

## 7. Keep it running

- [ ] **Back up** these folders with the rest of LabNet's data:
      - `C:\ProgramData\LabNet\automation` (projects, runs, approvals, audit
        trails);
      - `experiments`;
      - `literature`.
- [ ] **After an update:** rebuild and restart the runner (`build`, then
      `up -d egress runner`), and restart the watchdog with the Control
      Center.
- [ ] **When an instrument is added or changed:** set its site limits before
      ticking it under Allowed devices.
- [ ] **When someone joins or leaves:** run `setup_logins.ps1` again, and
      restart Caddy.
- [ ] **If the lab's Claude login changes:** run the runner's `login` step
      again.
