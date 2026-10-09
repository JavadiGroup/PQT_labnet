"""Human ingress: the proxy secret and Caddy's user name, cross-site changes, host names, body limits."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labnet_control_center.app import create_app
from labnet_control_center.config import ControlCenterSettings

from .test_automation import FakeCentral, _settings


SECRET = "s3cret-" + "x" * 41  # 48 characters, like setup_logins.ps1 makes
SIGNED_IN = {"X-LabNet-Proxy": SECRET, "X-LabNet-User": "alice"}
NOT_THROUGH_CADDY = "Open the Control Center through its sign-in address (Caddy)."
NO_USER = "The sign-in proxy sent no usable user name."
CROSS_SITE = "Changes must come from the Control Center's own pages."


def _client(settings: ControlCenterSettings, **kwargs) -> TestClient:
    return TestClient(create_app(settings, client_factory=lambda _: FakeCentral()), **kwargs)


@pytest.fixture
def lab(tmp_path: Path):
    """A Control Center with logins: only requests from Caddy get in."""

    with _client(_settings(tmp_path, proxy_secret=SECRET)) as client:
        yield client


@pytest.fixture
def dev(tmp_path: Path):
    """A Control Center without logins (development on one computer)."""

    with _client(_settings(tmp_path)) as client:
        yield client


# ---- the proxy secret ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({}, NOT_THROUGH_CADDY),
        ({"X-LabNet-User": "alice"}, NOT_THROUGH_CADDY),
        ({"X-LabNet-Proxy": SECRET[:-1] + "y", "X-LabNet-User": "alice"}, NOT_THROUGH_CADDY),
        ({"X-LabNet-Proxy": SECRET[:20], "X-LabNet-User": "alice"}, NOT_THROUGH_CADDY),
        ({"X-LabNet-Proxy": SECRET}, NO_USER),
        ({"X-LabNet-Proxy": SECRET, "X-LabNet-User": "   "}, NO_USER),
        ({"X-LabNet-Proxy": SECRET, "X-LabNet-User": ("a" + chr(0x202E) + "b").encode("utf-8")}, NO_USER),
    ],
)
def test_requests_that_did_not_come_through_caddy_are_refused(lab: TestClient, headers: dict, message: str) -> None:
    for path in ("/api/me", "/api/status", "/experiments", "/api/automation/projects"):
        reply = lab.get(path, headers=headers)
        assert reply.status_code == 403, path
        assert reply.json() == {"detail": message}
        assert reply.headers["cache-control"] == "no-store"
    assert lab.post("/api/experiments", json={"name": "x"}, headers=headers).status_code == 403


def test_signed_in_requests_get_through_and_know_who_is_asking(lab: TestClient) -> None:
    reply = lab.get("/api/me", headers=SIGNED_IN)
    assert reply.status_code == 200
    assert reply.json() == {"user": "alice", "logins": True}
    assert lab.get("/experiments", headers=SIGNED_IN).status_code == 200
    assert lab.get("/api/status", headers=SIGNED_IN).status_code == 200


def test_without_logins_nobody_is_signed_in_and_the_header_is_ignored(dev: TestClient) -> None:
    assert dev.get("/api/me").json() == {"user": None, "logins": False}
    # Anyone could send the header, so without a secret it means nothing.
    assert dev.get("/api/me", headers=SIGNED_IN).json() == {"user": None, "logins": False}


def test_health_checks_need_no_login(lab: TestClient) -> None:
    assert lab.get("/health/live").status_code == 200
    assert lab.get("/health/ready").status_code != 403


@pytest.mark.parametrize("path", ["/health/..%2Fapi%2Fstatus", "/api/automation/runner/..%2F..%2Fautomation%2Fprojects"])
def test_exempt_prefixes_lead_nowhere_else(lab: TestClient, path: str) -> None:
    reply = lab.get(path)
    assert reply.status_code == 404
    assert "experiments" not in reply.text and "projects" not in reply.text


@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
def test_the_runner_api_is_not_behind_the_human_login(lab: TestClient, method: str) -> None:
    # Runners prove themselves with a bearer token instead (automation_runner_api.py);
    # whatever that router answers, it isn't the ingress refusal.
    reply = lab.request(
        method,
        "/api/automation/runner/claim",
        headers={"Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"},
        content=b"{}" if method != "GET" else None,
    )
    try:
        detail = reply.json().get("detail")
    except ValueError:
        detail = None
    assert detail not in (NOT_THROUGH_CADDY, NO_USER, CROSS_SITE)


# ---- cross-site changes -------------------------------------------------------------


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_changes_from_other_sites_are_refused(dev: TestClient, site: str) -> None:
    headers = {"Sec-Fetch-Site": site}
    reply = dev.post("/api/devices/dev01-aom/release", headers=headers)
    assert reply.status_code == 403 and reply.json() == {"detail": CROSS_SITE}
    reply = dev.post("/api/experiments", json={"name": "Forged"}, headers=headers)
    assert reply.status_code == 403 and reply.json() == {"detail": CROSS_SITE}
    assert dev.get("/api/experiments").json()["experiments"] == []
    # Reading is fine: a page elsewhere can't see the answer anyway.
    assert dev.get("/api/status", headers=headers).status_code == 200


def test_cross_site_changes_are_refused_even_when_signed_in(lab: TestClient) -> None:
    reply = lab.post("/api/experiments", json={"name": "Forged"}, headers={**SIGNED_IN, "Sec-Fetch-Site": "cross-site"})
    assert reply.status_code == 403 and reply.json() == {"detail": CROSS_SITE}


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}])
def test_changes_from_our_own_pages_and_programs_are_allowed(dev: TestClient, headers: dict) -> None:
    assert dev.post("/api/devices/dev01-aom/release", headers=headers).status_code != 403
    reply = dev.post("/api/experiments", json={"name": "AOM scan"}, headers=headers)
    assert reply.status_code == 201, reply.text


# ---- who is recorded as the author ------------------------------------------------------


def test_the_signed_in_user_is_the_author_not_the_typed_name(lab: TestClient) -> None:
    reply = lab.post("/api/experiments", json={"name": "AOM scan", "created_by": "Mallory"}, headers=SIGNED_IN)
    assert reply.status_code == 201, reply.text
    experiment = reply.json()["experiment"]
    assert experiment["created_by"] == "alice"

    reply = lab.post(
        "/api/automation/projects", json={"name": "AOM study", "created_by": "Mallory"}, headers=SIGNED_IN
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["project"]["created_by"] == "alice"


def test_without_logins_the_typed_name_is_the_author(dev: TestClient) -> None:
    reply = dev.post("/api/experiments", json={"name": "AOM scan", "created_by": "Rob"})
    assert reply.json()["experiment"]["created_by"] == "Rob"
    reply = dev.post("/api/automation/projects", json={"name": "AOM study", "created_by": "Rob"})
    assert reply.json()["project"]["created_by"] == "Rob"


# ---- body limits ----------------------------------------------------------------------


def test_an_oversized_experiment_without_a_length_is_refused(dev: TestClient) -> None:
    def chunks():
        for _ in range(64):
            yield b" " * (16 * 1024)  # 1 MB in all, more than an experiment may be

    reply = dev.post("/api/experiments", content=chunks(), headers={"Content-Type": "application/json"})
    assert reply.status_code == 413
    assert dev.get("/api/experiments").json()["experiments"] == []


def test_the_body_limit_counts_chunked_bodies_itself(tmp_path: Path) -> None:
    # Automation's limit here (10 KB files + 64 KB) is below what read_json allows,
    # so this 192 KB body can only be stopped by _BodyLimit counting the chunks.
    with _client(_settings(tmp_path, automation_max_file_mb=0.01)) as client:
        def chunks():
            yield b'{"name": "AOM study", "description": "'
            for _ in range(12):
                yield b"x" * (16 * 1024)
            yield b'"}'

        reply = client.post("/api/automation/projects", content=chunks(), headers={"Content-Type": "application/json"})
        assert reply.status_code == 413
        assert reply.json() == {"detail": "Files can be at most 0.01 MB."}


# ---- host names (DNS rebinding) and settings ----------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The process environment from_env reads, with the folders in tmp_path."""

    monkeypatch.setenv("LABNET_API_KEY", "key")
    monkeypatch.setenv("LABNET_CONTROL_CENTER_EXPERIMENTS_DIR", str(tmp_path / "experiments"))
    monkeypatch.setenv("LABNET_AUTOMATION_DIR", str(tmp_path / "automation"))
    monkeypatch.setenv("LABNET_AGENT_WORKSPACE", str(tmp_path / "agent"))
    for name in ("LABNET_CONTROL_CENTER_PROXY_SECRET", "LABNET_CONTROL_CENTER_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _from_env() -> ControlCenterSettings:
    return ControlCenterSettings.from_env(search_cwd=False)


def test_without_logins_only_loopback_host_names_are_answered(env: pytest.MonkeyPatch) -> None:
    settings = _from_env()
    assert settings.allowed_hosts == ("127.0.0.1", "localhost")
    with _client(settings, base_url="http://127.0.0.1:8080") as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/api/me", headers={"Host": "localhost:8080"}).status_code == 200
        # A web page that points its own name at 127.0.0.1 gets nowhere.
        assert client.get("/api/me", headers={"Host": "evil.example"}).status_code == 400
        assert client.post("/api/experiments", json={"name": "x"}, headers={"Host": "evil.example"}).status_code == 400


def test_with_logins_any_host_name_caddy_forwards_is_answered(env: pytest.MonkeyPatch) -> None:
    env.setenv("LABNET_CONTROL_CENTER_PROXY_SECRET", SECRET)
    settings = _from_env()
    assert settings.allowed_hosts == ("*",)
    with _client(settings) as client:
        reply = client.get("/api/me", headers={**SIGNED_IN, "Host": "control-center.example"})
        assert reply.status_code == 200 and reply.json()["user"] == "alice"


def test_allowed_hosts_can_be_set(env: pytest.MonkeyPatch) -> None:
    env.setenv("LABNET_CONTROL_CENTER_ALLOWED_HOSTS", "control-center.example, 192.0.2.10")
    settings = _from_env()
    assert settings.allowed_hosts == ("control-center.example", "192.0.2.10")
    with _client(settings) as client:
        assert client.get("/api/me", headers={"Host": "control-center.example"}).status_code == 200
        assert client.get("/api/me", headers={"Host": "127.0.0.1"}).status_code == 400


@pytest.mark.parametrize(
    "secret",
    ["short", "x" * 31, "x" * 31 + " ", "x" * 20 + " " + "x" * 20, "x" * 31 + chr(0xE9), "x" * 31 + "\t"],
)
def test_a_weak_or_odd_proxy_secret_is_refused(env: pytest.MonkeyPatch, secret: str) -> None:
    env.setenv("LABNET_CONTROL_CENTER_PROXY_SECRET", secret)
    if secret.strip() != secret:
        # from_env strips the line; check the setting itself too.
        with pytest.raises(ValueError, match="PROXY_SECRET"):
            _settings(Path("."), proxy_secret=secret).validate()
        return
    with pytest.raises(ValueError, match="PROXY_SECRET"):
        _from_env()


@pytest.mark.parametrize("hosts", ["control-center.example:443", "https://control-center.example", "a b", ",", "host/path"])
def test_allowed_hosts_must_be_plain_host_names(env: pytest.MonkeyPatch, hosts: str) -> None:
    env.setenv("LABNET_CONTROL_CENTER_ALLOWED_HOSTS", hosts)
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        _from_env()
