"""Experiments: the document, its checks, the store and the API."""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labnet.client import LabPCInfo
from labnet.client.core.models import DeviceInfo, EndpointDescriptor
from labnet.control_center import experiment_store
from labnet.control_center.app import create_app
from labnet.control_center.config import ControlCenterSettings
from labnet.control_center.experiment_checks import check_experiment
from labnet.control_center.experiment_schema import parse_content
from labnet.control_center.experiment_store import ExperimentError, ExperimentStore, VersionConflict
from labnet.devices.DummyAOM.physical import PhysicalDummyAOM
from labnet.devices.DummyAWG.physical import PhysicalDummyAWG


AOM_HINTS = PhysicalDummyAOM("x").metadata()["hints"]
AWG_HINTS = PhysicalDummyAWG("x").metadata()["hints"]


def scan(**changes) -> dict:
    """A small, valid AOM frequency scan."""

    doc = {
        "name": "AOM frequency scan",
        "description": "Efficiency against RF frequency.",
        "run": {"settle_seconds": 0.0, "samples": 3, "sample_rate_hz": 20.0, "output_name": "aom_scan"},
        "nodes": [
            {"id": "laser", "kind": "laser", "label": "780 nm ECDL", "x": 0, "y": 0},
            {"id": "aom", "kind": "instrument", "label": "AOM 1", "x": 256, "y": 0,
             "device_id": "dev01-aom", "device_type": "dummy_aom",
             "settings": [{"id": "s1", "method": "set_rf_power", "args": {"value": 1.0}},
                          {"id": "s2", "method": "set_frequency",
                           "sweep": {"start": 70, "stop": 90, "points": 5}}],
             "records": [{"id": "r1", "method": "read_diffraction_efficiency"}]},
            {"id": "awg", "kind": "instrument", "label": "AWG", "x": 256, "y": -160,
             "device_id": "dev02-awg", "device_type": "dummy_awg",
             "settings": [{"id": "s1", "method": "set_waveform", "args": {"waveform": "sine", "channel": "CH1"}}]},
            {"id": "m1", "kind": "mirror", "x": 512, "y": 0},
        ],
        "links": [
            {"id": "l1", "source": "laser", "target": "aom", "type": "beam"},
            {"id": "l2", "source": "awg", "target": "aom", "type": "signal", "label": "CH1 → RF"},
            {"id": "l3", "source": "aom", "target": "m1"},
        ],
    }
    doc.update(changes)
    return doc


LIVE = {
    "dev01-aom": {"id": "dev01-aom", "type": "dummy_aom", "state": "available", "hints": AOM_HINTS},
    "dev02-awg": {"id": "dev02-awg", "type": "dummy_awg", "state": "available", "hints": AWG_HINTS},
}


def report_for(doc: dict, live: dict | None = LIVE) -> dict:
    content, problems = parse_content(doc)
    assert content is not None, problems
    return check_experiment(content, live)


def codes(items: list[dict]) -> list[str]:
    return [item["code"] for item in items]


# ---- the document ------------------------------------------------------------------


def test_a_clean_experiment_is_exportable() -> None:
    report = report_for(scan())
    assert report["exportable"] and report["errors"] == [] and report["warnings"] == []
    assert report["points"] == 5


@pytest.mark.parametrize(
    "path, value, message",
    [
        (("nodes", 1, "settings", 0, "args", "value"), float("nan"), "finite number"),
        (("nodes", 0, "x"), "12", "valid number"),
        (("nodes", 0, "label"), "evil‮text", "invisible character"),
        (("nodes", 1, "settings", 1, "sweep", "points"), 1, "greater than or equal to 2"),
        (("run", "output_name"), "../../etc/passwd", "pattern"),
        (("nodes", 0, "kind"), "flux_capacitor", "Input should be"),
    ],
)
def test_the_document_is_strict(path: tuple, value: object, message: str) -> None:
    doc = scan()
    target = doc
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    content, problems = parse_content(doc)
    assert content is None and any(message in problem["message"] for problem in problems), problems


def test_extra_keys_and_broken_structure_are_refused() -> None:
    content, problems = parse_content({**scan(), "secret": 1})
    assert content is None and problems[0]["field"] == "secret"

    doc = scan()
    doc["nodes"].append({"id": "aom2", "kind": "instrument", "device_id": "dev01-aom", "device_type": "dummy_aom"})
    doc["links"] += [
        {"id": "l4", "source": "laser", "target": "ghost"},
        {"id": "l5", "source": "m1", "target": "m1"},
        {"id": "l1", "source": "m1", "target": "laser"},
    ]
    doc["nodes"][1]["settings"].append(
        {"id": "s3", "method": "set_rf_power", "sweep": {"start": 0, "stop": 1, "points": 7}}
    )
    content, problems = parse_content(doc)
    messages = " | ".join(problem["message"] for problem in problems)
    assert content is None
    for expected in ("on the diagram twice", "isn't on the diagram", "to itself", "share the id l1", "same number of points"):
        assert expected in messages
    assert next(p for p in problems if "twice" in p["message"])["node_id"] == "aom2"


def test_too_many_points_are_refused() -> None:
    doc = scan()
    doc["nodes"][1]["settings"][0] = {"id": "s1", "method": "set_rf_power",
                                     "sweep": {"start": 0, "stop": 2, "points": 1001, "axis": 1}}
    doc["nodes"][1]["settings"][1]["sweep"]["points"] = 11
    content, problems = parse_content(doc)
    assert content is None and "10,000 points" in problems[0]["message"]


# ---- checks against contracts and the network ------------------------------------------------


def _set(doc: dict, node: int, item: str, index: int, value: dict) -> dict:
    doc["nodes"][node][item][index] = value
    return doc


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: _set(d, 1, "settings", 0, {"id": "s1", "method": "set_volume", "args": {"value": 1.0}}),
         "has no method set_volume"),
        (lambda d: _set(d, 1, "settings", 0, {"id": "s1", "method": "read_frequency"}), "record it instead"),
        (lambda d: _set(d, 1, "records", 0, {"id": "r1", "method": "set_frequency"}), "can't be recorded"),
        (lambda d: _set(d, 2, "settings", 0, {"id": "s1", "method": "set_waveform", "args": {"channel": "CH1"}}),
         "needs waveform"),
        (lambda d: _set(d, 2, "settings", 0, {"id": "s1", "method": "set_waveform", "args": {"waveform": 3}}),
         "waveform needs text"),
        (lambda d: _set(d, 1, "settings", 0, {"id": "s1", "method": "set_rf_power", "args": {"value": True}}),
         "value needs a number"),
        (lambda d: _set(d, 1, "settings", 0, {"id": "s1", "method": "set_rf_power", "args": {"watts": 1.0}}),
         "has no argument watts"),
        (lambda d: _set(d, 2, "settings", 0, {"id": "s1", "method": "set_waveform", "args": {"channel": "CH1"},
                                               "sweep": {"field": "waveform", "start": 0, "stop": 1, "points": 2,
                                                         "axis": 1}}),
         "can't be swept"),
        (lambda d: _set(d, 1, "settings", 1, {"id": "s2", "method": "set_frequency", "args": {"value": 80.0},
                                               "sweep": {"start": 70, "stop": 90, "points": 5}}),
         "remove the fixed value"),
    ],
)
def test_contract_errors_stop_export(change, message: str) -> None:
    report = report_for(change(scan()))
    assert not report["exportable"]
    assert any(message in error["message"] for error in report["errors"]), report["errors"]


def test_warnings_explain_but_do_not_stop_export() -> None:
    doc = scan()
    doc["nodes"][1]["settings"][1]["sweep"].update(start=50, stop=50)  # below 60 MHz, and flat
    doc["nodes"][2]["settings"][0]["args"].update(waveform="noise", channel="CH3")
    live = {**LIVE, "dev02-awg": {**LIVE["dev02-awg"], "state": "leased"}}
    report = report_for(doc, live)
    assert report["exportable"]
    assert set(codes(report["warnings"])) >= {"limits", "flat-sweep", "choices", "channel", "leased"}

    report = report_for(scan(), {})  # nothing on the network
    assert report["exportable"] and codes(report["warnings"]).count("offline") == 2

    changed = {**LIVE, "dev01-aom": {**LIVE["dev01-aom"], "type": "dummy_eom", "type_label": "Dummy EOM"}}
    report = report_for(scan(), changed)
    assert not report["exportable"] and "now a Dummy EOM" in report["errors"][0]["message"]


def test_unused_instruments_and_missing_records_are_flagged() -> None:
    doc = scan()
    doc["nodes"][1]["records"] = []
    doc["nodes"][2]["settings"] = []
    report = report_for(doc)
    assert {"unused", "nothing-recorded"} <= set(codes(report["warnings"]))


def test_a_command_with_one_number_can_be_swept_and_int_widths_are_checked() -> None:
    doc = {
        "name": "Laser scan",
        "nodes": [{"id": "laser", "kind": "instrument", "device_id": "laser", "device_type": "simulated_tunable_laser",
                   "settings": [{"id": "s1", "method": "go_to_wavelength_nm",
                                 "sweep": {"field": "wavelength_nm", "start": 770, "stop": 771, "points": 3}},
                                {"id": "s2", "method": "move_steps", "args": {"relative_steps": 2**40}}]}],
    }
    report = report_for(doc, None)
    assert codes(report["errors"]) == ["argument"]
    assert "relative_steps must be between" in report["errors"][0]["message"]


# ---- the store ------------------------------------------------------------------------


def _content(doc: dict | None = None):
    content, problems = parse_content(doc or scan())
    assert content is not None, problems
    return content


def test_the_store_saves_versions_and_refuses_stale_edits(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "experiments")
    assert store.list() == ([], [])  # no folder yet: nothing, no error

    created = store.create(_content(), author="Rob")
    assert created["id"].startswith("aom-frequency-scan-") and created["version"] == 1
    assert (tmp_path / "experiments" / f"{created['id']}.json").is_file()

    renamed = _content(scan(name="AOM scan, take 2"))
    saved = store.update(created["id"], renamed, base_version=1, author="Anna")
    assert saved["version"] == 2 and saved["name"] == "AOM scan, take 2"
    assert saved["created_by"] == "Rob" and saved["updated_by"] == "Anna"

    with pytest.raises(VersionConflict) as conflict:
        store.update(created["id"], renamed, base_version=1, author="Rob")
    assert conflict.value.status_code == 409 and conflict.value.extra["current"]["version"] == 2

    summaries, unreadable = store.list()
    assert [item["id"] for item in summaries] == [created["id"]] and unreadable == []
    assert summaries[0]["devices"][0]["device_id"] == "dev01-aom" and summaries[0]["points"] == 5
    assert summaries[0]["optics"] == {"laser": 1, "mirror": 1}

    store.delete(created["id"], version=2)
    assert store.list() == ([], [])
    assert len(list((tmp_path / "experiments" / ".trash").iterdir())) == 1


@pytest.mark.parametrize("hostile", ["../x", "CON", "a:b", "AOM-SCAN-ABCDEF", "x/../../y-abcdef", "scan-abc"])
def test_hostile_ids_are_never_paths(tmp_path: Path, hostile: str) -> None:
    with pytest.raises(ExperimentError) as missing:
        ExperimentStore(tmp_path).get(hostile)
    assert missing.value.status_code == 404


def test_a_damaged_file_is_listed_but_does_not_hide_the_rest(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path)
    good = store.create(_content(), author="")
    (tmp_path / "broken-scan-abcdef.json").write_text("{not json", encoding="utf-8")
    summaries, unreadable = store.list()
    assert [item["id"] for item in summaries] == [good["id"]]
    assert unreadable[0]["file"] == "broken-scan-abcdef.json"


def test_two_saves_from_the_same_version_cannot_both_win(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path)
    experiment = store.create(_content(), author="")
    outcomes: list[str] = []

    def save(name: str) -> None:
        try:
            store.update(experiment["id"], _content(scan(name=name)), base_version=1, author=name)
            outcomes.append("saved")
        except VersionConflict:
            outcomes.append("conflict")

    threads = [threading.Thread(target=save, args=(name,)) for name in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict", "saved"]


def test_a_briefly_locked_file_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ExperimentStore(tmp_path)
    real_replace = os.replace
    calls = []

    def flaky_replace(source, target):
        calls.append(target)
        if len(calls) == 1:
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(source, target)

    monkeypatch.setattr(experiment_store.os, "replace", flaky_replace)
    experiment = store.create(_content(), author="")
    assert len(calls) == 2 and store.get(experiment["id"])["version"] == 1
    assert not list(tmp_path.glob(".*.tmp"))


# ---- the API ------------------------------------------------------------------------


ENDPOINT = EndpointDescriptor(transport="grpc", target="127.0.0.1:50051", api_version="labnet.devices.v1", secure=False)


def _info(device_id: str, device_type: str, hints: dict, leased: bool = False) -> DeviceInfo:
    return DeviceInfo(
        device_id=device_id, manager_id="lab-pc-dev-01", endpoint=ENDPOINT, device_type=device_type,
        runtime_state="ready", availability="available", status_text="ready",
        last_seen=datetime.now(timezone.utc), effective_available=not leased,
        allocation_state="leased" if leased else "free", metadata={"hints": hints},
    )


class FakeCentral:
    async def list_lab_pcs(self):
        return [
            LabPCInfo(
                manager_id="lab-pc-dev-01", host="127.0.0.1", endpoint=ENDPOINT, online=True,
                last_seen=datetime.now(timezone.utc),
                devices=(_info("dev01-aom", "dummy_aom", AOM_HINTS), _info("dev02-awg", "dummy_awg", AWG_HINTS)),
            )
        ]

    async def aclose(self) -> None:
        pass


@pytest.fixture
def browser(tmp_path: Path):
    settings = ControlCenterSettings(
        server_url="http://127.0.0.1:8008", api_key="key", ca_file=None, bind_host="127.0.0.1", port=8080,
        refresh_seconds=60.0, experiments_dir=tmp_path / "experiments",
    )
    with TestClient(create_app(settings, client_factory=lambda _: FakeCentral())) as client:
        yield client


def test_the_api_creates_saves_lists_and_deletes(browser: TestClient) -> None:
    created = browser.post("/api/experiments", json={**scan(), "created_by": "Rob"})
    assert created.status_code == 201
    experiment = created.json()["experiment"]
    assert created.headers["location"] == f"/experiments/{experiment['id']}"
    assert created.json()["report"]["exportable"] is True

    edited = copy.deepcopy(experiment)
    edited["nodes"][3]["x"] = 640
    edited["updated_by"] = "Anna"
    saved = browser.put(f"/api/experiments/{experiment['id']}", json=edited)
    assert saved.status_code == 200 and saved.json()["experiment"]["version"] == 2

    stale = browser.put(f"/api/experiments/{experiment['id']}", json=edited)  # still says version 1
    assert stale.status_code == 409 and stale.json()["current"]["version"] == 2

    listed = browser.get("/api/experiments").json()
    assert [item["name"] for item in listed["experiments"]] == ["AOM frequency scan"]
    assert browser.get(f"/api/experiments/{experiment['id']}").json()["experiment"]["updated_by"] == "Anna"

    assert browser.delete(f"/api/experiments/{experiment['id']}?version=1").status_code == 409
    assert browser.delete(f"/api/experiments/{experiment['id']}?version=2").status_code == 204
    assert browser.get(f"/api/experiments/{experiment['id']}").status_code == 404


def test_bad_experiments_get_a_list_of_problems(browser: TestClient) -> None:
    bad = scan()
    bad["nodes"][0]["x"] = float("inf")
    reply = browser.post("/api/experiments", content=json.dumps(bad), headers={"content-type": "application/json"})
    assert reply.status_code == 422 and reply.json()["errors"][0]["node_id"] == "laser"
    assert browser.post("/api/experiments", content=b"{nope", headers={"content-type": "application/json"}).status_code == 422
    assert browser.post("/api/experiments", json={**scan(), "created_by": "a‮b"}).status_code == 422
    huge = browser.post("/api/experiments", content=b"x" * (600 * 1024), headers={"content-type": "application/json"})
    assert huge.status_code == 413


def test_validate_checks_a_proposal_without_saving(browser: TestClient) -> None:
    proposal = scan()
    del proposal["nodes"][1]["device_type"]  # filled from the live network
    for node in proposal["nodes"]:
        node.pop("x"), node.pop("y")
    reply = browser.post("/api/experiments/validate", json=proposal).json()
    assert reply["report"]["exportable"] is True
    assert reply["experiment"]["nodes"][1]["device_type"] == "dummy_aom"
    assert browser.get("/api/experiments").json()["experiments"] == []

    broken = browser.post("/api/experiments/validate", json={"name": ""}).json()
    assert broken["experiment"] is None and broken["report"]["errors"]


def test_meta_describes_every_component_and_device_type(browser: TestClient) -> None:
    meta = browser.get("/api/experiments/meta").json()
    assert {"mirror", "beamsplitter", "filter"} <= {item["kind"] for item in meta["optics"]}
    assert meta["device_types"]["dummy_awg"]["python_class"] == "DummyAWG"
    assert meta["device_types"]["dummy_aom"]["label"] == "Dummy AOM"
    assert meta["link_types"] == ["beam", "signal"] and "properties" in meta["json_schema"]


def test_the_script_downloads_for_the_version_on_screen(browser: TestClient) -> None:
    experiment = browser.post("/api/experiments", json=scan()).json()["experiment"]
    url = f"/api/experiments/{experiment['id']}/script"

    script = browser.get(url, params={"version": 1})
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/x-python")
    assert script.headers["content-disposition"] == 'attachment; filename="aom_scan.py"'
    assert 'DummyAOM(device_id="dev01-aom", client=central)' in script.text
    compile(script.text, "aom_scan.py", "exec")

    assert browser.get(url, params={"version": 7}).status_code == 409

    broken = copy.deepcopy(experiment)
    broken["nodes"][1]["records"] = [{"id": "r1", "method": "set_frequency"}]
    assert browser.put(f"/api/experiments/{experiment['id']}", json=broken).status_code == 200  # saving is fine
    refused = browser.get(url)
    assert refused.status_code == 422 and "can't be recorded" in refused.json()["errors"][0]["message"]


def test_experiment_pages_are_served(browser: TestClient) -> None:
    assert browser.get("/experiments").status_code == 200
    assert browser.get("/experiments/aom-scan-abcdef").status_code == 200
    assert browser.get("/experiments/not-an-id").status_code == 404
    assert browser.get("/experiments", follow_redirects=False).headers["content-type"].startswith("text/html")
