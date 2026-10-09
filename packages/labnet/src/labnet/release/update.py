"""``labnet update --from <bundle> --role <role>``: install or upgrade one role on a lab computer.

1. Verify every file of the bundle against MANIFEST.json.
2. Refuse while the role is running (its health port answers).
3. Build a new virtual environment beside the current one:
   ``<home>/venvs/<role>-<version>-<time>``, from the bundle's wheels.
4. Check it before anything changes: Central backs up its SQLite database and
   applies schema migrations; a Lab PC unpacks the site and builds its
   inventory's devices (without connecting) to check each has a driver; the
   Control Center loads its settings.
5. Switch over: ``<home>/<role>-venv`` (what services start) now points at
   the new environment. The previous one stays, for rolling back.

Each role keeps its settings in ``<home>/<role>.env``; updates never touch it.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from .bundle import MANIFEST, BundleError, sha256

WINDOWS = os.name == "nt"
DEFAULT_HOME = Path("C:/ProgramData/LabNet") if WINDOWS else Path("/opt/labnet")
ROLES = ("central", "lab-pc", "control-center", "client")
# role -> (settings key of its health port, default port)
HEALTH = {
    "central": ("LABNET_CENTRAL_BIND_PORT", 8008),
    "lab-pc": ("LABNET_HTTP_PORT", 9001),
    "control-center": ("LABNET_CONTROL_CENTER_PORT", 8080),
}
KEEP_ENVIRONMENTS = 3  # the current one and the two before it


def _say(kind: str, message: str) -> None:
    print(f"{kind:>4} {message}", flush=True)


def _running(port: int) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=2):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        return True  # it answered
    except (urllib.error.URLError, OSError):
        return False


def read_manifest(bundle: Path) -> dict:
    path = bundle / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BundleError(f"No {MANIFEST} in {bundle}; is it a bundle from labnet bundle?") from None
    if manifest.get("format") != 1:
        raise BundleError(f"{path} is format {manifest.get('format')!r}; this labnet reads format 1.")
    return manifest


def verify(bundle: Path, manifest: dict) -> None:
    """Every file the manifest lists, unchanged; nothing else is used."""

    bad = []
    for name, expected in sorted(manifest["files"].items()):
        path = bundle / name
        if not path.is_file():
            bad.append(f"{name} is missing")
        elif sha256(path) != expected:
            bad.append(f"{name} does not match its sha256")
    if bad:
        raise BundleError("This bundle is damaged or was changed; do not install it: " + "; ".join(bad))


def requirements(manifest: dict, role: str, wheels: Path) -> list[str]:
    """What the role installs: labnet with the role's extra, the Control Center, and the drivers."""

    packages = manifest["packages"]
    core = str(wheels / packages["labnet"])
    drivers = [str(wheels / packages[name]) for name in manifest["drivers"]]
    return {
        "central": [f"{core}[central]"],
        "lab-pc": [f"{core}[lab-pc]", *drivers],
        "control-center": [core, str(wheels / packages["labnet-control-center"]), *drivers],
        "client": [core, *drivers],
    }[role]


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if WINDOWS else "bin/python")


def point(link: Path, target: Path) -> None:
    """Make ``link`` (a junction on Windows, a symlink elsewhere) lead to ``target``."""

    if link.is_symlink() or _is_junction(link):
        if WINDOWS:
            os.rmdir(link)  # removes the junction, not what it points at
        else:
            temporary = link.with_name(f".{link.name}.new")
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(target, target_is_directory=True)
            os.replace(temporary, link)
            return
    elif link.exists():
        raise BundleError(f"{link} is a folder, not a link; move it away first.")
    if WINDOWS:
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if result.returncode != 0:
            raise BundleError(f"Could not link {link} to {target}: {result.stdout}{result.stderr}")
    else:
        link.symlink_to(target, target_is_directory=True)


def _is_junction(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)  # Python 3.12+
    if isjunction is not None:
        return isjunction(path)
    if not WINDOWS:
        return False
    try:
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False


def backup_sqlite(settings: dict, version: str, home: Path) -> Path | None:
    url = settings.get("LABNET_DATABASE_URL") or ""
    if not url.startswith("sqlite:///"):
        return None
    database = Path(url.removeprefix("sqlite:///"))
    if not database.is_absolute():
        database = home / database
    if not database.is_file():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = database.with_name(f"{database.name}.before-{version}.{stamp}")
    shutil.copy2(database, backup)
    return backup


def _replace_tree(archive: Path, target: Path) -> None:
    """Unpack into target.new, keep the old tree as target.previous, then swap."""

    staging = target.with_name(f"{target.name}.new")
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(staging)
    if target.exists():
        previous = target.with_name(f"{target.name}.previous")
        shutil.rmtree(previous, ignore_errors=True)
        target.rename(previous)
    staging.rename(target)


def _run(argv: list[str], what: str) -> str:
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise BundleError(f"{what} failed:\n{(result.stdout + result.stderr).strip()[-2000:]}")
    return result.stdout


_INVENTORY_CHECK = """
import sys
from labnet.contracts import registry
from labnet.lab_pc.app import load_device_factory
from labnet.lab_pc.config import LabPCSettings
settings = LabPCSettings.from_env(sys.argv[1])
devices = list(load_device_factory(settings.device_inventory)())  # built, not connected
missing = sorted({d.device_type for d in devices if registry.find(d.device_type) is None})
if missing:
    sys.exit("no driver installed for: " + ", ".join(missing))
print(f"{len(devices)} devices from {settings.device_inventory}")
"""


@dataclass
class Update:
    bundle: Path
    role: str
    home: Path = DEFAULT_HOME
    python: str = sys.executable  # the interpreter new environments are made from

    def run(self) -> Path:
        if self.role not in ROLES:
            raise BundleError(f"Role must be one of {', '.join(ROLES)}.")
        bundle, home, role = self.bundle.resolve(), self.home.resolve(), self.role
        manifest = read_manifest(bundle)
        version = manifest["labnet_version"]
        _say("==>", f"Verifying LabNet {version} ({manifest['site']}) from {bundle}")
        verify(bundle, manifest)
        _say("ok", f"{len(manifest['files'])} files match {MANIFEST}")

        settings_file = home / f"{role}.env"
        settings = {key: value for key, value in dotenv_values(settings_file).items() if value is not None} \
            if settings_file.is_file() else {}
        self._refuse_while_running(settings, home)
        if role == "central" and not settings_file.is_file():
            raise BundleError(f"Create {settings_file} first (template: central.env.example in the site).")

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        venv = home / "venvs" / f"{role}-{version}-{stamp}"
        _say("==>", f"Building {venv}")
        venv.parent.mkdir(parents=True, exist_ok=True)
        try:
            _run([*self.python.split(" "), "-m", "venv", str(venv)] if " " in self.python
                 else [self.python, "-m", "venv", str(venv)], "Creating the environment")
            python = venv_python(venv)
            wheels = bundle / "wheels"
            source = ["--no-index"] if manifest.get("dependencies") else []
            _run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *source,
                  "--find-links", str(wheels), *requirements(manifest, role, wheels)], "Installing")
            _say("ok", f"installed LabNet {version} for {role}")
            self._check(python, settings_file, settings, manifest, bundle, home)
        except BaseException:
            shutil.rmtree(venv, ignore_errors=True)  # the current environment is untouched
            raise

        link = home / f"{role}-venv"
        previous = os.path.realpath(link) if link.exists() else None
        point(link, venv)
        _say("ok", f"{link} -> {venv.name}" + (f" (was {Path(previous).name})" if previous else ""))
        self._prune(role, home, keep={venv, Path(previous) if previous else venv})
        record = {**manifest, "installed": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                  "environment": str(venv), "previous_environment": previous}
        (home / f"{role}-RELEASE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return venv

    def _refuse_while_running(self, settings: dict, home: Path) -> None:
        checks = []
        if self.role in HEALTH:
            key, default = HEALTH[self.role]
            checks.append((self.role, int(settings.get(key, default))))
        if self.role == "control-center":  # the watchdog runs from the same environment
            watchdog = home / "watchdog.env"
            values = dotenv_values(watchdog) if watchdog.is_file() else {}
            checks.append(("the safe-state watchdog", int(values.get("LABNET_WATCHDOG_PORT") or 8081)))
        for name, port in checks:
            if _running(port):
                raise BundleError(f"{name} is still running (answers on 127.0.0.1:{port}). Stop it, then run this again.")

    def _check(self, python: Path, settings_file: Path, settings: dict, manifest: dict, bundle: Path, home: Path) -> None:
        version = manifest["labnet_version"]
        if self.role == "central":
            backup = backup_sqlite(settings, version, home)
            if backup:
                _say("ok", f"database backed up to {backup}")
            elif not (settings.get("LABNET_DATABASE_URL") or "").startswith("sqlite"):
                _say("warn", "Not SQLite: back up the database with its own tools first.")
            _run([str(python), "-m", "labnet.central.manage", "--env-file", str(settings_file), "db", "upgrade"],
                 "Applying Central's schema migrations")
            _say("ok", "schema migrations applied")
        elif self.role == "lab-pc":
            _replace_tree(bundle / f"site-{manifest['site']}.zip", home / "site")
            _say("ok", f"site installed in {home / 'site'} (the previous one in site.previous)")
            if settings_file.is_file():
                out = _run([str(python), "-c", _INVENTORY_CHECK, str(settings_file)], "Checking the inventory")
                _say("ok", out.strip())
            else:
                _say("warn", f"No {settings_file} yet: start from lab-pc.env.example and setup --write-env.")
        elif self.role == "control-center":
            if settings_file.is_file():
                _run([str(python), "-c", "import sys; from labnet_control_center.config import ControlCenterSettings "
                      "as S; S.from_env(sys.argv[1], search_cwd=False)", str(settings_file)],
                     "Checking control-center.env")
                _say("ok", "settings load")
            else:
                _say("warn", f"No {settings_file} yet: start from control-center.env.example.")

    def _prune(self, role: str, home: Path, keep: set[Path]) -> None:
        environments = sorted((home / "venvs").glob(f"{role}-*"), key=lambda path: path.stat().st_mtime)
        for old in environments[:-KEEP_ENVIRONMENTS]:
            if old not in keep:
                shutil.rmtree(old, ignore_errors=True)


__all__ = ["DEFAULT_HOME", "ROLES", "Update", "point", "read_manifest", "requirements", "verify"]
