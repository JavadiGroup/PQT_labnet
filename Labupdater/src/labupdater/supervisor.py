from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import TextIO

from .api import LabPCControlClient
from .artifacts import runtime_python
from .config import UpdaterConfig


class StopSafetyError(RuntimeError):
    """The old runtime did not prove that its hardware closed safely."""


class LabPCSupervisor:
    def __init__(self, config: UpdaterConfig, control: LabPCControlClient) -> None:
        self.config = config
        self.control = control
        self.process: subprocess.Popen[bytes] | None = None
        self._log_stream: TextIO | None = None

    def start(
        self,
        runtime: Path,
        expected_version: str,
        *,
        accept_existing_not_ready: bool = False,
    ) -> None:
        try:
            ready = self.control.ready()
        except Exception:
            ready = {}
        if _ready_response(ready):
            if ready.get("package_version") == expected_version:
                return
            raise RuntimeError(
                "A different Lab-PC version is already listening on the control port."
            )
        try:
            status = self.control.status()
        except Exception:
            status = {}
        if status.get("package_version"):
            if status.get("package_version") != expected_version:
                raise RuntimeError(
                    "A different Lab-PC version is already listening on the control port."
                )
            if accept_existing_not_ready:
                return
            self.wait_ready(expected_version, self.config.startup_timeout_seconds)
            return
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("A supervised Lab-PC process is already running.")

        logs = self.config.release_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        self._log_stream = (logs / "lab-pc.log").open(
            "a", encoding="utf-8", buffering=1
        )
        environment = os.environ.copy()
        environment["LABNET_UPDATER_CONTROL_TOKEN"] = self.config.read_control_token()
        if self.config.labpc_environment_file is not None:
            environment["LABNET_ENV_FILE"] = str(self.config.labpc_environment_file)
        if self.config.labpc_pythonpath:
            configured = os.pathsep.join(str(path) for path in self.config.labpc_pythonpath)
            existing = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = (
                configured + os.pathsep + existing if existing else configured
            )
        command = [str(runtime_python(runtime)), "-m", "lab_pc.main"]
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        self.process = subprocess.Popen(
            command,
            cwd=str(self.config.release_root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log_stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        try:
            self.wait_ready(expected_version, self.config.startup_timeout_seconds)
        except BaseException:
            self._discard_failed_start()
            raise

    def wait_ready(self, expected_version: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        last_error = "Lab-PC did not answer."
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(
                    f"Lab-PC exited during startup with code {self.process.returncode}."
                )
            try:
                payload = self.control.ready()
                if _ready_response(payload) and payload.get("package_version") == expected_version:
                    return payload
                last_error = f"readiness response was {payload!r}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.5)
        raise RuntimeError(
            f"Lab-PC version {expected_version} failed readiness: {last_error}"
        )

    def stop_after_quiescence(self) -> None:
        try:
            self.control.stop()
        except Exception as exc:
            raise StopSafetyError(
                "Lab-PC rejected its graceful disconnect; Central remains drained."
            ) from exc
        deadline = time.monotonic() + self.config.stop_timeout_seconds
        while time.monotonic() < deadline:
            if self.process is not None:
                if self.process.poll() is not None:
                    self._close_log()
                    return
            else:
                try:
                    self.control.live()
                except Exception:
                    return
            time.sleep(0.25)
        raise StopSafetyError(
            "Lab-PC did not stop cleanly; the active runtime was left unchanged."
        )

    def is_running(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        try:
            self.control.live()
        except Exception:
            return False
        return True

    def close(self) -> None:
        self._close_log()

    def _close_log(self) -> None:
        if self._log_stream is not None:
            self._log_stream.close()
            self._log_stream = None

    def _discard_failed_start(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        self.process = None
        self._close_log()


__all__ = ["LabPCSupervisor", "StopSafetyError"]


def _ready_response(payload: dict) -> bool:
    return payload.get("ready") is True or payload.get("status") == "ready"
