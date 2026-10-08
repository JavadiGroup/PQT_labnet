"""Control Center settings: one env file, identical in development and the lab.

Only the values change between the two:

* development: ``LABNET_SERVER_URL=http://127.0.0.1:8008`` and a dev key
  (``scripts/dev/seed.sh`` writes ``.devnet/control-center.env``);
* lab: ``LABNET_SERVER_URL=https://192.168.50.10``, the lab key, and
  ``LABNET_CA_FILE``. Caddy serves it as ``https://pqt-control-center``.

The ``LABNET_AGENT_*`` settings configure Photo-Agent (see ``agent.py``);
``LABNET_AUTOMATION_*`` the Automation tab (see ``automation_store.py``).
``LABNET_CONTROL_CENTER_PROXY_SECRET`` turns on logins (see ``ingress.py``).
"""

from __future__ import annotations

import ipaddress
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

import labnet
from labnet.envfile import load_env_file


# Claude Code's --effort levels (Haiku has none; see agent.supports_effort).
AGENT_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Aliases for the latest of each model; full ids such as claude-opus-5-5 work too.
DEFAULT_AGENT_MODELS = ("fable", "opus", "sonnet", "haiku")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,99}")
# Host names the pages answer to: a name, *.suffix, an IPv4 address, or "*".
_HOST_NAME = re.compile(r"\*|(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]{0,252}[A-Za-z0-9])?")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
MIN_PROXY_SECRET_CHARACTERS = 32


def _is_loopback(host: str) -> bool:
    value = host.strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ControlCenterSettings:
    server_url: str
    api_key: str
    ca_file: Path | None
    bind_host: str
    port: int
    refresh_seconds: float
    # Device control pages: Play stops itself after this long,
    max_hold_minutes: float = 30.0
    # a device is released this long after the last viewer leaves,
    idle_release_seconds: float = 15.0
    # and a script is stopped after this long.
    script_timeout_seconds: float = 600.0
    # Experiments tab: one JSON file per experiment (created on the first save).
    experiments_dir: Path = Path("experiments")
    # Automation tab: projects/ (people's and agents' files) and state/ (the
    # Control Center's own), and the largest file people may upload.
    automation_dir: Path = Path("automation")
    automation_max_file_mb: float = 100.0
    # Automation runners: how much one run may upload in all.
    automation_run_quota_mb: float = 500.0
    # and how long an active run may go without a word from its runner (a
    # request about it, or a heartbeat) before it is marked failed.
    automation_run_stale_minutes: float = 10.0
    # Device grants (Phase 3): the longest an approved protocol may hold its
    # devices, and how long a submitted protocol may wait for a person before
    # its run is failed.
    automation_grant_max_minutes: float = 120.0
    automation_approval_hours: float = 24.0
    # Logins: Caddy adds X-LabNet-Proxy: <secret> and X-LabNet-User to each
    # request; with a secret set, requests without it are refused.
    proxy_secret: str | None = None
    # Host names the pages answer to (guards against DNS rebinding); "*" is any.
    allowed_hosts: tuple[str, ...] = ("*",)
    # Photo-Agent: the `claude` command (found on PATH when unset), its working
    # folder, the folders it may read, and limits.
    agent_command: Path | None = None
    agent_workspace: Path = Path("agent")
    agent_sources: tuple[Path, ...] = ()
    agent_literature_dir: Path | None = None
    agent_models: tuple[str, ...] = DEFAULT_AGENT_MODELS  # the chat's model menu
    agent_model: str = "sonnet"  # selected at first
    agent_effort: str = "high"
    agent_timeout_seconds: float = 300.0
    agent_max_concurrent: int = 2

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = None,
        *,
        search_cwd: bool = True,
    ) -> "ControlCenterSettings":
        loaded = load_env_file(env_file, search_cwd=search_cwd)
        base_directory = loaded.parent if loaded is not None else Path.cwd()

        api_key = os.getenv("LABNET_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "LABNET_API_KEY is not set. Create a Central user for the Control "
                "Center (labnet-central-admin setup --user control-center ...)."
            )
        def path(raw: str) -> Path:
            value = Path(raw.strip()).expanduser()
            return value if value.is_absolute() else (base_directory / value).resolve()

        def optional_path(name: str) -> Path | None:
            raw = os.getenv(name, "").strip()
            return path(raw) if raw else None

        ca_file = optional_path("LABNET_CA_FILE")
        raw_sources = os.getenv("LABNET_AGENT_SOURCES", "").strip()
        agent_sources = (
            tuple(path(item) for item in raw_sources.split(os.pathsep) if item.strip())
            if raw_sources
            else (Path(labnet.__file__).resolve().parent,)  # the installed package
        )

        proxy_secret = os.getenv("LABNET_CONTROL_CENTER_PROXY_SECRET", "").strip() or None
        raw_hosts = os.getenv("LABNET_CONTROL_CENTER_ALLOWED_HOSTS", "").strip()
        # Without logins, only loopback names: a web page can't point its own
        # host name at 127.0.0.1 and talk to the Control Center. With logins,
        # the secret already stops that, so any name Caddy forwards is fine.
        allowed_hosts = (
            tuple(item.strip() for item in raw_hosts.split(",") if item.strip())
            if raw_hosts
            else ("*",) if proxy_secret else LOOPBACK_HOSTS
        )

        settings = cls(
            server_url=os.getenv("LABNET_SERVER_URL", "").strip() or "http://127.0.0.1:8008",
            api_key=api_key,
            ca_file=ca_file,
            bind_host=os.getenv("LABNET_CONTROL_CENTER_HOST", "127.0.0.1").strip(),
            port=int(os.getenv("LABNET_CONTROL_CENTER_PORT", "8080")),
            refresh_seconds=float(os.getenv("LABNET_CONTROL_CENTER_REFRESH_SECONDS", "3")),
            max_hold_minutes=float(os.getenv("LABNET_CONTROL_CENTER_MAX_HOLD_MINUTES", "30")),
            idle_release_seconds=float(os.getenv("LABNET_CONTROL_CENTER_IDLE_RELEASE_SECONDS", "15")),
            script_timeout_seconds=float(os.getenv("LABNET_CONTROL_CENTER_SCRIPT_TIMEOUT_SECONDS", "600")),
            # Next to the env file unless set; worth backing up in the lab.
            experiments_dir=optional_path("LABNET_CONTROL_CENTER_EXPERIMENTS_DIR") or base_directory / "experiments",
            automation_dir=optional_path("LABNET_AUTOMATION_DIR") or base_directory / "automation",
            automation_max_file_mb=float(os.getenv("LABNET_AUTOMATION_MAX_FILE_MB", "100")),
            automation_run_quota_mb=float(os.getenv("LABNET_AUTOMATION_RUN_QUOTA_MB", "500")),
            automation_run_stale_minutes=float(os.getenv("LABNET_AUTOMATION_RUN_STALE_MINUTES", "10")),
            automation_grant_max_minutes=float(os.getenv("LABNET_AUTOMATION_GRANT_MAX_MINUTES", "120")),
            automation_approval_hours=float(os.getenv("LABNET_AUTOMATION_APPROVAL_HOURS", "24")),
            proxy_secret=proxy_secret,
            allowed_hosts=allowed_hosts,
            agent_command=optional_path("LABNET_AGENT_COMMAND"),
            # Next to the env file unless set: .devnet/agent, C:\ProgramData\LabNet\agent.
            agent_workspace=optional_path("LABNET_AGENT_WORKSPACE") or base_directory / "agent",
            agent_sources=agent_sources,
            agent_literature_dir=optional_path("LABNET_AGENT_LITERATURE_DIR"),
            agent_models=tuple(
                name.strip()
                for name in os.getenv("LABNET_AGENT_MODELS", ",".join(DEFAULT_AGENT_MODELS)).split(",")
                if name.strip()
            ),
            agent_model=os.getenv("LABNET_AGENT_MODEL", "").strip() or "sonnet",
            agent_effort=os.getenv("LABNET_AGENT_EFFORT", "").strip().lower() or "high",
            agent_timeout_seconds=float(os.getenv("LABNET_AGENT_TIMEOUT_SECONDS", "300")),
            agent_max_concurrent=int(os.getenv("LABNET_AGENT_MAX_CONCURRENT", "2")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not _is_loopback(self.bind_host):
            raise ValueError(
                "LABNET_CONTROL_CENTER_HOST must be loopback. The built-in server is "
                "plain HTTP; put Caddy (HTTPS) in front of it to reach it from the LAN."
            )
        if not 1 <= self.port <= 65535:
            raise ValueError("LABNET_CONTROL_CENTER_PORT must be between 1 and 65535.")
        if not math.isfinite(self.refresh_seconds) or self.refresh_seconds < 0.5:
            raise ValueError("LABNET_CONTROL_CENTER_REFRESH_SECONDS must be at least 0.5.")
        for name, value in (
            ("LABNET_CONTROL_CENTER_MAX_HOLD_MINUTES", self.max_hold_minutes),
            ("LABNET_CONTROL_CENTER_IDLE_RELEASE_SECONDS", self.idle_release_seconds),
            ("LABNET_CONTROL_CENTER_SCRIPT_TIMEOUT_SECONDS", self.script_timeout_seconds),
            ("LABNET_AGENT_TIMEOUT_SECONDS", self.agent_timeout_seconds),
            ("LABNET_AUTOMATION_RUN_STALE_MINUTES", self.automation_run_stale_minutes),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive number.")
        if self.ca_file is not None and not self.ca_file.is_file():
            raise ValueError(f"LABNET_CA_FILE does not exist: {self.ca_file}")
        if not 1 <= self.agent_max_concurrent <= 16:
            raise ValueError("LABNET_AGENT_MAX_CONCURRENT must be between 1 and 16.")
        for source in self.agent_sources:
            if not source.is_dir():
                raise ValueError(f"LABNET_AGENT_SOURCES: not a folder: {source}")
        if self.experiments_dir.exists() and not self.experiments_dir.is_dir():
            raise ValueError(f"LABNET_CONTROL_CENTER_EXPERIMENTS_DIR is not a folder: {self.experiments_dir}")
        if self.automation_dir.exists() and not self.automation_dir.is_dir():
            raise ValueError(f"LABNET_AUTOMATION_DIR is not a folder: {self.automation_dir}")
        if not math.isfinite(self.automation_max_file_mb) or not 0 < self.automation_max_file_mb <= 2048:
            raise ValueError("LABNET_AUTOMATION_MAX_FILE_MB must be between 0 and 2048.")
        if not math.isfinite(self.automation_run_quota_mb) or not 0 < self.automation_run_quota_mb <= 100_000:
            raise ValueError("LABNET_AUTOMATION_RUN_QUOTA_MB must be between 0 and 100000.")
        if not math.isfinite(self.automation_grant_max_minutes) or not 1 <= self.automation_grant_max_minutes <= 1440:
            raise ValueError("LABNET_AUTOMATION_GRANT_MAX_MINUTES must be between 1 and 1440.")
        if not math.isfinite(self.automation_approval_hours) or not 0 < self.automation_approval_hours <= 720:
            raise ValueError("LABNET_AUTOMATION_APPROVAL_HOURS must be more than 0 and at most 720.")
        if self.proxy_secret is not None and (
            len(self.proxy_secret) < MIN_PROXY_SECRET_CHARACTERS
            or not self.proxy_secret.isascii()
            or not self.proxy_secret.isprintable()
            or " " in self.proxy_secret
        ):
            raise ValueError(
                f"LABNET_CONTROL_CENTER_PROXY_SECRET must be at least {MIN_PROXY_SECRET_CHARACTERS} "
                "printable ASCII characters without spaces (scripts/lab/setup_logins.ps1 makes one)."
            )
        if not self.allowed_hosts:
            raise ValueError("LABNET_CONTROL_CENTER_ALLOWED_HOSTS must name at least one host.")
        for host in self.allowed_hosts:
            if _HOST_NAME.fullmatch(host) is None:
                raise ValueError(
                    f"LABNET_CONTROL_CENTER_ALLOWED_HOSTS: {host!r} is not a host name "
                    "(no ports or schemes; e.g. pqt-control-center,192.168.50.10)."
                )
        if self.agent_literature_dir is not None and not self.agent_literature_dir.is_dir():
            raise ValueError(f"LABNET_AGENT_LITERATURE_DIR is not a folder: {self.agent_literature_dir}")
        if not self.agent_models:
            raise ValueError("LABNET_AGENT_MODELS must name at least one model.")
        for model in self.agent_models:
            if _MODEL_NAME.fullmatch(model) is None:
                raise ValueError(f"LABNET_AGENT_MODELS: {model!r} is not a model name or alias.")
        if self.agent_model not in self.agent_models:
            raise ValueError(
                f"LABNET_AGENT_MODEL={self.agent_model} must be one of LABNET_AGENT_MODELS "
                f"({', '.join(self.agent_models)})."
            )
        if self.agent_effort not in AGENT_EFFORTS:
            raise ValueError(f"LABNET_AGENT_EFFORT must be one of {', '.join(AGENT_EFFORTS)}.")


__all__ = ["AGENT_EFFORTS", "ControlCenterSettings", "DEFAULT_AGENT_MODELS", "LOOPBACK_HOSTS"]
