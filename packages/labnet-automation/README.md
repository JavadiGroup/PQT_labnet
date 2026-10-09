# labnet-automation

The LabNet Automation runner (`labnet-runner`). It claims runs from a
Control Center, runs the agent and its scripts in locked-down containers
that reach only the Control Center and the model API, and touches lab
devices only through a grant a person approved.

The runner runs in Docker, from the images in this package's
[`docker/`](https://github.com/JavadiGroup/labnet/tree/main/packages/labnet-automation/docker)
folder: on a Linux PC in the lab, or in WSL next to a development network:

```bash
labnet runner build        # the images
labnet runner login        # once: sign Claude in
labnet runner up           # issue a runner token and start it
labnet runner self-check   # check its isolation
```

Documentation: [the runner](https://javadigroup.github.io/labnet/automation/runner/)
and the [security model](https://javadigroup.github.io/labnet/automation/security/).

LabNet is MIT licensed. Source: <https://github.com/JavadiGroup/labnet>.
