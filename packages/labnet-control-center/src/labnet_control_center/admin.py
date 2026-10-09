"""Control Center administration from the command line (run it on the Control Center PC).

    labnet-control-center-admin --env-file control-center.env runner add lab-laptop
    labnet-control-center-admin --env-file control-center.env runner list
    labnet-control-center-admin --env-file control-center.env runner revoke lab-laptop

``runner add`` prints the new runner's token once: put it in the runner's
settings. The Control Center keeps only its sha256, in
``<LABNET_AUTOMATION_DIR>/state/runners/``, so a lost token is replaced by
revoking the runner and adding it again.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .automation_runners import RunnerRegistry
from .automation_store import AutomationError
from .config import ControlCenterSettings


PROG = "labnet-control-center-admin"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="Administer the LabNet Control Center.")
    parser.add_argument(
        "--env-file", type=Path, help="Settings file such as control-center.env (process environment wins)."
    )
    sections = parser.add_subparsers(dest="section", required=True)
    runner = sections.add_parser("runner", help="Automation runners and their tokens.")
    actions = runner.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="Register a runner and print its token (shown only once).")
    add.add_argument("name", help="Lowercase letters, digits and dashes, e.g. lab-laptop.")
    actions.add_parser("list", help="List the registered runners.")
    revoke = actions.add_parser("revoke", help="Revoke a runner's token at once.")
    revoke.add_argument("name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        settings = ControlCenterSettings.from_env(arguments.env_file)
    except (ValueError, FileNotFoundError) as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    registry = RunnerRegistry(settings.automation_dir / "state" / "runners")
    try:
        if arguments.action == "add":
            token = registry.add(arguments.name)
            print(f"Runner {arguments.name} added. Its token is shown only now; put it in the runner's settings:")
            print()
            print(f"    {token}")
            print()
            print("The Control Center keeps only a hash of it. If it is lost, revoke the runner and add it again.")
        elif arguments.action == "list":
            runners = registry.list()
            if not runners:
                print("No runners are registered.")
            for item in runners:
                print(f"{item['name']}\tadded {item['created_at']}")
        elif arguments.action == "revoke":
            registry.revoke(arguments.name)
            print(f"Runner {arguments.name} revoked; its token no longer works.")
    except AutomationError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
