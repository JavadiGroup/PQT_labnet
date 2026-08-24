"""Authentication metadata helpers for Labnet gRPC services.

TLS establishes the identity and confidentiality of the connection. A lease is
still required on every device RPC to authorize use of a particular device.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Protocol


LEASE_METADATA_KEY: Final[str] = "x-labnet-lease-id"
MAX_LEASE_ID_LENGTH: Final[int] = 512


class InvocationMetadataContext(Protocol):
    def invocation_metadata(self) -> Iterable[object]: ...


class LeaseMetadataError(ValueError):
    """Raised when a call does not contain one well-formed lease identifier."""

    code = "LEASE_METADATA_INVALID"


def extract_lease_id(
    context: InvocationMetadataContext,
    *,
    metadata_key: str = LEASE_METADATA_KEY,
) -> str:
    """Return the lease ID carried by a gRPC call.

    Exactly one value is accepted. Rejecting duplicate metadata prevents
    different intermediaries from disagreeing about which credential applies.
    """

    values: list[str] = []
    expected_key = metadata_key.lower()

    for item in context.invocation_metadata():
        if hasattr(item, "key"):
            key = getattr(item, "key")
            value = getattr(item, "value")
        else:
            try:
                key, value = item  # type: ignore[misc]
            except (TypeError, ValueError) as exc:
                raise LeaseMetadataError("Malformed RPC metadata.") from exc

        if str(key).lower() != expected_key:
            continue

        if not isinstance(value, str):
            raise LeaseMetadataError("Lease metadata must be an ASCII string.")
        values.append(value)

    if not values:
        raise LeaseMetadataError(
            f"Missing required RPC metadata: {metadata_key}."
        )
    if len(values) != 1:
        raise LeaseMetadataError(
            f"RPC metadata {metadata_key} must appear exactly once."
        )

    lease_id = values[0].strip()
    if not lease_id:
        raise LeaseMetadataError("Lease ID cannot be empty.")
    if len(lease_id) > MAX_LEASE_ID_LENGTH:
        raise LeaseMetadataError("Lease ID is too long.")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in lease_id):
        raise LeaseMetadataError("Lease ID contains invalid characters.")

    return lease_id


__all__ = [
    "LEASE_METADATA_KEY",
    "LeaseMetadataError",
    "extract_lease_id",
]
