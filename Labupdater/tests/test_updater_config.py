from __future__ import annotations

from pathlib import Path

import pytest

from labupdater.config import UpdaterConfig


def test_config_resolves_paths_relative_to_file(tmp_path: Path) -> None:
    (tmp_path / "manager.key").write_text("manager-secret\n", encoding="utf-8")
    (tmp_path / "control.token").write_text("0123456789abcdef\n", encoding="utf-8")
    config_file = tmp_path / "updater.toml"
    config_file.write_text(
        "\n".join(
            [
                'manager_id = "lab-pc-01"',
                'central_url = "https://central.example"',
                'api_key_file = "manager.key"',
                'release_root = "releases"',
                'local_control_token_file = "control.token"',
            ]
        ),
        encoding="utf-8",
    )
    config = UpdaterConfig.load(config_file)
    assert config.release_root == tmp_path / "releases"
    assert config.read_api_key() == "manager-secret"
    assert config.read_control_token() == "0123456789abcdef"


def test_non_loopback_plain_http_central_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "updater.toml"
    config_file.write_text(
        "\n".join(
            [
                'manager_id = "lab-pc-01"',
                'central_url = "http://central.example"',
                'api_key_file = "manager.key"',
                'release_root = "releases"',
                'local_control_token_file = "control.token"',
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="HTTPS"):
        UpdaterConfig.load(config_file)
