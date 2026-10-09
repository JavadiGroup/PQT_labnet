"""``labnet bundle`` and ``labnet update``: releasing LabNet to a lab's computers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def register_bundle(commands: argparse._SubParsersAction) -> None:
    bundle = commands.add_parser(
        "bundle", help="Make a release folder for a lab: wheels, the site, and their checksums.",
        description="Build LabNet's and the drivers' wheels (plus every dependency, for computers without "
                    "internet), zip the lab's site folder, and list it all with sha256s in MANIFEST.json.",
    )
    bundle.add_argument("--site", type=Path, required=True, help="The lab's site folder, e.g. sites/<lab>.")
    bundle.add_argument("--out", type=Path, help="Where to make it (default: dist/labnet-<version>-<site>).")
    bundle.add_argument("--source", type=Path, default=Path("."), help="The LabNet checkout (default: here).")
    bundle.add_argument("--driver", type=Path, action="append", default=[],
                        help="A driver package folder to include (repeatable; default: packages/labnet-sim).")
    bundle.add_argument("--python", default="3.11", help="The lab's Python version (default 3.11).")
    bundle.add_argument("--platform", default="win_amd64", help="The lab's platform tag (default win_amd64).")
    bundle.add_argument("--no-dependencies", action="store_true",
                        help="Only LabNet's wheels: the lab's computers install the rest from PyPI.")
    bundle.set_defaults(handler=_bundle)


def register_update(commands: argparse._SubParsersAction) -> None:
    from .update import DEFAULT_HOME, ROLES

    update = commands.add_parser(
        "update", help="Install or upgrade one LabNet role on this computer from a bundle.",
        description="Verify the bundle, refuse while the role runs, build a new environment beside the current "
                    "one, check it (Central: database backup and migrations; Lab PC: site and inventory), then "
                    "switch <home>/<role>-venv to it. The previous environment stays for rolling back.",
    )
    update.add_argument("--from", dest="bundle", type=Path, required=True, help="The bundle folder (a share is fine).")
    update.add_argument("--role", required=True, choices=ROLES)
    update.add_argument("--home", type=Path, default=DEFAULT_HOME, help=f"The install root (default {DEFAULT_HOME}).")
    update.add_argument("--python", default=sys.executable,
                        help="The interpreter new environments are made from (default: this one).")
    update.set_defaults(handler=_update)


def _bundle(arguments: argparse.Namespace) -> int:
    from .bundle import Bundle, BundleError

    try:
        out = Bundle(
            source=arguments.source, site=arguments.site, out=arguments.out, drivers=arguments.driver,
            python_version=arguments.python, platform=arguments.platform,
            dependencies=not arguments.no_dependencies,
        ).make()
    except BundleError as exc:
        print(f"labnet bundle: {exc}")
        return 1
    print(f"  ok Bundle ready: {out}\n"
          f"     On each computer: powershell -ExecutionPolicy Bypass -File <it>\\install.ps1 -Role <role>\n"
          f"     (or, anywhere labnet is installed: labnet update --from <it> --role <role>)")
    return 0


def _update(arguments: argparse.Namespace) -> int:
    from .bundle import BundleError
    from .update import Update

    try:
        venv = Update(bundle=arguments.bundle, role=arguments.role, home=arguments.home,
                      python=arguments.python).run()
    except BundleError as exc:
        print(f"labnet update: {exc}")
        return 1
    link = arguments.home / f"{arguments.role}-venv"
    print(f"  ok {arguments.role} updated ({venv.name}). Start it from {link}.")
    return 0
