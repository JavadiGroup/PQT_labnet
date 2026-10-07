"""The self-check: every check on its own, with actionable details, against a fake docker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labnet.automation_runner import selfcheck
from labnet.automation_runner.backend import PythonResult
from labnet.automation_runner.client import RunnerApiError
from labnet.automation_runner.config import RunnerSettings
from labnet.automation_runner.docker_backend import RUN_ID, CommandResult
from labnet.automation_runner.selfcheck import CLAUDE_FLAGS, SELFCHECK_RUN_ID, Check, format_checks, run_all


NAMES = ["Control Center", "docker", "agent image", "run image", "agent network", "Claude login",
         "run container isolation", "agent network isolation", "claude --version", "claude flags"]
HELP = "Usage: claude [options]\n" + "\n".join(f"  {flag} <x>  something" for flag in CLAUDE_FLAGS)


class FakeClient:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def whoami(self):
        if self.error:
            raise self.error
        return "lab-runner"


class FakeBackend:
    """``command`` answers like a docker host where everything is set up right; tests change ``answers``."""

    def __init__(self):
        self.commands: list[list[str]] = []
        self.python: list = []
        self.answers = {
            "version": CommandResult(0, "29.2.0\n", ""),
            "image": CommandResult(0, "sha256:0123456789abcdef0123\n", ""),
            "internal": CommandResult(0, "true\n", ""),
            "gateway": CommandResult(0, "172.30.0.1 \n", ""),
            "volume": CommandResult(0, "labnet-automation-claude-login\n", ""),
            "login": {"exists": True, "readable": True, "kinds": ["claudeAiOauth"], "refresh_token": True},
            "agent": {"direct": "blocked (OSError)", "gateway_open": [], "anthropic": "200", "anthropic_tls": "TLSv1.3",
                      "example": "403"},
            "version_claude": CommandResult(0, "2.1.285 (Claude Code)\n", ""),
            "help": CommandResult(0, HELP, ""),
            "run": {"uid": 1000, "internet": "blocked (OSError)", "inside": True, "visible": []},
        }
        self.seen_probe = None

    def _probe(self, key):
        value = self.answers[key]
        return value if isinstance(value, CommandResult) else CommandResult(0, "noise\n" + json.dumps(value) + "\n", "")

    def command(self, args, timeout=30.0):
        self.commands.append(args)
        if isinstance(self.answers.get("raise"), Exception):
            raise self.answers["raise"]
        if args[0] == "version":
            return self.answers["version"]
        if args[:2] == ["image", "inspect"]:
            return self.answers["image"]
        if args[:2] == ["network", "inspect"]:
            return self.answers["internal" if ".Internal" in args[3] else "gateway"]
        if args[:2] == ["volume", "inspect"]:
            return self.answers["volume"]
        if args[0] == "run":
            if args[-2:] == ["claude", "--version"]:
                return self.answers["version_claude"]
            if args[-2:] == ["claude", "--help"]:
                return self.answers["help"]
            if selfcheck.LOGIN_PROBE in args:
                return self._probe("login")
            if selfcheck.AGENT_PROBE in args:
                return self._probe("agent")
        raise AssertionError(f"unexpected docker command {args}")

    def run_python(self, request):
        self.python.append(request)
        probe = request.work / request.script
        self.seen_probe = (probe.read_text(encoding="utf-8"), (request.work / "inside.txt").exists())
        value = self.answers["run"]
        if isinstance(value, PythonResult):
            return value
        return PythonResult(exit_code=0, stdout=json.dumps(value) + "\n", stderr="", seconds=1.0, timed_out=False)


@pytest.fixture
def settings(tmp_path: Path) -> RunnerSettings:
    return RunnerSettings(control_center_url="https://pqt-control-center", token="lnr_" + "A" * 43,
                          work_dir=tmp_path / "runner")


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {check.name: check for check in checks}


def test_everything_passes_on_a_good_setup(settings):
    settings.work_dir.mkdir()
    backend = FakeBackend()
    checks = run_all(settings, backend, FakeClient())
    assert [c.name for c in checks] == NAMES
    assert all(c.ok for c in checks), format_checks(checks)
    found = by_name(checks)
    assert "“lab-runner”" in found["Control Center"].detail
    assert found["agent network isolation"].detail.startswith("no direct internet; api.anthropic.com via the proxy (TLSv1.3)")
    assert found["claude --version"].detail == "Claude Code 2.1.285 (Claude Code)."
    # The run probe went through the real run_python path, with paths it must not see.
    (request,) = backend.python
    assert request.run_id == SELFCHECK_RUN_ID and request.script == "ScriptsUtilized/selfcheck_probe.py"
    assert settings.work_dir.as_posix() in request.args and "/var/run/docker.sock" in request.args
    assert request.work.as_posix() in request.args
    assert backend.seen_probe == (selfcheck.RUN_PROBE, True)
    assert list((settings.work_dir / "selfcheck").iterdir()) == []  # cleaned up


def test_the_throwaway_containers_are_hardened(settings):
    backend = FakeBackend()
    run_all(settings, backend, FakeClient())
    runs = [args for args in backend.commands if args[0] == "run"]
    assert len(runs) == 4  # login, agent network, claude --version, claude --help
    for args in runs:
        image = args.index(settings.agent_image)
        flags = args[:image]
        assert flags[:2] == ["run", "--rm"]
        for flag in (["--pull", "never"], ["--user", "1000:1000"], ["--cap-drop", "ALL"],
                     ["--security-opt", "no-new-privileges"]):
            assert any(flags[i:i + 2] == flag for i in range(len(flags))), (flag, flags)
        assert "--read-only" in flags and "/var/run/docker.sock" not in " ".join(flags)
    networks = [args[args.index("--network") + 1] for args in runs]
    assert networks == ["none", settings.agent_network, "none", "none"]
    login = runs[0]
    assert f"{settings.claude_login_volume}:/claude:ro" in login
    for args in runs[2:]:
        assert "CLAUDE_CONFIG_DIR=/tmp/claude" in args and not any(":/claude" in a for a in args)
    agent = runs[1]
    assert agent[-3:] == ["labnet-automation-egress", "3128", "172.30.0.1"]


def test_selfcheck_run_id_is_a_run_id():
    assert RUN_ID.fullmatch(SELFCHECK_RUN_ID)


@pytest.mark.parametrize("change, name, words", [
    ({"internal": CommandResult(0, "false\n", "")}, "agent network", "not internal"),
    ({"internal": CommandResult(1, "", "Error: No such network")}, "agent network", "doesn't exist"),
    ({"image": CommandResult(1, "", "No such image")}, "agent image", "docker compose"),
    ({"volume": CommandResult(1, "", "no such volume")}, "Claude login", "run --rm login"),
    ({"login": {"exists": False, "readable": False, "error": "FileNotFoundError"}}, "Claude login", "holds no Claude login"),
    ({"login": {"exists": True, "readable": False, "error": "PermissionError"}}, "Claude login", "uid 1000"),
    ({"login": {"exists": True, "readable": True, "refresh_token": False}}, "Claude login", "refresh token"),
    ({"run": {"uid": 1000, "internet": "open", "inside": True, "visible": []}}, "run container isolation", "1.1.1.1"),
    ({"run": {"uid": 1000, "internet": "blocked (OSError)", "inside": True, "visible": ["/var/run/docker.sock"]}},
     "run container isolation", "can see /var/run/docker.sock"),
    ({"run": {"uid": 1000, "internet": "blocked (OSError)", "inside": False, "visible": []}},
     "run container isolation", "can't see its working copy"),
    ({"run": {"uid": 0, "internet": "blocked (OSError)", "inside": True, "visible": []}}, "run container isolation", "uid 0"),
    ({"run": PythonResult(exit_code=125, stdout="", stderr="Unable to find image", seconds=0.1, timed_out=False)},
     "run container isolation", "Unable to find image"),
    ({"agent": {"direct": "open", "gateway_open": [], "anthropic": "200", "anthropic_tls": "TLSv1.3", "example": "403"}},
     "agent network isolation", "directly"),
    ({"agent": {"direct": "blocked", "gateway_open": ["172.30.0.1:8080"], "anthropic": "200", "anthropic_tls": "TLSv1.3",
                "example": "403"}}, "agent network isolation", "172.30.0.1:8080"),
    ({"agent": {"direct": "blocked", "gateway_open": [], "anthropic": "error: gaierror: Name or service not known",
                "example": "403"}}, "agent network isolation", "egress container running"),
    ({"agent": {"direct": "blocked", "gateway_open": [], "anthropic": "200", "anthropic_tls": "TLSv1.3",
                "example": "200"}}, "agent network isolation", "not 403"),
    ({"agent": CommandResult(125, "", "network labnet-automation-agent not found")}, "agent network isolation", "not found"),
    ({"version_claude": CommandResult(127, "", "claude: not found")}, "claude --version", "claude: not found"),
    ({"help": CommandResult(0, "Usage: claude --agents x --strict-mcp-config", "")}, "claude flags",
     "lacks --max-budget-usd, --setting-sources, --append-system-prompt-file"),
])
def test_each_failure_is_reported_alone(settings, change, name, words):
    backend = FakeBackend()
    backend.answers.update(change)
    checks = by_name(run_all(settings, backend, FakeClient()))
    assert not checks[name].ok and words in checks[name].detail, checks[name].detail
    if name == "agent image":  # the same fake answer covers both images
        assert not checks["run image"].ok
        failed = {"agent image", "run image"}
    else:
        failed = {name}
    assert {n for n, c in checks.items() if not c.ok} == failed


def test_docker_unreachable_marks_the_rest_not_checked(settings):
    backend = FakeBackend()
    backend.answers["version"] = CommandResult(1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    checks = by_name(run_all(settings, backend, FakeClient()))
    assert checks["Control Center"].ok
    assert not checks["docker"].ok and "Cannot connect to the Docker daemon" in checks["docker"].detail
    for name in NAMES[2:]:
        assert not checks[name].ok and checks[name].detail.startswith("not checked")
    assert backend.python == [] and len(backend.commands) == 1


def test_a_crashing_check_does_not_hide_the_others(settings):
    backend = FakeBackend()

    def broken(request):
        raise OSError("disk full")

    backend.run_python = broken
    checks = by_name(run_all(settings, backend, FakeClient()))
    assert not checks["run container isolation"].ok and "OSError: disk full" in checks["run container isolation"].detail
    assert all(c.ok for n, c in checks.items() if n != "run container isolation")


@pytest.mark.parametrize("error, words", [
    (RunnerApiError(401, "Send a valid runner token"), "refused the runner token"),
    (ConnectionError("connection refused"), "isn't reachable"),
])
def test_control_center_failures(settings, error, words):
    checks = by_name(run_all(settings, FakeBackend(), FakeClient(error)))
    assert not checks["Control Center"].ok and words in checks["Control Center"].detail
    assert checks["docker"].ok


def test_probes_are_valid_python():
    for probe in (selfcheck.RUN_PROBE, selfcheck.AGENT_PROBE, selfcheck.LOGIN_PROBE):
        compile(probe, "probe", "exec")
    assert "for port in (22, 80, 443, 8080)" in selfcheck.AGENT_PROBE


def test_format_checks():
    text = format_checks([Check("docker", True, "Docker Engine 29.2.0."), Check("agent network", False, "missing")])
    assert text == "[ok] docker: Docker Engine 29.2.0.\n[FAILED] agent network: missing"


def test_claude_flags_report_options_help_does_not_list(settings):
    backend = FakeBackend()
    checks = by_name(run_all(settings, backend, FakeClient()))
    assert "doesn't list --tools" in checks["claude flags"].detail and checks["claude flags"].ok
    backend.answers["help"] = CommandResult(0, HELP + "\n" + "\n".join(selfcheck.CLAUDE_OTHER_FLAGS), "")
    checks = by_name(run_all(settings, backend, FakeClient()))
    assert checks["claude flags"].ok and "doesn't list" not in checks["claude flags"].detail


def test_an_option_listed_in_its_short_form_counts() -> None:
    from labnet.automation_runner.selfcheck import _listed

    help_text = "  via: --system-prompt[-file], --append-system-prompt[-file], --add-dir"
    assert _listed("--append-system-prompt-file", help_text)
    assert not _listed("--append-system-prompt-file", "  --append-system-prompt <prompt>")
    assert not _listed("--strict-mcp-config", help_text)
