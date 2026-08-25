from __future__ import annotations

import json
from pathlib import Path

import pytest

from labupdater.agent import UpdateAgent
from labupdater.config import UpdaterConfig
from labupdater.state import StateStore, UpdaterState


class FakeCentral:
    def __init__(self, *, complete_error: bool = False) -> None:
        self.events: list[tuple] = []
        self.complete_error = complete_error

    def phase(self, attempt_id, state, details=None):
        self.events.append(("phase", state))
        return {}

    def download(self, artifact_url, target, *, expected_size):
        self.events.append(("download", artifact_url))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"bundle")

    def begin_drain(self, attempt_id):
        self.events.append(("begin_drain",))
        return {"ready_to_apply": True}

    def drain_status(self, attempt_id):
        return {"ready_to_apply": True}

    def complete(self, attempt_id, version):
        self.events.append(("complete", version))
        if self.complete_error:
            raise OSError("completion response lost")
        return {}

    def abort(
        self,
        attempt_id,
        error,
        *,
        rolled_back,
        observed_version,
        reopen_admission,
    ):
        self.events.append(
            ("abort", rolled_back, observed_version, reopen_admission)
        )
        return {}


class FakeControl:
    def __init__(self) -> None:
        self.events: list[str] = []

    def drain(self):
        self.events.append("drain")
        return {}

    def status(self):
        return {"state": "quiescent", "quiescent": True}

    def resume(self):
        self.events.append("resume")
        return {}


class FakeInstaller:
    def __init__(self, root: Path) -> None:
        self.root = root

    def download_path(self, attempt_id, manifest):
        return self.root / "downloads" / "release.zip"

    def verify_artifact(self, artifact, manifest):
        return None

    def stage(self, artifact, manifest, *, attempt_id):
        runtime = self.root / "runtimes" / manifest.version
        runtime.mkdir(parents=True)
        (runtime / "runtime.json").write_text(
            json.dumps({"artifact_sha256": manifest.artifact_sha256}),
            encoding="utf-8",
        )
        return runtime


class FakeSupervisor:
    def __init__(self, *, fail_version: str | None = None) -> None:
        self.events: list[tuple] = []
        self.fail_version = fail_version

    def stop_after_quiescence(self):
        self.events.append(("stop",))

    def start(self, runtime, version, *, accept_existing_not_ready=False):
        self.events.append(("start", version))
        if version == self.fail_version:
            raise RuntimeError("new runtime failed readiness")

    def wait_ready(self, version, timeout):
        self.events.append(("ready", version))
        return {"ready": True}

    def is_running(self):
        return False


def _config(root: Path) -> UpdaterConfig:
    return UpdaterConfig(
        manager_id="lab-pc-01",
        central_url="https://central.example",
        api_key_file=root / "api.key",
        release_root=root,
        local_control_token_file=root / "control.token",
    )


def _directive() -> dict:
    return {
        "manager_id": "lab-pc-01",
        "attempt_id": "upd_123",
        "artifact_url": "/artifact",
        "manifest": {
            "version": "0.3.0",
            "artifact_filename": "labnet-0.3.0-bundle.zip",
            "artifact_sha256": "a" * 64,
            "artifact_size": 6,
            "wheel_filename": "labnet-0.3.0-py3-none-any.whl",
            "wheel_sha256": "b" * 64,
            "files": {},
            "roles": ["lab_pc"],
            "minimum_updater_version": "1.0.0",
        },
    }


def _store_with_old_runtime(root: Path) -> StateStore:
    runtime = root / "runtimes" / "0.2.0"
    runtime.mkdir(parents=True)
    (runtime / "runtime.json").write_text("{}", encoding="utf-8")
    store = StateStore(root)
    store.activate("0.2.0")
    store.save(UpdaterState(active_version="0.2.0"))
    return store


def test_apply_switches_only_after_both_drains(tmp_path: Path) -> None:
    central = FakeCentral()
    control = FakeControl()
    supervisor = FakeSupervisor()
    store = _store_with_old_runtime(tmp_path)
    agent = UpdateAgent(
        _config(tmp_path),
        central=central,
        control=control,
        installer=FakeInstaller(tmp_path),
        state_store=store,
        supervisor=supervisor,
    )
    agent.apply(_directive())
    assert store.current_version() == "0.3.0"
    assert store.load().phase == "idle"
    assert control.events == ["drain"]
    assert supervisor.events == [
        ("start", "0.2.0"),
        ("stop",),
        ("start", "0.3.0"),
        ("ready", "0.3.0"),
    ]
    assert central.events[-1] == ("complete", "0.3.0")


def test_failed_new_runtime_rolls_back_pointer_and_process(tmp_path: Path) -> None:
    central = FakeCentral()
    store = _store_with_old_runtime(tmp_path)
    supervisor = FakeSupervisor(fail_version="0.3.0")
    agent = UpdateAgent(
        _config(tmp_path),
        central=central,
        control=FakeControl(),
        installer=FakeInstaller(tmp_path),
        state_store=store,
        supervisor=supervisor,
    )
    with pytest.raises(RuntimeError, match="failed readiness"):
        agent.apply(_directive())
    assert store.current_version() == "0.2.0"
    assert store.load().phase == "rolled_back"
    assert ("start", "0.2.0") in supervisor.events
    assert central.events[-1] == ("abort", True, "0.2.0", True)


def test_lost_completion_response_does_not_roll_back_healthy_runtime(
    tmp_path: Path,
) -> None:
    central = FakeCentral(complete_error=True)
    store = _store_with_old_runtime(tmp_path)
    supervisor = FakeSupervisor()
    agent = UpdateAgent(
        _config(tmp_path),
        central=central,
        control=FakeControl(),
        installer=FakeInstaller(tmp_path),
        state_store=store,
        supervisor=supervisor,
    )
    agent.apply(_directive())
    assert store.current_version() == "0.3.0"
    assert store.load().phase == "awaiting_complete"
    assert not any(event[0] == "abort" for event in central.events)
