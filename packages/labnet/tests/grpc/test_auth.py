from __future__ import annotations

import pytest

from labnet.grpc.auth import LeaseMetadataError, extract_lease_id


class _Context:
    def __init__(self, metadata: tuple[tuple[str, str], ...]) -> None:
        self._metadata = metadata

    def invocation_metadata(self) -> tuple[tuple[str, str], ...]:
        return self._metadata


def test_extract_lease_id_accepts_one_ascii_value() -> None:
    context = _Context((("x-labnet-lease-id", "lease-123"),))

    assert extract_lease_id(context) == "lease-123"


@pytest.mark.parametrize(
    "metadata",
    [
        (),
        (("x-labnet-lease-id", ""),),
        (("x-labnet-lease-id", "one"), ("x-labnet-lease-id", "two")),
        (("x-labnet-lease-id", "contains a space"),),
    ],
)
def test_extract_lease_id_rejects_missing_or_ambiguous_values(
    metadata: tuple[tuple[str, str], ...],
) -> None:
    with pytest.raises(LeaseMetadataError):
        extract_lease_id(_Context(metadata))
