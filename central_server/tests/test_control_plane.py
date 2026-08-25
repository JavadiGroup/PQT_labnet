from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql

from central_server.app.config import Settings
from central_server.app.main import create_app
from central_server.app.models.common import ErrorCode, ServiceError
from central_server.app.services.auth import UserService
from central_server.app.services.leases import (
    _locked_active_device_leases_statement,
    _locked_device_statement,
    _locked_expiring_leases_statement,
    _locked_manager_devices_statement,
)
from central_server.manage import _alembic_config
from central_server.app.storage.database import Database
from central_server.app.storage.orm import DeviceManagerRow


def _settings(path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{path.as_posix()}",
        lease_min_seconds=1,
        lease_max_seconds=3600,
        heartbeat_timeout_seconds=30,
        maintenance_interval_seconds=60,
        create_schema_on_startup=True,
    )


def _setup(tmp_path: Path):
    app = create_app(_settings(tmp_path / "central.db"))
    users: UserService = app.state.user_service
    manager = users.create_user("lab-pc-01", "device_manager")
    user = users.create_user("researcher", "user")
    other_manager = users.create_user("lab-pc-02", "device_manager")
    return app, manager["api_key"], user["api_key"], other_manager["api_key"]


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _registration(manager_id: str = "lab-pc-01") -> dict:
    return {
        "manager_id": manager_id,
        "host": manager_id,
        "endpoint": {
            "transport": "grpc",
            "target": "127.0.0.1:50051",
            "api_version": "labnet.devices.v1",
            "secure": False,
            "attributes": {"site": "test"},
        },
        "devices": [
            {
                "device_id": "wavemeter-1",
                "device_type": "dummy_device",
                "runtime_state": "ready",
                "availability": "available",
                "status_text": "locked",
                "metadata": {"unit": "Hz"},
            }
        ],
    }


def _heartbeat(timestamp: str) -> dict:
    registration = _registration()
    device = dict(registration["devices"][0])
    device.pop("device_type")
    return {
        "manager_id": registration["manager_id"],
        "host": registration["host"],
        "endpoint": registration["endpoint"],
        "timestamp": timestamp,
        "devices": [device],
    }


def test_full_registration_lease_validation_release_flow(tmp_path: Path) -> None:
    app, manager_key, user_key, _ = _setup(tmp_path)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        me = client.get("/api/v1/auth/me", headers=_auth(user_key))
        assert me.json() == {"user": "researcher", "role": "user"}

        registered = client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration(),
        )
        assert registered.status_code == 200, registered.text
        device = registered.json()["devices"][0]
        assert device["status_text"] == "locked"
        assert device["allocation_state"] == "free"
        assert device["effective_available"] is True
        assert datetime.fromisoformat(device["last_seen"]).tzinfo is not None
        filtered = client.get(
            "/api/v1/devices",
            headers=_auth(user_key),
            params={"runtime_state": "ready", "effective_available": "true"},
        )
        assert [item["device_id"] for item in filtered.json()["devices"]] == [
            "wavemeter-1"
        ]

        grant = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "experiment-1"},
            json={"device_id": "wavemeter-1", "duration_seconds": 60},
        )
        assert grant.status_code == 201, grant.text
        body = grant.json()
        lease_id = body["lease"]["lease_id"]
        assert body["lease"]["state"] == "active"
        assert body["device"]["device_type"] == "dummy_device"
        assert body["device"]["endpoint"]["transport"] == "grpc"
        assert datetime.fromisoformat(body["lease"]["expires_at"]).tzinfo is not None

        replay = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "experiment-1"},
            json={"device_id": "wavemeter-1", "duration_seconds": 60},
        )
        assert replay.status_code == 201
        assert replay.json()["lease"]["lease_id"] == lease_id

        validation = client.post(
            f"/api/v1/leases/{lease_id}/validate",
            headers=_auth(manager_key),
            json={"device_id": "wavemeter-1"},
        )
        assert validation.status_code == 200
        assert validation.json()["valid"] is True
        assert datetime.fromisoformat(validation.json()["expires_at"]).tzinfo is not None

        released = client.delete(
            f"/api/v1/leases/{lease_id}", headers=_auth(user_key)
        )
        assert released.json()["released"] is True
        repeated = client.delete(
            f"/api/v1/leases/{lease_id}", headers=_auth(user_key)
        )
        assert repeated.status_code == 200
        assert repeated.json() == {
            "lease_id": lease_id,
            "state": "released",
            "released": False,
        }

        terminal_replay = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "experiment-1"},
            json={"device_id": "wavemeter-1", "duration_seconds": 60},
        )
        assert terminal_replay.status_code == 409
        assert terminal_replay.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        missing_route = client.get("/not-a-route")
        assert missing_route.json()["error"]["code"] == "NOT_FOUND"


def test_manager_identity_and_validation_do_not_disclose_lease(tmp_path: Path) -> None:
    app, manager_key, user_key, other_manager_key = _setup(tmp_path)
    admin_key = app.state.user_service.create_user("central-admin", "admin")["api_key"]
    with TestClient(app) as client:
        forbidden_registration = client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration("someone-else"),
        )
        assert forbidden_registration.status_code == 403

        assert client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration(),
        ).status_code == 200
        grant = client.post(
            "/api/v1/leases",
            headers={**_auth(user_key), "Idempotency-Key": "lease-for-oracle-test"},
            json={"device_id": "wavemeter-1", "duration_seconds": 60},
        ).json()
        lease_id = grant["lease"]["lease_id"]

        wrong_manager = client.post(
            f"/api/v1/leases/{lease_id}/validate",
            headers=_auth(other_manager_key),
            json={"device_id": "wavemeter-1"},
        )
        missing = client.post(
            "/api/v1/leases/not-a-real-lease/validate",
            headers=_auth(other_manager_key),
            json={"device_id": "wavemeter-1"},
        )
        assert wrong_manager.status_code == missing.status_code == 403
        assert wrong_manager.json() == missing.json()
        assert wrong_manager.json()["error"]["code"] == "LEASE_INVALID"
        admin_validation = client.post(
            f"/api/v1/leases/{lease_id}/validate",
            headers=_auth(admin_key),
            json={"device_id": "wavemeter-1"},
        )
        assert admin_validation.status_code == 403
        assert admin_validation.json()["error"]["code"] == "PERMISSION_DENIED"


def test_roles_are_separated_between_clients_and_device_managers(
    tmp_path: Path,
) -> None:
    app, manager_key, user_key, _ = _setup(tmp_path)
    admin_key = app.state.user_service.create_user("central-admin", "admin")["api_key"]
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/device-managers/register",
            headers=_auth(user_key),
            json=_registration(),
        ).status_code == 403
        assert client.post(
            "/api/v1/device-managers/register",
            headers=_auth(admin_key),
            json=_registration(),
        ).status_code == 403
        assert client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration(),
        ).status_code == 200

        manager_lease = client.post(
            "/api/v1/leases",
            headers={**_auth(manager_key), "Idempotency-Key": "manager-denied"},
            json={"device_id": "wavemeter-1", "duration_seconds": 60},
        )
        assert manager_lease.status_code == 403
        assert manager_lease.json()["error"]["code"] == "PERMISSION_DENIED"

        admin_grant = client.post(
            "/api/v1/leases",
            headers={**_auth(admin_key), "Idempotency-Key": "admin-allowed"},
            json={"device_id": "wavemeter-1", "duration_seconds": 60},
        )
        assert admin_grant.status_code == 201
        lease_id = admin_grant.json()["lease"]["lease_id"]
        assert client.post(
            f"/api/v1/leases/{lease_id}/renew",
            headers=_auth(manager_key),
            json={"duration_seconds": 60},
        ).status_code == 403
        assert client.delete(
            f"/api/v1/leases/{lease_id}", headers=_auth(manager_key)
        ).status_code == 403
        assert client.delete(
            f"/api/v1/leases/{lease_id}", headers=_auth(admin_key)
        ).status_code == 200


@pytest.mark.parametrize(
    ("mutation", "path"),
    [
        (("manager_id", "bad manager"), "/api/v1/device-managers/register"),
        (("device_id", "bad/device"), "/api/v1/device-managers/register"),
    ],
)
def test_registration_rejects_ids_outside_grpc_identifier_contract(
    tmp_path: Path, mutation: tuple[str, str], path: str
) -> None:
    app, manager_key, _, _ = _setup(tmp_path)
    payload = _registration()
    field, value = mutation
    if field == "manager_id":
        payload[field] = value
    else:
        payload["devices"][0][field] = value
    with TestClient(app) as client:
        response = client.post(path, headers=_auth(manager_key), json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_wire_identifier_is_rejected_on_discovery_path(tmp_path: Path) -> None:
    app, _, user_key, _ = _setup(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/v1/devices/bad!device", headers=_auth(user_key))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_heartbeat_requires_an_aware_reported_timestamp(tmp_path: Path) -> None:
    app, manager_key, _, _ = _setup(tmp_path)
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/device-managers/register",
            headers=_auth(manager_key),
            json=_registration(),
        ).status_code == 200
        naive = client.post(
            "/api/v1/device-managers/heartbeat",
            headers=_auth(manager_key),
            json=_heartbeat("2026-08-24T12:00:00"),
        )
        assert naive.status_code == 422
        assert naive.json()["error"]["code"] == "VALIDATION_ERROR"
        aware = client.post(
            "/api/v1/device-managers/heartbeat",
            headers=_auth(manager_key),
            json=_heartbeat("2026-08-24T12:00:00Z"),
        )
        assert aware.status_code == 200


def test_twenty_simultaneous_requests_create_one_active_lease(tmp_path: Path) -> None:
    app, manager_key, user_key, _ = _setup(tmp_path)
    registry = app.state.registry_service
    users = app.state.user_service
    manager_actor = users.authenticate(manager_key)
    user_actor = users.authenticate(user_key)
    from central_server.app.models.devices import DeviceManagerRegistrationRequest

    registry.register(
        DeviceManagerRegistrationRequest.model_validate(_registration()), manager_actor
    )
    service = app.state.lease_service
    barrier = Barrier(20)

    def acquire(index: int):
        barrier.wait(timeout=5)
        try:
            return service.request_lease(
                actor=user_actor,
                device_id="wavemeter-1",
                duration_seconds=60,
                idempotency_key=f"concurrent-{index}-{uuid4().hex}",
            )
        except ServiceError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(acquire, range(20)))

    successes = [result for result in results if not isinstance(result, ServiceError)]
    failures = [result for result in results if isinstance(result, ServiceError)]
    assert len(successes) == 1
    assert len(failures) == 19
    assert {failure.code for failure in failures} == {ErrorCode.DEVICE_BUSY}
    assert len(service.list_active()) == 1


def test_simultaneous_replay_of_one_idempotency_key_returns_one_lease(
    tmp_path: Path,
) -> None:
    app, manager_key, user_key, _ = _setup(tmp_path)
    users = app.state.user_service
    from central_server.app.models.devices import DeviceManagerRegistrationRequest

    app.state.registry_service.register(
        DeviceManagerRegistrationRequest.model_validate(_registration()),
        users.authenticate(manager_key),
    )
    actor = users.authenticate(user_key)
    barrier = Barrier(12)

    def replay(_index: int):
        barrier.wait(timeout=5)
        return app.state.lease_service.request_lease(
            actor=actor,
            device_id="wavemeter-1",
            duration_seconds=60,
            idempotency_key="same-concurrent-request",
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        grants = list(pool.map(replay, range(12)))

    assert len({grant.lease.lease_id for grant in grants}) == 1
    assert len(app.state.lease_service.list_active()) == 1


def test_postgresql_device_lock_targets_only_the_device_table() -> None:
    sql = str(
        _locked_device_statement("wavemeter-1").compile(
            dialect=postgresql.dialect()
        )
    )
    normalized = " ".join(sql.split()).upper()
    assert "FOR UPDATE OF DEVICES" in normalized
    assert "FOR UPDATE OF DEVICE_MANAGERS" not in normalized


def test_postgresql_maintenance_locks_only_expiry_candidates() -> None:
    sql = str(
        _locked_expiring_leases_statement(["lease-1"]).compile(
            dialect=postgresql.dialect()
        )
    )
    normalized = " ".join(sql.split()).upper()
    assert "LEASES.LEASE_ID IN" in normalized
    assert "FOR UPDATE OF LEASES" in normalized
    assert "DEVICE_MANAGERS" not in normalized
    assert "DEVICES" not in normalized


def test_postgresql_offline_sweep_locks_devices_then_active_leases() -> None:
    device_sql = str(
        _locked_manager_devices_statement("lab-pc-01").compile(
            dialect=postgresql.dialect()
        )
    )
    lease_sql = str(
        _locked_active_device_leases_statement(["wavemeter-1"]).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "FOR UPDATE OF DEVICES" in " ".join(device_sql.split()).upper()
    assert "FOR UPDATE OF LEASES" in " ".join(lease_sql.split()).upper()


def test_alembic_upgrade_creates_the_expected_revision(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    command.upgrade(_alembic_config(url), "head")
    database = Database(url)
    try:
        assert database.ready() is True
    finally:
        database.dispose()


def test_heartbeat_timeout_revokes_lease_and_marks_device_unavailable(
    tmp_path: Path,
) -> None:
    app, manager_key, user_key, _ = _setup(tmp_path)
    registry = app.state.registry_service
    users = app.state.user_service
    manager_actor = users.authenticate(manager_key)
    user_actor = users.authenticate(user_key)
    from central_server.app.models.devices import DeviceManagerRegistrationRequest

    registry.register(
        DeviceManagerRegistrationRequest.model_validate(_registration()), manager_actor
    )
    grant = app.state.lease_service.request_lease(
        actor=user_actor,
        device_id="wavemeter-1",
        duration_seconds=60,
        idempotency_key="timeout-test",
    )
    with app.state.database.transaction() as session:
        manager = session.get(DeviceManagerRow, "lab-pc-01")
        assert manager is not None
        manager.last_seen = datetime.now(timezone.utc) - timedelta(seconds=31)

    result = app.state.lease_service.maintain(heartbeat_timeout_seconds=30)
    assert result == {"expired": 0, "revoked": 1, "offline": 1}
    assert registry.get_device("wavemeter-1").effective_available is False
    with pytest.raises(ServiceError) as raised:
        app.state.lease_service.validate(
            actor=manager_actor,
            lease_id=grant.lease.lease_id,
            device_id="wavemeter-1",
        )
    assert raised.value.code is ErrorCode.LEASE_INVALID


def test_unmigrated_database_is_not_ready(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    try:
        assert database.ready() is False
    finally:
        database.dispose()


def test_test_schema_helper_refuses_to_stamp_partial_database(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'partial.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (broken INTEGER)"))
    engine.dispose()

    with pytest.raises(RuntimeError, match="partial database"):
        Database(url, create_schema=True)

    check_engine = create_engine(url)
    try:
        assert "alembic_version" not in inspect(check_engine).get_table_names()
    finally:
        check_engine.dispose()


def test_readiness_requires_audit_and_idempotency_tables(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'incomplete.db').as_posix()}"
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE audit_events"))
        connection.execute(text("DROP TABLE lease_requests"))
    engine.dispose()
    database = Database(url)
    try:
        assert database.ready() is False
    finally:
        database.dispose()


def test_builtin_server_configuration_is_loopback_only(monkeypatch) -> None:
    monkeypatch.delenv("LABNET_CREATE_SCHEMA_ON_STARTUP", raising=False)
    assert Settings().create_schema_on_startup is False
    assert Settings.from_env().create_schema_on_startup is False
    assert Settings(bind_host="localhost").validated().bind_host == "localhost"
    assert Settings(bind_host="::1").validated().bind_host == "::1"
    with pytest.raises(ValueError, match="loopback"):
        Settings(bind_host="0.0.0.0").validated()
    with pytest.raises(ValueError, match="HTTPS reverse proxy"):
        Settings(bind_host="central.example.edu").validated()


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), -1.0, 0.0])
def test_maintenance_interval_must_be_finite_and_positive(interval: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        Settings(maintenance_interval_seconds=interval).validated()


def test_default_database_is_user_writable_and_sqlite_creates_parent(
    tmp_path: Path,
) -> None:
    normalized_default = Settings().database_url.replace("\\", "/").lower()
    assert "/central_server/data/" not in normalized_default

    database_path = tmp_path / "new" / "nested" / "central.db"
    database = Database(
        f"sqlite:///{database_path.as_posix()}", create_schema=True
    )
    try:
        assert database_path.is_file()
        assert database.ready() is True
    finally:
        database.dispose()


def test_constructing_database_does_not_create_sqlite_parent(tmp_path: Path) -> None:
    database_path = tmp_path / "not-created" / "central.db"
    database = Database(f"sqlite:///{database_path.as_posix()}")
    try:
        assert database_path.parent.exists() is False
        assert database_path.exists() is False
    finally:
        database.dispose()


def test_device_manager_username_must_be_a_wire_identifier(tmp_path: Path) -> None:
    database = Database(
        f"sqlite:///{(tmp_path / 'users.db').as_posix()}", create_schema=True
    )
    try:
        with pytest.raises(ValueError, match="manager_id"):
            UserService(database).create_user("bad manager", "device_manager")
    finally:
        database.dispose()
