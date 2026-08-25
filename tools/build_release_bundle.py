from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a hashed, offline-installable LabNet release bundle."
    )
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument(
        "--role",
        action="append",
        choices=("lab_pc", "client", "central"),
        default=[],
    )
    parser.add_argument(
        "--extras",
        default="lab-pc",
        help="Comma-separated LabNet extras resolved into the wheelhouse.",
    )
    parser.add_argument(
        "--wheelhouse",
        type=Path,
        help="Use an existing complete wheelhouse instead of invoking pip download.",
    )
    parser.add_argument("--contract-digest", required=True)
    parser.add_argument("--minimum-updater-version", default="1.0.0")
    return parser


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    wheel = args.wheel.expanduser().resolve()
    if not wheel.is_file():
        raise SystemExit(f"Wheel does not exist: {wheel}")
    distribution, version, _build, _tags = parse_wheel_filename(wheel.name)
    if canonicalize_name(str(distribution)) != "labnet":
        raise SystemExit("--wheel must be a LabNet wheel.")
    version_text = str(version)
    _validate_labnet_wheel(wheel, version_text)
    roles = list(dict.fromkeys(args.role or ["lab_pc", "client"]))
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact_name = f"labnet-{version_text}-bundle.zip"
    artifact = output / artifact_name
    manifest_path = output / f"labnet-{version_text}-manifest.json"
    if artifact.exists() or manifest_path.exists():
        raise SystemExit(
            "Refusing to overwrite an existing release artifact or manifest."
        )

    with tempfile.TemporaryDirectory(prefix="labnet-release-") as temporary:
        payload = Path(temporary) / "payload"
        wheelhouse = payload / "wheelhouse"
        wheelhouse.mkdir(parents=True)
        if args.wheelhouse:
            source = args.wheelhouse.expanduser().resolve()
            for candidate in source.glob("*.whl"):
                shutil.copy2(candidate, wheelhouse / candidate.name)
            if not (wheelhouse / wheel.name).exists():
                shutil.copy2(wheel, wheelhouse / wheel.name)
        else:
            extras = f"[{args.extras}]" if args.extras else ""
            requirement = f"{wheel}{extras}"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "--disable-pip-version-check",
                    "--only-binary=:all:",
                    "--dest",
                    str(wheelhouse),
                    requirement,
                ],
                check=False,
            )
            if completed.returncode != 0:
                raise SystemExit("pip could not resolve a complete binary wheelhouse.")

        wheels = sorted(wheelhouse.glob("*.whl"), key=lambda item: item.name.lower())
        if not wheels:
            raise SystemExit("The release wheelhouse is empty.")
        lock_lines: list[str] = []
        seen: set[str] = set()
        for candidate in wheels:
            name, candidate_version, _candidate_build, _candidate_tags = (
                parse_wheel_filename(candidate.name)
            )
            normalized = canonicalize_name(str(name))
            if normalized in seen:
                raise SystemExit(
                    f"Wheelhouse has more than one wheel for distribution {normalized}."
                )
            seen.add(normalized)
            lock_lines.append(
                f"{normalized}=={candidate_version} "
                f"--hash=sha256:{sha256_file(candidate)}"
            )
        (payload / "requirements.lock").write_text(
            "\n".join(lock_lines) + "\n", encoding="utf-8"
        )

        file_hashes = {
            path.relative_to(payload).as_posix(): sha256_file(path)
            for path in sorted(payload.rglob("*"))
            if path.is_file()
        }
        temporary_artifact = output / f".{artifact_name}.tmp"
        temporary_artifact.unlink(missing_ok=True)
        with zipfile.ZipFile(
            temporary_artifact, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for path in sorted(payload.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(payload).as_posix())
        temporary_artifact.replace(artifact)

    wheel_digest = next(
        digest
        for name, digest in file_hashes.items()
        if name == f"wheelhouse/{wheel.name}"
    )
    manifest = {
        "schema_version": 1,
        "package": "labnet",
        "version": version_text,
        "artifact_filename": artifact.name,
        "artifact_sha256": sha256_file(artifact),
        "artifact_size": artifact.stat().st_size,
        "wheel_filename": wheel.name,
        "wheel_sha256": wheel_digest,
        "files": file_hashes,
        "git_commit": args.git_commit,
        "roles": roles,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform_tag": sysconfig.get_platform().replace("-", "_"),
        "contract_digest": args.contract_digest,
        "minimum_updater_version": args.minimum_updater_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signature": None,
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(artifact)
    print(manifest_path)


def _validate_labnet_wheel(path: Path, expected_version: str) -> None:
    if not zipfile.is_zipfile(path):
        raise SystemExit("The supplied LabNet wheel is not a valid ZIP wheel.")
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise SystemExit("The LabNet wheel must contain exactly one METADATA file.")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
    if canonicalize_name(metadata.get("Name", "")) != "labnet":
        raise SystemExit("Wheel METADATA name is not labnet.")
    if metadata.get("Version") != expected_version:
        raise SystemExit("Wheel filename and METADATA versions do not match.")


if __name__ == "__main__":
    main()
