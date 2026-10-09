# The Automation runner

The runner (`labnet-runner`, in `labnet_automation`) works through
queued runs, one at a time. It runs in Docker: on a Linux PC in the lab, and
in WSL on a developer's laptop. Both use the same images and compose file
(`packages/labnet-automation/docker/`); only an address, a token and a CA file differ.

```text
Control Center (Windows) ◄── HTTPS, runner token ── runner container (trusted supervisor)
                                                     ├─ agent container per run: Claude Code + labnet tools
                                                     │     internal network; out only via the egress proxy
                                                     │     (Anthropic's servers); no token; tools via a
                                                     │     Unix socket in its run folder
                                                     ├─ run container per run_python call: the working
                                                     │     copy only, no network, CPU/memory/process limits;
                                                     │     during a device grant also the relay's socket
                                                     └─ device relay (in the runner): a script's device
                                                           calls → the gateway, with the grant's token
```

**A run, step by step.**
1. The runner claims the oldest queued run and downloads its inputs.
2. It unpacks them into a fresh working copy, refusing unsafe zip entries,
   links, `.claude/`, `.mcp.json` and `CLAUDE*.md`.
3. It starts Claude Code (`claude -p`) in an agent container, with the
   project's model, effort, maximum turns and budget. Its tools:
   - Read, Glob, Grep, and Edit and Write in the agent's folders only;
   - subagents (`literature-reviewer`, `data-analyst`, `experiment-runner`);
   - the labnet tools `search_literature`, `list_papers`, `read_paper_pages`
     (through the Control Center's [literature search](index.md#literature-search)),
     `run_python`, and for experiments `submit_protocol`, `grant_status` and
     `end_experiment`.

   There is no shell, and no web access beyond Anthropic's servers.
4. **`run_python`** runs one script the agent wrote under `ScriptsUtilized/`.
   It runs in a new container: Python 3.11 with numpy, scipy, pandas and
   matplotlib, no network, a read-only system, 2 CPUs, 2 GB and 10 minutes by
   default. Its output is saved in `ScriptsUtilized/logs/<run_id>/`.
5. The run's progress streams to its log on the project page. The runner
   sends a heartbeat, and stops the agent at once if someone presses Stop.
   **Experiments on lab devices** (Phase 3) happen in the same conversation:
   - the agent writes `Protocols/<task>.protocol.json` (devices, methods,
     bounds, duration, safe state) and `.protocol.md`; `submit_protocol`
     uploads both and the Control Center validates them;
   - the agent ends its turn; the runner waits for a person's decision,
     heartbeating and polling the run every `LABNET_RUNNER_POLL_SECONDS`
     (Stop, the stale rule and the approval timeout end the wait);
   - **approved:** the runner takes the grant's token, starts its device
     relay (a Unix socket in the run folder) and resumes Claude Code
     (`--resume <session id>`) with the approved devices, bounds and end time,
     taken from the normalized protocol the Control Center sends with the
     token (what the gateway enforces), never from the agent's own file; the
     prompt and `grant_status` also say that every bounded argument is passed
     in every call, that steps start from the device's current reading, and
     that a refused call is final.
     While the grant lasts, `run_python` containers get the relay's socket at
     `/run/labnet-devices` and scripts use `labnet_devices`; the relay adds the
     token to each call and the Control Center's gateway checks every one;
   - **rejected:** the agent is resumed with the reviewer's note (at most 3
     reviews per run);
   - when that session ends, the runner ends the grant (the safe state) and,
     if `Analysis/Analysis.md` is missing, resumes the agent once more to
     write it. However a run ends, a grant whose token was taken is ended.
6. **At the end**, it uploads the new and changed files in the agent's
   folders, plus the transcript and its own log
   (`ScriptsUtilized/logs/<run_id>/`). Anything changed elsewhere is
   reported, never uploaded. It finishes the run `done`, `failed` or
   `stopped`.

**It refuses to start unless the isolation holds.** At start, and on
`labnet-runner self-check`, it checks each of these and logs what failed:
- the Control Center answers and knows its token;
- the images exist;
- the agent network is internal and has a Claude login;
- a script container can reach nothing and can't see the runner's folders;
- an agent container can reach `api.anthropic.com` through the proxy, but
  not `example.com`, and nothing directly;
- the installed Claude Code has the options the runner uses.

**What's trusted:**
- **The runner container** holds the token and the Docker socket. Agent and
  script containers never get either, nor a grant's token: the device relay
  adds it to each call itself.
- **The agent container** holds the Claude login (a Docker volume). Claude
  Code in it can read the login, but it can reach only Anthropic, and its
  uploads go only to the agent's folders.
- **The egress proxy** (`egress-allow.txt`) allows HTTPS to
  `api.anthropic.com`, `platform.claude.com` and `claude.ai` only. It refuses
  names that resolve to LAN addresses.

| Setting (`runner.env`) | Default | |
| --- | --- | --- |
| `LABNET_RUNNER_CONTROL_CENTER_URL` | required | `https://lab-control-center` in the lab. Plain `http://` only with `LABNET_RUNNER_DEV=1` |
| `LABNET_RUNNER_TOKEN` (or `_TOKEN_FILE`) | required | From `labnet-control-center-admin runner add <name>` |
| `LABNET_RUNNER_CA_FILE` | system trust | `/etc/labnet/labnet-ca.pem` in the lab |
| `LABNET_RUNNER_WORK_DIR` | `/var/lib/labnet-runner` | Working copies; set by compose |
| `LABNET_RUNNER_POLL_SECONDS` | `10` | How often it asks for a run, and for a protocol's review decision |
| `LABNET_RUNNER_AGENT_TIMEOUT_MINUTES` | `120` | One agent session's time limit (waiting for approval doesn't count) |
| `LABNET_RUNNER_PYTHON_TIMEOUT_SECONDS` / `_CPUS` / `_MEMORY_MB` / `_PIDS` | `600` / `2` / `2048` / `256` | One `run_python` call's limits |
| `LABNET_RUNNER_KEEP_RUNS` | off | Keep each run's working copy after it ends (for debugging) |
| `LABNET_RUNNER_DEV` | off | Development: allows plain HTTP and marks every run "development only" |

## In development: Docker in WSL on your laptop

Needs, once:
1. WSL 2 with a Linux distribution (`wsl --install -d Ubuntu-22.04`).
2. Docker Engine in that distribution
   ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)).
   Then, in WSL, `sudo usermod -aG docker $USER`.
3. **Mirrored networking**, so WSL reaches the laptop's `127.0.0.1:8080`.
   `%UserProfile%\.wslconfig` must contain:

   ```ini
   [wsl2]
   networkingMode=mirrored
   ```

   Then run `wsl --shutdown`.

Then, from the repository root, with the dev network up (`labnet dev up`):

```bash
labnet runner build        # the three images (again after changing labnet_automation)
labnet runner login        # once: in Claude, type /login, open the link, then /exit
labnet runner up           # issues a runner token into .devnet/runner.env, starts the runner
labnet runner logs         # watch it check itself, then claim runs
labnet runner self-check   # the checks alone
labnet runner status       # its containers, and whether the keep-alive runs
labnet runner down
```

`labnet runner` comes with `labnet-automation`; `uv sync` installs it.

WSL stops a distribution, and its containers, about a minute after the last
Windows program attached to it exits. So `up` also starts a hidden
`wsl.exe … sleep` that keeps it running, as Docker Desktop does, and `down`
stops it. After a reboot, run `up` again.

The working copies live in WSL's own file system (`~/.labnet-runner`). Use a
different distribution with `LABNET_WSL_DISTRO=<name>`.

## In the lab: the runner PC

The runner gets its own Linux PC (Ubuntu 24.04 LTS or Debian 12; about 8 GB
of RAM; wired to the lab network at a fixed address). It needs outbound
HTTPS to the Control Center and to Anthropic, and nothing inbound.

1. **Docker Engine.** Follow
   [docs.docker.com/engine/install](https://docs.docker.com/engine/install/)
   for the distribution, then `sudo systemctl enable --now docker`. Don't add
   people to the `docker` group on this PC: it is root-equivalent.
2. **The code.** Copy or clone the repository to `/opt/labnet-src`, and
   update it the same way.
3. **Trust and the address.**
   - Copy `labnet-ca.pem` to `/etc/labnet/labnet-ca.pem`.
   - Add `192.168.50.10  lab-control-center` to `/etc/hosts`.
   - Check with `curl --cacert /etc/labnet/labnet-ca.pem https://lab-control-center/health/live`.
4. **A token.** On the Control Center PC, `labnet-control-center-admin …
   runner add lab-runner` ([Runners and their tokens](#runners-and-their-tokens)).
5. **Optional: limit the runner API to this PC's address.** Run
   `setup_logins.ps1 -RunnerAddress <its IP>` there, then restart Caddy
   ([Logins](../control-center/logins.md#logins)).
6. **Settings.** Create `/etc/labnet/runner.env`, owned by root, mode 600:

   ```ini
   LABNET_RUNNER_CONTROL_CENTER_URL=https://lab-control-center
   LABNET_RUNNER_TOKEN=lnr_…
   LABNET_RUNNER_CA_FILE=/etc/labnet/labnet-ca.pem
   ```

7. **Build, sign in, start.** In `/opt/labnet-src`:

   ```bash
   sudo mkdir -p /var/lib/labnet-runner
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml build
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml --profile login run --rm login   # /login, then /exit
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml run --rm --no-deps runner labnet-runner self-check
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml up -d egress runner
   sudo docker compose -f packages/labnet-automation/docker/compose.yaml logs -f runner
   ```

   The containers restart with Docker, so the runner is back after a reboot.

**Updating:** pull the new code into `/opt/labnet-src`, then `build` and
`up -d egress runner` again. A run in progress is stopped, keeps what it has
uploaded, and ends `stopped`; queue it again.

**On the Central PC instead:** the same steps work with Docker in WSL on
`central_server_pc`. There, set `LABNET_RUNNER_CONTROL_CENTER_URL` to the
Control Center's HTTPS address, as in the lab, and keep WSL running. The
[Automation go-live checklist](../deploying/go-live.md#automation-go-live-checklist) walks through
it. Keep in mind that the agent then shares a computer with Central.

**Claude login.** One login, saved in the `labnet-automation-claude-login`
volume, serves every run. Confirm that your Claude plan allows lab-wide
automated use. To change it, run the `login` step again. Removing the volume
signs the runner out.

## Runners and their tokens

A **runner** is the machine that works through the runs (see
[The Automation runner](#the-automation-runner)): the lab's Linux PC, or
Docker in WSL on a developer's laptop. It talks to the Control
Center only over the [runner API](#the-runner-api), with its own token,
never with a person's login. Manage runners on the Control Center PC:

```powershell
$ccadmin = "C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-control-center-admin.exe"
& $ccadmin --env-file C:\ProgramData\LabNet\control-center.env runner add lab-laptop
& $ccadmin --env-file C:\ProgramData\LabNet\control-center.env runner list
& $ccadmin --env-file C:\ProgramData\LabNet\control-center.env runner revoke lab-laptop
```

- **Names:** lowercase letters, digits and dashes, at most 32 characters.
- **`add`** prints the token (`lnr_…`) once. Put it in the runner's
  settings. The Control Center keeps only its sha256, in
  `state/runners/<name>.json`. A lost token can't be shown again: revoke the
  runner and add it again.
- **`revoke`** works at once: the runner's next request is refused.
- The Control Center doesn't need a restart for either.
- In development, `labnet-control-center-admin --env-file .devnet/control-center.env runner …`.
  On a checkout set up before this command existed, run `uv sync` again to
  install it, or use `python -m labnet_control_center.admin`.

## The runner API

Every request carries `Authorization: Bearer lnr_…`. Paths start with
`/api/automation/runner/`.

| Request | Does |
| --- | --- |
| `GET whoami` | The runner's name |
| `POST claim` | Claims the oldest queued run (200 with the run), or 204 when nothing is queued |
| `GET runs/<run_id>` | The run's record, to notice a cancel |
| `GET runs/<run_id>/inputs` | A zip of the project's inputs, with `run.json` at its root |
| `GET runs/<run_id>/library/<path>` | One paper, if it is within the project's library scope |
| `GET runs/<run_id>/literature/search?q=&k=&paper=` | [Literature search](index.md#literature-search) in the run's project |
| `GET runs/<run_id>/literature/papers` | The papers that search covers |
| `GET runs/<run_id>/literature/pages?paper=&first=&last=` | Up to 20 pages' text of one of them |
| `PUT runs/<run_id>/files/<path>` | Creates or replaces a file in the agent's folders. The body is the file |
| `POST runs/<run_id>/events` | `{"events": [{"kind": "progress", "text": "…"}, …]}`: progress and transcript lines |
| `POST runs/<run_id>/finish` | `{"status": "done" \| "failed" \| "stopped", "summary": "…"}` |
| `POST runs/<run_id>/heartbeat` | Still working on it: `{"status": "active"}` (see [Runs](index.md#runs) for the timeout) |

- **Refused:** no token or a wrong one (401); any request a browser made,
  which says so in `Sec-Fetch-Site` (403); a run this runner didn't claim
  (404, as if it didn't exist); anything but `GET runs/<run_id>` on a run
  that isn't active any more (409, with its `status`, which is how a runner
  learns of a cancel).
- **The inputs zip** holds the folders people fill (`Literature`,
  `Data/example_data`, `Experiment_layout` with `DEVICES.md`, `Tasks`), never
  the agent's folders, hidden names, `CLAUDE*.md` or links. `run.json` holds
  the run, its `settings` (model, effort, max turns and budget, as copied
  when it was queued), the project's settings (allowed devices, linked experiments,
  library scope), the list of library papers in scope, and `truncated` when
  the project had more files than a listing holds. A project that names no
  topic folders or reading lists gets the whole library.
- **Events:** each is exactly a `kind` (lowercase letters, digits and `_`)
  and a `text` of at most 16 KiB; at most 100 per request, and 16 MiB per
  run. The summary is at most 4000 characters. The Control Center stamps the
  time and redacts both.

**What a runner may write.** Only the agent's folders: `Data/new_data`,
`Protocols`, `ScriptsUtilized` and `Analysis`. Uploads pass the same path
checks as a person's, plus:
- no hidden name (`.claude/`, `.mcp.json`) and no `CLAUDE*.md` anywhere on
  the path, which a later run's Claude Code would read as settings or
  instructions;
- a file name, never one of the fixed folders (`Analysis/outputs`,
  `ScriptsUtilized/logs`, …);
- each file at most `LABNET_AUTOMATION_MAX_FILE_MB`;
- the whole run at most `LABNET_AUTOMATION_RUN_QUOTA_MB` (500 MB). The quota
  is counted as bytes stream in, so two uploads at once can't pass it
  together, and replacing a file counts again. Past it, uploads get 413.

The Control Center, not the runner's machine, decides what the agent may
change.
