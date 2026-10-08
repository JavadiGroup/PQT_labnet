# docker/automation: the Automation runner's containers

The build context for every image is the repository root (`compose.yaml` uses `context: ../..`).
Each Dockerfile has its own `Dockerfile.<name>.dockerignore`, which sends only `Labnet/` (no vendor DLLs) and the files below that the image needs.

| File | What it is |
| --- | --- |
| `compose.yaml` | Project `labnet-automation`. It defines the `agent` network (internal), the `egress` network, the `claude-login` volume, the `egress` proxy service, the `runner` service, the `login` service (profile `login`) and two build-only entries. `docker compose build` builds all three images. |
| `Dockerfile.runner` | `labnet-automation-runner:latest`: Python 3.11, the docker CLI and `labnet[runner]`. It runs `labnet-runner run`, and the `egress` service runs the proxy from this same image. |
| `Dockerfile.agent` | `labnet-automation-agent:latest`: Node 22, Claude Code (pinned by `ARG CLAUDE_CODE_VERSION`) and a venv at `/opt/labnet` with labnet plus `mcp` and `httpx` for the MCP server. It runs as user `node` (uid 1000) with `CLAUDE_CONFIG_DIR=/claude` and has no entrypoint. |
| `Dockerfile.run` | `labnet-automation-run:latest`: Python 3.11 with the pinned analysis packages, `labnet_devices`, uid 1000 and `MPLBACKEND=Agg`. Each `run_python` call gets one of these containers, which is deleted afterwards. |
| `labnet_devices.py` | The device shim installed into the run image's site-packages (standard library only). While an approved experiment's grant is active, `labnet_devices.connect()` reaches the runner's device relay at `/run/labnet-devices/gateway.sock`; it holds no token. |
| `requirements-run.txt` | Pinned numpy, scipy, pandas and matplotlib for the run image. |
| `egress-allow.txt` | The hosts agent containers may reach through the proxy: `api.anthropic.com`, `platform.claude.com` and `claude.ai`. Each entry, and each host deliberately left out, has a comment. |

## Commands

```sh
docker compose -f docker/automation/compose.yaml build
docker compose -f docker/automation/compose.yaml run --rm login        # once: /login in Claude Code
docker compose -f docker/automation/compose.yaml --env-file /etc/labnet/runner.env up -d
docker compose -f docker/automation/compose.yaml run --rm runner labnet-runner self-check
```

Set `LABNET_RUNNER_WORK_DIR` and `LABNET_RUNNER_ENV_FILE` either in the environment or in the file passed with `--env-file`. They default to `/var/lib/labnet-runner` and `/etc/labnet/runner.env`. The runner mounts its work folder at the same path inside its container, so the paths it gives to `docker run -v` are valid paths on the host.

## Isolation in brief

The runner applies these flags in `Labnet/labnet/automation_runner/docker_backend.py`, and the self-check (`selfcheck.py`) confirms them on the real host:

- **Agent containers** run on the `internal` network, and the egress proxy is their only way out. The proxy allows only CONNECT to port 443 on hosts in the allow-list, and only to public addresses. The containers run as uid 1000 with a read-only root, `--cap-drop ALL` and no-new-privileges, under pid, memory and CPU limits. They hold no runner token; their tools reach the supervisor through a Unix socket.
- **Run containers** have `--network none`. Only the working copy is mounted, they have no credentials, and they have CPU, memory (no swap), pid and time limits. Their output is captured by the runner; Docker's log driver is off. While a device grant is active, and only then, the runner also mounts its device relay's folder at `/run/labnet-devices`: the relay adds the grant's token to each call and the Control Center's gateway checks it against the approved protocol.
- **The runner** holds the token and the Docker socket. Nothing else gets either one.
