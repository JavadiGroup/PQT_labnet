"""``labnet driver …``: build a driver package's protos (and labnet's own with ``--core``)."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

ENTRY_POINT_GROUP = "labnet.devices"


def register(commands: argparse._SubParsersAction) -> None:
    driver = commands.add_parser("driver", help="Build LabNet device drivers.")
    actions = driver.add_subparsers(dest="driver_command", required=True)

    build = actions.add_parser("build", help="Generate protobuf code, device contracts and facades from the protos.")
    build.add_argument("--check", action="store_true", help="Fail if the generated files are stale; write nothing.")
    build.add_argument(
        "--import-check", action="store_true", help="Then import every generated module in a fresh interpreter."
    )
    build.add_argument("--core", action="store_true", help="Build labnet's own protos (a labnet source checkout).")
    build.add_argument(
        "--project", type=Path, default=Path("."), help="The driver package's folder (default: this one)."
    )
    build.set_defaults(handler=_build)

    new = actions.add_parser("new", help="Start a driver package from LabNet's template.")
    new.add_argument("name", help="The package's pip name, like acme-thing.")
    new.add_argument("--service", required=True, help="The device family, like AcmeThing (its gRPC service: AcmeThingService).")
    new.add_argument("--directory", type=Path, help="Where to create it (default: ./<name>).")
    new.set_defaults(handler=_new)


def _new(arguments: argparse.Namespace) -> int:
    from .new import DriverNames, create
    from .validate import ContractValidationError

    directory = arguments.directory or Path(arguments.name)
    try:
        names = DriverNames.of(arguments.name, arguments.service)
        create(directory, names)
    except ContractValidationError as exc:
        print(f"labnet driver new: {exc}")
        return 1
    print(
        f"Created {names.distribution} in {directory}: device type {names.device_type}, "
        f"service {names.package}.v1.{names.service}.\n"
        "Next:\n"
        f"  cd {directory}\n"
        '  pip install -e ".[dev]"\n'
        "  labnet driver build\n"
        "  pytest"
    )
    return 0


def entry_point_problems(project: Path, python_package: str, contracts_module: str, contracts) -> list[str]:
    """What the package's ``[project.entry-points."labnet.devices"]`` lacks or has wrong."""

    from .render import _variable
    from .validate import _snake_case

    data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    declared = data.get("project", {}).get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
    expected = {}
    for spec in contracts:
        base = spec.service_name.removesuffix("Service")
        expected[_snake_case(base)] = f"{python_package}.{contracts_module}:{_variable(spec.service_name)}"
    problems = [
        f'{name} = "{value}"' for name, value in sorted(expected.items()) if declared.get(name) != value
    ]
    if problems:
        problems.insert(0, f'Add to [project.entry-points."{ENTRY_POINT_GROUP}"] in pyproject.toml, then reinstall:')
    extra = sorted(set(declared) - set(expected))
    if extra:
        problems.append(f"These entry points name no contract this package builds: {', '.join(extra)}.")
    return problems


def _build(arguments: argparse.Namespace) -> int:
    from .build import build, core_target, import_check, proto_files_of, target_from_project
    from .validate import ContractValidationError

    try:
        if arguments.core:
            target, command = core_target(), "labnet driver build --core"
        else:
            target, command = target_from_project(arguments.project), "labnet driver build"
        contracts = build(target, check=arguments.check, command=command)
        if arguments.import_check:
            import_check(target)
    except ContractValidationError as exc:
        print(f"labnet driver build: {exc}")
        return 1
    services = {spec.service_full_name for spec in contracts}
    action = "Checked" if arguments.check else "Generated"
    print(
        f"{action} {len(proto_files_of(target))} protobuf schemas, {len(services)} device "
        f"families and {len(contracts)} RPC contracts."
    )
    if not arguments.core:
        problems = entry_point_problems(
            arguments.project.resolve(), target.python_package, target.contracts_module, contracts
        )
        if problems:
            print("\n".join(problems))
            return 1
    return 0
