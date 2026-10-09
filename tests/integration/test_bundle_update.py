"""`labnet bundle` and `labnet update`: what they check, without downloading anything.

The full round trip (a bundle with every dependency, then each role installed
from it) needs PyPI and minutes; it is run by hand before a release.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

from labnet.release.bundle import INSTALLER, Bundle, BundleError, check_site, clean_copy, sha256
from labnet.release.update import Update, backup_sqlite, point, read_manifest, requirements, verify

REPO = Path(__file__).resolve().parents[2]


def _site(tmp_path: Path) -> Path:
    site = tmp_path / "acme"
    (site / "inventories").mkdir(parents=True)
    (site / "central.env.example").write_text("LABNET_DATABASE_URL=sqlite:///labnet.db\n", encoding="utf-8")
    (site / "inventories" / "lab_pc_01.py").write_text("def create_devices():\n    return []\n", encoding="utf-8")
    return site


@pytest.mark.parametrize("secret", ["central.env", ".env", "certs/central.key", "ca.pem", "logins.txt"])
def test_a_site_folder_with_secrets_is_refused(tmp_path: Path, secret: str) -> None:
    site = _site(tmp_path)
    (site / secret).parent.mkdir(parents=True, exist_ok=True)
    (site / secret).write_text("secret", encoding="utf-8")
    with pytest.raises(BundleError, match="must not carry"):
        check_site(site)


def test_every_site_in_the_repository_is_templates_only() -> None:
    sites = [path for path in (REPO / "sites").iterdir() if path.is_dir()]
    assert sites
    for site in sites:
        assert check_site(site)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bundle")
    return Bundle(source=REPO, site=_site(root), out=root / "out", dependencies=False).make()


def test_a_bundle_lists_every_file_with_its_sha256(bundle: Path) -> None:
    manifest = read_manifest(bundle)
    assert manifest["site"] == "acme" and manifest["dependencies"] is False
    assert set(manifest["packages"]) == {"labnet", "labnet-control-center", "labnet-sim"}
    assert manifest["drivers"] == ["labnet-sim"] and INSTALLER in manifest["files"]
    on_disk = {path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()} - {"MANIFEST.json"}
    assert set(manifest["files"]) == on_disk
    with zipfile.ZipFile(bundle / "site-acme.zip") as archive:
        assert sorted(archive.namelist()) == ["central.env.example", "inventories/lab_pc_01.py"]
    verify(bundle, manifest)


def test_wheels_carry_only_modules_in_the_source(bundle: Path) -> None:
    """A build/ left by an earlier build must not put deleted modules back into a release."""

    manifest = read_manifest(bundle)
    for name, folder in (("labnet", "packages/labnet/src"), ("labnet-control-center", "packages/labnet-control-center/src")):
        with zipfile.ZipFile(bundle / "wheels" / manifest["packages"][name]) as wheel:
            shipped = [entry for entry in wheel.namelist() if ".dist-info/" not in entry]
        stale = [entry for entry in shipped if not (REPO / folder / entry).is_file()]
        assert not stale, f"{name} wheel has files not in {folder}: {stale[:5]}"


def test_a_clean_copy_leaves_build_leftovers_behind(tmp_path: Path) -> None:
    project = tmp_path / "pkg"
    for leftover in ("build/lib/pkg/old.py", "src/pkg.egg-info/SOURCES.txt", "src/pkg/__pycache__/x.pyc"):
        (project / leftover).parent.mkdir(parents=True, exist_ok=True)
        (project / leftover).write_text("", encoding="utf-8")
    (project / "src/pkg/new.py").write_text("", encoding="utf-8")
    copy = clean_copy(project, tmp_path / "copy")
    assert sorted(path.relative_to(copy).as_posix() for path in copy.rglob("*") if path.is_file()) == ["src/pkg/new.py"]


def test_a_changed_or_missing_file_is_refused(bundle: Path, tmp_path: Path) -> None:
    manifest = read_manifest(bundle)
    tampered = {**manifest, "files": {**manifest["files"], "site-acme.zip": "0" * 64, "wheels/gone.whl": "1" * 64}}
    with pytest.raises(BundleError, match="site-acme.zip does not match its sha256; wheels/gone.whl is missing"):
        verify(bundle, tampered)
    with pytest.raises(BundleError, match="No MANIFEST.json"):
        read_manifest(tmp_path)


def test_each_role_installs_what_it_needs(bundle: Path) -> None:
    manifest = read_manifest(bundle)
    wheels = bundle / "wheels"
    names = {role: [Path(item).name for item in requirements(manifest, role, wheels)]
             for role in ("central", "lab-pc", "control-center", "client")}
    core = manifest["packages"]["labnet"]
    assert names["central"] == [f"{core}[central]"]
    assert names["lab-pc"] == [f"{core}[lab-pc]", manifest["packages"]["labnet-sim"]]
    assert manifest["packages"]["labnet-control-center"] in names["control-center"]
    assert names["client"] == [core, manifest["packages"]["labnet-sim"]]


def test_the_role_link_switches_and_never_replaces_a_folder(tmp_path: Path) -> None:
    first, second = tmp_path / "venvs" / "a", tmp_path / "venvs" / "b"
    for venv in (first, second):
        (venv / "marker").mkdir(parents=True)
    link = tmp_path / "central-venv"
    point(link, first)
    assert (link / "marker").is_dir() and Path(os.path.realpath(link)) == first.resolve()
    point(link, second)
    assert Path(os.path.realpath(link)) == second.resolve() and first.is_dir()  # the previous one stays
    folder = tmp_path / "lab-pc-venv"
    folder.mkdir()
    with pytest.raises(BundleError, match="is a folder, not a link"):
        point(folder, first)


def test_a_sqlite_database_is_backed_up_before_migrating(tmp_path: Path) -> None:
    database = tmp_path / "labnet.db"
    database.write_bytes(b"data")
    backup = backup_sqlite({"LABNET_DATABASE_URL": f"sqlite:///{database.as_posix()}"}, "3.0.0", tmp_path)
    assert backup is not None and backup.read_bytes() == b"data" and backup.name.startswith("labnet.db.before-3.0.0.")
    assert backup_sqlite({"LABNET_DATABASE_URL": "postgresql://lab"}, "3.0.0", tmp_path) is None


def test_a_running_role_is_never_updated(bundle: Path, tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = http.server.HTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        (tmp_path / "lab-pc.env").write_text(f"LABNET_HTTP_PORT={port}\n", encoding="utf-8")
        with pytest.raises(BundleError, match=f"lab-pc is still running \\(answers on 127.0.0.1:{port}\\)"):
            Update(bundle=bundle, role="lab-pc", home=tmp_path).run()
    finally:
        server.shutdown()
    assert not (tmp_path / "venvs").exists() and not (tmp_path / "lab-pc-venv").exists()


def test_the_manifest_hashes_are_sha256(bundle: Path) -> None:
    manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    name, digest = next(iter(manifest["files"].items()))
    assert sha256(bundle / name) == digest and len(digest) == 64


def test_the_interpreter_may_be_a_path_with_spaces_or_a_command(tmp_path: Path) -> None:
    python = tmp_path / "Program Files" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    assert Update(bundle=tmp_path, role="client", python=str(python))._interpreter() == [str(python)]
    assert Update(bundle=tmp_path, role="client", python="py -3.11")._interpreter() == ["py", "-3.11"]


def _install_ps1(bundle: Path, home: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(bundle / INSTALLER),
         "-Role", "client", "-LabNetHome", str(home), *extra],
        capture_output=True, text=True, timeout=120,
    )


@pytest.mark.skipif(shutil.which("powershell") is None, reason="needs Windows PowerShell")
def test_install_ps1_refuses_a_changed_bundle_before_installing_anything(bundle: Path, tmp_path: Path) -> None:
    copy = tmp_path / "bundle"
    shutil.copytree(bundle, copy)
    (copy / "site-acme.zip").write_bytes(b"changed")
    result = _install_ps1(copy, tmp_path / "home")
    assert result.returncode == 1, result.stdout + result.stderr
    # exactly the changed file: every other file's hash matches
    assert "do not install it: site-acme.zip does not match its sha256\n" in result.stdout, result.stdout + result.stderr
    assert not (tmp_path / "home").exists()


@pytest.mark.skipif(shutil.which("powershell") is None, reason="needs Windows PowerShell")
def test_install_ps1_accepts_an_unchanged_bundle(bundle: Path, tmp_path: Path) -> None:
    # It stops at the interpreter (there is none at that path), right after checking every file.
    result = _install_ps1(bundle, tmp_path / "home", "-Python", str(tmp_path / "no-python.exe"))
    files = len(read_manifest(bundle)["files"])
    assert f"ok {files} files match MANIFEST.json" in result.stdout, result.stdout + result.stderr
    assert result.returncode != 0 and not (tmp_path / "home").exists()
