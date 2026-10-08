"""The Automation runner: works through queued Automation runs, one at a time.

It runs on its own computer (in the lab: a Linux PC; in development: Docker
in WSL on the developer's laptop) and talks to the Control Center only over
its runner API, with a runner token. Nothing it does can write outside a
project's agent folders: the Control Center checks every upload.

    runner container (trusted; this package's supervisor)
      ├─ holds the runner token; claims runs; uploads results; streams events
      ├─ agent container per run: Claude Code + the labnet MCP tools
      │     network: an internal Docker network whose only way out is the
      │     egress proxy (Anthropic's API, nothing else); talks to the
      │     supervisor through a Unix socket in its run folder (no token)
      ├─ run container per run_python call: the working copy only,
      │     no network, no credentials, CPU/memory/process limits; while a
      │     device grant is active, also the device relay's socket
      └─ device relay (Phase 3): relays scripts' device calls to the Control
            Center's gateway, adding the grant's token itself

Modules:

* ``config``        RunnerSettings (env file / environment)
* ``backend``       the container interface; ``docker_backend`` implements it
* ``client``        the Control Center's runner API
* ``workcopy``      unpacking a run's inputs, finding its outputs
* ``events``        Claude Code's stream-json → run events
* ``tools_server``  what the agent's tools call (over the Unix socket)
* ``mcp_server``    the MCP server inside the agent container
* ``devices_relay`` the run's protocol submissions and grant; the device relay
* ``protocol_text``  a protocol's devices and bounds as text for the agent
* ``egress_proxy``  the allow-listing HTTPS proxy (its own container)
* ``selfcheck``     refuses to start unless the isolation really holds
* ``supervisor``    the claim → run → upload loop; ``labnet-runner``
"""
