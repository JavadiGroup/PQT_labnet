"""3.0 moved every device service into its driver's package, so 2.x and 3.0 refuse each other."""

from __future__ import annotations

import pytest

from labnet.client.core.grpc_client import SUPPORTED_DEVICE_API_VERSION, validate_endpoint
from labnet.client.core.models import EndpointDescriptor


def test_a_2x_lab_pc_endpoint_is_refused_with_a_version_error() -> None:
    old = EndpointDescriptor(transport="grpc", target="127.0.0.1:50051", api_version="labnet.devices.v1", secure=False)
    with pytest.raises(ValueError, match=r"Unsupported device API version 'labnet.devices.v1'; expected 'labnet.v3'"):
        validate_endpoint(old)
    current = EndpointDescriptor(transport="grpc", target="127.0.0.1:50051", api_version="labnet.v3", secure=False)
    validate_endpoint(current)
    assert SUPPORTED_DEVICE_API_VERSION == "labnet.v3"
