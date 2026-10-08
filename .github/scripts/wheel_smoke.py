"""Check installed LabNet wheels: run with a clean venv's Python, outside the repository.

    python .github/scripts/wheel_smoke.py <repository root>

Catches what only shows up in a built wheel: missing package data (static
files, briefings, alembic, protos), a console script that doesn't start, or an
import that only worked from the source tree.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
from importlib import resources
from pathlib import Path

SCRIPTS = (
    "labnet-central",
    "labnet-central-admin",
    "labnet-lab-pc",
    "labnet-certs",
    "labnet-control-center",
    "labnet-control-center-admin",
    "labnet-watchdog",
    "labnet-runner",
)

# (installed package, its source folder, data folders that must ship complete)
PACKAGE_DATA = (
    ("labnet_control_center", "packages/labnet-control-center/src/labnet_control_center", ("static",)),
    ("labnet.central", "packages/labnet/src/labnet/central", ("alembic",)),
)
FILES = (
    ("labnet_control_center", "agent_briefing.md"),
    ("labnet_automation", "automation_briefing.md"),
    ("labnet.central", "alembic.ini"),
    ("labnet", "py.typed"),
)


def shipped(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def main(repository: Path) -> None:
    problems: list[str] = []
    import labnet
    import labnet_automation
    import labnet_control_center

    for module in (labnet, labnet_control_center, labnet_automation):
        if repository.resolve() in Path(module.__file__).resolve().parents:
            problems.append(f"{module.__name__} was imported from the repository, not the wheel")

    for script in SCRIPTS:
        path = shutil.which(script, path=sysconfig.get_path("scripts"))
        result = subprocess.run([path or script, "--help"], capture_output=True, text=True)
        if path is None or result.returncode != 0:
            problems.append(f"{script} --help failed: {result.stderr.strip()[-300:]}")

    for package, source, folders in PACKAGE_DATA:
        installed = Path(str(resources.files(package)))
        for folder in folders:
            missing = shipped(repository / source / folder) - shipped(installed / folder)
            problems += [f"{package}: {folder}/{name} is not in the wheel" for name in sorted(missing)]
    for package, name in FILES:
        if not resources.files(package).joinpath(name).is_file():
            problems.append(f"{package}: {name} is not in the wheel")

    protos = Path(sys.prefix) / "share" / "labnet" / "protos" / "labnet" / "devices" / "v1"
    missing = {p.name for p in (repository / "packages/labnet/protos/labnet/devices/v1").glob("*.proto")}
    missing -= {p.name for p in protos.glob("*.proto")}
    problems += [f"labnet: protos/{name} is not installed" for name in sorted(missing)]

    from labnet.contracts import registry

    contracts = registry.all()
    if not contracts or registry.problems():
        problems.append(f"registry: {len(contracts)} contracts, problems {registry.problems()}")

    if problems:
        print("\n".join(problems))
        sys.exit(1)
    print(f"Wheels OK: {len(SCRIPTS)} scripts start, package data complete, {len(contracts)} contracts.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
