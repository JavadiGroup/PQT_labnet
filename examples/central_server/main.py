"""Minimal Central Server composition using the installed LabNet package."""

from __future__ import annotations

import uvicorn

from labnet.central import Settings, create_app


settings = Settings.from_env()
app = create_app(settings)


def main() -> None:
    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
