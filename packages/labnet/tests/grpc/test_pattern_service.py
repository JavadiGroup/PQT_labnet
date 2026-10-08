from __future__ import annotations

from dataclasses import replace
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from labnet.grpc import GrpcServerConfig, LabnetGrpcServer
from labnet_sim._generated.contracts import DUMMY_DEVICE_CONTRACT
from labnet.grpc.services import (
    PatternDeviceGrpcService,
    UnsupportedRpcPatternError,
)


class _UnusedExecutor:
    async def execute(self, **_kwargs: Any) -> Any:
        raise AssertionError("The unsupported contract must fail at startup.")

    async def authorize(self, **_kwargs: Any) -> Any:
        raise AssertionError("The unsupported contract must fail at startup.")


def test_unsupported_generated_pattern_fails_at_service_construction() -> None:
    unsupported_method = replace(
        DUMMY_DEVICE_CONTRACT.methods[0],
        pattern="RPC_PATTERN_NATIVE_STREAM",
        pattern_value=7,
    )
    unsupported_contract = replace(
        DUMMY_DEVICE_CONTRACT,
        methods=(unsupported_method,),
    )

    with pytest.raises(
        UnsupportedRpcPatternError,
        match="unsupported LabNet pattern RPC_PATTERN_NATIVE_STREAM",
    ):
        PatternDeviceGrpcService(_UnusedExecutor(), unsupported_contract)


def test_pattern_service_exposes_generated_rpc_methods_structurally() -> None:
    service = PatternDeviceGrpcService(
        _UnusedExecutor(),
        DUMMY_DEVICE_CONTRACT,
    )

    assert service.SERVICE_NAME == DUMMY_DEVICE_CONTRACT.service_full_name
    assert callable(service.ReadValue)
    assert callable(service.WriteValue)
    assert callable(service.StreamData)
    with pytest.raises(AttributeError):
        _ = service.NotAContractMethod


def test_runtime_rejects_pattern_cardinality_mismatch_defensively() -> None:
    invalid_method = replace(
        DUMMY_DEVICE_CONTRACT.methods[0],
        server_streaming=True,
        cardinality="server_streaming",
    )
    invalid_contract = replace(
        DUMMY_DEVICE_CONTRACT,
        methods=(invalid_method,),
    )

    with pytest.raises(ValueError, match="cardinality.*incompatible"):
        PatternDeviceGrpcService(_UnusedExecutor(), invalid_contract)


@pytest.mark.asyncio
async def test_server_health_registers_every_generated_contract() -> None:
    def no_op_registration(_service: object, _server: object) -> None:
        return None

    first = replace(
        DUMMY_DEVICE_CONTRACT,
        device_type="family_a",
        service_full_name="labnet.test.FamilyAService",
        service_name="FamilyAService",
        add_servicer_to_server=no_op_registration,
    )
    second = replace(
        DUMMY_DEVICE_CONTRACT,
        device_type="family_b",
        service_full_name="labnet.test.FamilyBService",
        service_name="FamilyBService",
        add_servicer_to_server=no_op_registration,
    )
    server = LabnetGrpcServer(
        _UnusedExecutor(),
        config=GrpcServerConfig(bind="127.0.0.1:0"),
        contracts=(first, second),
    )
    channel: grpc.aio.Channel | None = None
    try:
        assert tuple(server.device_services) == ("family_a", "family_b")
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{server.bound_port}")
        health_stub = health_pb2_grpc.HealthStub(channel)
        for service_name in server.service_names:
            response = await health_stub.Check(
                health_pb2.HealthCheckRequest(service=service_name)
            )
            assert response.status == health_pb2.HealthCheckResponse.SERVING
    finally:
        if channel is not None:
            await channel.close()
        await server.stop(grace=0)
