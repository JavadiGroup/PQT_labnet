"""Control Center settings: one env file, identical in development and the lab.

Only the values change between the two:

* development: ``LABNET_SERVER_URL=http://127.0.0.1:8008`` and a dev key
  (``scripts/dev/seed.sh`` writes ``.devnet/control-center.env``);
* lab: ``LABNET_SERVER_URL=https://192.168.50.10``, the lab key, and
  ``LABNET_CA_FILE``. Caddy serves it as ``https://pqt-control-center``.
"""

from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass
from pathlib import Path

from labnet.envfile import load_env_file


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
        raw_ca = os.getenv("LABNET_CA_FILE", "").strip()
        ca_file = None
        if raw_ca:
            ca_file = Path(raw_ca).expanduser()
            if not ca_file.is_absolute():
                ca_file = (base_directory / ca_file).resolve()

        settings = cls(
            server_url=os.getenv("LABNET_SERVER_URL", "").strip() or "http://127.0.0.1:8008",
            api_key=api_key,
            ca_file=ca_file,
            bind_host=os.getenv("LABNET_CONTROL_CENTER_HOST", "127.0.0.1").strip(),
            port=int(os.getenv("LABNET_CONTROL_CENTER_PORT", "8080")),
            refresh_seconds=float(os.getenv("LABNET_CONTROL_CENTER_REFRESH_SECONDS", "3")),
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
        if self.ca_file is not None and not self.ca_file.is_file():
            raise ValueError(f"LABNET_CA_FILE does not exist: {self.ca_file}")


__all__ = ["ControlCenterSettings"]
