"""Unary-command results survive the Lab PC encoding and client decoding unchanged."""

from __future__ import annotations

from fractions import Fraction

import pytest

from labnet.client.core.grpc_client import _command_value
from labnet.grpc.generated import common_pb2
from labnet.grpc.services.patterns import _command_result
from labnet.grpc.status import GrpcServiceError


def _round_trip(result: object) -> object:
    response = common_pb2.CommandResponse(operation_id="op", result=_command_result(result))
    return _command_value(common_pb2.CommandResponse.FromString(response.SerializeToString()))


@pytest.mark.parametrize(
    ("returned", "expected", "kind"),
    [
        (None, None, "none_value"),
        (True, True, "boolean_value"),
        (-7, -7, "integer_value"),
        (2.5, 2.5, "floating_point_value"),
        ("square", "square", "text_value"),
        ([3, -4, 2**62], (3, -4, 2**62), "integer_sequence"),
        ((), (), "integer_sequence"),
        ([749.5, 750.0, 750.5], (749.5, 750.0, 750.5), "floating_point_sequence"),
        ((1, 2.5, 3), (1.0, 2.5, 3.0), "floating_point_sequence"),
        ([Fraction(1, 4), 0.5], (0.25, 0.5), "floating_point_sequence"),
    ],
)
def test_results_round_trip_with_their_kind(returned: object, expected: object, kind: str) -> None:
    assert _command_result(returned).WhichOneof("kind") == kind
    decoded = _round_trip(returned)
    assert decoded == expected
    assert type(decoded) is type(expected)
    if kind == "floating_point_sequence":
        assert all(type(value) is float for value in decoded)


def test_a_spectrum_sized_float_list_keeps_full_precision() -> None:
    counts = [1000.0 + index / 3 for index in range(2048)]
    assert _round_trip(counts) == tuple(counts)


@pytest.mark.parametrize(
    "returned",
    [
        [1.0, float("nan")],
        [float("inf"), 2.0],
        [1.0, True],
        [1.0, "2"],
        [1.0, None],
        [[1.0], [2.0]],
        {"a": 1.0},
        b"\x00\x01",
    ],
)
def test_unsupported_or_non_finite_results_are_refused(returned: object) -> None:
    with pytest.raises(GrpcServiceError) as caught:
        _command_result(returned)
    assert caught.value.error_code == "INVALID_DEVICE_RESPONSE"
