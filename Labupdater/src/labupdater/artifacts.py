from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


class ArtifactError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    version: str
    artifact_filename: str
    artifact_sha256: str
    artifact_size: int
    wheel_filename: str
    wheel_sha256: str
    files: dict[str, str]
    roles: tuple[str, ...]
    minimum_updater_version: str
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: object) -> "ReleaseManifest":
        if not isinstance(raw, dict):
            raise ArtifactError("Update directive did not contain a manifest object.")
        required = {
            "version",
            "artifact_filename",
            "artifact_sha256",
            "artifact_size",
            "wheel_filename",
            "wheel_sha256",
            "files",
            "roles",
            "minimum_updater_version",
        }
        missing = required.difference(raw)
        if missing:
            raise ArtifactError(f"Release manifest is missing {sorted(missing)!r}.")
        version = str(raw["version"])
        if _SAFE_VERSION.fullmatch(version) is None:
            raise ArtifactError("Release version is not safe for a directory name.")
        files_raw = raw["files"]
        if not isinstance(files_raw, dict):
            raise ArtifactError("Release manifest files must be an object.")
        files = {str(name): str(digest) for name, digest in files_raw.items()}
        for name, digest in files.items():
            _safe_relative_path(name)
            if _SHA256.fullmatch(digest) is None:
                raise ArtifactError(f"Invalid SHA-256 for bundle member {name!r}.")
        artifact_digest = str(raw["artifact_sha256"])
        wheel_digest = str(raw["wheel_sha256"])
        if _SHA256.fullmatch(artifact_digest) is None or _SHA256.fullmatch(wheel_digest) is None:
            raise ArtifactError("Release manifest contains an invalid SHA-256 digest.")
        roles = raw["roles"]
        if not isinstance(roles, list) or not all(isinstance(item, str) for item in roles):
            raise ArtifactError("Release manifest roles must be a string list.")
        size = int(raw["artifact_size"])
        if size <= 0:
            raise ArtifactError("Release artifact size must be positive.")
        artifact_filename = str(raw["artifact_filename"])
        wheel_filename = str(raw["wheel_filename"])
        if PurePosixPath(artifact_filename).name != artifact_filename or "\\" in artifact_filename:
            raise ArtifactError("Release artifact filename is unsafe.")
        if PurePosixPath(wheel_filename).name != wheel_filename or "\\" in wheel_filename:
            raise ArtifactError("Release wheel filename is unsafe.")
        return cls(
            version=version,
            artifact_filename=artifact_filename,
            artifact_sha256=artifact_digest,
            artifact_size=size,
            wheel_filename=wheel_filename,
            wheel_sha256=wheel_digest,
            files=files,
            roles=tuple(roles),
            minimum_updater_version=str(raw["minimum_updater_version"]),
            raw=dict(raw),
        )


class RuntimeInstaller:
    def __init__(
        self,
        release_root: Path,
        *,
        install_timeout_seconds: float,
        python_executable: Path | None = None,
    ) -> None:
        self.release_root = release_root.expanduser().resolve()
        self.runtimes_root = self.release_root / "runtimes"
        self.work_root = self.release_root / "work"
        self.download_root = self.release_root / "downloads"
        self.install_timeout_seconds = install_timeout_seconds
        self.python_executable = python_executable or Path(sys.executable)

    def ensure_roots(self) -> None:
        self.runtimes_root.mkdir(parents=True, exist_ok=True)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.download_root.mkdir(parents=True, exist_ok=True)

    def download_path(self, attempt_id: str, manifest: ReleaseManifest) -> Path:
        self.ensure_roots()
        safe_attempt = _safe_identifier(attempt_id)
        return self.download_root / f"{safe_attempt}-{manifest.artifact_filename}"

    def verify_artifact(self, path: Path, manifest: ReleaseManifest) -> None:
        if not path.is_file():
            raise ArtifactError("Downloaded release artifact is missing.")
        size = path.stat().st_size
        digest = sha256_file(path)
        if size != manifest.artifact_size or digest != manifest.artifact_sha256:
            raise ArtifactError(
                "Downloaded artifact does not match the signed Central manifest."
            )
        if not zipfile.is_zipfile(path):
            raise ArtifactError("Downloaded release artifact is not a ZIP bundle.")

    def stage(
        self,
        artifact: Path,
        manifest: ReleaseManifest,
        *,
        attempt_id: str,
    ) -> Path:
        self.ensure_roots()
        if "lab_pc" not in manifest.roles:
            raise ArtifactError("The assigned release is not intended for Lab PCs.")
        self.verify_artifact(artifact, manifest)
        runtime = (self.runtimes_root / manifest.version).resolve()
        _require_child(runtime, self.runtimes_root)
        marker = runtime / "runtime.json"
        if marker.is_file():
            installed = json.loads(marker.read_text(encoding="utf-8"))
            if installed.get("artifact_sha256") != manifest.artifact_sha256:
                raise ArtifactError(
                    "An immutable runtime already exists with a different artifact hash."
                )
            return runtime
        if runtime.exists():
            shutil.rmtree(runtime)

        work = (self.work_root / _safe_identifier(attempt_id)).resolve()
        _require_child(work, self.work_root)
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=False)
        payload = work / "payload"
        payload.mkdir()
        try:
            _safe_extract(artifact, payload)
            _verify_payload(payload, manifest)
            runtime.mkdir(parents=False)
            venv = runtime / "venv"
            self._run([str(self.python_executable), "-m", "venv", str(venv)])
            python = runtime_python(runtime)
            wheelhouse = payload / "wheelhouse"
            lock = payload / "requirements.lock"
            if not lock.is_file() or not wheelhouse.is_dir():
                raise ArtifactError(
                    "Release bundle must contain requirements.lock and wheelhouse/."
                )
            self._run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-index",
                    "--require-hashes",
                    "--find-links",
                    str(wheelhouse),
                    "-r",
                    str(lock),
                ]
            )
            self._run(
                [
                    str(python),
                    "-c",
                    (
                        "from importlib.metadata import version; "
                        f"assert version('labnet') == {manifest.version!r}; "
                        "import lab_pc.main, labnet"
                    ),
                ]
            )
            _atomic_json(
                marker,
                {
                    "version": manifest.version,
                    "artifact_sha256": manifest.artifact_sha256,
                    "wheel_sha256": manifest.wheel_sha256,
                },
            )
            return runtime
        except BaseException:
            if runtime.exists() and not marker.exists():
                shutil.rmtree(runtime, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, command: list[str]) -> None:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.install_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ArtifactError(f"Runtime command failed to execute: {command[0]}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout)[-4000:]
            raise ArtifactError(
                f"Runtime command failed ({completed.returncode}): {detail}"
            )


def runtime_python(runtime: Path) -> Path:
    if os.name == "nt":
        return runtime / "venv" / "Scripts" / "python.exe"
    return runtime / "venv" / "bin" / "python"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            relative = _safe_relative_path(member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ArtifactError("Release bundles may not contain symbolic links.")
            destination = (target / Path(*relative.parts)).resolve()
            _require_child(destination, target)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)


def _verify_payload(payload: Path, manifest: ReleaseManifest) -> None:
    actual = {
        path.relative_to(payload).as_posix(): sha256_file(path)
        for path in payload.rglob("*")
        if path.is_file()
    }
    if actual != manifest.files:
        missing = sorted(set(manifest.files).difference(actual))
        unexpected = sorted(set(actual).difference(manifest.files))
        changed = sorted(
            name
            for name in set(actual).intersection(manifest.files)
            if actual[name] != manifest.files[name]
        )
        raise ArtifactError(
            f"Bundle member hashes failed verification; missing={missing}, "
            f"unexpected={unexpected}, changed={changed}."
        )
    wheel_path = payload / "wheelhouse" / manifest.wheel_filename
    if not wheel_path.is_file() or sha256_file(wheel_path) != manifest.wheel_sha256:
        raise ArtifactError("The LabNet wheel does not match the release manifest.")


def _safe_relative_path(value: str) -> PurePosixPath:
    if "\\" in value:
        raise ArtifactError("Bundle paths must use forward slashes.")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ArtifactError(f"Unsafe bundle path {value!r}.")
    return path


def _safe_identifier(value: str) -> str:
    if not value or re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None:
        raise ArtifactError("Update attempt identifier is not filesystem-safe.")
    return value


def _require_child(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ArtifactError("Release path escaped the configured release root.") from exc


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


__all__ = [
    "ArtifactError",
    "ReleaseManifest",
    "RuntimeInstaller",
    "runtime_python",
    "sha256_file",
]
