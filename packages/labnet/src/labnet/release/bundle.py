"""``labnet bundle``: everything a lab's computers need to install a LabNet release, without internet.

A bundle is a folder::

    labnet-<version>-<site>/
      MANIFEST.json            the version, the site, which wheels each role uses, sha256 of every file
      wheels/*.whl             LabNet's packages, the driver packages, and (by default) every dependency
      site-<site>.zip          the lab's site folder: env templates, Caddyfile template, Lab PC inventories

Copy it to a share the lab's computers can read; ``labnet update --from <it>`` installs from it.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = "MANIFEST.json"
FORMAT = 1
# What a site folder may hold: templates and inventories, never a lab's secrets.
_SECRET = re.compile(r"(^|/)(\.env|[^/]*\.env|[^/]*\.(key|pem|p12|pfx)|logins\.txt)$", re.IGNORECASE)
LABNET_PACKAGES = ("labnet", "labnet-control-center")


class BundleError(RuntimeError):
    """The bundle can't be made or used; the message says why."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def project_name(project: Path) -> tuple[str, str]:
    """(name, version) from a package folder's pyproject.toml."""

    data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["name"], data["project"]["version"]


def _pip(*arguments: str) -> None:
    result = subprocess.run([sys.executable, "-m", "pip", "--disable-pip-version-check", *arguments],
                            capture_output=True, text=True)
    if result.returncode != 0:
        if "No module named pip" in result.stderr:
            raise BundleError("labnet bundle needs pip in this environment (uv: add pip, or uv pip install pip).")
        raise BundleError(f"pip {arguments[0]} failed:\n{result.stderr.strip()[-2000:]}")


def _wheel_file(folder: Path, name: str, version: str) -> Path:
    normalized = re.sub(r"[-_.]+", "_", name).lower()
    found = sorted(folder.glob(f"{normalized}-{version}-*.whl"))
    if not found:
        raise BundleError(f"pip did not produce a wheel for {name} {version}.")
    return found[0]


def check_site(site: Path) -> list[Path]:
    """The site folder's files; refuses one that holds anything secret-looking."""

    if not site.is_dir():
        raise BundleError(f"No site folder at {site}.")
    files = sorted(path for path in site.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    secrets = [path.relative_to(site).as_posix() for path in files if _SECRET.search(path.relative_to(site).as_posix())]
    if secrets:
        raise BundleError(f"{site} holds files a bundle must not carry (keys, logins, filled-in settings): "
                          f"{', '.join(secrets)}. A site folder has templates (*.env.example) only.")
    return files


@dataclass
class Bundle:
    source: Path  # the LabNet checkout: packages/labnet, packages/labnet-control-center, ...
    site: Path  # e.g. sites/pqt
    out: Path | None = None  # default: <source>/dist/labnet-<version>-<site>
    drivers: list[Path] = field(default_factory=list)  # driver package folders (labnet-sim by default)
    python_version: str = "3.11"
    platform: str = "win_amd64"
    dependencies: bool = True  # download every dependency wheel, for computers without internet

    def make(self) -> Path:
        source = self.source.resolve()
        labnet_name, version = project_name(source / "packages" / "labnet")
        site_name = self.site.resolve().name
        out = (self.out or source / "dist" / f"labnet-{version}-{site_name}").resolve()
        if out.exists():
            raise BundleError(f"{out} already exists; choose another --out or remove it.")
        site_files = check_site(self.site.resolve())
        drivers = [path.resolve() for path in (self.drivers or [source / "packages" / "labnet-sim"])]
        projects = [source / "packages" / name for name in LABNET_PACKAGES] + drivers
        for project in projects:
            name, project_version = project_name(project)
            if name in LABNET_PACKAGES and project_version != version:
                raise BundleError(f"{name} is {project_version} but labnet is {version}; they release together.")

        wheels = out / "wheels"
        wheels.mkdir(parents=True)
        try:
            print(f" ==> Building {len(projects)} wheels")
            _pip("wheel", "--no-deps", "--wheel-dir", str(wheels), *(str(project) for project in projects))
            packages = {}
            for project in projects:
                name, project_version = project_name(project)
                packages[name] = _wheel_file(wheels, name, project_version).name
            if self.dependencies:
                print(f" ==> Downloading dependencies for Python {self.python_version} on {self.platform}")
                _pip("download", "--dest", str(wheels), "--only-binary=:all:", "--platform", self.platform,
                     "--python-version", self.python_version, "--implementation", "cp",
                     f"{wheels / packages[labnet_name]}[central,lab-pc,certs]",
                     *(str(wheels / packages[name]) for name in packages if name != labnet_name))
            site_zip = out / f"site-{site_name}.zip"
            with zipfile.ZipFile(site_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in site_files:
                    archive.write(path, path.relative_to(self.site.resolve()).as_posix())
            files = {path.relative_to(out).as_posix(): sha256(path)
                     for path in sorted(out.rglob("*")) if path.is_file()}
            manifest = {
                "format": FORMAT,
                "labnet_version": version,
                "site": site_name,
                "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "git_revision": _git_revision(source),
                "python": self.python_version,
                "platform": self.platform,
                "dependencies": self.dependencies,
                "packages": packages,
                "drivers": [project_name(project)[0] for project in drivers],
                "files": files,
            }
            (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        except BaseException:
            shutil.rmtree(out, ignore_errors=True)
            raise
        return out


def _git_revision(source: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True)
    except OSError:
        return None
    return result.stdout.strip() or None


__all__ = ["Bundle", "BundleError", "MANIFEST", "check_site", "sha256"]
