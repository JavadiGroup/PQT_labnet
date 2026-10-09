# labnet-control-center

The LabNet Control Center: a web interface for a lab network. Live device
pages, saved experiments and scripts, and two optional parts: Photo-Agent,
an AI assistant in a chat sidebar, and the Automation tab, where an agent
runs experiments within limits a person approves. It also ships the
safe-state watchdog (`labnet-watchdog`), which puts devices into an
approved protocol's safe state if the Control Center stops mid-run.

```bash
pip install labnet-control-center
labnet-control-center --env-file control-center.env
```

It needs a LabNet Central to talk to. To try it with a simulated lab:

```bash
pip install "labnet[central,lab-pc]" labnet-sim labnet-control-center
labnet dev up          # then open http://127.0.0.1:8080/status
```

Documentation: [the Control Center](https://mill0851.github.io/labnet/control-center/),
[logins](https://mill0851.github.io/labnet/control-center/logins/), and
[Automation](https://mill0851.github.io/labnet/automation/) with its
[security model](https://mill0851.github.io/labnet/automation/security/).

LabNet is MIT licensed. Source: <https://github.com/mill0851/labnet>.
