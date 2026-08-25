from __future__ import annotations

from concurrent import futures
from datetime import datetime, timedelta, timezone
import json
from typing import Any

import grpc
import httpx
import pytest

from labnet.client import LabClient
from labnet.devices.DummyDevice import DummyDevice
from labnet.devices.core import remote as remote_module
from labnet.devices.core.facade import ContractDeviceFacade
from labnet.grpc.generated import common_pb2, dummy_device_pb2
from labnet.grpc.generated import dummy_device_pb2_grpc
from labnet.grpc.generated.device_contracts import DUMMY_DEVICE_CONTRACT


def _measurement(value: float, channel: str = "") -> common_pb2.ScalarMeasurement:
    return common_pb2.ScalarMeasurement(
        timestamp_ns=1_700_000_000_000_000_000,
        value=value,
        unit="arb",
        channel=channel,
        quality=common_pb2.MEASUREMENT_QUALITY_GOOD,
        status="OK",
    )


class _StatefulDummyService(
    dummy_device_pb2_grpc.DummyDeviceServiceServicer
):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], float] = {}

    def ReadValue(self, request, context):  # noqa: N802
        channel = request.channel if request.HasField("channel") else ""
        return dummy_device_pb2.ReadValueResponse(
            operation_id=request.operation_id,
            measurement=_measurement(
                self.values.get((request.device_id, channel), 0.0),
                channel,
            ),
        )

    def WriteValue(self, request, context):  # noqa: N802
        channel = request.channel if request.HasField("channel") else ""
        self.values[(request.device_id, channel)] = request.value
        return dummy_device_pb2.WriteValueResponse(
            operation_id=request.operation_id,
            measurement=_measurement(request.value, channel),
        )


class _CentralLeaseTransport:
    def __init__(
        self,
        endpoint: str,
        *,
        device_type: str = "dummy_device",
    ) -> None:
        self.endpoint = endpoint
        self.device_type = device_type
        self.acquired_device_ids: list[str] = []
        self.released_lease_ids: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/leases":
            body: dict[str, Any] = json.loads(request.content)
            device_id = str(body["device_id"])
            self.acquired_device_ids.append(device_id)
            lease_id = f"lease-{device_id}"
            now = datetime.now(timezone.utc)
            return httpx.Response(
                201,
                json={
                    "lease": {
                        "lease_id": lease_id,
                        "device_id": device_id,
                        "user": "developer",
                        "created_at": now.isoformat(),
                        "expires_at": (now + timedelta(seconds=60)).isoformat(),
                        "state": "active",
                    },
                    "device": {
                        "device_id": device_id,
                        "manager_id": "lab-pc-1",
                        "device_type": self.device_type,
                        "runtime_state": "ready",
                        "availability": "available",
                        "effective_available": False,
                        "endpoint": {
                            "transport": "grpc",
                            "target": self.endpoint,
                            "api_version": "labnet.devices.v1",
                            "secure": False,
                        },
                    },
                },
            )
        if request.method == "DELETE" and request.url.path.startswith(
            "/api/v1/leases/"
        ):
            lease_id = request.url.path.rsplit("/", 1)[-1]
            self.released_lease_ids.append(lease_id)
            return httpx.Response(
                200,
                json={
                    "lease_id": lease_id,
                    "state": "released",
                    "released": True,
                },
            )
        raise AssertionError(f"Unexpected Central request: {request.method} {request.url}")


def _round_trip(device: DummyDevice, value: float, channel: str) -> float:
    """Experiment code intentionally has no local/remote branch."""

    device.write_value(value, channel=channel)
    return device.read_value(channel=channel)


class _PartiallyConnectingPhysical:
    latest: _PartiallyConnectingPhysical | None = None
    disconnect_fails = False

    def __init__(self, device_id: str = "partial-device") -> None:
        self.device_id = device_id
        self.disconnect_calls = 0
        type(self).latest = self

    def connect(self) -> None:
        raise RuntimeError("primary physical connection failure")

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.disconnect_fails:
            raise RuntimeError("secondary disconnect failure")


class _PartiallyConnectingDevice(ContractDeviceFacade):
    contract = DUMMY_DEVICE_CONTRACT
    physical_type = _PartiallyConnectingPhysical


@pytest.mark.parametrize("disconnect_fails", [False, True])
def test_local_facade_rolls_back_a_partial_connection(
    disconnect_fails: bool,
) -> None:
    _PartiallyConnectingPhysical.latest = None
    _PartiallyConnectingPhysical.disconnect_fails = disconnect_fails

    with pytest.raises(
        RuntimeError,
        match="primary physical connection failure",
    ) as caught:
        _PartiallyConnectingDevice(device_id="partial-device")

    physical = _PartiallyConnectingPhysical.latest
    assert physical is not None
    assert physical.disconnect_calls == 1
    notes = getattr(caught.value, "__notes__", ())
    if disconnect_fails:
        assert any("secondary disconnect failure" in note for note in notes)
    else:
        assert not notes


class _TrackingGrpcClient:
    instances: list[_TrackingGrpcClient] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.closed = False
        type(self).instances.append(self)

    def connect(self, _timeout_seconds: float | None = None) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def test_remote_facade_releases_a_wrong_family_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TrackingGrpcClient.instances.clear()
    monkeypatch.setattr(
        remote_module,
        "ContractDeviceGrpcClient",
        _TrackingGrpcClient,
    )
    central_transport = _CentralLeaseTransport(
        "127.0.0.1:50051",
        device_type="wavemeter",
    )
    client = LabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(central_transport),
    )
    try:
        with pytest.raises(ValueError, match="LeaseGrant device_type") as caught:
            DummyDevice(
                client=client,
                device_id="wrong-family-device",
                auto_renew=False,
            )
    finally:
        client.close()

    assert "requested contract device_type 'dummy_device'" in str(caught.value)
    assert central_transport.acquired_device_ids == ["wrong-family-device"]
    assert central_transport.released_lease_ids == [
        "lease-wrong-family-device"
    ]
    assert _TrackingGrpcClient.instances == []


def test_same_dummy_device_workflow_works_locally_and_remotely() -> None:
    service = _StatefulDummyService()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    dummy_device_pb2_grpc.add_DummyDeviceServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    central_transport = _CentralLeaseTransport(f"127.0.0.1:{port}")
    client = LabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(central_transport),
    )
    local: DummyDevice | None = None
    remote: DummyDevice | None = None
    try:
        local = DummyDevice(device_id="local-dummy")
        remote = DummyDevice(
            client=client,
            device_id="remote-dummy",
            auto_renew=False,
        )
        assert type(local) is type(remote) is DummyDevice
        assert local.is_remote is False
        assert remote.is_remote is True
        assert _round_trip(local, 1.25, "phase") == 1.25
        assert _round_trip(remote, 2.5, "phase") == 2.5
    finally:
        if remote is not None:
            remote.close()
        if local is not None:
            local.close()
        client.close()
        server.stop(None)

    assert central_transport.acquired_device_ids == ["remote-dummy"]
    assert central_transport.released_lease_ids == ["lease-remote-dummy"]


def test_one_lab_client_can_be_shared_by_multiple_device_handles() -> None:
    service = _StatefulDummyService()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    dummy_device_pb2_grpc.add_DummyDeviceServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    central_transport = _CentralLeaseTransport(f"127.0.0.1:{port}")
    client = LabClient(
        "https://central.example",
        "secret",
        authenticate_on_start=False,
        transport=httpx.MockTransport(central_transport),
    )
    first: DummyDevice | None = None
    second: DummyDevice | None = None
    try:
        first = DummyDevice(
            client=client,
            device_id="dummy-a",
            auto_renew=False,
        )
        second = DummyDevice(
            client=client,
            device_id="dummy-b",
            auto_renew=False,
        )
        assert _round_trip(first, 10.0, "signal") == 10.0
        assert _round_trip(second, 20.0, "signal") == 20.0
        assert first.read_value(channel="signal") == 10.0
        assert second.read_value(channel="signal") == 20.0
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        client.close()
        server.stop(None)

    assert central_transport.acquired_device_ids == ["dummy-a", "dummy-b"]
    assert set(central_transport.released_lease_ids) == {
        "lease-dummy-a",
        "lease-dummy-b",
    }
