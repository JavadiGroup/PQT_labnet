from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from labupdater.artifacts import ArtifactError, ReleaseManifest, RuntimeInstaller


def _manifest(artifact: Path, files: dict[str, str]) -> ReleaseManifest:
    wheel = b"wheel"
    return ReleaseManifest.parse(
        {
            "version": "0.3.0",
            "artifact_filename": artifact.name,
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "artifact_size": artifact.stat().st_size,
            "wheel_filename": "labnet-0.3.0-py3-none-any.whl",
            "wheel_sha256": hashlib.sha256(wheel).hexdigest(),
            "files": files,
            "roles": ["lab_pc"],
            "minimum_updater_version": "1.0.0",
        }
    )


def test_verify_rejects_changed_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "release.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("requirements.lock", "")
    manifest = _manifest(artifact, {})
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    installer = RuntimeInstaller(tmp_path / "store", install_timeout_seconds=1)
    with pytest.raises(ArtifactError, match="does not match"):
        installer.verify_artifact(artifact, manifest)


def test_stage_rejects_zip_path_traversal(tmp_path: Path) -> None:
    artifact = tmp_path / "release.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    manifest = _manifest(artifact, {})
    installer = RuntimeInstaller(tmp_path / "store", install_timeout_seconds=1)
    with pytest.raises(ArtifactError, match="Unsafe bundle path"):
        installer.stage(artifact, manifest, attempt_id="attempt-1")
    assert not (tmp_path / "escape.txt").exists()

