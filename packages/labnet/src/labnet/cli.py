"""``labnet``: one command, with subcommands from every installed LabNet package.

A package adds subcommands through the entry-point group ``labnet.commands``:
each entry is a ``register(subparsers)`` function that adds its parser and sets
``handler`` (a function of the parsed arguments returning an exit code).
"""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import entry_points
from typing import Sequence

GROUP = "labnet.commands"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="labnet", description="LabNet: device control for a lab network.")
    commands = root.add_subparsers(dest="command", metavar="<command>", required=True)
    for entry in sorted(entry_points(group=GROUP), key=lambda entry: entry.name):
        try:
            register = entry.load()
        except Exception as exc:  # one broken package must not take the others down
            print(f"labnet: skipping command {entry.name!r} ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue
        register(commands)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    return int(arguments.handler(arguments) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
