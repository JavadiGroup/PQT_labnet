"""``labnet driver …``: build a driver package's protos (and labnet's own with ``--core``)."""

from __future__ import annotations

import argparse


def register(commands: argparse._SubParsersAction) -> None:
    driver = commands.add_parser("driver", help="Build LabNet device drivers.")
    actions = driver.add_subparsers(dest="driver_command", required=True)

    build = actions.add_parser("build", help="Generate protobuf code and device contracts from the protos.")
    build.add_argument("--check", action="store_true", help="Fail if the generated files are stale; write nothing.")
    build.add_argument(
        "--import-check", action="store_true", help="Then import every generated module in a fresh interpreter."
    )
    build.add_argument("--core", action="store_true", help="Build labnet's own protos (a labnet source checkout).")
    build.set_defaults(handler=_build)


def _build(arguments: argparse.Namespace) -> int:
    from .build import build, core_target, import_check, proto_files_of
    from .validate import ContractValidationError

    if not arguments.core:
        print("labnet driver build: only --core builds are available in this version of labnet.")
        return 2
    target = core_target()
    command = "labnet driver build --core"
    try:
        contracts = build(target, check=arguments.check, command=command)
        if arguments.import_check:
            import_check(target)
    except ContractValidationError as exc:
        print(f"labnet driver build: {exc}")
        return 1
    action = "Checked" if arguments.check else "Generated"
    print(f"{action} {len(proto_files_of(target))} protobuf schemas and {len(contracts)} device RPC contracts.")
    return 0
