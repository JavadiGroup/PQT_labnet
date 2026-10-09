"""``labnet runner …``: the Automation runner next to a development network (``labnet dev``).

    labnet runner build        # build the three images (again after code changes)
    labnet runner login        # once: sign Claude in, inside the container (/login)
    labnet runner up           # start the egress proxy and the runner
    labnet runner self-check   # prove the isolation holds (the runner also checks at start)
    labnet runner logs         # follow the runner's log
    labnet runner down         # stop them

The images build from a LabNet source checkout (its ``packages/``), with
Docker Compose. On Windows that is Docker Engine in a WSL 2 distribution with
mirrored networking (so it reaches the Control Center at 127.0.0.1); a hidden
``wsl.exe`` keeps the distribution running while the runner is up. On Linux,
Docker runs directly. The first ``up`` issues a runner token into
``.devnet/runner.env``.

The lab's runner host uses docker compose directly: see docs/automation/runner.md.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from labnet.dev.network import DevNetwork
from labnet.dev.processes import DevError, command, http_ok

WINDOWS = os.name == "nt"
COMPOSE_FILE = "packages/labnet-automation/docker/compose.yaml"
KEEPALIVE_TAG = "labnet-runner-keepalive"
ACTIONS = ("build", "login", "up", "down", "logs", "self-check", "status")


def find_source(start: Path) -> Path:
    """The LabNet checkout the images build from: ``start`` or a folder above it."""

    for folder in (start, *start.parents):
        if (folder / COMPOSE_FILE).is_file():
            return folder
    raise DevError(
        f"No {COMPOSE_FILE} here or above {start}. The runner's images build from a LabNet source "
        "checkout: run this there, or pass --source."
    )


def wsl_path(path: Path) -> str:
    """A Windows path as WSL sees it: C:\\Users\\me -> /mnt/c/Users/me."""

    resolved = Path(path).resolve()
    drive = resolved.drive.rstrip(":").lower()
    return str(PurePosixPath("/mnt", drive, *resolved.parts[1:]))


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


class Runner:
    def __init__(self, source: Path, net: DevNetwork, distro: str, name: str) -> None:
        self.source = source
        self.net = net
        self.distro = distro
        self.name = name
        self.env_file = net.devnet / "runner.env"

    # ---- where Docker runs ------------------------------------------------------------------

    def shell(self, script: str, *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
        """Run a bash script where Docker is: in the WSL distribution, or here."""

        argv = ["wsl.exe", "-d", self.distro, "-e", "bash", "-lc", script] if WINDOWS else ["bash", "-c", script]
        result = subprocess.run(argv, text=True, capture_output=capture)
        if check and result.returncode != 0:
            raise DevError(f"Failed (exit {result.returncode}): {script.splitlines()[-1].strip()}")
        return result

    def compose(self, arguments: str, *, check: bool = True) -> subprocess.CompletedProcess:
        repo = wsl_path(self.source) if WINDOWS else str(self.source)
        env_file = wsl_path(self.env_file) if WINDOWS else str(self.env_file)
        # The work folder lives in the Linux file system (fast, and Linux permissions work there).
        return self.shell(
            "set -e\n"
            'export LABNET_RUNNER_WORK_DIR="$HOME/.labnet-runner" '
            f"LABNET_RUNNER_ENV_FILE={_quote(env_file)}\n"
            'export LABNET_RUNNER_CONFIG_DIR="$HOME/.labnet-runner-config"  # no CA file in development\n'
            'mkdir -p "$LABNET_RUNNER_WORK_DIR" "$LABNET_RUNNER_CONFIG_DIR"\n'
            f"cd {_quote(repo)} && docker compose -f {_quote(COMPOSE_FILE)} {arguments}",
            check=check,
        )

    def check_docker(self) -> None:
        if WINDOWS and command("wsl.exe") is None and not Path(os.environ.get("SystemRoot", "C:/Windows"), "System32", "wsl.exe").exists():
            raise DevError(f"WSL isn't installed: wsl --install -d {self.distro}")
        if self.shell("docker info > /dev/null 2>&1", check=False).returncode != 0:
            where = f"in WSL ({self.distro})" if WINDOWS else "here"
            raise DevError(f"Docker {where} isn't usable without sudo: sudo usermod -aG docker $USER, then log in again"
                           + (" (wsl --shutdown)." if WINDOWS else "."))
        if WINDOWS:
            mode = subprocess.run(["wsl.exe", "-d", self.distro, "--", "wslinfo", "--networking-mode"],
                                  capture_output=True, text=True).stdout.strip()
            if mode != "mirrored":
                raise DevError(
                    "WSL isn't in mirrored networking mode, so it can't reach the Control Center at 127.0.0.1. "
                    "Put networkingMode=mirrored under [wsl2] in %UserProfile%\\.wslconfig, then wsl --shutdown."
                )

    # ---- the runner's token -----------------------------------------------------------------

    def write_env(self) -> None:
        if self.env_file.is_file():
            return
        port = self.net.control_center_port
        if not http_ok(f"http://127.0.0.1:{port}/health/live"):
            raise DevError("The dev Control Center isn't running; start the network first: labnet dev up")
        admin = command("labnet-control-center-admin")
        if admin is None:
            raise DevError("labnet-control-center-admin isn't installed here: pip install labnet-control-center")
        settings = self.net.env_file(self.net.control_center_user)
        # A token is shown only once: a lost runner.env means a new runner entry.
        subprocess.run([str(admin), "--env-file", str(settings), "runner", "revoke", self.name], capture_output=True)
        added = subprocess.run([str(admin), "--env-file", str(settings), "runner", "add", self.name],
                               capture_output=True, text=True)
        token = re.search(r"lnr_[A-Za-z0-9_-]+", added.stdout)
        if added.returncode != 0 or token is None:
            raise DevError(f"labnet-control-center-admin didn't issue a runner token: {added.stderr.strip()[-200:]}")
        self.env_file.write_text(
            "# The dev runner (labnet runner). Development only: plain HTTP to this computer's own\n"
            "# Control Center; the lab uses its HTTPS address and a CA file.\n"
            f"LABNET_RUNNER_CONTROL_CENTER_URL=http://127.0.0.1:{port}\n"
            f"LABNET_RUNNER_TOKEN={token.group(0)}\n"
            "LABNET_RUNNER_DEV=1\n",
            encoding="utf-8", newline="\n",
        )
        if not WINDOWS:
            self.env_file.chmod(0o600)
        print(f"  ok Runner '{self.name}' issued; its token is in {self.env_file}")

    # ---- keeping WSL running (Windows) ------------------------------------------------------

    def _powershell(self, script: str) -> str:
        return subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                              capture_output=True, text=True).stdout.strip()

    def keepalive_running(self) -> bool:
        count = self._powershell(
            "@(Get-CimInstance Win32_Process -Filter \"Name='wsl.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{KEEPALIVE_TAG}*' }}).Count"
        )
        return count not in ("", "0")

    def start_keepalive(self) -> None:
        # WSL stops a distribution (and its containers) soon after the last Windows program
        # attached to it exits; a hidden `sleep` keeps it running, as Docker Desktop does.
        if not WINDOWS or self.keepalive_running():
            return
        self._powershell(
            "Start-Process wsl.exe -WindowStyle Hidden -ArgumentList "
            f"'-d {self.distro} -e bash -c \"exec -a {KEEPALIVE_TAG} sleep infinity\"'"
        )

    def stop_keepalive(self) -> None:
        if WINDOWS:
            self._powershell(
                "Get-CimInstance Win32_Process -Filter \"Name='wsl.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*{KEEPALIVE_TAG}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
            )

    # ---- actions ------------------------------------------------------------------------------

    def run(self, action: str) -> int:
        if action == "build":
            self.check_docker()
            print(" ==> Building the runner, agent and run images")
            self.compose("build")
        elif action == "login":
            self.check_docker()
            print(" ==> Sign Claude in: type /login, follow the link, then /exit")
            self.compose("--profile login run --rm login")
        elif action == "up":
            self.check_docker()
            self.write_env()
            self.start_keepalive()
            self.compose("up -d egress runner")
            print("  ok Runner started. It checks its isolation first: labnet runner logs")
        elif action == "down":
            self.check_docker()
            self.compose("down")
            self.stop_keepalive()
            print("  ok Runner stopped")
        elif action == "logs":
            self.compose("logs -f --tail 100 runner", check=False)
        elif action == "self-check":
            self.check_docker()
            self.write_env()
            self.compose("up -d egress")  # the checks go through the proxy
            return self.compose("run --rm --no-deps runner labnet-runner self-check", check=False).returncode
        elif action == "status":
            return self.compose("ps", check=False).returncode
        return 0


def register(commands: argparse._SubParsersAction) -> None:
    runner = commands.add_parser(
        "runner", help="The Automation runner next to a dev network (Docker; WSL on Windows).",
        description="The Automation runner next to a dev network (labnet dev): build its images, sign Claude in "
                    "(login), start it (up), check its isolation (self-check), follow it (logs), stop it (down).",
    )
    runner.add_argument("action", choices=ACTIONS)
    runner.add_argument("--dir", type=Path, default=Path("."), help="The dev network's working folder (its .devnet).")
    runner.add_argument("--source", type=Path, help="The LabNet checkout to build from (default: --dir or above).")
    runner.add_argument("--distro", default=os.environ.get("LABNET_WSL_DISTRO", "Ubuntu-22.04"),
                        help="The WSL distribution with Docker (Windows).")
    runner.add_argument("--name", default=os.environ.get("LABNET_DEV_RUNNER_NAME", "dev-laptop"),
                        help="The runner's name in the Control Center.")
    runner.set_defaults(handler=_run)


def _run(arguments: argparse.Namespace) -> int:
    root = arguments.dir.resolve()
    try:
        source = (arguments.source or find_source(root)).resolve()
        return Runner(source, DevNetwork(root=root), arguments.distro, arguments.name).run(arguments.action)
    except DevError as exc:
        print(f"labnet runner {arguments.action}: {exc}")
        return 1


__all__ = ["Runner", "find_source", "register", "wsl_path"]
