# labnet-automation

The LabNet Automation runner (`labnet-runner`). It claims runs from a
Control Center, runs the agent and its scripts in locked-down containers,
and reaches lab devices only through a grant a person approved.

The container images are built from [`docker/`](docker/README.md) on a Linux
Docker host:

```
docker compose -f packages/labnet-automation/docker/compose.yaml build
```

See "Automation runner" in the [labnet README](../labnet/README.md).
