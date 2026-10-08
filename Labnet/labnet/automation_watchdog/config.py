"""The watchdog's settings (``watchdog.env``), read like every other LabNet settings file."""

from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from labnet.envfile import load_env_file
from labnet.lease_policy import DEFAULT_LEASE_DURATION_SECONDS


def _is_loopback(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class WatchdogSettings:
    """What the watchdog needs; :meth:`from_env` checks every value, tests build it directly."""

    server_url: str
    api_key: str
    automation_dir: Path
    ca_file: Path | None = None
    control_center_url: str = "http://127.0.0.1:8080"
    bind_host: str = "127.0.0.1"
    port: int = 8081
    # A grant whose heartbeat is this old, while the Control Center's /health/live doesn't answer
    # for as long (or answers while the heartbeat stays stale twice as long), is taken over.
    stale_seconds: float = 20.0
    poll_seconds: float = 2.0
    # How long to keep asking Central for the leases (it refuses while the Control Center's live).
    lease_wait_seconds: float = 120.0
    retry_seconds: float = 3.0
    # A grant whose last heartbeat is older than this is never acted on (its devices may have been
    # used since): it is logged for a person to check.
    max_age_minutes: float = 60.0
    health_timeout_seconds: float = 3.0

    @property
    def health_url(self) -> str:
        return self.control_center_url.rstrip("/") + "/health/live"

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "WatchdogSettings":
        loaded = load_env_file(env_file, search_cwd=False)
        base_directory = loaded.parent if loaded is not None else Path.cwd()

        def path(raw: str) -> Path:
            value = Path(raw.strip()).expanduser()
            return value if value.is_absolute() else (base_directory / value).resolve()

        def number(name: str, default: float) -> float:
            raw = os.getenv(name, "").strip()
            try:
                return float(raw) if raw else default
            except ValueError:
                raise ValueError(f"{name} must be a number, not {raw!r}.") from None

        api_key = os.getenv("LABNET_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "LABNET_API_KEY is not set. Create the watchdog's own Central user "
                "(labnet-central-admin setup --user automation-watchdog ...)."
            )
        automation = os.getenv("LABNET_AUTOMATION_DIR", "").strip()
        if not automation:
            raise ValueError("LABNET_AUTOMATION_DIR is not set: use the Control Center's (its control-center.env).")
        ca_raw = os.getenv("LABNET_CA_FILE", "").strip()
        try:
            port = int(os.getenv("LABNET_WATCHDOG_PORT", "8081"))
        except ValueError:
            raise ValueError("LABNET_WATCHDOG_PORT must be a whole number.") from None
        settings = cls(
            server_url=os.getenv("LABNET_SERVER_URL", "").strip() or "http://127.0.0.1:8008",
            api_key=api_key,
            automation_dir=path(automation),
            ca_file=path(ca_raw) if ca_raw else None,
            control_center_url=os.getenv("LABNET_WATCHDOG_CONTROL_CENTER_URL", "").strip() or "http://127.0.0.1:8080",
            bind_host=os.getenv("LABNET_WATCHDOG_HOST", "").strip() or "127.0.0.1",
            port=port,
            stale_seconds=number("LABNET_WATCHDOG_STALE_SECONDS", 20.0),
            poll_seconds=number("LABNET_WATCHDOG_POLL_SECONDS", 2.0),
            lease_wait_seconds=number("LABNET_WATCHDOG_LEASE_WAIT_SECONDS", 120.0),
            max_age_minutes=number("LABNET_WATCHDOG_MAX_AGE_MINUTES", 60.0),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not _is_loopback(self.bind_host):
            raise ValueError("LABNET_WATCHDOG_HOST must be loopback: its health page is for this computer only.")
        if not 1 <= self.port <= 65535:
            raise ValueError("LABNET_WATCHDOG_PORT must be between 1 and 65535.")
        parts = urlsplit(self.control_center_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("LABNET_WATCHDOG_CONTROL_CENTER_URL must be an http(s) URL, e.g. http://127.0.0.1:8080.")
        for name, value, low, high in (
            # The Control Center writes its heartbeat every 5 s: a few missed beats, never one slow write.
            ("LABNET_WATCHDOG_STALE_SECONDS", self.stale_seconds, 10.0, 600.0),
            ("LABNET_WATCHDOG_POLL_SECONDS", self.poll_seconds, 0.5, 10.0),
            # Central refuses the leases until the Control Center's lapse: give that time to happen.
            ("LABNET_WATCHDOG_LEASE_WAIT_SECONDS", self.lease_wait_seconds, float(DEFAULT_LEASE_DURATION_SECONDS), 3600.0),
            ("LABNET_WATCHDOG_MAX_AGE_MINUTES", self.max_age_minutes, 1.0, 1440.0),
        ):
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low:g} and {high:g}.")
        if self.poll_seconds * 2 > self.stale_seconds:
            raise ValueError("LABNET_WATCHDOG_POLL_SECONDS must be at most half of LABNET_WATCHDOG_STALE_SECONDS.")
        if not self.automation_dir.is_dir():
            raise ValueError(f"LABNET_AUTOMATION_DIR is not a folder: {self.automation_dir}")
        if self.ca_file is not None and not self.ca_file.is_file():
            raise ValueError(f"LABNET_CA_FILE does not exist: {self.ca_file}")


__all__ = ["WatchdogSettings"]
