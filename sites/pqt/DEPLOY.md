# Deploying the PQT lab on atto-pc

A checklist for the first deployment, with this lab's values filled in. It
follows LabNet's [Deploying a lab](../../docs/deploying/index.md); go there
for the why behind each step.

atto-pc (192.168.1.149) runs all four parts, and the WS7, the Sacher VIS laser
and the Horiba spectrometer are plugged into it:

| Part | Reached at | Window |
| --- | --- | --- |
| Central, behind Caddy | `https://192.168.1.149` (port 443) | Central, Caddy |
| Control Center, behind Caddy | `https://192.168.1.149:8443` | Control Center |
| Lab PC `atto-pc` (`ws7`, `sacher-vis`, `horiba-spec-vis`) | `192.168.1.149:50051` (gRPC) | Lab PC |

Automation and Photo-Agent stay off for now (`control-center.env`), so there
is no safe-state watchdog. Turning them on later is in the workspace `TODO.md`.

**Placeholders.** Replace before running:
- `<admin>`: the LabNet administrator's user name;
- `<member-1>`, `<member-2>`, …: lab members who run scripts from their own PCs;
- `<bundle>`: the bundle's folder name, e.g. `pqt-2026-11-02`.

The **development PC** is the one with `PQT_workspace` and its `.venv`. If that
is atto-pc itself, skip the copying steps.

## 0. Before you start (atto-pc)

- [ ] 192.168.1.149 is static, or reserved for atto-pc in DHCP. The certificate
      holds this IP, so a new IP means a new certificate.
- [ ] **Python 3.13** from python.org, with the `py` launcher: `py -3.13 --version` works.
- [ ] **Caddy 2.8 or later**: `caddy version` works in PowerShell (put `caddy.exe` on `PATH`).
- [ ] **Instrument software**, each tested on its own before LabNet:
  - [ ] **WS7:** the HighFinesse wavelength meter software. The driver starts it if needed.
  - [ ] **Sacher:** maxon's EPOS2 USB driver; the EPOS2 controller shows in Device Manager.
  - [ ] **Horiba:** LabSpec 6 with its remote (TCP) server on `127.0.0.1:1234`.
        LabSpec must be running before the Lab PC starts.
  - [ ] The examples in `PQT_projects/device_testing/` work on atto-pc. Run them only
        while you are at the instruments: the Sacher example moves the laser.

## 1. Certificate (development PC)

In PowerShell, with the workspace `.venv` active:

```powershell
labnet-certs init-ca --dir C:\LabNet-CA                       # asks for a passphrase: keep it in the lab's password manager
labnet-certs issue atto-pc --ip 192.168.1.149 --dir C:\LabNet-CA
labnet-certs show C:\LabNet-CA\atto-pc.crt --ca C:\LabNet-CA\labnet-ca.pem
```

- [ ] Copy `atto-pc.crt`, `atto-pc.key` and `labnet-ca.pem` to atto-pc's
      `C:\ProgramData\LabNet\certs\`. One certificate serves Caddy (both
      sites) and the Lab PC.
- [ ] Restrict `atto-pc.key` (Properties → Security → Advanced: no inheritance;
      only Administrators and the account that runs LabNet).
- [ ] Never copy `labnet-ca.key` anywhere. Keep `C:\LabNet-CA` in encrypted
      offline storage; it is needed again only to renew the certificate (yearly).

## 2. Bundle (development PC)

The bundle holds LabNet, `pqt_drivers`, PQT_devices and every dependency, so
atto-pc installs offline. Build it from up-to-date `main` branches:

```powershell
cd C:\Users\optics-user\Desktop\PQT_workspace
git -C PQT_labnet switch main; git -C PQT_labnet pull
git -C PQT_devices switch main; git -C PQT_devices pull     # must be the version pqt_drivers pins (pqt-devices==1.0.1)
cd PQT_labnet
labnet bundle --site sites/pqt --python 3.13 --driver drivers/pqt_drivers --driver ..\PQT_devices --driver packages/labnet-sim --out dist\<bundle>
```

- [ ] Copy `dist\<bundle>` to atto-pc, e.g. `C:\LabNet-Releases\<bundle>`.

Every install below uses (in each new PowerShell window on atto-pc):

```powershell
$install = 'C:\LabNet-Releases\<bundle>\install.ps1'
$admin = 'C:\ProgramData\LabNet\central-venv\Scripts\labnet-central-admin.exe'
```

## 3. Central (atto-pc)

- [ ] Save `central.env.example` (in the bundle's `site-pqt.zip`, or this folder)
      as `C:\ProgramData\LabNet\central.env`. It needs no changes.
- [ ] Install: `powershell -ExecutionPolicy Bypass -File $install -Role central`
- [ ] Create the users and their settings files:

  ```powershell
  # The Control Center and the Lab PC reach Central on this computer, over loopback.
  & $admin --env-file C:\ProgramData\LabNet\central.env setup --user control-center --lab-pc atto-pc `
      --write-env C:\LabNet-Keys --server-url http://127.0.0.1:8008
  # People reach it over the network, through Caddy.
  & $admin --env-file C:\ProgramData\LabNet\central.env setup --admin <admin> --user <member-1> --user <member-2> `
      --write-env C:\LabNet-Keys --server-url https://192.168.1.149 --ca-file C:/ProgramData/LabNet/certs/labnet-ca.pem
  ```

  `C:\LabNet-Keys` now holds one `.env` file per user, each with its API key.
  They are the only copies (Central keeps only hashes): steps 4, 6 and 8 move
  them, then delete the folder.
- [ ] Start **Central** in its own window and leave it open:

  ```powershell
  C:\ProgramData\LabNet\central-venv\Scripts\labnet-central.exe --env-file C:\ProgramData\LabNet\central.env
  ```

  In another window, `Invoke-RestMethod http://127.0.0.1:8008/health/ready` shows `status: ok`.

## 4. Control Center (atto-pc)

- [ ] Move `C:\LabNet-Keys\control-center.env` to `C:\ProgramData\LabNet\control-center.env`,
      and add the lines from `control-center.env.example` below its two Central
      settings (site name, Automation and Photo-Agent `false`, host and port 8080).
- [ ] Install: `powershell -ExecutionPolicy Bypass -File $install -Role control-center`
- [ ] **Logins.** List the people who use the web pages in
      `C:\ProgramData\LabNet\logins.txt`, one name per line (`<admin>`,
      `<member-1>`, …). Copy `PQT_labnet\scripts\lab\setup_logins.ps1` from the
      development PC and run it on atto-pc:

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\setup_logins.ps1 -WhatIf   # shows what it would change
  powershell -ExecutionPolicy Bypass -File .\setup_logins.ps1
  ```

  It writes `C:\ProgramData\LabNet\caddy-logins.caddy`, sets the proxy secret
  in `control-center.env`, and prints each password once: hand them out.
  ([Logins](../../docs/control-center/logins.md) has the details.)
- [ ] Start the **Control Center** in its own window and leave it open:

  ```powershell
  C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-control-center.exe --env-file C:\ProgramData\LabNet\control-center.env
  ```

## 5. Caddy and the firewall (atto-pc)

- [ ] Save `Caddyfile.example` as `C:\ProgramData\LabNet\Caddyfile` (no changes).
- [ ] `caddy validate --config C:\ProgramData\LabNet\Caddyfile`
- [ ] Start **Caddy** in its own window and leave it open:
      `caddy run --config C:\ProgramData\LabNet\Caddyfile`.
      To restart it, press Ctrl+C and run it again (`caddy reload` doesn't work with `admin off`).
- [ ] Open the three ports, in PowerShell **as Administrator**:

  ```powershell
  New-NetFirewallRule -DisplayName "LabNet Central (443)" -Direction Inbound -Protocol TCP -LocalPort 443 -RemoteAddress LocalSubnet -Action Allow
  New-NetFirewallRule -DisplayName "LabNet Control Center (8443)" -Direction Inbound -Protocol TCP -LocalPort 8443 -RemoteAddress LocalSubnet -Action Allow
  New-NetFirewallRule -DisplayName "LabNet Lab PC gRPC (50051)" -Direction Inbound -Protocol TCP -LocalPort 50051 -RemoteAddress LocalSubnet -Action Allow
  ```

  Never open 8008, 8080 or 9001: they stay on loopback.

## 6. Lab PC (atto-pc)

- [ ] Move `C:\LabNet-Keys\atto-pc.env` to `C:\ProgramData\LabNet\lab-pc.env`,
      and add the lines from `lab-pc.env.example` below its first three settings
      (`LABNET_ENVIRONMENT`, the gRPC address and certificate, the inventory).
- [ ] Install: `powershell -ExecutionPolicy Bypass -File $install -Role lab-pc`.
      It installs the site to `C:\ProgramData\LabNet\site\` and builds the
      three devices without connecting, so a missing driver shows here.
- [ ] Instruments on, LabSpec 6 running with its TCP server.
- [ ] Start the **Lab PC** in its own window and leave it open:

  ```powershell
  C:\ProgramData\LabNet\lab-pc-venv\Scripts\labnet-lab-pc.exe --env-file C:\ProgramData\LabNet\lab-pc.env
  ```

  `Invoke-RestMethod http://127.0.0.1:9001/health/ready` reports gRPC serving
  and each of the three devices.

## 7. Check from another computer

- [ ] Trust the lab's CA, once per viewing computer (PowerShell as Administrator):
      `certutil -addstore Root labnet-ca.pem`. Without it, browsers warn about the certificate.
- [ ] `curl.exe --cacert labnet-ca.pem https://192.168.1.149/health/ready` shows `status: ok`.
- [ ] `https://192.168.1.149:8443/status` asks for a login, then shows atto-pc online
      with `ws7`, `sacher-vis` and `horiba-spec-vis`.
- [ ] Each device page opens with its own panel (not just a script box):
      `https://192.168.1.149:8443/ws7`, `/sacher-vis`, `/horiba-spec-vis`.
- [ ] First touches that change nothing: **Refresh** on each page, **Play** on the WS7.
      Move, Rebase and the Horiba's settings change the instruments: use them
      only while someone is at the bench.

## 8. Lab members' PCs (clients)

For each `<member>`:
- [ ] Copy `labnet-ca.pem` to `C:\ProgramData\LabNet\certs\`, and
      `C:\LabNet-Keys\<member>.env` to `C:\ProgramData\LabNet\client.env`.
- [ ] `powershell -ExecutionPolicy Bypass -File <bundle path>\install.ps1 -Role client`
      (Python 3.13 needed there too).
- [ ] `[Environment]::SetEnvironmentVariable("LABNET_ENV_FILE", "C:\ProgramData\LabNet\client.env", "User")`

Keep `<admin>.env` with whoever administers LabNet, then **delete `C:\LabNet-Keys`**.

## Every day

Start the windows in this order on atto-pc; stop them in reverse (Ctrl+C).
Only Central has to come first: the others retry until it answers.

| Order | Window | Command |
| --- | --- | --- |
| 1 | Central | `C:\ProgramData\LabNet\central-venv\Scripts\labnet-central.exe --env-file C:\ProgramData\LabNet\central.env` |
| 2 | Caddy | `caddy run --config C:\ProgramData\LabNet\Caddyfile` |
| 3 | Control Center | `C:\ProgramData\LabNet\control-center-venv\Scripts\labnet-control-center.exe --env-file C:\ProgramData\LabNet\control-center.env` |
| 4 | Lab PC (LabSpec running first) | `C:\ProgramData\LabNet\lab-pc-venv\Scripts\labnet-lab-pc.exe --env-file C:\ProgramData\LabNet\lab-pc.env` |

**Updates** (a new driver sync, a LabNet release): build a new bundle as in
step 2, stop the windows, run `install.ps1` for each role in the order
central, control-center, lab-pc, and start them again. Settings files and the
database stay. See [Updating a lab](../../docs/deploying/updating.md),
including rolling back.
