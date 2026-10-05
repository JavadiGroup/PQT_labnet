from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import update

from labnet import LabClient
from labnet.central.main import create_app
from labnet.central.storage.orm import DeviceManagerRow

from .test_control_plane import _auth, _registration, _settings


def _network(tmp_path: Path):
    app = create_app(_settings(tmp_path / "central.db"))
    users = app.state.user_service
    manager_key = users.create_user("lab-pc-01", "device_manager")["api_key"]
    user_key = users.create_user("researcher", "user")["api_key"]
    return app, manager_key, user_key


def test_lab_pcs_are_listed_with_their_devices_and_lease_state(tmp_path: Path) -> None:
    app, manager_key, user_key = _network(tmp_path)
    with TestClient(app) as http:
        assert http.get("/api/v1/device-managers").status_code == 401
        http.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration(),
        ).raise_for_status()

        listed = http.get("/api/v1/device-managers", headers=_auth(user_key))
        assert listed.status_code == 200, listed.text
        (manager,) = listed.json()["device_managers"]
        assert manager["manager_id"] == "lab-pc-01"
        assert manager["status"] == "online"
        assert manager["endpoint"]["target"] == "127.0.0.1:50051"
        assert "owner_user_id" not in manager
        assert manager["devices"][0]["allocation_state"] == "free"

        http.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "list-test"},
            json={"device_id": "wavemeter-1", "duration_seconds": 60},
        ).raise_for_status()

        # The typed client sees the same picture, including the lease.
        client = LabClient(
            "http://127.0.0.1", user_key, authenticate_on_start=False, transport=http._transport
        )
        try:
            (lab_pc,) = client.list_lab_pcs()
        finally:
            client.close()
        assert lab_pc.online is True
        assert lab_pc.endpoint.target == "127.0.0.1:50051"
        (device,) = lab_pc.devices
        assert device.leased is True
        assert device.effective_available is False


def test_a_silent_lab_pc_is_reported_offline(tmp_path: Path) -> None:
    app, manager_key, user_key = _network(tmp_path)
    with TestClient(app) as http:
        http.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration(),
        ).raise_for_status()
        with app.state.registry_service.database.transaction() as session:
            session.execute(
                update(DeviceManagerRow).values(
                    last_seen=datetime.now(timezone.utc) - timedelta(hours=1)
                )
            )

        (manager,) = http.get(
            "/api/v1/device-managers", headers=_auth(user_key)
        ).json()["device_managers"]

        assert manager["status"] == "offline"
        assert manager["devices"][0]["effective_available"] is False
