"""`labnet runner`: finding the checkout, WSL paths, the compose command, and issuing the runner's token."""

from __future__ import annotations

import subprocess
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from labnet.dev.network import DevNetwork
from labnet.dev.processes import DevError
from labnet_automation import host

REPO = Path(__file__).resolve().parents[4]


def test_the_checkout_is_found_from_any_folder_inside_it(tmp_path: Path) -> None:
    assert host.find_source(REPO / "packages" / "labnet" / "src") == REPO
    with pytest.raises(DevError, match="LabNet source checkout"):
        host.find_source(tmp_path)


@pytest.mark.skipif(not host.WINDOWS, reason="Windows paths")
def test_windows_paths_as_wsl_sees_them() -> None:
    assert host.wsl_path(Path(PureWindowsPath("C:/Users/me/Lab Net"))) == "/mnt/c/Users/me/Lab Net"


def test_compose_runs_from_the_checkout_with_the_runner_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: seen.append(argv) or SimpleNamespace(returncode=0))
    runner = host.Runner(REPO, DevNetwork(root=tmp_path), "Ubuntu-22.04", "dev-laptop")
    runner.compose("ps")
    script = seen[0][-1]
    assert script.endswith(f"docker compose -f '{host.COMPOSE_FILE}' ps")
    assert "LABNET_RUNNER_ENV_FILE=" in script and "runner.env" in script
    if host.WINDOWS:
        assert seen[0][:6] == ["wsl.exe", "-d", "Ubuntu-22.04", "-e", "bash", "-lc"]


def test_up_issues_a_token_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    net = DevNetwork(root=tmp_path)
    net.devnet.mkdir()
    calls = []

    def fake_run(argv, **_):
        calls.append(argv[3:])
        out = "Runner dev-laptop added.\n\n    lnr_" + "A" * 43 + "\n" if "add" in argv else ""
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(host, "http_ok", lambda _url: True)
    monkeypatch.setattr(host, "command", lambda name: Path(name))
    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = host.Runner(REPO, net, "Ubuntu-22.04", "dev-laptop")
    runner.write_env()
    assert calls == [["runner", "revoke", "dev-laptop"], ["runner", "add", "dev-laptop"]]
    settings = (net.devnet / "runner.env").read_text(encoding="utf-8")
    assert "LABNET_RUNNER_TOKEN=lnr_" + "A" * 43 in settings and "LABNET_RUNNER_CONTROL_CENTER_URL=http://127.0.0.1:8080" in settings
    runner.write_env()  # a token is shown only once: an existing runner.env is kept
    assert len(calls) == 2


def test_up_needs_the_control_center(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "http_ok", lambda _url: False)
    with pytest.raises(DevError, match="labnet dev up"):
        host.Runner(REPO, DevNetwork(root=tmp_path), "Ubuntu-22.04", "dev-laptop").write_env()
