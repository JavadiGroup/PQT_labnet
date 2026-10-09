"""Runner settings: one env file, the same in development and the lab.

Only the values change:

* development: ``LABNET_RUNNER_CONTROL_CENTER_URL=http://127.0.0.1:8080``
  (Docker in WSL with mirrored networking reaches the laptop's loopback);
* lab: the Control Center's HTTPS address (through Caddy) and ``LABNET_RUNNER_CA_FILE``.

The token comes from ``labnet-control-center-admin runner add <name>`` on the
Control Center PC.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, fields
from pathlib import Path

from labnet.envfile import load_env_file


_IMAGE = re.compile(r"[a-z0-9][a-z0-9._/:@-]{0,254}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
TOKEN = re.compile(r"lnr_[A-Za-z0-9_-]{43}")


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    control_center_url: str
    token: str
    ca_file: Path | None = None
    # On the Docker host, mounted at the same path in the runner container, so
    # the paths it hands to `docker run -v` mean the same thing to Docker.
    work_dir: Path = Path("/var/lib/labnet-runner")
    agent_image: str = "labnet-automation-agent:latest"
    run_image: str = "labnet-automation-run:latest"
    # Created by compose.yaml: the agent's internal network, and the proxy on it.
    agent_network: str = "labnet-automation-agent"
    egress_proxy: str = "http://labnet-automation-egress:3128"
    claude_login_volume: str = "labnet-automation-claude-login"
    poll_seconds: float = 10.0
    heartbeat_seconds: float = 60.0
    # One agent run, and one run_python call.
    agent_timeout_minutes: float = 120.0
    python_timeout_seconds: float = 600.0
    python_cpus: float = 2.0
    python_memory_mb: int = 2048
    python_pids: int = 256
    # "Development only" banner in the log and run events.
    dev: bool = False
    # Keep <work_dir>/runs/<run_id>/ after a run (for debugging); normally deleted.
    keep_runs: bool = False

    @classmethod
    def from_env(cls, env_file: str | Path | None = None, *, search_cwd: bool = False) -> "RunnerSettings":
        load_env_file(env_file, search_cwd=search_cwd)

        def text(name: str, default: str = "") -> str:
            return os.getenv(name, "").strip() or default

        # The field defaults (with slots=True, cls.<field> is a slot descriptor, not the default).
        default = {field.name: field.default for field in fields(cls)}

        token = text("LABNET_RUNNER_TOKEN")
        token_file = text("LABNET_RUNNER_TOKEN_FILE")
        if not token and token_file:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        ca = text("LABNET_RUNNER_CA_FILE")
        settings = cls(
            control_center_url=text("LABNET_RUNNER_CONTROL_CENTER_URL").rstrip("/"),
            token=token,
            ca_file=Path(ca) if ca else None,
            work_dir=Path(text("LABNET_RUNNER_WORK_DIR", "/var/lib/labnet-runner")),
            agent_image=text("LABNET_RUNNER_AGENT_IMAGE", default["agent_image"]),
            run_image=text("LABNET_RUNNER_RUN_IMAGE", default["run_image"]),
            agent_network=text("LABNET_RUNNER_AGENT_NETWORK", default["agent_network"]),
            egress_proxy=text("LABNET_RUNNER_EGRESS_PROXY", default["egress_proxy"]),
            claude_login_volume=text("LABNET_RUNNER_CLAUDE_LOGIN_VOLUME", default["claude_login_volume"]),
            poll_seconds=float(text("LABNET_RUNNER_POLL_SECONDS", "10")),
            heartbeat_seconds=float(text("LABNET_RUNNER_HEARTBEAT_SECONDS", "60")),
            agent_timeout_minutes=float(text("LABNET_RUNNER_AGENT_TIMEOUT_MINUTES", "120")),
            python_timeout_seconds=float(text("LABNET_RUNNER_PYTHON_TIMEOUT_SECONDS", "600")),
            python_cpus=float(text("LABNET_RUNNER_PYTHON_CPUS", "2")),
            python_memory_mb=int(text("LABNET_RUNNER_PYTHON_MEMORY_MB", "2048")),
            python_pids=int(text("LABNET_RUNNER_PYTHON_PIDS", "256")),
            dev=text("LABNET_RUNNER_DEV").lower() in ("1", "true", "yes"),
            keep_runs=text("LABNET_RUNNER_KEEP_RUNS").lower() in ("1", "true", "yes"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.control_center_url.startswith(("https://", "http://")):
            raise ValueError("LABNET_RUNNER_CONTROL_CENTER_URL must be the Control Center's http(s) address.")
        if self.control_center_url.startswith("http://") and not self.dev:
            raise ValueError(
                "LABNET_RUNNER_CONTROL_CENTER_URL is plain HTTP: use the Control Center's HTTPS address "
                "(through Caddy), or set LABNET_RUNNER_DEV=1 on a development laptop."
            )
        if TOKEN.fullmatch(self.token) is None:
            raise ValueError(
                "LABNET_RUNNER_TOKEN is missing or malformed. On the Control Center PC run "
                "`labnet-control-center-admin runner add <name>` and copy the lnr_… token it prints."
            )
        if self.ca_file is not None and not self.ca_file.is_file():
            raise ValueError(f"LABNET_RUNNER_CA_FILE does not exist: {self.ca_file}")
        if not self.work_dir.is_absolute():
            raise ValueError("LABNET_RUNNER_WORK_DIR must be an absolute path on the Docker host.")
        for name, value in (("LABNET_RUNNER_AGENT_IMAGE", self.agent_image), ("LABNET_RUNNER_RUN_IMAGE", self.run_image)):
            if _IMAGE.fullmatch(value) is None:
                raise ValueError(f"{name}: {value!r} is not an image name.")
        for name, value in (("LABNET_RUNNER_AGENT_NETWORK", self.agent_network),
                            ("LABNET_RUNNER_CLAUDE_LOGIN_VOLUME", self.claude_login_volume)):
            if _NAME.fullmatch(value) is None:
                raise ValueError(f"{name}: {value!r} is not a Docker name.")
        for name, value in (
            ("LABNET_RUNNER_POLL_SECONDS", self.poll_seconds),
            ("LABNET_RUNNER_HEARTBEAT_SECONDS", self.heartbeat_seconds),
            ("LABNET_RUNNER_AGENT_TIMEOUT_MINUTES", self.agent_timeout_minutes),
            ("LABNET_RUNNER_PYTHON_TIMEOUT_SECONDS", self.python_timeout_seconds),
            ("LABNET_RUNNER_PYTHON_CPUS", self.python_cpus),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive number.")
        if not 64 <= self.python_memory_mb <= 65536:
            raise ValueError("LABNET_RUNNER_PYTHON_MEMORY_MB must be between 64 and 65536.")
        if not 16 <= self.python_pids <= 4096:
            raise ValueError("LABNET_RUNNER_PYTHON_PIDS must be between 16 and 4096.")


__all__ = ["RunnerSettings", "TOKEN"]
