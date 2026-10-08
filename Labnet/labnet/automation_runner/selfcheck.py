"""The runner's self-check: it refuses to work unless the isolation really holds.

    python -m labnet.automation_runner.selfcheck [--env-file FILE]

Each check runs on its own (one failing never hides another) and says in a
line what is wrong and what to do. They test the real thing, with throwaway
containers started exactly as runs start them:

* the Control Center answers, and knows this runner's token (whoami);
* Docker answers; the agent and run images exist (the runner image is the
  one this runs in); the agent network exists and is ``internal``;
* the Claude login volume exists and holds a saved login;
* a run container (``Backend.run_python``, so with its real flags) cannot
  open a connection to the internet, and sees its working copy but nothing
  else of the runner's (the work folder, a file next to its working copy,
  the Docker socket, the login);
* a container on the agent network cannot reach the internet directly nor
  the network's gateway (the host), reaches api.anthropic.com through the
  egress proxy (a TLS handshake completes), and is refused example.com (403);
* Claude Code runs in the agent image and has every flag the supervisor uses.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .backend import PythonRequest
from .config import RunnerSettings


SELFCHECK_RUN_ID = "run-00000000-000000-self"  # matches the Control Center's RUN_ID, never a real run's
AGENT_PYTHON = "/opt/labnet/bin/python"  # as in Dockerfile.agent (agent_config.AGENT_PYTHON)
CLAUDE_FLAGS = ("--max-budget-usd", "--setting-sources", "--agents", "--strict-mcp-config",
                "--append-system-prompt-file")
# Also on the supervisor's command line; reported, not required (--help may not list every option).
CLAUDE_OTHER_FLAGS = ("--tools", "--mcp-config", "--settings", "--permission-mode", "--session-id",
                      "--output-format", "--max-turns", "--effort")
PROBE_SECONDS = 120.0
# `claude --version` / `--help` with a scratch config folder: never the login volume, and the root is read-only.
SCRATCH_CLAUDE = {"CLAUDE_CONFIG_DIR": "/tmp/claude"}
GATEWAY_PORTS = (22, 80, 443, 8080)


def _listed(flag: str, help_text: str) -> bool:
    """``--help`` names an option, also in its short form: 2.1.x lists ``--append-system-prompt[-file]``."""

    return flag in help_text or (flag.endswith("-file") and f"{flag.removesuffix('-file')}[-file]" in help_text)
LOGIN_HINT = ("sign in once: `docker compose -f docker/automation/compose.yaml run --rm login`, "
              "then /login in Claude Code")


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


# What runs inside the throwaway containers. Each prints one JSON line.

RUN_PROBE = r'''
import json, os, socket, sys
out = {"uid": os.getuid()}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    out["internet"] = "open"
except OSError as exc:
    out["internet"] = "blocked (%s)" % type(exc).__name__
out["inside"] = os.path.isfile("/work/inside.txt")
out["visible"] = [p for p in sys.argv[1:] if os.path.exists(p)]
try:
    import labnet_devices
    out["shim"] = callable(getattr(labnet_devices, "connect", None))
except Exception as exc:
    out["shim"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out))
'''

AGENT_PROBE = r'''
import json, socket, ssl, sys
proxy_host, proxy_port, gateways = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
out = {}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    out["direct"] = "open"
except OSError as exc:
    out["direct"] = "blocked (%s)" % type(exc).__name__
out["gateway_open"] = []
for address in gateways:
    for port in (__PORTS__):
        try:
            socket.create_connection((address, port), timeout=3).close()
            out["gateway_open"].append("%s:%d" % (address, port))
        except OSError:
            pass
def connect(host):
    s = socket.create_connection((proxy_host, proxy_port), timeout=15)
    s.settimeout(15)
    s.sendall(("CONNECT %s:443 HTTP/1.1\r\nHost: %s:443\r\n\r\n" % (host, host)).encode())
    head = b""
    while b"\r\n\r\n" not in head and len(head) < 65536:
        chunk = s.recv(4096)
        if not chunk:
            break
        head += chunk
    parts = head.split(b" ", 2)
    return s, (parts[1].decode("ascii", "replace") if head.startswith(b"HTTP/") and len(parts) > 1 else "no answer")
for key, host in (("anthropic", "api.anthropic.com"), ("example", "example.com")):
    try:
        s, status = connect(host)
        out[key] = status
        if key == "anthropic" and status == "200":
            with ssl.create_default_context().wrap_socket(s, server_hostname=host) as tls:
                out["anthropic_tls"] = tls.version()
        else:
            s.close()
    except Exception as exc:
        out[key] = "error: %s: %s" % (type(exc).__name__, str(exc)[:200])
print(json.dumps(out))
'''.replace("__PORTS__", ", ".join(str(p) for p in GATEWAY_PORTS))

LOGIN_PROBE = r'''
import json, os
path = "/claude/.credentials.json"
out = {"exists": os.path.isfile(path), "readable": os.access(path, os.R_OK)}
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out["kinds"] = sorted(k for k in data if isinstance(k, str))[:5]
    oauth = data.get("claudeAiOauth") or {}
    out["refresh_token"] = bool(oauth.get("refreshToken"))
except Exception as exc:
    out["error"] = type(exc).__name__
print(json.dumps(out))
'''


def _last_json(text: str) -> dict[str, Any]:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("the probe printed no result")


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(text.strip().split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _Checks:
    def __init__(self, settings: RunnerSettings, backend: Any, client: Any) -> None:
        self.settings = settings
        self.backend = backend
        self.client = client
        self.results: list[Check] = []
        self.docker_ok = False

    def run(self, name: str, check: Callable[[], str], *, needs_docker: bool = True) -> None:
        """One check: its function returns the detail when it passes and raises when it fails."""

        if needs_docker and not self.docker_ok:
            self.results.append(Check(name, False, "not checked: Docker isn't reachable (see the docker check)."))
            return
        try:
            self.results.append(Check(name, True, check()))
        except Exception as exc:  # noqa: BLE001  (each check stands alone)
            message = str(exc) if isinstance(exc, (Failed, ValueError, RuntimeError)) else f"{type(exc).__name__}: {exc}"
            self.results.append(Check(name, False, _short(message, 500)))

    def docker(self, args: Sequence[str], timeout: float = 30.0):
        return self.backend.command(list(args), timeout=timeout)

    def isolated_run(self, image: str, network: str, command: Sequence[str], *, mounts: Sequence[str] = (),
                     env: dict[str, str] | None = None, timeout: float = PROBE_SECONDS):
        """A throwaway container with the agent's hardening (``docker run --rm``)."""

        args = ["run", "--rm", "--pull", "never", "--network", network, "--user", "1000:1000",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
                "--tmpfs", "/tmp:rw,nosuid,size=64m", "--tmpfs", "/home/node:rw,nosuid,size=64m",
                "--label", "labnet.selfcheck=1"]
        for mount in mounts:
            args += ["-v", mount]
        for name, value in (env or {}).items():
            args += ["-e", f"{name}={value}"]
        return self.docker([*args, image, *command], timeout=timeout)


class Failed(Exception):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


def run_all(settings: RunnerSettings, backend: Any, client: Any) -> list[Check]:
    checks = _Checks(settings, backend, client)
    s = settings

    def control_center() -> str:
        try:
            who = client.whoami()
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if status == 401:
                raise Failed(f"{s.control_center_url} refused the runner token (401): issue a new one with "
                             "`labnet-control-center-admin runner add <name>`.") from None
            raise Failed(f"{s.control_center_url} isn't reachable ({type(exc).__name__}: {_short(str(exc), 200)}); "
                         "check LABNET_RUNNER_CONTROL_CENTER_URL and LABNET_RUNNER_CA_FILE.") from None
        name = who.get("runner") if isinstance(who, dict) else who
        return f"{s.control_center_url} knows this runner as “{name}”."

    def docker() -> str:
        result = checks.docker(["version", "--format", "{{.Server.Version}}"])
        _require(result.ok and result.stdout.strip() != "",
                 f"`docker version` failed: {_short(result.stderr) or 'no server version'}. Is /var/run/docker.sock "
                 "mounted into the runner container and the Docker service running?")
        checks.docker_ok = True
        return f"Docker Engine {result.stdout.strip()}."

    def image(name: str, kind: str) -> Callable[[], str]:
        def check() -> str:
            result = checks.docker(["image", "inspect", "--format", "{{.Id}}", name])
            _require(result.ok, f"The {kind} image {name} is missing: build it with "
                                "`docker compose -f docker/automation/compose.yaml build`.")
            return f"{name} ({result.stdout.strip()[:19]})."
        return check

    def network() -> str:
        result = checks.docker(["network", "inspect", "--format", "{{json .Internal}}", s.agent_network])
        _require(result.ok, f"The network {s.agent_network} doesn't exist: start the egress proxy with "
                            "`docker compose -f docker/automation/compose.yaml up -d` (it creates the network).")
        _require(result.stdout.strip() == "true",
                 f"The network {s.agent_network} is not internal: it would let agents reach the internet directly. "
                 "Remove it (`docker network rm`) and let compose.yaml create it.")
        return f"{s.agent_network} exists and is internal."

    def login() -> str:
        result = checks.docker(["volume", "inspect", "--format", "{{.Name}}", s.claude_login_volume])
        _require(result.ok, f"The login volume {s.claude_login_volume} doesn't exist: {LOGIN_HINT}.")
        probe = checks.isolated_run(s.agent_image, "none", [AGENT_PYTHON, "-I", "-c", LOGIN_PROBE],
                                    mounts=[f"{s.claude_login_volume}:/claude:ro"])
        _require(probe.ok, f"Couldn't look inside {s.claude_login_volume}: {_short(probe.stderr)}")
        found = _last_json(probe.stdout)
        _require(found.get("exists"), f"{s.claude_login_volume} holds no Claude login (.credentials.json): {LOGIN_HINT}.")
        _require(found.get("readable") and "error" not in found,
                 f"{s.claude_login_volume}/.credentials.json can't be read by uid 1000 "
                 f"({found.get('error', 'permission denied')}); sign in again with the login service.")
        _require(found.get("refresh_token"), "The saved login has no refresh token (it will expire): " + LOGIN_HINT + ".")
        return f"{s.claude_login_volume} holds a Claude login with a refresh token."

    def run_container() -> str:
        root = s.work_dir / "selfcheck" / secrets.token_hex(4)
        work = root / "work"
        outside = root / "outside.txt"
        try:
            (work / "ScriptsUtilized").mkdir(parents=True)
            for folder in (s.work_dir / "selfcheck", root, work, work / "ScriptsUtilized"):
                folder.chmod(0o755)
            (work / "inside.txt").write_text("inside\n", encoding="utf-8")
            outside.write_text("outside\n", encoding="utf-8")
            (work / "ScriptsUtilized" / "selfcheck_probe.py").write_text(RUN_PROBE, encoding="utf-8")
            hidden = [s.work_dir.as_posix(), outside.as_posix(), work.as_posix(), "/var/run/docker.sock",
                      "/run/docker.sock", "/claude", "/run/labnet", "/run/labnet-devices"]
            result = backend.run_python(PythonRequest(
                run_id=SELFCHECK_RUN_ID, work=work, script="ScriptsUtilized/selfcheck_probe.py", args=hidden,
                timeout_seconds=60))
        finally:
            shutil.rmtree(root, ignore_errors=True)
        _require(result.exit_code == 0, f"The probe in {s.run_image} didn't run (exit {result.exit_code}, "
                                        f"timed out: {result.timed_out}): {_short(result.stderr)}")
        found = _last_json(result.stdout)
        _require(str(found.get("internet", "")).startswith("blocked"),
                 "A run container reached 1.1.1.1:443: run containers must have no network (--network none).")
        _require(found.get("inside") is True, "A run container can't see its working copy at /work "
                                              "(is LABNET_RUNNER_WORK_DIR mounted at the same path in the runner?).")
        _require(not found.get("visible"), f"A run container can see {', '.join(found['visible'])}.")
        _require(found.get("uid") == 1000, f"A run container runs as uid {found.get('uid')}, not 1000.")
        _require(found.get("shim") is True, f"{s.run_image} has no usable labnet_devices module "
                                            f"({found.get('shim')}): rebuild the images.")
        return f"no network ({found['internet']}); sees only its working copy; uid 1000; labnet_devices installed."

    def agent_network() -> str:
        proxy = urlsplit(s.egress_proxy)
        _require(proxy.scheme == "http" and proxy.hostname is not None,
                 f"LABNET_RUNNER_EGRESS_PROXY {s.egress_proxy!r} should be http://<proxy container>:<port>.")
        gateways: list[str] = []
        inspected = checks.docker(["network", "inspect", "--format",
                                   "{{range .IPAM.Config}}{{.Gateway}} {{end}}", s.agent_network])
        if inspected.ok:
            gateways = [g for g in inspected.stdout.split() if g and "/" not in g]
        result = checks.isolated_run(
            s.agent_image, s.agent_network,
            [AGENT_PYTHON, "-I", "-c", AGENT_PROBE, proxy.hostname, str(proxy.port or 3128), *gateways])
        _require(result.ok, f"The probe on {s.agent_network} didn't run: {_short(result.stderr)}")
        found = _last_json(result.stdout)
        _require(str(found.get("direct", "")).startswith("blocked"),
                 f"A container on {s.agent_network} reached 1.1.1.1:443 directly: the network must be internal.")
        _require(not found.get("gateway_open"),
                 f"A container on {s.agent_network} reached the host at {', '.join(found.get('gateway_open', []))}: "
                 "close those ports to Docker networks (firewall) or bind the services to other addresses.")
        _require(found.get("anthropic") == "200" and found.get("anthropic_tls"),
                 f"api.anthropic.com through {s.egress_proxy}: {found.get('anthropic')} "
                 "(is the egress container running, and the name on egress-allow.txt?).")
        _require(found.get("example") == "403",
                 f"example.com through the proxy answered {found.get('example')}, not 403: the allow-list isn't enforced.")
        detail = f"no direct internet; api.anthropic.com via the proxy ({found['anthropic_tls']}); example.com refused (403)"
        if not gateways:
            detail += "; the network has no gateway address"
        return detail + "."

    def claude_version() -> str:
        result = checks.isolated_run(s.agent_image, "none", ["claude", "--version"], env=SCRATCH_CLAUDE)
        _require(result.ok and result.stdout.strip(), f"`claude --version` failed in {s.agent_image}: "
                                                      f"{_short(result.stderr or result.stdout)}")
        return f"Claude Code {result.stdout.strip().splitlines()[0]}."

    def claude_flags() -> str:
        result = checks.isolated_run(s.agent_image, "none", ["claude", "--help"], env=SCRATCH_CLAUDE)
        _require(result.ok, f"`claude --help` failed in {s.agent_image}: {_short(result.stderr)}")
        missing = [flag for flag in CLAUDE_FLAGS if not _listed(flag, result.stdout)]
        _require(not missing, f"This Claude Code lacks {', '.join(missing)}: rebuild the agent image with a newer "
                              "CLAUDE_CODE_VERSION.")
        unlisted = [flag for flag in CLAUDE_OTHER_FLAGS if not _listed(flag, result.stdout)]
        detail = f"has {', '.join(CLAUDE_FLAGS)}"
        if unlisted:
            detail += f"; --help doesn't list {', '.join(unlisted)} (may be hidden options; a run will show)"
        return detail + "."

    checks.run("Control Center", control_center, needs_docker=False)
    checks.run("docker", docker, needs_docker=False)
    checks.run("agent image", image(s.agent_image, "agent"))
    checks.run("run image", image(s.run_image, "run"))
    checks.run("agent network", network)
    checks.run("Claude login", login)
    checks.run("run container isolation", run_container)
    checks.run("agent network isolation", agent_network)
    checks.run("claude --version", claude_version)
    checks.run("claude flags", claude_flags)
    return checks.results


def format_checks(checks: Sequence[Check]) -> str:
    return "\n".join(f"[{'ok' if c.ok else 'FAILED'}] {c.name}: {c.detail}" for c in checks)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="labnet-runner-selfcheck", description="Check the runner's isolation.")
    parser.add_argument("--env-file", help="the runner's env file (default: the environment)")
    args = parser.parse_args(argv)
    from .client import ControlCenterClient
    from .docker_backend import DockerBackend

    try:
        settings = RunnerSettings.from_env(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"The runner's settings are not usable: {exc}", file=sys.stderr)
        return 2
    with ControlCenterClient(settings) as client:
        checks = run_all(settings, DockerBackend(settings), client)
    print(format_checks(checks))
    return 0 if checks and all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CLAUDE_FLAGS", "Check", "SELFCHECK_RUN_ID", "format_checks", "main", "run_all"]
