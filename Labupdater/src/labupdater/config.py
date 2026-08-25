from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


_MANAGER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class UpdaterConfig:
    manager_id: str
    central_url: str
    api_key_file: Path
    release_root: Path
    local_control_token_file: Path
    labpc_environment_file: Path | None = None
    labpc_pythonpath: tuple[Path, ...] = ()
    labpc_host: str = "127.0.0.1"
    labpc_port: int = 9001
    poll_interval_seconds: float = 15.0
    drain_poll_seconds: float = 5.0
    startup_timeout_seconds: float = 60.0
    stop_timeout_seconds: float = 60.0
    install_timeout_seconds: float = 600.0
    ca_file: Path | None = None

    @classmethod
    def load(cls, path: Path) -> "UpdaterConfig":
        source = path.expanduser().resolve()
        with source.open("rb") as stream:
            raw = tomllib.load(stream)
        base = source.parent

        def required(name: str) -> object:
            if name not in raw:
                raise ValueError(f"Missing required updater setting {name!r}.")
            return raw[name]

        def resolved(value: object) -> Path:
            candidate = Path(str(value)).expanduser()
            return (base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

        manager_id = str(required("manager_id"))
        central_url = str(required("central_url")).rstrip("/")
        if _MANAGER_ID.fullmatch(manager_id) is None:
            raise ValueError("manager_id contains unsupported characters.")
        parsed = urlparse(central_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("central_url must be an absolute HTTP(S) URL.")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("A non-loopback Central URL must use HTTPS.")
        pythonpath_raw = raw.get("labpc_pythonpath", [])
        if not isinstance(pythonpath_raw, list):
            raise ValueError("labpc_pythonpath must be a TOML array of directories.")

        result = cls(
            manager_id=manager_id,
            central_url=central_url,
            api_key_file=resolved(required("api_key_file")),
            release_root=resolved(required("release_root")),
            local_control_token_file=resolved(required("local_control_token_file")),
            labpc_environment_file=(
                resolved(raw["labpc_environment_file"])
                if raw.get("labpc_environment_file")
                else None
            ),
            labpc_pythonpath=tuple(
                resolved(item) for item in pythonpath_raw
            ),
            labpc_host=str(raw.get("labpc_host", "127.0.0.1")),
            labpc_port=int(raw.get("labpc_port", 9001)),
            poll_interval_seconds=float(raw.get("poll_interval_seconds", 15.0)),
            drain_poll_seconds=float(raw.get("drain_poll_seconds", 5.0)),
            startup_timeout_seconds=float(raw.get("startup_timeout_seconds", 60.0)),
            stop_timeout_seconds=float(raw.get("stop_timeout_seconds", 60.0)),
            install_timeout_seconds=float(raw.get("install_timeout_seconds", 600.0)),
            ca_file=resolved(raw["ca_file"]) if raw.get("ca_file") else None,
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.labpc_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The Lab-PC control endpoint must be loopback-only.")
        if not 1 <= self.labpc_port <= 65535:
            raise ValueError("labpc_port must be between 1 and 65535.")
        if self.release_root == Path(self.release_root.anchor) or self.release_root == Path.home().resolve():
            raise ValueError("release_root must be a dedicated directory, not a filesystem or home root.")
        for name, value in (
            ("poll_interval_seconds", self.poll_interval_seconds),
            ("drain_poll_seconds", self.drain_poll_seconds),
            ("startup_timeout_seconds", self.startup_timeout_seconds),
            ("stop_timeout_seconds", self.stop_timeout_seconds),
            ("install_timeout_seconds", self.install_timeout_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive.")

    def read_api_key(self) -> str:
        return _read_secret(self.api_key_file, "Central API key")

    def read_control_token(self) -> str:
        value = _read_secret(self.local_control_token_file, "local control token")
        if len(value) < 16:
            raise ValueError("The local control token must be at least 16 characters.")
        return value

    @property
    def labpc_url(self) -> str:
        host = f"[{self.labpc_host}]" if ":" in self.labpc_host else self.labpc_host
        return f"http://{host}:{self.labpc_port}"


def _read_secret(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"Could not read {label} file {path}.") from exc
    if not value:
        raise ValueError(f"The {label} file is empty.")
    return value


__all__ = ["UpdaterConfig"]
