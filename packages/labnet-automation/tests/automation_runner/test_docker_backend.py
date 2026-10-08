"""DockerBackend: the exact docker argv (every isolation flag), no leaked environment, limits and kills."""

from __future__ import annotations

import io
import subprocess
import threading
from pathlib import Path, PurePosixPath

import pytest

from labnet_automation import docker_backend
from labnet_automation.backend import AgentSpec, PythonRequest
from labnet_automation.config import RunnerSettings
from labnet_automation.docker_backend import (
    DockerBackend, DockerError, docker_environment, host_path, script_path,
)
from labnet_control_center.automation_runs import RUN_ID as CONTROL_CENTER_RUN_ID


RUN = "run-20261007-120000-abcd"
TOKEN = "lnr_" + "A" * 43
WORK = Path("/var/lib/labnet-runner/runs") / RUN / "work"


def settings(**overrides) -> RunnerSettings:
    values = dict(control_center_url="https://pqt-control-center", token=TOKEN, python_cpus=1.5,
                  python_memory_mb=1024, python_pids=128, python_timeout_seconds=30.0)
    values.update(overrides)
    return RunnerSettings(**values)


class FakeProcess:
    """Stands in for subprocess.Popen: records argv/env, plays back output and an exit code."""

    def __init__(self, argv, popen_kwargs, *, out=b"", err=b"", returncode=0, hang=False):
        self.argv = argv
        self.env = popen_kwargs["env"]
        assert popen_kwargs["stdout"] == popen_kwargs["stderr"] == subprocess.PIPE
        self.stdin = _Sink() if popen_kwargs["stdin"] == subprocess.PIPE else None
        self.stdout = io.BytesIO(out)
        self.stderr = io.BytesIO(err)
        self._returncode = returncode
        self.returncode = None
        self._released = threading.Event()
        if not hang:
            self._released.set()
        self.killed = False

    def release(self, code: int) -> None:
        self._returncode = code
        self._released.set()

    def wait(self, timeout=None):
        if not self._released.wait(timeout):
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.returncode = self._returncode
        return self.returncode

    def communicate(self, timeout=None):
        self.wait(timeout)
        return self.stdout.read(), self.stderr.read()

    def kill(self):
        self.killed = True
        self.release(-9)


class _Sink(io.BytesIO):
    def close(self):
        self.data = self.getvalue()
        super().close()


class FakeDocker:
    """A popen= for DockerBackend. ``script`` maps a docker subcommand to what the process does."""

    def __init__(self):
        self.calls: list[FakeProcess] = []
        self.behaviour: dict[str, dict] = {}
        self.on_kill = None

    def __call__(self, argv, **kwargs):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        assert "shell" not in kwargs
        sub = argv[1]
        options = dict(self.behaviour.get(sub, {}))
        process = FakeProcess(argv, kwargs, **options)
        self.calls.append(process)
        if sub == "kill" and self.on_kill:
            self.on_kill(argv)
        return process

    def argvs(self, sub: str) -> list[list[str]]:
        return [p.argv for p in self.calls if p.argv[1] == sub]


def flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


ENVIRON = {"PATH": "/usr/bin", "HOME": "/root", "DOCKER_HOST": "unix:///var/run/docker.sock",
           "LABNET_RUNNER_TOKEN": TOKEN, "ANTHROPIC_API_KEY": "sk-ant-secret", "AWS_SECRET_ACCESS_KEY": "x"}


def backend(fake: FakeDocker, **overrides) -> DockerBackend:
    return DockerBackend(settings(**overrides), popen=fake, environ=ENVIRON)


def test_run_id_pattern_matches_the_control_centers():
    assert docker_backend.RUN_ID.pattern == CONTROL_CENTER_RUN_ID.pattern


def test_agent_argv_has_every_isolation_flag():
    fake = FakeDocker()
    spec = AgentSpec(run_id=RUN, work=WORK, config=WORK.parent / "config", socket_dir=WORK.parent / "sock",
                     argv=["claude", "-p", "--output-format", "stream-json"],
                     env={"HTTPS_PROXY": "http://labnet-automation-egress:3128", "LABNET_TOOLS_SOCKET": "/run/labnet/tools.sock"},
                     stdin="Do Task1.")
    agent = backend(fake).start_agent(spec)
    assert list(agent.lines()) == []
    assert agent.wait() == 0
    (process,) = fake.calls
    argv = process.argv
    assert argv[:5] == ["docker", "run", "--rm", "-i", "--name"]
    assert flag_values(argv, "--name") == [f"labnet-agent-{RUN}"]
    assert flag_values(argv, "--network") == ["labnet-automation-agent"]
    assert flag_values(argv, "--user") == ["1000:1000"]
    assert flag_values(argv, "--cap-drop") == ["ALL"]
    assert flag_values(argv, "--security-opt") == ["no-new-privileges"]
    assert flag_values(argv, "--pids-limit") == ["512"]
    assert flag_values(argv, "--memory") == ["4g"] and flag_values(argv, "--memory-swap") == ["4g"]
    assert flag_values(argv, "--cpus") == ["2"]
    assert flag_values(argv, "--pull") == ["never"]
    assert "--read-only" in argv and "--init" in argv
    assert flag_values(argv, "--tmpfs") == ["/tmp:rw,nosuid,size=512m", "/home/node:rw,nosuid,size=256m"]
    assert flag_values(argv, "-v") == [
        f"/var/lib/labnet-runner/runs/{RUN}/work:/work",
        f"/var/lib/labnet-runner/runs/{RUN}/config:/labnet:ro",
        f"/var/lib/labnet-runner/runs/{RUN}/sock:/run/labnet",
        "labnet-automation-claude-login:/claude",
    ]
    assert flag_values(argv, "-w") == ["/work"]
    assert flag_values(argv, "--label") == [f"labnet.run={RUN}"]
    assert flag_values(argv, "-e") == ["HTTPS_PROXY=http://labnet-automation-egress:3128",
                                       "LABNET_TOOLS_SOCKET=/run/labnet/tools.sock"]
    image = argv.index("labnet-automation-agent:latest")
    assert argv[image + 1:] == ["claude", "-p", "--output-format", "stream-json"]
    for forbidden in ("--privileged", "--cap-add", "--network=host", "host", "/var/run/docker.sock", "--env-file"):
        assert forbidden not in argv
    # The prompt went to stdin, which was then closed.
    agent._stdin.join(5)
    assert process.stdin.data == b"Do Task1."


def test_nothing_from_the_runners_environment_reaches_docker_or_a_container():
    fake = FakeDocker()
    b = backend(fake)
    spec = AgentSpec(run_id=RUN, work=WORK, config=WORK, socket_dir=WORK, argv=["claude"], env={}, stdin="")
    b.start_agent(spec).wait()
    b.run_python(PythonRequest(run_id=RUN, work=WORK, script="ScriptsUtilized/a.py", args=[], timeout_seconds=5))
    for process in fake.calls:
        assert "LABNET_RUNNER_TOKEN" not in process.env and "ANTHROPIC_API_KEY" not in process.env
        assert "AWS_SECRET_ACCESS_KEY" not in process.env
        assert process.env["DOCKER_HOST"] == "unix:///var/run/docker.sock"
        joined = "\n".join(process.argv)
        assert TOKEN not in joined and "sk-ant-secret" not in joined
        # Never a bare "-e NAME", which would copy the CLI's own value into the container.
        assert all("=" in value for value in flag_values(process.argv, "-e"))


def test_docker_environment_is_an_allow_list():
    env = docker_environment({"PATH": "/bin", "LC_ALL": "C.UTF-8", "LABNET_X": "1", "CLAUDE_CODE_OAUTH_TOKEN": "t"})
    assert env == {"PATH": "/bin", "LC_ALL": "C.UTF-8"}


def test_run_python_argv_is_isolated_and_limited():
    fake = FakeDocker()
    fake.behaviour["run"] = {"out": b"fit ok\n", "err": b"warning\n", "returncode": 0}
    result = backend(fake).run_python(PythonRequest(
        run_id=RUN, work=WORK, script="ScriptsUtilized/analysis_scripts/fit.py", args=["--n", "3", "; rm -rf /"],
        timeout_seconds=10))
    assert (result.exit_code, result.timed_out, result.stdout, result.stderr) == (0, False, "fit ok\n", "warning\n")
    (argv,) = fake.argvs("run")
    assert flag_values(argv, "--name") == [f"labnet-py-{RUN}-1"]
    assert flag_values(argv, "--network") == ["none"]
    assert flag_values(argv, "--log-driver") == ["none"]
    assert flag_values(argv, "--user") == ["1000:1000"]
    assert flag_values(argv, "--cap-drop") == ["ALL"]
    assert flag_values(argv, "--security-opt") == ["no-new-privileges"]
    assert flag_values(argv, "--pids-limit") == ["128"]
    assert flag_values(argv, "--memory") == ["1024m"] and flag_values(argv, "--memory-swap") == ["1024m"]
    assert flag_values(argv, "--cpus") == ["1.5"]
    assert flag_values(argv, "--tmpfs") == ["/tmp:rw,nosuid,size=256m"]
    assert flag_values(argv, "--pull") == ["never"]
    assert "--read-only" in argv and "-i" not in argv
    assert flag_values(argv, "-v") == [f"/var/lib/labnet-runner/runs/{RUN}/work:/work"]  # nothing else mounted
    assert flag_values(argv, "-e") == []
    assert flag_values(argv, "--label") == [f"labnet.run={RUN}"]
    image = argv.index("labnet-automation-run:latest")
    assert argv[image + 1:] == ["python", "-I", "/work/ScriptsUtilized/analysis_scripts/fit.py", "--n", "3", "; rm -rf /"]


def test_run_python_names_count_up():
    fake = FakeDocker()
    b = backend(fake)
    for _ in range(2):
        b.run_python(PythonRequest(run_id=RUN, work=WORK, script="ScriptsUtilized/a.py", args=[], timeout_seconds=1))
    assert [flag_values(a, "--name")[0] for a in fake.argvs("run")] == [f"labnet-py-{RUN}-1", f"labnet-py-{RUN}-2"]


def test_run_python_time_limit_kills_the_container_by_name(monkeypatch):
    monkeypatch.setattr(docker_backend, "KILL_GRACE_SECONDS", 2)
    fake = FakeDocker()
    fake.behaviour["run"] = {"hang": True, "out": b"partial\n"}
    fake.on_kill = lambda argv: [p.release(137) for p in fake.calls if p.argv[1] == "run"]
    b = backend(fake, python_timeout_seconds=0.2)
    result = b.run_python(PythonRequest(run_id=RUN, work=WORK, script="ScriptsUtilized/a.py", args=[],
                                        timeout_seconds=999))  # capped by the settings
    assert result.timed_out and result.exit_code is None
    assert result.stdout == "partial\n"
    assert "time limit of 0.2 s" in result.stderr
    assert fake.argvs("kill") == [["docker", "kill", f"labnet-py-{RUN}-1"]]
    assert result.seconds < 5


def test_run_python_output_is_capped(monkeypatch):
    monkeypatch.setattr(docker_backend, "OUTPUT_CAP_BYTES", 100)
    fake = FakeDocker()
    fake.behaviour["run"] = {"out": b"x" * 1000, "returncode": 1}
    result = backend(fake).run_python(PythonRequest(run_id=RUN, work=WORK, script="ScriptsUtilized/a.py",
                                                    args=[], timeout_seconds=5))
    assert result.exit_code == 1
    assert result.stdout.startswith("x" * 100) and "900 more bytes" in result.stdout


def test_stop_run_kills_every_container_of_the_run():
    fake = FakeDocker()
    fake.behaviour["ps"] = {"out": b"abc123\ndef456\n"}
    b = backend(fake)
    b.stop_run(RUN)
    assert fake.argvs("ps") == [["docker", "ps", "-q", "--filter", f"label=labnet.run={RUN}"]]
    assert fake.argvs("kill") == [["docker", "kill", "abc123", "def456"]]
    # A script that ends after the stop counts as killed.
    fake.behaviour["run"] = {"returncode": 137}
    result = b.run_python(PythonRequest(run_id=RUN, work=WORK, script="ScriptsUtilized/a.py", args=[], timeout_seconds=1))
    assert result.exit_code is None


def test_stop_run_with_nothing_running_kills_nothing():
    fake = FakeDocker()
    backend(fake).stop_run(RUN)
    assert fake.argvs("kill") == []


def test_agent_kill_and_output():
    fake = FakeDocker()
    fake.behaviour["run"] = {"hang": True, "out": b'{"type":"system"}\r\n{"type":"result"}\n', "err": b"oops\n"}
    fake.on_kill = lambda argv: [p.release(137) for p in fake.calls if p.argv[1] == "run"]
    spec = AgentSpec(run_id=RUN, work=WORK, config=WORK, socket_dir=WORK, argv=["claude"], env={}, stdin="x")
    agent = backend(fake).start_agent(spec)
    assert list(agent.lines()) == ['{"type":"system"}', '{"type":"result"}']
    agent.kill()
    assert fake.argvs("kill") == [["docker", "kill", f"labnet-agent-{RUN}"]]
    assert agent.wait() == 137
    assert agent.stderr_tail() == "oops\n"


def test_overlong_agent_lines_are_dropped_whole(monkeypatch):
    monkeypatch.setattr(docker_backend, "MAX_LINE_BYTES", 10)
    fake = FakeDocker()
    fake.behaviour["run"] = {"out": b"short\n" + b"y" * 25 + b"\nafter\n"}
    spec = AgentSpec(run_id=RUN, work=WORK, config=WORK, socket_dir=WORK, argv=["claude"], env={}, stdin="")
    agent = backend(fake).start_agent(spec)
    assert list(agent.lines()) == ["short", "after"]
    agent.wait()
    assert "dropped an output line" in agent.stderr_tail()


@pytest.mark.parametrize("run_id", ["run-1", "../x", "run-20261007-120000-ABCD", "run-20261007-120000-abcd;x", ""])
def test_bad_run_ids_never_reach_docker(run_id):
    fake = FakeDocker()
    b = backend(fake)
    with pytest.raises(DockerError):
        b.run_python(PythonRequest(run_id=run_id, work=WORK, script="ScriptsUtilized/a.py", args=[], timeout_seconds=1))
    with pytest.raises(DockerError):
        b.start_agent(AgentSpec(run_id=run_id, work=WORK, config=WORK, socket_dir=WORK, argv=[], env={}, stdin=""))
    with pytest.raises(DockerError):
        b.stop_run(run_id)
    assert fake.calls == []


@pytest.mark.parametrize("path", ["relative/work", "/a:b", "/a,b", "/a/../etc", "/a\nb"])
def test_unusable_mount_paths_are_refused(path):
    with pytest.raises(DockerError):
        host_path(PurePosixPath(path).as_posix() if "\n" not in path else path)


@pytest.mark.parametrize("script", ["/etc/passwd", "../x.py", "a/../../x.py", "-c", "a\\b.py", ""])
def test_unusable_script_paths_are_refused(script):
    with pytest.raises(DockerError):
        script_path(script)


@pytest.mark.parametrize("env", [{"BAD NAME": "x"}, {"X": "a\nb"}, {"1X": "y"}, {"X": "a\0"}])
def test_bad_environment_entries_are_refused(env):
    fake = FakeDocker()
    with pytest.raises(DockerError):
        backend(fake).start_agent(AgentSpec(run_id=RUN, work=WORK, config=WORK, socket_dir=WORK, argv=[], env=env, stdin=""))
    assert fake.calls == []


def test_command_reports_a_missing_cli():
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("docker")

    with pytest.raises(DockerError, match="Cannot run docker"):
        DockerBackend(settings(), popen=missing).command(["version"])


def test_stop_run_is_idempotent_and_never_raises():
    fake = FakeDocker()
    b = backend(fake)
    b.stop_run(RUN)
    b.stop_run(RUN)  # after a normal end: nothing to kill, twice
    assert fake.argvs("kill") == []
    fake.behaviour["ps"] = {"out": b"abc123\n"}
    fake.behaviour["kill"] = {"returncode": 1, "err": b"Error response from daemon: No such container: abc123"}
    b.stop_run(RUN)  # ended between ps and kill
    fake.behaviour["ps"] = {"returncode": 1, "err": b"Cannot connect to the Docker daemon"}
    b.stop_run(RUN)  # Docker trouble is logged, not raised

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("docker")

    DockerBackend(settings(), popen=missing).stop_run(RUN)


def test_stop_run_from_another_thread_while_a_script_runs(monkeypatch):
    monkeypatch.setattr(docker_backend, "KILL_GRACE_SECONDS", 2)
    fake = FakeDocker()
    fake.behaviour["run"] = {"hang": True}
    fake.behaviour["ps"] = {"out": b"abc123\n"}
    fake.on_kill = lambda argv: [p.release(137) for p in fake.calls if p.argv[1] == "run"]
    b = backend(fake)
    results = []
    worker = threading.Thread(target=lambda: results.append(b.run_python(PythonRequest(
        run_id=RUN, work=WORK, script="ScriptsUtilized/a.py", args=[], timeout_seconds=30))))
    worker.start()
    while not fake.argvs("run"):
        threading.Event().wait(0.01)
    b.stop_run(RUN)
    worker.join(10)
    assert results[0].exit_code is None and not results[0].timed_out
    assert "stopped with the run" in results[0].stderr


def test_no_script_container_starts_after_the_run_was_stopped(monkeypatch, tmp_path) -> None:
    """A run_python request still on its way in when the run stops must not start a container."""

    from labnet_automation import docker_backend as module
    from labnet_automation.backend import PythonRequest
    from labnet_automation.config import RunnerSettings

    settings = RunnerSettings(control_center_url="http://127.0.0.1:8080", token="lnr_" + "a" * 43, dev=True)
    backend = module.DockerBackend(settings)
    monkeypatch.setattr(backend, "run_containers", lambda run_id: [])
    spawned = []
    monkeypatch.setattr(backend, "_spawn", lambda argv: spawned.append(argv))
    run_id = "run-20261007-120000-abcd"
    backend.stop_run(run_id)
    result = backend.run_python(PythonRequest(run_id=run_id, work=tmp_path, script="ScriptsUtilized/x.py",
                                              args=(), timeout_seconds=5))
    assert spawned == [] and result.exit_code is None and "stopped" in result.stderr
