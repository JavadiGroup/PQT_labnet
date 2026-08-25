from __future__ import annotations

import logging
import re
from pathlib import Path
from threading import Event
from typing import Any

from .api import CentralUpdateClient, LabPCControlClient
from . import __version__
from .artifacts import ReleaseManifest, RuntimeInstaller
from .config import UpdaterConfig
from .state import StateStore, UpdaterState
from .supervisor import LabPCSupervisor, StopSafetyError


LOGGER = logging.getLogger("labupdater")


class UpdateAgent:
    def __init__(
        self,
        config: UpdaterConfig,
        *,
        central: CentralUpdateClient | None = None,
        control: LabPCControlClient | None = None,
        installer: RuntimeInstaller | None = None,
        state_store: StateStore | None = None,
        supervisor: LabPCSupervisor | None = None,
    ) -> None:
        self.config = config
        self.central = central or CentralUpdateClient(
            config.central_url,
            config.read_api_key(),
            ca_file=config.ca_file,
        )
        self.control = control or LabPCControlClient(
            config.labpc_url, config.read_control_token()
        )
        self.installer = installer or RuntimeInstaller(
            config.release_root,
            install_timeout_seconds=config.install_timeout_seconds,
        )
        self.state_store = state_store or StateStore(config.release_root)
        self.supervisor = supervisor or LabPCSupervisor(config, self.control)
        self.stop_event = Event()

    def run_forever(self) -> None:
        state = self.state_store.load()
        current = self.state_store.current_version()
        if current:
            state.active_version = current
            self.state_store.save(state)
            try:
                self.supervisor.start(
                    self._runtime(current),
                    current,
                    accept_existing_not_ready=True,
                )
            except Exception as exc:
                if state.attempt_id and state.previous_version:
                    LOGGER.exception(
                        "The selected runtime failed after an interrupted update; rolling back."
                    )
                    previous = state.previous_version
                    self.state_store.activate(previous)
                    self.supervisor.start(self._runtime(previous), previous)
                    try:
                        self.central.abort(
                            state.attempt_id,
                            str(exc),
                            rolled_back=True,
                            observed_version=previous,
                            reopen_admission=True,
                        )
                    except Exception:
                        LOGGER.exception("Could not report startup recovery to Central.")
                    state.active_version = previous
                    state.attempt_id = None
                    state.phase = "rolled_back"
                    state.last_error = str(exc)
                    self.state_store.save(state)
                else:
                    raise
        else:
            LOGGER.warning(
                "No active LabNet runtime is installed; waiting for an assignment."
            )

        while not self.stop_event.is_set():
            try:
                directive = self.central.directive()
                if directive.get("update_available"):
                    self.apply(directive)
                else:
                    self._reconcile_no_update(directive)
                    current = self.state_store.current_version()
                    if current:
                        self.supervisor.start(
                            self._runtime(current),
                            current,
                            accept_existing_not_ready=True,
                        )
            except Exception:
                LOGGER.exception("Updater poll failed; the current runtime is unchanged.")
            self.stop_event.wait(self.config.poll_interval_seconds)

    def apply(self, directive: dict[str, Any]) -> None:
        if directive.get("manager_id") != self.config.manager_id:
            raise RuntimeError(
                "Central directive identity does not match this updater configuration."
            )
        manifest = ReleaseManifest.parse(directive.get("manifest"))
        if _version_key(__version__) < _version_key(manifest.minimum_updater_version):
            raise RuntimeError(
                f"Release requires Labupdater {manifest.minimum_updater_version} or newer; "
                f"this supervisor is {__version__}."
            )
        attempt_id = _required_string(directive, "attempt_id")
        artifact_url = _required_string(directive, "artifact_url")
        remote_phase = str(directive.get("state") or "assigned")
        state = self.state_store.load()
        current_version = self.state_store.current_version()
        previous_version = (
            state.previous_version
            if state.attempt_id == attempt_id and state.previous_version
            else current_version
        )
        old_process_stopped = False
        state.attempt_id = attempt_id
        state.previous_version = previous_version
        state.phase = "downloading"
        state.last_error = None
        self.state_store.save(state)

        try:
            if remote_phase in {"assigned", "downloading"}:
                self.central.phase(attempt_id, "downloading")
            artifact = self.installer.download_path(attempt_id, manifest)
            artifact.unlink(missing_ok=True)
            self.central.download(
                artifact_url,
                artifact,
                expected_size=manifest.artifact_size,
            )
            self.installer.verify_artifact(artifact, manifest)
            runtime = self.installer.stage(
                artifact, manifest, attempt_id=attempt_id
            )
            if (
                self.state_store.current_version() == manifest.version
                and remote_phase in {"switching", "verifying"}
            ):
                old_process_stopped = previous_version != manifest.version
                self.supervisor.start(runtime, manifest.version)
                if remote_phase == "switching":
                    self.central.phase(attempt_id, "verifying")
                self.supervisor.wait_ready(
                    manifest.version, self.config.startup_timeout_seconds
                )
                self._complete_or_defer(attempt_id, manifest, state, artifact)
                return
            state.phase = "staged"
            self.state_store.save(state)
            if remote_phase in {"assigned", "downloading", "staged"}:
                self.central.phase(attempt_id, "staged")

            if remote_phase in {"assigned", "downloading", "staged", "draining"}:
                drain = self.central.begin_drain(attempt_id)
            else:
                drain = self.central.drain_status(attempt_id)
            state.phase = "draining"
            self.state_store.save(state)
            while not drain.get("ready_to_apply"):
                if self.stop_event.wait(self.config.drain_poll_seconds):
                    raise RuntimeError("Updater is shutting down while waiting for leases.")
                drain = self.central.drain_status(attempt_id)

            current_before_switch = self.state_store.current_version()
            if current_before_switch:
                self.supervisor.start(
                    self._runtime(current_before_switch),
                    current_before_switch,
                    accept_existing_not_ready=True,
                )
            self.control.drain()
            if remote_phase in {"assigned", "downloading", "staged", "draining"}:
                self.central.phase(attempt_id, "quiescing")
            state.phase = "quiescing"
            self.state_store.save(state)
            self._wait_local_quiescence()
            self.supervisor.stop_after_quiescence()
            old_process_stopped = True

            if remote_phase not in {"switching", "verifying"}:
                self.central.phase(attempt_id, "switching")
            state.phase = "switching"
            self.state_store.save(state)
            self.state_store.activate(manifest.version)
            self.supervisor.start(runtime, manifest.version)

            if remote_phase != "verifying":
                self.central.phase(attempt_id, "verifying")
            state.phase = "verifying"
            self.state_store.save(state)
            self.supervisor.wait_ready(
                manifest.version, self.config.startup_timeout_seconds
            )
            self._complete_or_defer(attempt_id, manifest, state, artifact)
        except Exception as exc:
            LOGGER.exception("Update attempt %s failed.", attempt_id)
            if isinstance(exc, StopSafetyError):
                state.phase = "quiescing"
                state.last_error = str(exc)
                self.state_store.save(state)
                LOGGER.error(
                    "Central admission remains drained until physical disconnect succeeds."
                )
                raise
            rolled_back = False
            resumed = False
            if old_process_stopped and previous_version:
                try:
                    if self.supervisor.is_running():
                        self.control.drain()
                        self._wait_local_quiescence()
                        self.supervisor.stop_after_quiescence()
                    self.state_store.activate(previous_version)
                    self.supervisor.start(
                        self._runtime(previous_version), previous_version
                    )
                    self.supervisor.wait_ready(
                        previous_version, self.config.startup_timeout_seconds
                    )
                    rolled_back = True
                except StopSafetyError:
                    state.phase = "recovery_required"
                    state.last_error = str(exc)
                    self.state_store.save(state)
                    raise
                except Exception:
                    LOGGER.exception("Automatic rollback also failed.")
            elif not old_process_stopped:
                try:
                    self.control.resume()
                    if previous_version:
                        self.supervisor.wait_ready(
                            previous_version, self.config.startup_timeout_seconds
                        )
                    resumed = True
                except Exception:
                    LOGGER.warning("Could not resume local admission after failure.")
            reopen_admission = rolled_back or resumed
            abort_reported = False
            try:
                self.central.abort(
                    attempt_id,
                    str(exc),
                    rolled_back=rolled_back,
                    observed_version=(previous_version if reopen_admission else None),
                    reopen_admission=reopen_admission,
                )
                abort_reported = True
            except Exception:
                LOGGER.exception("Could not report failed attempt to Central.")
            state.active_version = previous_version
            state.phase = (
                "rolled_back"
                if rolled_back
                else "failed"
                if resumed
                else "recovery_required"
            )
            state.last_error = str(exc)
            if abort_reported and reopen_admission:
                state.attempt_id = None
            self.state_store.save(state)
            raise

    def _complete_or_defer(
        self,
        attempt_id: str,
        manifest: ReleaseManifest,
        state: UpdaterState,
        artifact: Path,
    ) -> None:
        state.phase = "awaiting_complete"
        self.state_store.save(state)
        try:
            self.central.complete(attempt_id, manifest.version)
        except Exception as exc:
            state.last_error = str(exc)
            self.state_store.save(state)
            LOGGER.warning(
                "The new runtime is healthy but Central completion is pending: %s", exc
            )
            return
        state.active_version = manifest.version
        state.phase = "idle"
        state.attempt_id = None
        state.last_error = None
        try:
            self.state_store.save(state)
            artifact.unlink(missing_ok=True)
        except Exception:
            LOGGER.exception(
                "Central committed the update, but local cleanup must be retried."
            )
        LOGGER.info("Activated LabNet %s.", manifest.version)

    def _reconcile_no_update(self, directive: dict[str, Any]) -> None:
        state = self.state_store.load()
        current = self.state_store.current_version()
        if (
            state.attempt_id
            and directive.get("attempt_id") == state.attempt_id
            and directive.get("state") == "succeeded"
            and directive.get("observed_version") == current
        ):
            state.active_version = current
            state.attempt_id = None
            state.phase = "idle"
            state.last_error = None
            self.state_store.save(state)

    def _wait_local_quiescence(self) -> None:
        while True:
            status = self.control.status()
            if status.get("state") == "failed":
                raise RuntimeError(
                    f"Lab-PC local drain failed: {status.get('last_error')}"
                )
            if status.get("quiescent"):
                return
            if self.stop_event.wait(0.25):
                raise RuntimeError("Updater is shutting down during local drain.")

    def _runtime(self, version: str) -> Path:
        return self.config.release_root / "runtimes" / version


def _required_string(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Update directive is missing {name!r}.")
    return value


def _version_key(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:[-+].*)?", value)
    if match is None:
        raise RuntimeError(f"Unsupported updater version format {value!r}.")
    parts = tuple(int(part) for part in match.group(1).split("."))
    return (parts + (0, 0, 0, 0))[:4]


__all__ = ["UpdateAgent"]
