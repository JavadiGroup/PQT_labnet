"""Shared ``--env-file`` handling for every LabNet command and client."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_env_file(
    env_file: str | Path | None,
    *,
    search_cwd: bool = False,
) -> Path | None:
    """Load ``KEY=value`` settings into the process environment.

    An explicit file must exist. With ``search_cwd``, ``./.env`` is used when
    no file is given. Values already in the process environment always win,
    so a service manager or shell can override any line. Returns the loaded
    path, or ``None`` when nothing was loaded.
    """

    if env_file is not None:
        path = Path(env_file).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Settings file not found: {path}")
    elif search_cwd and Path(".env").is_file():
        path = Path(".env").resolve()
    else:
        return None
    load_dotenv(path, override=False)
    return path


__all__ = ["load_env_file"]
