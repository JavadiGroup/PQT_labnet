from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from central_server.app.storage.database import Database
from central_server.app.storage.orm import AuditEventRow, LeaseRow
from central_server.app.main import create_app
from central_server.manage import _alembic_config

from .test_control_plane import _auth, _registration, _settings, _setup


def _setup_with_idle_timeout(tmp_path: Path, seconds: int):
    app = create_app(
        replace(
            _settings(tmp_path / "central.db"),
            lease_default_idle_timeout_seconds=seconds,
        )
    )
    users = app.state.user_service
    manager = users.create_user("lab-pc-01", "device_manager")
    user = users.create_user("researcher", "user")
    other = users.create_user("lab-pc-02", "device_manager")
    return app, manager["api_key"], user["api_key"], other["api_key"]


def _register_and_grant(
    client: TestClient,
    manager_key: str,
    user_key: str,
    *,
    key: str,
    duration_seconds: int | None = None,
) -> dict:
    assert client.post(
        "/api/v1/device-managers/register",
        headers=_auth(manager_key),
        json=_registration(),
    ).status_code == 200
    body: dict[str, object] = {"device_id": "wavemeter-1"}
    if duration_seconds is not None:
        body["duration_seconds"] = duration_seconds
    response = client.post(
        "/api/v1/leases",
        headers={**_auth(user_key), "Idempotency-Key": key},
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_defaults_and_renewal_advance_only_the_liveness_clock(tmp_path: Path) -> None:
    app, manager_key, user_key, _ = _setup(tmp_path)
    with TestClient(app) as client:
        grant = _register_and_grant(
            client, manager_key, user_key, key="renew-defaults"
        )
        original = grant["lease"]
        assert original["idle_timeout_seconds"] == 900
        assert original["last_renewed_at"] == original["created_at"]
        assert original["last_activity_at"] == original["created_at"]
        created_at = datetime.fromisoformat(original["created_at"])
        expires_at = datetime.fromisoformat(original["expires_at"])
        assert created_at.tzinfo is not None
        assert datetime.fromisoformat(original["last_renewed_at"]).tzinfo is not None
        assert datetime.fromisoformat(original["last_activity_at"]).tzinfo is not None
        assert 59.9 <= (expires_at - created_at).total_seconds() <= 60.1

        renewed = client.post(
            f"/api/v1/leases/{original['lease_id']}/renew",
            headers=_auth(user_key),
            json={"duration_seconds": 120},
        )
        assert renewed.status_code == 200, renewed.text
        current = renewed.json()
        assert datetime.fromisoformat(current["expires_at"]) > datetime.fromisoformat(
            original["expires_at"]
        )
        assert datetime.fromisoformat(current["last_renewed_at"]) > datetime.fromisoformat(
            original["last_renewed_at"]
        )
        assert current["last_activity_at"] == original["last_activity_at"]


def test_successful_validation_atomically_touches_command_activity(tmp_path: Path) -> None:
    app, manager_key, user_key, _ = _setup_with_idle_timeout(tmp_path, 300)
    with TestClient(app) as client:
        grant = _register_and_grant(
            client,
            manager_key,
            user_key,
            key="validation-activity",
            duration_seconds=600,
        )
        lease_id = grant["lease"]["lease_id"]
        old_activity = datetime.now(timezone.utc) - timedelta(seconds=30)
        with app.state.database.transaction() as session:
            lease = session.get(LeaseRow, lease_id)
            assert lease is not None
            lease.last_activity_at = old_activity

        response = client.post(
            f"/api/v1/leases/{lease_id}/validate",
            headers=_auth(manager_key),
            json={"device_id": "wavemeter-1"},
        )
        assert response.status_code == 200, response.text
        assert datetime.fromisoformat(response.json()["expires_at"]) < datetime.fromisoformat(
            grant["lease"]["expires_at"]
        )
        with app.state.database.session() as session:
            lease = session.get(LeaseRow, lease_id)
            assert lease is not None
            assert lease.last_activity_at > old_activity


def test_rejected_validation_cannot_touch_activity_and_deadline_is_minimum(
    tmp_path: Path,
) -> None:
    app, manager_key, user_key, other_manager_key = _setup_with_idle_timeout(
        tmp_path, 300
    )
    with TestClient(app) as client:
        grant = _register_and_grant(
            client,
            manager_key,
            user_key,
            key="validation-boundaries",
            duration_seconds=600,
        )
        lease_id = grant["lease"]["lease_id"]
        old_activity = datetime.now(timezone.utc) - timedelta(seconds=30)
        with app.state.database.transaction() as session:
            lease = session.get(LeaseRow, lease_id)
            assert lease is not None
            lease.last_activity_at = old_activity

        wrong_manager = client.post(
            f"/api/v1/leases/{lease_id}/validate",
            headers=_auth(other_manager_key),
            json={"device_id": "wavemeter-1"},
        )
        wrong_device = client.post(
            f"/api/v1/leases/{lease_id}/validate",
            headers=_auth(manager_key),
            json={"device_id": "another-device"},
        )
        assert wrong_manager.status_code == 403
        assert wrong_device.status_code == 403
        with app.state.database.session() as session:
            unchanged = session.get(LeaseRow, lease_id)
            assert unchanged is not None
            assert unchanged.last_activity_at == old_activity

        accepted = client.post(
            f"/api/v1/leases/{lease_id}/validate",
            headers=_auth(manager_key),
            json={"device_id": "wavemeter-1"},
        )
        assert accepted.status_code == 200, accepted.text
        effective_deadline = datetime.fromisoformat(accepted.json()["expires_at"])
        with app.state.database.session() as session:
            touched = session.get(LeaseRow, lease_id)
            assert touched is not None
            expected_deadline = min(
                touched.expires_at,
                touched.last_activity_at
                + timedelta(seconds=touched.idle_timeout_seconds),
            )
        assert effective_deadline == expected_deadline


def test_idle_expiry_wins_even_when_lease_was_successfully_renewed(tmp_path: Path) -> None:
    app, manager_key, user_key, _ = _setup_with_idle_timeout(tmp_path, 1)
    with TestClient(app) as client:
        grant = _register_and_grant(
            client,
            manager_key,
            user_key,
            key="idle-despite-renewal",
        )
        lease_id = grant["lease"]["lease_id"]
        first_renewal = client.post(
            f"/api/v1/leases/{lease_id}/renew",
            headers=_auth(user_key),
            json={"duration_seconds": 120},
        )
        assert first_renewal.status_code == 200
        activity = first_renewal.json()["last_activity_at"]

        with app.state.database.transaction() as session:
            lease = session.get(LeaseRow, lease_id)
            assert lease is not None
            lease.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=2)

        rejected = client.post(
            f"/api/v1/leases/{lease_id}/renew",
            headers=_auth(user_key),
            json={"duration_seconds": 120},
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "LEASE_EXPIRED"
        with app.state.database.session() as session:
            lease = session.get(LeaseRow, lease_id)
            assert lease is not None
            assert lease.state == "expired"
            assert lease.active_slot is None
            assert lease.terminal_reason == "command_idle_timeout_elapsed"
            # The successful renewal did not masquerade as command activity.
            assert activity == grant["lease"]["last_activity_at"]


def test_client_cannot_override_or_disable_central_idle_policy(tmp_path: Path) -> None:
    app, manager_key, user_key, _ = _setup(tmp_path)
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration(),
        ).status_code == 200
        rejected = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "override-idle"},
            json={
                "device_id": "wavemeter-1",
                "idle_timeout_seconds": None,
            },
        )
        assert rejected.status_code == 422
        grant = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "central-idle"},
            json={"device_id": "wavemeter-1", "duration_seconds": 120},
        ).json()
        lease_id = grant["lease"]["lease_id"]
        assert grant["lease"]["idle_timeout_seconds"] == 900
        with app.state.database.transaction() as session:
            lease = session.get(LeaseRow, lease_id)
            assert lease is not None
            lease.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=901)

        maintained = app.state.lease_service.maintain(
            heartbeat_timeout_seconds=30
        )
        assert maintained["expired"] == 1
        renewal = client.post(
            f"/api/v1/leases/{lease_id}/renew",
            headers=_auth(user_key),
            json={"duration_seconds": 120},
        )
        assert renewal.status_code == 409, renewal.text


def test_maintenance_audits_idle_reason_and_frees_unique_active_slot(
    tmp_path: Path,
) -> None:
    app, manager_key, user_key, _ = _setup_with_idle_timeout(tmp_path, 1)
    with TestClient(app) as client:
        grant = _register_and_grant(
            client,
            manager_key,
            user_key,
            key="maintenance-idle",
        )
        lease_id = grant["lease"]["lease_id"]
        with app.state.database.transaction() as session:
            lease = session.get(LeaseRow, lease_id)
            assert lease is not None
            lease.last_activity_at = datetime.now(timezone.utc) - timedelta(seconds=2)

        result = app.state.lease_service.maintain(heartbeat_timeout_seconds=30)
        assert result == {"expired": 1, "revoked": 0, "offline": 0}
        with app.state.database.session() as session:
            lease = session.get(LeaseRow, lease_id)
            event = session.scalar(
                select(AuditEventRow)
                .where(
                    AuditEventRow.lease_id == lease_id,
                    AuditEventRow.event_type == "LEASE_EXPIRED",
                )
                .order_by(AuditEventRow.event_id.desc())
            )
            assert lease is not None
            assert lease.state == "expired"
            assert lease.active_slot is None
            assert lease.terminal_reason == "command_idle_timeout_elapsed"
            assert event is not None
            assert event.details == {"reason": "command_idle_timeout_elapsed"}

        replacement = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "after-idle-expiry"},
            json={"device_id": "wavemeter-1"},
        )
        assert replacement.status_code == 201, replacement.text
        assert replacement.json()["lease"]["lease_id"] != lease_id


def test_migration_0001_to_0002_backfills_existing_leases(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    config = _alembic_config(url)
    command.upgrade(config, "20260824_0001")
    engine = create_engine(url)
    created_at = "2026-08-24 12:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(user_id, username, role, enabled, created_at, updated_at) VALUES "
                "('manager-user', 'lab-pc-01', 'device_manager', 1, :now, :now), "
                "('lease-user', 'researcher', 'user', 1, :now, :now)"
            ),
            {"now": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO device_managers "
                "(manager_id, owner_user_id, hostname, transport, target, "
                "api_version, secure, tls_server_name, endpoint_attributes, status, "
                "registered_at, last_seen) VALUES "
                "('lab-pc-01', 'manager-user', 'host', 'grpc', '127.0.0.1:50051', "
                "'labnet.devices.v1', 0, NULL, '{}', 'online', :now, :now)"
            ),
            {"now": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO devices "
                "(device_id, manager_id, device_type, runtime_state, availability, "
                "status_text, metadata, first_seen, last_seen) VALUES "
                "('wavemeter-1', 'lab-pc-01', 'dummy_device', 'ready', "
                "'available', '', '{}', :now, :now)"
            ),
            {"now": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO leases "
                "(lease_id, device_id, user_id, state, active_slot, created_at, "
                "expires_at, renewed_at) VALUES "
                "('lease-existing', 'wavemeter-1', 'lease-user', 'active', 1, "
                ":created, :expires, NULL)"
            ),
            {"created": created_at, "expires": "2026-08-24 12:01:00"},
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        columns = {column["name"]: column for column in inspect(engine).get_columns("leases")}
        assert columns["renewed_at"]["nullable"] is False
        assert columns["last_activity_at"]["nullable"] is False
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT created_at, renewed_at, last_activity_at, "
                    "idle_timeout_seconds FROM leases WHERE lease_id='lease-existing'"
                )
            ).mappings().one()
        assert str(row["renewed_at"]) == str(row["created_at"])
        assert str(row["last_activity_at"]) == str(row["created_at"])
        assert row["idle_timeout_seconds"] == 900
    finally:
        engine.dispose()
    database = Database(url)
    try:
        assert database.ready() is True
    finally:
        database.dispose()
