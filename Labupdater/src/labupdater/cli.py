from __future__ import annotations

import argparse
import json
import logging
import signal
from dataclasses import asdict
from pathlib import Path

from .agent import UpdateAgent
from .artifacts import ReleaseManifest, RuntimeInstaller
from .config import UpdaterConfig
from .state import StateStore, UpdaterState


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="labupdater",
        description="Independent LabNet Lab-PC release supervisor",
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="Path to updater TOML config"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug logging"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Run the polling agent and Lab-PC supervisor")
    install = commands.add_parser(
        "install-bundle", help="Install a local release bundle for bootstrap"
    )
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--manifest", type=Path, required=True)
    install.add_argument("--activate", action="store_true")
    commands.add_parser("status", help="Print updater state")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = UpdaterConfig.load(args.config)
    if args.command == "run":
        agent = UpdateAgent(config)

        def stop(_signum, _frame) -> None:
            agent.stop_event.set()

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        agent.run_forever()
        return
    if args.command == "install-bundle":
        raw = json.loads(args.manifest.read_text(encoding="utf-8"))
        manifest = ReleaseManifest.parse(raw)
        installer = RuntimeInstaller(
            config.release_root,
            install_timeout_seconds=config.install_timeout_seconds,
        )
        runtime = installer.stage(
            args.bundle.resolve(), manifest, attempt_id=f"manual-{manifest.version}"
        )
        if args.activate:
            store = StateStore(config.release_root)
            previous = store.current_version()
            store.activate(manifest.version)
            store.save(
                UpdaterState(
                    active_version=manifest.version,
                    previous_version=previous,
                )
            )
        print(runtime)
        return
    if args.command == "status":
        print(json.dumps(asdict(StateStore(config.release_root).load()), indent=2))


__all__ = ["main"]
