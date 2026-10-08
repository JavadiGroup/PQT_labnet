# labnet-control-center

The LabNet Control Center: a web interface for a lab network. Experiments,
device panels and scripts, Photo-Agent, and the Automation tab with its
approval flow. It also ships the safe-state watchdog (`labnet-watchdog`),
which puts devices into an approved protocol's safe state when a run's
grant ends.

```
pip install labnet-control-center
labnet-control-center --env-file control-center.env
```

It needs a LabNet Central to talk to; see the
[labnet README](../labnet/README.md) for deploying a lab.
