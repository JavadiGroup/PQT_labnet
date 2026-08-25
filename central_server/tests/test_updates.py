from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from central_server.app.config import Settings
from central_server.app.main import create_app
from central_server.app.services.auth import UserService


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _registration() -> dict[str, object]:
    return {
        "manager_id": "lab-pc-01",
        "host": "lab-pc-01",
        "endpoint": {
            "transport": "grpc",
            "target": "127.0.0.1:50051",
            "api_version": "labnet.devices.v1",
            "secure": False,
            "attributes": {},
        },
        "devices": [
            {
                "device_id": "scope-1",
                "device_type": "dummy_device",
                "runtime_state": "ready",
                "availability": "available",
                "status_text": "idle",
                "metadata": {},
            }
        ],
    }


def _setup(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'central.db').as_posix()}",
        release_root=tmp_path / "release-store",
        lease_min_seconds=1,
        lease_max_seconds=3600,
        heartbeat_timeout_seconds=30,
        maintenance_interval_seconds=60,
        create_schema_on_startup=True,
    )
    app = create_app(settings)
    users: UserService = app.state.user_service
    admin = users.create_user("release-admin", "admin")
    manager = users.create_user("lab-pc-01", "device_manager")
    user = users.create_user("researcher", "user")
    return app, admin["api_key"], manager["api_key"], user["api_key"]


def _publish(
    client: TestClient,
    admin_key: str,
    *,
    version: str = "0.3.0",
) -> dict[str, object]:
    artifact = b"test release bundle"
    digest = hashlib.sha256(artifact).hexdigest()
    manifest = {
        "schema_version": 1,
        "package": "labnet",
        "version": version,
        "artifact_filename": f"labnet-{version}-bundle.zip",
        "artifact_sha256": digest,
        "artifact_size": len(artifact),
        "wheel_filename": f"labnet-{version}-py3-none-any.whl",
        "wheel_sha256": "a" * 64,
        "files": {},
        "git_commit": "0123456789abcdef",
        "roles": ["lab_pc", "client"],
        "python_version": "3.11",
        "platform_tag": "win_amd64",
        "contract_digest": "b" * 64,
        "minimum_updater_version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    created = client.post(
        "/api/v1/releases",
        headers=_auth(admin_key),
        json={"manifest": manifest},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    uploaded = client.put(
        body["upload_url"],
        headers={**_auth(admin_key), "Content-Type": "application/zip"},
        content=artifact,
    )
    assert uploaded.status_code == 200, uploaded.text
    promoted = client.post(
        f"/api/v1/releases/{body['release_id']}/promote",
        headers=_auth(admin_key),
    )
    assert promoted.status_code == 200, promoted.text
    return promoted.json()


def test_release_deployment_drain_and_complete(tmp_path: Path) -> None:
    app, admin_key, manager_key, user_key = _setup(tmp_path)
    with TestClient(app) as client:
        registered = client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration(),
        )
        assert registered.status_code == 200, registered.text
        release = _publish(client, admin_key)

        assigned = client.put(
            "/api/v1/deployments/lab-pc-01",
            headers=_auth(admin_key),
            json={"version": release["version"]},
        )
        assert assigned.status_code == 200, assigned.text
        attempt_id = assigned.json()["active_attempt_id"]

        directive = client.get(
            "/api/v1/updates/directive", headers=_auth(manager_key)
        )
        assert directive.status_code == 200, directive.text
        assert directive.json()["attempt_id"] == attempt_id
        assert directive.json()["update_available"] is True
        artifact = client.get(
            f"/api/v1/updates/artifacts/{release['release_id']}",
            headers=_auth(manager_key),
        )
        assert artifact.status_code == 200
        assert artifact.headers["x-labnet-sha256"] == release["artifact_sha256"]

        for phase in ("downloading", "staged"):
            response = client.post(
                f"/api/v1/updates/{attempt_id}/phase",
                headers=_auth(manager_key),
                json={"state": phase, "details": {}},
            )
            assert response.status_code == 200, response.text

        bypass = client.post(
            f"/api/v1/updates/{attempt_id}/phase",
            headers=_auth(manager_key),
            json={"state": "draining", "details": {}},
        )
        assert bypass.status_code == 422

        lease = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "running-job"},
            json={"device_id": "scope-1", "duration_seconds": 60},
        )
        assert lease.status_code == 201, lease.text
        lease_id = lease.json()["lease"]["lease_id"]

        draining = client.post(
            f"/api/v1/updates/{attempt_id}/begin-drain",
            headers=_auth(manager_key),
        )
        assert draining.status_code == 200, draining.text
        assert draining.json()["active_lease_count"] == 1
        assert draining.json()["ready_to_apply"] is False

        changed_inventory = _registration()
        changed_inventory["devices"].append(  # type: ignore[union-attr]
            {
                "device_id": "scope-2",
                "device_type": "dummy_device",
                "runtime_state": "ready",
                "availability": "available",
                "status_text": "idle",
                "metadata": {},
            }
        )
        reregistered = client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=changed_inventory,
        )
        assert reregistered.status_code == 200, reregistered.text
        new_device = next(
            item
            for item in reregistered.json()["devices"]
            if item["device_id"] == "scope-2"
        )
        assert new_device["admission_state"] == "draining"
        blocked_new_device = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "new-device-race"},
            json={"device_id": "scope-2", "duration_seconds": 60},
        )
        assert blocked_new_device.status_code == 409

        client.delete(f"/api/v1/leases/{lease_id}", headers=_auth(user_key))
        blocked = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "late-job"},
            json={"device_id": "scope-1", "duration_seconds": 60},
        )
        assert blocked.status_code == 409, blocked.text

        drained = client.get(
            f"/api/v1/updates/{attempt_id}/drain",
            headers=_auth(manager_key),
        )
        assert drained.status_code == 200, drained.text
        assert drained.json()["ready_to_apply"] is True

        for phase in ("quiescing", "switching", "verifying"):
            response = client.post(
                f"/api/v1/updates/{attempt_id}/phase",
                headers=_auth(manager_key),
                json={"state": phase, "details": {}},
            )
            assert response.status_code == 200, response.text

        updated_registration = _registration()
        updated_registration["endpoint"]["attributes"] = {  # type: ignore[index]
            "package_version": release["version"]
        }
        updated = client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=updated_registration,
        )
        assert updated.status_code == 200, updated.text

        completed = client.post(
            f"/api/v1/updates/{attempt_id}/complete",
            headers=_auth(manager_key),
            json={"installed_version": release["version"]},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["state"] == "succeeded"

        available = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "after-update"},
            json={"device_id": "scope-1", "duration_seconds": 60},
        )
        assert available.status_code == 201, available.text


def test_release_upload_rejects_mismatched_bytes(tmp_path: Path) -> None:
    app, admin_key, _manager_key, _user_key = _setup(tmp_path)
    artifact = b"declared bytes"
    manifest = {
        "schema_version": 1,
        "package": "labnet",
        "version": "0.4.0",
        "artifact_filename": "labnet-0.4.0-bundle.zip",
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        "artifact_size": len(artifact),
        "wheel_filename": "labnet-0.4.0-py3-none-any.whl",
        "wheel_sha256": "c" * 64,
        "files": {},
        "git_commit": "abcdef0123456789",
        "roles": ["lab_pc"],
        "python_version": "3.11",
        "platform_tag": "win_amd64",
        "minimum_updater_version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/releases",
            headers=_auth(admin_key),
            json={"manifest": manifest},
        )
        assert created.status_code == 201
        uploaded = client.put(
            created.json()["upload_url"],
            headers=_auth(admin_key),
            content=b"wrong contents",
        )
        assert uploaded.status_code in {413, 422}


def test_assignment_is_noop_when_manager_reports_same_version(tmp_path: Path) -> None:
    app, admin_key, manager_key, _user_key = _setup(tmp_path)
    registration = _registration()
    registration["endpoint"]["attributes"] = {"package_version": "0.3.0"}  # type: ignore[index]
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=registration,
        )
        assert response.status_code == 200, response.text
        _publish(client, admin_key, version="0.3.0")
        assignment = client.put(
            "/api/v1/deployments/lab-pc-01",
            headers=_auth(admin_key),
            json={"version": "0.3.0"},
        )
        assert assignment.status_code == 200, assignment.text
        assert assignment.json()["state"] == "succeeded"
        assert assignment.json()["active_attempt_id"] is None
        directive = client.get(
            "/api/v1/updates/directive", headers=_auth(manager_key)
        )
        assert directive.status_code == 200
        assert directive.json()["update_available"] is False
