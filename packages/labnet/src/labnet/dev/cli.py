"""``labnet dev …``: run a whole LabNet network on this computer, for development and demos."""

from __future__ import annotations

import argparse
from pathlib import Path

HELP = {
    "up": "Start Central, the Lab PCs, the Control Center and the watchdog (seeding first if needed).",
    "down": "Stop them (or only the named components). Settings and data are kept.",
    "status": "Each component's health, and the devices Central hands to clients.",
    "smoke": "Lease every available device as a researcher, call one read, release it.",
    "seed": "Start over: a fresh .devnet with Central's database, users and settings files.",
    "restart": "Restart named components, e.g. after editing a Lab PC's inventory.",
}


def register(commands: argparse._SubParsersAction) -> None:
    dev = commands.add_parser("dev", help="Run a LabNet network on this computer for development.")
    dev.add_argument("--dir", type=Path, default=Path("."), help="The working folder; state goes in its .devnet (default: here).")
    actions = dev.add_subparsers(dest="dev_command", required=True)
    for name, text in HELP.items():
        action = actions.add_parser(name, help=text, description=text)
        if name in ("down", "restart"):
            action.add_argument("components", nargs="*" if name == "down" else "+",
                                help="central, lab-pc-dev-01, lab-pc-dev-02, control-center, watchdog")
        if name == "seed":
            action.add_argument("--watchdog", action="store_true",
                                help="Only add the watchdog to a network seeded before it existed.")
        action.set_defaults(handler=_run)


def _run(arguments: argparse.Namespace) -> int:
    from . import network
    from .processes import DevError

    net = network.DevNetwork(root=arguments.dir.resolve())
    try:
        command = arguments.dev_command
        if command == "up":
            network.up(net)
        elif command == "down":
            network.down(net, arguments.components or None)
        elif command == "restart":
            network.restart(net, arguments.components)
        elif command == "seed":
            network.seed(net, watchdog_only=arguments.watchdog)
        elif command == "status":
            return network.status(net)
        elif command == "smoke":
            return network.smoke(net)
    except DevError as exc:
        print(f"labnet dev {arguments.dev_command}: {exc}")
        return 1
    return 0
