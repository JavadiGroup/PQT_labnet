from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import labnet
from labnet.client import LabPCInfo, ServerConnectionError
from labnet.client.core.models import DeviceInfo, EndpointDescriptor
from labnet_control_center.app import create_app
from labnet_control_center.config import ControlCenterSettings
from labnet_control_center.contracts import describe_device_type, facade_class_name, type_label
from labnet.contracts import registry


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("LABNET_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def _settings(**overrides) -> ControlCenterSettings:
    values = dict(
        server_url="http://127.0.0.1:8008",
        api_key="key",
        ca_file=None,
        bind_host="127.0.0.1",
        port=8080,
        refresh_seconds=60.0,
    )
    values.update(overrides)
    return ControlCenterSettings(**values)


ENDPOINT = EndpointDescriptor(
    transport="grpc", target="192.168.50.20:50051", api_version="labnet.devices.v1", secure=True
)
NOW = datetime.now(timezone.utc)


def _device(device_id: str, *, available: bool = True, leased: bool = False, device_type="dummy_device"):
    return DeviceInfo(
        device_id=device_id,
        manager_id="lab-pc-01",
        endpoint=ENDPOINT,
        device_type=device_type,
        runtime_state="ready",
        availability="available",
        status_text="idle",
        last_seen=NOW,
        effective_available=available and not leased,
        allocation_state="leased" if leased else "free",
    )


NETWORK = [
    LabPCInfo(
        manager_id="lab-pc-01",
        host="192.168.50.20",
        endpoint=ENDPOINT,
        online=True,
        last_seen=NOW,
        devices=(
            _device("free-dummy"),
            _device("busy-dummy", leased=True),
            _device("meter", device_type="simulated_wavemeter"),
            _device("mystery", device_type="not_in_this_build"),
        ),
    ),
    LabPCInfo(
        manager_id="lab-pc-02",
        host="192.168.50.21",
        endpoint=ENDPOINT,
        online=False,
        last_seen=NOW,
        devices=(_device("stranded", available=False),),
    ),
]


class FakeCentral:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.closed = False

    async def list_lab_pcs(self):
        self.calls += 1
        if self.fail:
            raise ServerConnectionError("Central is down")
        return NETWORK

    async def aclose(self) -> None:
        self.closed = True


def _app(central: FakeCentral, **settings) -> TestClient:
    return TestClient(create_app(_settings(**settings), client_factory=lambda _: central))


# ---- settings -----------------------------------------------------------------


def test_settings_come_from_one_env_file(tmp_path: Path) -> None:
    (tmp_path / "ca.pem").write_text("ca", encoding="utf-8")
    env_file = tmp_path / "control-center.env"
    env_file.write_text(
        "LABNET_SERVER_URL=https://192.168.50.10\nLABNET_API_KEY=secret\n"
        "LABNET_CA_FILE=ca.pem\nLABNET_CONTROL_CENTER_PORT=8090\n",
        encoding="utf-8",
    )

    settings = ControlCenterSettings.from_env(env_file)

    assert settings.server_url == "https://192.168.50.10"
    assert settings.ca_file == (tmp_path / "ca.pem").resolve()
    assert settings.port == 8090
    assert settings.bind_host == "127.0.0.1"
    # Photo-Agent: workspace next to the env file, reads the installed package.
    assert settings.agent_workspace == tmp_path / "agent"
    assert settings.agent_sources == (Path(labnet.__file__).resolve().parent,)
    assert settings.agent_literature_dir is None and settings.agent_model == "sonnet"


def test_agent_folders_resolve_against_the_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for folder in ("code", "examples", "papers"):
        (tmp_path / folder).mkdir()
    env_file = tmp_path / "control-center.env"
    env_file.write_text(
        f"LABNET_API_KEY=secret\nLABNET_AGENT_SOURCES=code{os.pathsep}examples\n"
        "LABNET_AGENT_LITERATURE_DIR=papers\nLABNET_AGENT_MODEL=opus\n",
        encoding="utf-8",
    )

    settings = ControlCenterSettings.from_env(env_file)

    assert settings.agent_sources == ((tmp_path / "code").resolve(), (tmp_path / "examples").resolve())
    assert settings.agent_literature_dir == (tmp_path / "papers").resolve()
    assert settings.agent_model == "opus"

    # The process environment wins over the file, so drop what the first load set.
    monkeypatch.delenv("LABNET_AGENT_LITERATURE_DIR")
    env_file.write_text("LABNET_API_KEY=secret\nLABNET_AGENT_LITERATURE_DIR=missing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="LABNET_AGENT_LITERATURE_DIR is not a folder"):
        ControlCenterSettings.from_env(env_file)


def test_settings_require_a_key_and_a_loopback_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="LABNET_API_KEY is not set"):
        ControlCenterSettings.from_env()

    monkeypatch.setenv("LABNET_API_KEY", "secret")
    monkeypatch.setenv("LABNET_CONTROL_CENTER_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="must be loopback"):
        ControlCenterSettings.from_env()


# ---- method descriptions --------------------------------------------------------


def test_every_generated_contract_can_be_described() -> None:
    for contract in registry.all():
        methods = describe_device_type(contract.device_type)
        assert [method["name"] for method in methods] == [
            spec.python_name for spec in contract.methods
        ]


def test_every_family_has_a_client_class() -> None:
    """Generated scripts import <Service minus "Service"> from labnet."""

    for contract in registry.all():
        name = facade_class_name(contract)
        assert name is not None, contract.service_name
        assert getattr(labnet, name).contract is contract


def test_methods_show_the_python_client_signature() -> None:
    methods = {method["name"]: method for method in describe_device_type("dummy_device")}

    assert methods["read_value"]["parameters"] == ["channel: str | None = None"]
    assert methods["read_value"]["returns"] == "float"  # what the sync facades return
    assert methods["read_value"]["role"] == "record"
    assert methods["write_value"]["parameters"] == [
        "value: float",
        "channel: str | None = None",
        "unit: str | None = None",
    ]
    assert methods["write_value"]["fields"] == [
        {"name": "value", "type": "float", "optional": False},
        {"name": "channel", "type": "str | None", "optional": True},
    ]
    assert methods["write_value"]["role"] == "setting" and methods["write_value"]["sweep_field"] == "value"
    assert methods["stream_data"]["kind"] == "stream"
    assert methods["stream_data"]["parameters"][0] == "sample_rate_hz: float"
    assert describe_device_type("not_in_this_build") is None
    assert type_label("simulated_wavemeter") == "Simulated Wavemeter"
    assert type_label("simulated_tunable_laser") == "Simulated Tunable Laser"
    assert type_label("not_in_this_build") == "Not In This Build"


def test_a_command_with_one_number_can_be_swept() -> None:
    laser = {method["name"]: method for method in describe_device_type("simulated_tunable_laser")}
    assert laser["go_to_wavelength_nm"]["role"] == "command"
    assert laser["go_to_wavelength_nm"]["sweep_field"] == "wavelength_nm"
    assert laser["stop"]["sweep_field"] is None


# ---- pages and API ----------------------------------------------------------------


def test_pages_are_served_and_the_root_opens_status() -> None:
    with _app(FakeCentral()) as client:
        home = client.get("/", follow_redirects=False)
        assert home.status_code == 307 and home.headers["location"] == "/status"
        assert 'data-page="network"' in client.get("/status").text
        devices = client.get("/devices").text
        assert 'data-page="devices"' in devices and 'id="devices"' in devices
        for asset in (
            "/static/css/app.css",
            "/static/js/shell.js",
            "/static/js/live.js",
            "/static/js/status.js",
            "/static/js/devices.js",
            "/static/js/device.js",
        ):
            assert client.get(asset).status_code == 200


def test_each_device_has_a_control_page_at_its_id() -> None:
    with _app(FakeCentral()) as client:
        page = client.get("/free-dummy")
        assert page.status_code == 200
        assert "/static/js/device.js" in page.text
        # Device ids may contain '.', ':' and '-'.
        assert client.get("/rack-2.bench:a").status_code == 200
        # The app's own paths are never mistaken for a device.
        assert client.get("/favicon.ico").status_code == 204
        for reserved in ("/api", "/health", "/static"):
            assert client.get(reserved).status_code == 404
        # Not a LabNet identifier (and the old /devices/<id> address is gone).
        assert client.get("/-bad").status_code == 404
        assert client.get("/devices/free-dummy").status_code == 404


def test_status_reports_lab_pcs_and_device_states() -> None:
    with _app(FakeCentral()) as client:
        snapshot = client.get("/api/status").json()

    assert snapshot["central"]["ok"] is True
    assert snapshot["summary"] == {
        "lab_pcs": 2, "online": 1, "devices": 5, "available": 3, "leased": 1,
    }
    first, second = snapshot["lab_pcs"]
    assert (first["address"], first["port"], first["secure"]) == ("192.168.50.20", "50051", True)
    states = {device["id"]: device["state"] for device in first["devices"]}
    assert states == {
        "free-dummy": "available",
        "busy-dummy": "leased",
        "meter": "available",
        "mystery": "available",
    }
    mystery = first["devices"][3]
    assert mystery["known_contract"] is False and mystery["methods"] == []
    assert first["devices"][0]["control_url"] == "/free-dummy"
    assert second["online"] is False
    assert second["devices"][0] == {
        **second["devices"][0], "state": "unavailable", "detail": "Lab PC is offline",
    }


def test_central_is_asked_once_per_refresh_interval() -> None:
    central = FakeCentral()
    with _app(central, refresh_seconds=60.0) as client:
        for _ in range(5):
            client.get("/api/status")
    assert central.calls == 1
    assert central.closed is True


def test_central_outage_keeps_the_last_known_network() -> None:
    central = FakeCentral()
    with _app(central, refresh_seconds=0.5) as client:
        client.get("/api/status")
        central.fail = True
        client.app.state.status._fetched_at = 0.0  # force a refresh
        snapshot = client.get("/api/status").json()
        ready = client.get("/health/ready")

    assert snapshot["central"]["ok"] is False
    assert "Central is down" in snapshot["central"]["error"]
    assert snapshot["summary"]["lab_pcs"] == 2
    assert ready.status_code == 503


def test_agent_chat_explains_when_claude_code_is_missing() -> None:
    with _app(FakeCentral()) as client:
        info = client.get("/api/agent").json()
        reply = client.post("/api/agent/messages", json={"message": "Plan a scan"})
        empty = client.post("/api/agent/messages", json={"message": ""})

    assert info["enabled"] is False and "not found" in info["reason"]
    assert reply.status_code == 200
    assert reply.json()["connected"] is False
    assert "Claude Code (claude) was not found" in reply.json()["reply"]
    assert empty.status_code == 422


def test_every_static_file_is_packaged() -> None:
    """Each file the Control Center serves must match a package-data glob."""

    import fnmatch
    import tomllib

    package = Path(__file__).resolve().parents[2] / "src" / "labnet_control_center"
    globs = tomllib.loads((package.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = globs["tool"]["setuptools"]["package-data"]["labnet_control_center"]
    for path in (package / "static").rglob("*"):
        if path.is_file():
            relative = path.relative_to(package).as_posix()
            assert any(fnmatch.fnmatch(relative, pattern) for pattern in patterns), relative
    assert "agent_briefing.md" in patterns
