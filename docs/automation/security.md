# Security model

Automation lets an AI agent run experiments on real instruments. These
rules keep a person in charge of the hardware:

- The agent can only *propose* a protocol: which devices, which methods,
  the bounds on every number, call and rate caps, a duration and a safe
  state. A person approves the exact protocol (by its sha256) or rejects it.
- Only an approved protocol yields a *grant*. During it, every device call
  goes through the Control Center's gateway, which refuses anything outside
  the protocol. The agent's scripts never hold a device or a key.
- Ending a grant, for any reason, applies the safe state and releases the
  devices. If the Control Center dies mid-grant, the safe-state watchdog
  does it.
- Independently of all this, each Lab PC enforces its drivers' published
  limits and its own [site limits](../drivers.md#site-limits) on every
  remote call, from any client.
- The runner runs the agent in locked-down containers that reach only the
  Control Center and the model API; see [The Automation runner](runner.md).

## Protocols and approval

Before an agent may touch hardware, it writes a **protocol**: which devices,
which methods, the bounds of every argument, call and rate caps, how long,
and the safe state. A person reviews it and approves or rejects it
(`automation_protocol.py`). While a run is active, its `phase` says where it
is:

```
planning ──(submits a protocol)──► awaiting_approval ──approve──► executing ──(grant ends)──► analyzing
    ▲                                    │
    └──────────reject + note─────────────┘
```

A run that never needs devices goes from `planning` straight to its finish.

**The files.** For `Tasks/Task1.md` the agent writes
`Protocols/Task1.protocol.json` and, in its own words,
`Protocols/Task1.protocol.md`. The runner uploads both, then calls
`POST /api/automation/runner/runs/<run>/protocol` `{}`. The Control Center
reads the JSON from the project itself:

```json
{
  "schema_version": 1,
  "task": "Tasks/Task1.md",
  "title": "AOM efficiency vs RF frequency",
  "duration_minutes": 20,
  "devices": [
    {
      "device_id": "dev01-aom",
      "device_type": "dummy_aom",
      "methods": {
        "set_frequency": {"args": {"value": {"min": 70, "max": 90, "max_step": 2}}, "max_calls": 200, "max_rate_hz": 5},
        "set_rf_enabled": {"args": {"enabled": {"choices": [true, false]}}, "max_calls": 10, "max_rate_hz": 1},
        "read_diffraction_efficiency": {"max_calls": 500, "max_rate_hz": 10},
        "stream_diffraction_efficiency": {
          "args": {"channel": {"choices": ["CH1"]}},
          "stream": {"max_sample_rate_hz": 20, "max_samples": 1000},
          "max_calls": 50, "max_rate_hz": 1
        }
      }
    }
  ],
  "safe_state": [{"device_id": "dev01-aom", "method": "set_rf_enabled", "args": {"enabled": false}}]
}
```

**The checks.** Everything in the file is untrusted, so the checks are strict.
Each refusal names the place (`devices[0].methods.set_frequency.args.value.max`)
and says what to change:
- **The file:** UTF-8 JSON, at most 64 KiB. Unknown keys are refused at
  every level, and so are repeated keys, `NaN` and `Infinity`.
- **Top level:** `schema_version` 1. `task` is the run's own task. `title`
  has at most 120 characters (text never has control or invisible characters,
  nor more than 2 combining marks on one character, which could draw over the
  review). `duration_minutes` is from 1 to
  `LABNET_AUTOMATION_GRANT_MAX_MINUTES` (120). There are 1 to 16 devices,
  each listed once.
- **Devices:** each one must be on a canvas linked to the project, allowed
  for automation in the project's settings, and on the network now.
  `device_type` must be its live type, and the linked experiment must agree.
- **Methods:** each one must be in that type's contract, with `max_calls`
  (a whole number, 1 … 100 000) and `max_rate_hz` (above 0, at most 100).
  A stream also needs `stream: {max_sample_rate_hz (above 0 … 200),
  max_samples (1 … 100 000)}`; nothing else may have one.
- **Arguments:** `args` bounds every argument the method sends. An optional
  one may be left out, and then it may never be sent; one that is bounded is
  **sent in every call** (otherwise leaving out a `channel` limited to `CH2`
  would drive the driver's default, `CH1`). A write's `unit` is never allowed.

  | Argument type | Bounds |
  | --- | --- |
  | number (`float`, `int`) | `{"min", "max", "max_step"?}`: finite, `min ≤ max`, `max_step > 0`; whole numbers for an `int` |
  | `bool` | `{"choices": [true, false]}` (one or both) |
  | `str` | `{"choices": [...]}`: up to 32, each up to 64 characters |

  `true` is never a number, and `1` is never `true`.
- **`max_step` needs a reading.** Steps are measured from where the device
  is, so `max_step` is allowed only on a `set_<x>` that has a scalar
  `read_<x>` taking the same choice arguments (`set_amplitude(value,
  channel)` → `read_amplitude(channel)`; `automation_protocol.step_reader`).
  Today that is every setter of `dummy_aom`, `dummy_awg`, `dummy_eom` and
  `dummy_laser`; not `dummy_device.write_value`, the `simulated_tunable_laser` methods
  or `simulated_wavemeter.set_exposure`, where the check says to drop
  `max_step` (any value in range is then allowed) or narrow the range.
- **Published limits are hard limits.** A device publishes the driver's
  hints narrowed by its Lab PC's [site limits](../drivers.md#site-limits). `[min, max]`
  must lie within its `limits["<method>.<argument>"]`. Choices (text or
  true/false) must be among its `choices`, and a `channel` among its
  `channels`, whenever it publishes them. Each refusal says whether the limit
  is the site's or the driver's.
- **No limit, no automation.** Every number a protocol bounds needs a
  published limit. Without one the protocol is refused (a problem, not a
  warning): *"dev05-laser publishes no limit for
  go_to_wavelength_nm.wavelength_nm; set one in the Lab PC inventory with
  site_limits(...) before automating it."* A person sets that limit in the
  inventory and restarts the Lab PC. Text arguments without published choices
  still need explicit `choices` in the protocol.
- **The safe state:** up to 16 calls, each one the protocol itself allows:
  a listed method (not a stream), every argument it sends and every argument
  the protocol bounds (e.g. its `channel`), and values within the bounds.
  `max_step`, call counts and rates don't apply to it. A whole-number
  argument stays an integer (`0`, not `0.0`), so the gateway accepts it.

A protocol that passes is **normalized**: defaults filled in, bounds and
rates as floats, counts as integers. It is serialized canonically
(`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).
That text is stored as `state/<project>/protocols/<run>-<n>.json`, with the
prose beside it as `<run>-<n>.md`. Its sha256 is what a person approves.

**Submitting.**
- **200** `{"phase": "awaiting_approval", "sha256", "submission"}`: the run
  now waits for a person.
- **422** `{"detail", "problems": [{"path", "message"}]}`: the run stays
  `planning`. A refused file doesn't count as a submission; the agent fixes it
  and submits again.
- **409** when the run isn't `planning`, or has already sent 3 protocols for
  approval.
- **503** while Central can't be reached: a protocol is never checked against
  a stale picture of the network.

**Reviewing.** Clicking a run that waits for approval (*Review* in the runs
list) shows:
- **The approval card:** the protocol's sha256 prefix and the requested
  duration. The duration box can shorten the time but never lengthen it.
  *Approve* asks to confirm. *Reject* needs a note for the agent.
- **The review:** built by the server from the normalized copy and the
  device contracts, never from the agent's words. Each device shows its
  type and live state. Each method shows every argument's range or choices,
  its largest step (the first measured from the device's own reading) and
  the device's published limit with its source (`limit`, `limit_source`:
  `site` or `driver`; choices as `offered`), that each bounded argument is always sent, which
  optional arguments may never be sent, its call and rate caps, and its
  stream caps. Agent-written text (the title, choices) is clipped to its own
  line, so it can't draw over the rest. Then come the
  safe state and warnings: no safe state, no `max_step`, or a limit no
  longer published (the device went offline; approving checks again).
- **The agent's explanation:** the `.md`, in a box labelled as the agent's,
  shown as plain text only.

Once approved, the page shows the **grant** with a countdown and *Stop now*.
A run whose safe state failed ends `ended_unsafe`, with a red banner.

**Approving** sends the sha256 the page showed. The approver is the
signed-in person (with [logins](../control-center/logins.md#logins)) or the name typed, and is
required. The Control Center then:
1. refuses a different sha256 (409), a run not waiting for approval (409),
   or a longer or unusable duration (422, also for an integer too large for a
   float);
2. checks the stored protocol again against the project and the live
   network. A device no longer allowed, gone or of another type answers 409
   with the problems, and the run keeps waiting: reject it so the agent can
   write a new one;
3. creates the [grant](#device-grants-and-the-gateway), taking every lease.
   A busy device answers 409, and nothing changes;
4. records the approval (`reviewed_by`, `approved_minutes`). The run is then
   `executing`.

One approval runs at a time. If the run ends while its grant is being
created, the grant is ended again.

**Editing before approving** (*Edit…* on the approval card). The approver
may change the protocol and approve their own version instead of rejecting
it:
- **What can be edited,** with plain fields: each argument's `min`, `max`
  and `max_step` (empty: any step) or its choices; `max_calls`,
  `max_rate_hz` and the stream caps; removing a method or a whole device; the
  duration it asks for; and the safe state (change a value, remove a call,
  move it earlier, or add a call to a method the protocol lists).
  *Advanced: edit as JSON* shows the whole edited protocol for anything else.
- **The same checks as the agent's.** The approver is trusted to widen or
  narrow anything, but only within the device's and the lab's limits:
  `validate_protocol` runs on the edit against the project and the live
  network, and a removed method still used by the safe state is refused (the
  page drops those calls itself and lists them). The duration may be raised
  up to `LABNET_AUTOMATION_GRANT_MAX_MINUTES`; the grant may then be as long
  as the edit asks.
- ***Review changes…*** sends the edit to `POST …/protocol/preview`
  `{"sha256", "protocol"}`, which checks it without approving and answers
  `{"normalized", "sha256", "problems", "changes", "review"}`. Problems are
  shown beside the fields. Otherwise a confirmation lists the changes the
  server found, one line each (`dev01-aom.set_frequency.value.max: 90 → 85`,
  `dev01-aom.read_diffraction_efficiency: removed (may not be called)`,
  `safe_state: added dev01-aom.set_frequency(value=75)`).
- **Approving** sends `{"sha256", "duration_minutes", "protocol",
  "edited_sha256"}`: `sha256` is the submission the edit started from (409 if
  another one is waiting now), `protocol` the previewed version and
  `edited_sha256` its sha256 (409 if the server's normalized copy differs).
  The server checks the edit again: one that no longer passes answers 422
  with `problems`, and nothing changes. An edit that changes nothing is a
  plain approval.
- **What is kept.** The edit is normalized and stored as
  `state/<project>/protocols/<run>-<n>-edited.json` (its canonical text, so
  the file's sha256 is its own); the agent's `<run>-<n>.json` stays as it
  was. The grant is created from the edit, under the edit's sha256, so
  `grant/token` hands the runner the edited protocol. The run's `protocol`
  record gains `approved_sha256` (the edit's), `edited_by` and `changes` (at
  most 60 lines, each clipped to 240 characters); the log says *approved …
  EDITED* with the changes.
- **The agent is told.** On resume the runner's prompt says the protocol was
  approved WITH EDITS, quotes the changes from the run record, and gives the
  edited bounds (the runner checks the protocol it got against
  `approved_sha256`). `grant_status` shows them too.
- Afterwards the run's page shows *Edited by … before approval* with the
  changes, the approved version's review, and the agent's submission under
  *As the agent submitted it*.

**Rejecting** (`{"note"}`, up to 2000 characters) returns the run to
`planning`. The note is in the run record (`protocol.note`), where the
runner reads it, and the agent may submit again, up to 3 protocols per run.

**Waiting.** A protocol not approved or rejected within
`LABNET_AUTOMATION_APPROVAL_HOURS` (24) fails the run. Meanwhile, the
runner keeps heartbeating; the 10-minute rule still applies.

**The run record** gains `phase` and `protocol`: `{submissions, status
(submitted | approved | rejected), sha256, title, duration_minutes,
submitted_at, reviewed_by, reviewed_at, approved_minutes, approved_sha256,
edited_by, changes, note}` for the latest submission. `approved_sha256` is
the version the grant enforces: `sha256` unless the approver edited it. It also gains `grant`, written by the GrantManager: `{id,
status (active | ending | ended | ended_unsafe | interrupted), approved_by,
starts_at, ends_at, devices, calls, refused_calls, ended_reason, ended_at}`.
- A run whose grant ended `ended_unsafe` ends `ended_unsafe` however it
  finishes (`done`, `failed`, `stopped` or `cancelled`), and keeps its
  summary.
- Run records from before Phase 3 load with no phase.

| Request | Does |
| --- | --- |
| `POST /api/automation/runner/runs/<run>/protocol` `{}` | Checks `Protocols/<task>.protocol.json` and sends it for approval (runner token) |
| `GET /api/automation/projects/<id>/runs/<run>/protocol` | `{"protocol" (normalized), "sha256", "review", "prose" (the agent's, untrusted), "status", "submission", "phase", "run_status", "max_minutes", "approved_sha256", "edited_by", "changes"}`; with an edit also `"approved_protocol"`, `"approved_review"` |
| `POST …/runs/<run>/protocol/preview` `{"sha256", "protocol"}` | Checks an approver's edit without approving: `{"normalized", "sha256", "problems", "changes", "review"}` |
| `POST …/runs/<run>/protocol/approve` `{"sha256", "duration_minutes", "approved_by"?, "protocol"?, "edited_sha256"?}` | Approves (the edit, if sent); starts the grant; `{"run", "grant"}` |
| `POST …/runs/<run>/protocol/reject` `{"note", "sha256"?, "rejected_by"?}` | Rejects; the run is planning again |

## Device grants and the gateway

When a person approves a run's protocol, the Control Center creates a
**grant** (`automation_grants.py`). A grant holds the protocol's bounds, a
time limit (at most what the protocol asked for), and the leases on every
device it names. It is the only way an agent's script reaches hardware, and
every call is checked against it.

**Creating a grant.** Every lease is taken up front, and each device's live
type must match the protocol's `device_type`. If one device is busy or
mismatched, the leases already taken are released and approval answers 409.
If the Control Center starts shutting down while the leases are taken, they
are released and no grant is made. The grant's token (`lng_…`) exists only
in memory until the runner takes it once (`POST runs/<run>/grant/token`),
together with the normalized protocol the person approved, from which the
runner tells the agent its bounds. `state/<project>/grants/<id>.json` keeps
only its sha256.

**Every call** goes to `POST /api/automation/runner/gateway/calls` with
`Authorization: Bearer lng_…`:
- The body is at most 16 KiB of `application/json`:
  `{"device_id", "method", "args": {…}, "sample_rate_hz"?, "sample_count"?}`.
- A browser's request (`Sec-Fetch-Site`) is refused. The path is under the
  runner prefix, so it needs no Caddy login, and the grant token is its only
  credential.
- An allowed call answers `{"result", "calls_left", "seconds_left"}`. A
  stream's result is its list of values.
- Anything else answers 4xx `{"detail", "code"}`:

| Code | HTTP | Meaning |
| --- | --- | --- |
| `refused` | 422 (413/415 for the body, 403 for a browser) | Outside the approved protocol. Final: fix the script, don't retry. |
| `busy` | 409 | A call to that device is still running (one at a time per device). |
| `expired` | 410 | The grant's time is up. |
| `ended` | 410 (401 for an unknown token) | Stopped, ended by the agent, too many violations, or the run ended. |
| `device_error` | 424 | The device failed. The message is redacted: no lease ids, keys or tokens. |

**What the gateway checks.** Anything it does not expect is refused, and a
malformed protocol refuses rather than allows.
- The grant is active and has time left.
- The device is in the grant, and the method is listed for it.
- `args` is a JSON object of exactly the method's keyword arguments:
  - every required one is there;
  - every one the protocol bounds is there (a bounded `channel` left out
    would drive the driver's default channel);
  - an optional one only if the protocol bounds it;
  - never `unit`, and nothing else.
- Numbers:
  - they are JSON numbers (`true` is not a number), finite, and within
    `[min, max]`;
  - a whole-number argument takes only integers;
  - with `max_step`, a number is at most that far from where that setting
    may be now. "That setting" is the device, method, argument **and the
    call's choice arguments** (`dev02-awg.set_amplitude.value|channel=CH2`),
    so alternating channels can't double a step. Before the first change the
    gateway reads it with the matching reader and the same choices
    (`read_amplitude(channel="CH2")`): with the grant's lease, not counted
    against the protocol's calls, audited as a `reading` line. A reading that
    fails (or isn't a number) refuses the change (`device_error`, the change
    not sent). A call that succeeded sets the position to its value; one
    whose outcome is uncertain (a device error after sending, a timeout, the
    runner hanging up) widens it to `[min(old, sent), max(old, sent)]`, and
    the next value must be within `max_step` of both ends.
- Booleans and text must be one of the approved `choices`. A boolean must be
  a real JSON `true`/`false`.
- A stream sends `sample_rate_hz` (above 0, at most `max_sample_rate_hz`)
  and an integer `sample_count` (1 … `max_samples`), and
  `sample_count / sample_rate_hz` must fit in the time left. Other methods
  send neither.
- Each method keeps to its `max_calls` and to `max_rate_hz`: at least
  1/`max_rate_hz` seconds since that method's previous call.
- Each call is cut off when the grant's time runs out.

**Audit and violations.** Every call is one line in
`state/<project>/audit/<run>/grant-<id>.jsonl`, whether it was allowed or
not, including a body refused before it could be read (not JSON, too large,
the wrong media type, a number too long), which also counts as a refused
call once the token is known. A line has the arguments, the outcome and the
duration (and the result, or the number of samples). Ten refused calls in a
row end the grant. A `busy` reply or a device error during a call doesn't
count toward that; a change refused because its reading failed does.

**Lapsed leases.** The leases renew themselves while the grant holds them.
Central's idle timeout (15 minutes without a command) can still end one.
Then the gateway takes the lease again once. It retries the call only when
the call never left the Control Center, or when the call only reads. A write
or command the Lab PC refused is not sent twice. If the lease can't be taken
again, the grant ends.

**Ending.** A grant ends when:
- its time runs out;
- someone presses **Stop**;
- ten calls in a row are refused;
- the run is cancelled, fails, goes quiet or finishes;
- the agent ends it (`POST runs/<run>/grant/end {"reason"}`);
- the Control Center shuts down.

It always ends the same way:
1. New calls are refused at once (`ended`/`expired`).
2. Calls in flight are cancelled.
3. The protocol's `safe_state` calls are made. Each gets up to three tries,
   and a lost lease is taken again once.
4. The leases are released, and the end is written to the audit file.
5. The grant becomes `ended`. If any safe-state call failed, it becomes
   `ended_unsafe`, and the run is marked `ended_unsafe` with a red banner:
   check those devices by hand.
6. The run moves on to `analyzing`, unless it has already ended.

If the Control Center dies or hangs mid-grant, it can't do any of this
itself: its leases lapse about a minute later. The
[safe-state watchdog](#the-safe-state-watchdog), a separate process, then
applies the safe state. At the Control Center's next start, grants left
`active` are marked `interrupted`, unless the watchdog has already finished
with them; then its `ended`/`ended_unsafe` stands.

**While a grant holds a device:**
- The Network and Devices pages show *Automation: <project>/<task> · ends in
  N min*, with a **Stop** button anyone viewing can press, and a link to the
  project.
- The device's own page refuses Play and scripts with 409. So does a script
  on another page that tries to lease it.

| Request | Does |
| --- | --- |
| `POST /api/automation/runner/runs/<run>/grant/token` `{}` | The grant's token, `grant` and the approved `protocol`, once (runner token; 409 after that) |
| `POST /api/automation/runner/runs/<run>/grant/end` `{"reason"}` | Ends the grant now (safe state, release); `{"grant"}` |
| `POST /api/automation/runner/gateway/calls` | One device call, with the grant token (above) |
| `POST /api/automation/projects/<id>/runs/<run>/grant/stop` `{}` | Stop from the project page (anyone; same-origin JSON) |
| `POST /api/automation/grants/<grant_id>/stop` `{}` | Stop from the Network/Devices page (anyone; same-origin JSON) |

### The safe-state watchdog

`labnet-watchdog` is a small separate process on
`central_server_pc` (`labnet_control_center/watchdog/`). If the Control Center
dies, or its event loop hangs, while a grant holds devices, the watchdog
applies that grant's safe state. It never fights a live Control Center:
Central lets only one of them hold a device's lease at a time.

**What the Control Center writes for it.** While a grant holds its leases
(`active` or `ending`), a task on the Control Center's event loop rewrites
`state/<project>/grants/<id>.json` every 5 s with a fresh `alive_at`, so a
hung loop stops the heartbeat. The record holds everything the watchdog
needs: the approved normalized protocol (devices, types, safe-state calls),
its sha256, `ends_at` and the status. If the heartbeat can't be written
three times in a row, the Control Center ends the grant itself, with its safe
state.

**When the watchdog acts.** It reads every grant record every 2 s:
- A grant is taken over when its heartbeat hasn't changed for 20 s (timed on
  the watchdog's own clock) and either:
  - the Control Center's `/health/live` hasn't answered for 20 s either (it
    died); or
  - it answers but the heartbeat stays stale for 40 s (its loop hung).
- A grant the Control Center marked `interrupted` at a restart is taken over
  at once: the process that held it is gone.
- Never:
  - an `ended` or `ended_unsafe` grant;
  - one it has already finished;
  - one with no heartbeat for more than an hour, which is only logged
    (`LABNET_WATCHDOG_MAX_AGE_MINUTES`; check those devices by hand).
- Expired grants (time ran out while the Control Center was dead) are safed
  like the others.

**Taking over.** The watchdog writes only its own claim file,
`<id>.watchdog.json` next to the record, and lines of `"kind": "watchdog"`
in the grant's audit file. Two processes never write the same file.
1. The claim becomes `claimed`.
2. It asks Central for each device the safe state needs, every 3 s for up to
   120 s. Central refuses while the Control Center's lease lives.
3. If the Control Center's heartbeat comes back before a lease is taken, or
   it ends the grant itself, the watchdog **stands down** (`stood_down`).
   The same happens when nothing was leased in 120 s and the Control Center
   still answers.
4. With the first lease, the claim becomes `safing`. From then on the grant
   is the watchdog's, and a Control Center that comes back hands it over: it
   refuses calls, cancels calls in flight and releases its leases without a
   safe state of its own.
5. The protocol's sha256 is checked against the approved one, and each
   safe-state call is checked against its bounds as the gateway checks it.
   Each call gets up to three tries, re-taking a lost lease once. Then every
   lease is released.
6. The claim ends `ended` (*applied by the watchdog: the Control Center stopped
   answering*) or `ended_unsafe` (a call failed, or a device couldn't be
   leased in time: check it by hand).
7. The Control Center copies the outcome into the grant's record
   (`safe_state_by: "watchdog"`) and into the run, whose red banner shows an
   `ended_unsafe`. If it is down, its next start does this.

A watchdog stopped mid-takeover releases what it holds. If its claim was
`safing`, the next watchdog finishes it.

**Set it up on `central_server_pc`:**
1. Its own Central user (role `user`, never the Control Center's key):

   ```powershell
   $admin = "C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe"
   & $admin --env-file C:\ProgramData\LabNet\central.env setup `
       --user automation-watchdog --write-env C:\LabNet-Keys --server-url http://127.0.0.1:8008
   ```

2. Move `C:\LabNet-Keys\automation-watchdog.env` to
   `C:\ProgramData\LabNet\watchdog.env`, then delete `C:\LabNet-Keys`. Add
   the lines from [`sites/pqt/watchdog.env.example`](https://github.com/JavadiGroup/labnet/blob/main/sites/pqt/watchdog.env.example):
   at least `LABNET_AUTOMATION_DIR`, the Control Center's own (default
   `C:\ProgramData\LabNet\automation`), and `LABNET_CA_FILE` if
   `control-center.env` sets one.
3. Run it as the same Windows account as the Control Center, which owns that
   folder. The command is installed with the Control Center:

   ```powershell
   C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-watchdog.exe --env-file C:\ProgramData\LabNet\watchdog.env
   Invoke-RestMethod http://127.0.0.1:8081/health/ready
   ```

   Start it by hand in its own window, after the Control Center (see
   [Starting the lab by hand](../deploying/index.md#starting-the-lab-by-hand)). To start it
   automatically at logon instead, register a Task Scheduler task. It has no
   window, so it writes a log file:

   ```powershell
   New-Item -ItemType Directory -Force C:\ProgramData\LabNet\logs | Out-Null
   $exe = "C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-watchdog.exe"
   $action = New-ScheduledTaskAction -Execute "cmd.exe" `
       -Argument "/c $exe --env-file C:\ProgramData\LabNet\watchdog.env >> C:\ProgramData\LabNet\logs\watchdog.log 2>&1"
   $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
       -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
   Register-ScheduledTask -TaskName "LabNet safe-state watchdog" -Action $action -Trigger $trigger -Settings $settings
   ```

   `Start-ScheduledTask "LabNet safe-state watchdog"` starts it now, and
   `Stop-ScheduledTask` stops it.
   - Only one watchdog runs per computer: a second one can't take port 8081
     and exits.
   - It doesn't matter whether it starts before or after the Control Center.
     Stop it before an update, like the Control Center.

**In development,** `labnet dev seed` creates the user and
`.devnet/automation-watchdog.env`. `labnet dev up` starts the watchdog
after the Control Center (log: `.devnet/logs/watchdog.log`,
health: `http://127.0.0.1:8081/health/ready`). `labnet dev down` stops it first,
`labnet dev status` shows it, and `labnet dev restart watchdog` restarts it. A
network seeded before the watchdog existed gets its user with `labnet dev seed
--watchdog`, which deletes nothing.
