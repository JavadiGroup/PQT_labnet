from __future__ import annotations

import os
from pathlib import Path

import pytest

from labnet import LabClient
from labnet.central.config import Settings
from labnet.central.manage import main as admin
from labnet.lab_pc.config import LabPCSettings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Env files are loaded into os.environ; discard them after each test.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def central_env(tmp_path: Path) -> Path:
    env_file = tmp_path / "central.env"
    database = (tmp_path / "data" / "central.db").as_posix()
    env_file.write_text(
        f"LABNET_DATABASE_URL=sqlite:///{database}\nLABNET_CENTRAL_BIND_PORT=8123\n",
        encoding="utf-8",
    )
    return env_file


def test_settings_read_the_env_file(central_env: Path) -> None:
    assert Settings.from_env(central_env).bind_port == 8123


def test_setup_writes_ready_to_use_settings_files(
    central_env: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = tmp_path / "keys"

    admin([
        "--env-file", str(central_env), "setup",
        "--admin", "lab-admin", "--user", "researcher", "--lab-pc", "lab-pc-01",
        "--write-env", str(keys), "--server-url", "http://127.0.0.1:8008",
    ])

    output = capsys.readouterr().out
    assert "labnet_" not in output  # keys go to the files, not the terminal
    assert sorted(path.name for path in keys.iterdir()) == [
        "lab-admin.env", "lab-pc-01.env", "researcher.env",
    ]
    client = LabClient.from_env(keys / "researcher.env", authenticate_on_start=False)
    try:
        assert client.api_key.startswith("labnet_")
        assert client.server_url == "http://127.0.0.1:8008"
    finally:
        client.close()
    lab_pc = LabPCSettings.from_env(keys / "lab-pc-01.env")
    assert lab_pc.lab_pc_id == "lab-pc-01"
    assert lab_pc.central_enabled is True


def test_setup_is_idempotent_and_keeps_existing_keys(
    central_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin(["--env-file", str(central_env), "setup", "--user", "researcher"])
    first = capsys.readouterr().out
    admin(["--env-file", str(central_env), "setup", "--user", "researcher"])
    second = capsys.readouterr().out

    assert "created  researcher" in first
    assert "exists   researcher" in second
    assert "API key" not in second


def test_setup_refuses_to_overwrite_and_creates_nobody(
    central_env: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "researcher.env").write_text("keep me", encoding="utf-8")

    with pytest.raises(SystemExit):
        admin([
            "--env-file", str(central_env), "setup", "--user", "researcher",
            "--lab-pc", "lab-pc-01",
            "--write-env", str(keys), "--server-url", "http://127.0.0.1:8008",
        ])
    capsys.readouterr()

    admin(["--env-file", str(central_env), "user", "list"])
    assert "researcher" not in capsys.readouterr().out
    assert (keys / "researcher.env").read_text() == "keep me"


def test_setup_rejects_a_role_change(central_env: Path) -> None:
    admin(["--env-file", str(central_env), "setup", "--user", "alice"])

    with pytest.raises(SystemExit):
        admin(["--env-file", str(central_env), "setup", "--admin", "alice"])


def test_user_list_never_prints_keys(
    central_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin(["--env-file", str(central_env), "setup", "--lab-pc", "lab-pc-01"])
    capsys.readouterr()

    admin(["--env-file", str(central_env), "user", "list"])
    listing = capsys.readouterr().out

    assert "lab-pc-01" in listing and "device_manager" in listing
    assert "labnet_" not in listing


def test_write_env_requires_a_server_url(central_env: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        admin([
            "--env-file", str(central_env), "setup", "--user", "researcher",
            "--write-env", str(tmp_path / "keys"),
        ])
