# Logins

In the lab, each person signs in with their own login. Caddy checks it
([`basic_auth`](https://caddyserver.com/docs/caddyfile/directives/basic_auth),
Caddy 2.8 or later) and adds two headers to every request it forwards:

| Header | Value |
| --- | --- |
| `X-LabNet-Proxy` | `LABNET_CONTROL_CENTER_PROXY_SECRET`, shared by Caddy and the Control Center |
| `X-LabNet-User` | The login name |

With the secret set, the Control Center refuses (403) any request without
it. A program on the server that dials `127.0.0.1:8080` directly gets
nowhere, so the user name can be trusted:
- the top bar shows "Signed in as <name>";
- it is the author of experiments and projects, and who queued or
  cancelled a run (the *Created by* box is hidden);
- later, it is who approved a protocol.

There is no sign-out button: the browser remembers the login until it is
closed.

**Setting up or changing logins.** On `central_server_pc`, as the Windows
account that runs Caddy and the Control Center (otherwise name theirs with
`-ServiceAccount`):

1. List the logins in `C:\ProgramData\LabNet\logins.txt`, one per line.
   Names use letters, digits, `.`, `_` and `-` (at most 64); `#` starts a
   comment. Names that differ only in case are refused.
2. Copy `scripts/lab/setup_logins.ps1` from the repository (the release
   doesn't include it) and run it:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup_logins.ps1 -WhatIf   # shows what would change
   powershell -ExecutionPolicy Bypass -File .\setup_logins.ps1
   ```

3. Hand each new password to its owner. They are shown only this once.
4. Restart Caddy. If the script made a new secret, restart the Control
   Center too, so both use it.

Run it again whenever someone joins or leaves, or forgets their password.
Existing logins keep their passwords; new ones get a random password
(20 characters, no look-alikes); logins no longer listed are removed.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `-UsersFile` | `C:\ProgramData\LabNet\logins.txt` | The logins file |
| `-CaddySnippet` | `C:\ProgramData\LabNet\caddy-logins.caddy` | The snippet it writes |
| `-EnvFile` | `C:\ProgramData\LabNet\control-center.env` | Where it sets `LABNET_CONTROL_CENTER_PROXY_SECRET`; every other line stays as it is |
| `-Caddy` | `caddy` on `PATH` | Path to `caddy.exe`, used for `caddy hash-password` |
| `-Reset` | none | Logins that get a new password, e.g. `-Reset alice,bob` |
| `-RunnerAddress` | any address, or what an earlier run was given | IP addresses or CIDR ranges allowed to use the runner API, e.g. the runner laptop's (full dotted form) |
| `-AnyRunnerAddress` | | Removes an earlier `-RunnerAddress` restriction |
| `-Upstream` | `127.0.0.1:8080`, or what an earlier run was given | Where Caddy forwards to |
| `-ServiceAccount` | none | Accounts that may read the two files besides yours, e.g. `'NT AUTHORITY\NetworkService'` if Caddy runs as a service |
| `-Caddyfile` | `C:\ProgramData\LabNet\Caddyfile` | Only checked: the script warns if it doesn't say `admin off` |
| `-WhatIf` | | Writes nothing and says what would change |

It checks that it can write both files before writing either, writes the
env file first, and prints new passwords as soon as their hashes are in
the snippet, so a failure halfway never loses a password. Passwords reach
`caddy hash-password` on its standard input, never on a command line.

The script keeps a secret already in the env file if it is at least 32
characters of `A–Z a–z 0–9 . _ ~ + / = -`; otherwise it makes a new one.

**The snippet** defines `(labnet_control_center)`. The Caddyfile imports the
file at the top and uses `import labnet_control_center` in the Control
Center's site block (step 3 above). It holds bcrypt hashes, never
passwords, and the proxy secret, which `control-center.env` holds too.
So the script restricts both files to Administrators, SYSTEM, the account
that ran it and any `-ServiceAccount`, and refuses either file if another
account owns it (anyone may create files in `C:\ProgramData`, and an owner
can always grant themselves access again). Inside the snippet, three
blocks, in order:

| Path | Login | Headers |
| --- | --- | --- |
| `/health/*` | none | Both `X-LabNet-*` headers removed |
| `/api/automation/runner/*` (from `-RunnerAddress` only, if given) | none | Both `X-LabNet-*` headers removed |
| everything else | `basic_auth` | `Authorization` removed; `X-LabNet-Proxy` and `X-LabNet-User` added |

**What is exempt, and why.**
- **Health checks**, so monitoring and the check step work without a login.
  They show only whether the Control Center is up and reaching Central.
- **The runner API**, because a runner is a program, not a person: it
  proves itself with a [runner token](../automation/runner.md#runners-and-their-tokens) instead.
  It refuses anything a browser sends. With `-RunnerAddress`, the runner
  API from any other address falls through to the login, which strips the
  token, so it can't be used from there.

The Control Center exempts the same two prefixes (lowercase, exactly). Caddy
removes the two headers there, so they can't be forged.

**Caddy's admin API must be off** (`admin off` in the Caddyfile's first
block, as in [`sites/pqt/Caddyfile.example`](https://github.com/mill0851/labnet/blob/main/sites/pqt/Caddyfile.example)). Caddy
otherwise answers on `localhost:2019` without a password: any program on the
server could read the running config, secret included, or load one without
logins.

**Whatever the mode,** a change (POST, PUT, PATCH, DELETE) that a browser
says came from another web site is refused (403). The Experiments and
Automation APIs also take changes only as JSON or a raw upload, which a
cross-site form can't send. A page elsewhere can't pause a device, start a
script, or edit an experiment through someone's open tab.

**Development has no logins.** Without `LABNET_CONTROL_CENTER_PROXY_SECRET`
anyone who can reach the port is trusted, the headers are ignored (anyone
could send them), and the *Created by* name typed on the page is recorded.
That is safe only because the server binds to loopback on a one-person
computer.

**The host check.** The pages answer only to the host names in
`LABNET_CONTROL_CENTER_ALLOWED_HOSTS`; any other `Host` gets 400 "Invalid
host header". This stops a web page from pointing its own name at
`127.0.0.1` and reading the Control Center through a browser.
- Without the secret, the default is `127.0.0.1,localhost`.
- With the secret, the default is any host: the secret already stops that
  trick, and Caddy forwards the name people typed.

**Upgrading a lab install without logins.** Caddy passes on the name people
typed (`lab-control-center` or `192.168.50.10`), which the loopback-only
default refuses. Either set up logins, or add
`LABNET_CONTROL_CENTER_ALLOWED_HOSTS=lab-control-center,192.168.50.10` to
`control-center.env` before restarting the Control Center.

| Symptom | Cause | Fix |
| --- | --- | --- |
| 400 "Invalid host header" | The name used isn't allowed | Set `LABNET_CONTROL_CENTER_ALLOWED_HOSTS`, or set up logins |
| 403 "Open the Control Center through its sign-in address (Caddy)." | No secret, or a different one: port 8080 opened directly, a site block with its own `reverse_proxy` instead of `import labnet_control_center`, or Caddy and the Control Center not both restarted after a new secret | Open it through Caddy; fix the site block; restart both |
| 403 "The sign-in proxy sent no usable user name." | A hand-edited Caddyfile sends the secret without a login | Use the snippet `setup_logins.ps1` writes, unchanged |
| The Control Center won't start: "…PROXY_SECRET must be at least 32…" | A short or hand-made secret | Run `setup_logins.ps1` again |
