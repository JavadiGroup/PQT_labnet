from __future__ import annotations

import math
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import quote

import httpx

from .errors import (
    InvalidConfigurationError,
    LeaseInvalidError,
    LeaseValidationUnavailableError,
    ManagerClosedError,
)
from .security import validate_central_server_url


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    """Validated authorization for one lease/device pair."""

    lease_id: str
    device_id: str
    user: str | None = None
    expires_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.lease_id or not self.device_id:
            raise ValueError("lease_id and device_id must be non-empty.")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware.")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(
            timezone.utc
        )


@runtime_checkable
class LeaseValidator(Protocol):
    async def validate(self, *, lease_id: str, device_id: str) -> LeaseGrant:
        """Validate and return authorization for one device operation."""
        ...


class HttpLeaseValidator:
    """Fail-closed Central Server v1 lease validator.

    A successful response must contain ``valid: true``, the same ``lease_id``
    and ``device_id`` that were requested, and a timezone-aware ``expires_at``.
    ``user`` is optional. Any malformed, mismatched, expired, or unavailable
    response is rejected rather than granting device access.
    """

    def __init__(
        self,
        *,
        server_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        server_url = validate_central_server_url(server_url)
        api_key = api_key.strip()
        if not api_key:
            raise InvalidConfigurationError("api_key must be non-empty.")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise InvalidConfigurationError("timeout_seconds must be positive.")

        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=server_url,
            timeout=timeout_seconds,
            verify=verify,
        )
        self._closed = False

    async def validate(self, *, lease_id: str, device_id: str) -> LeaseGrant:
        _validate_identifiers(lease_id=lease_id, device_id=device_id)
        if self._closed:
            raise ManagerClosedError("The HTTP lease validator is closed.")

        try:
            response = await self._client.post(
                f"/api/v1/leases/{quote(lease_id, safe='')}/validate",
                json={"device_id": device_id},
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise LeaseValidationUnavailableError(
                "The Central Server could not be reached for lease validation.",
                details={"exception_type": type(exc).__name__},
            ) from exc

        if response.status_code in {401, 403, 404}:
            raise LeaseInvalidError(
                "The lease is not valid for this device.",
                details={"lease_id": lease_id, "device_id": device_id},
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise LeaseValidationUnavailableError(
                "The Central Server returned an unexpected lease-validation response.",
                details={"status_code": response.status_code},
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise LeaseValidationUnavailableError(
                "The Central Server returned malformed lease-validation data."
            ) from exc

        if not isinstance(payload, Mapping):
            raise LeaseValidationUnavailableError(
                "The Central Server returned malformed lease-validation data."
            )
        if payload.get("valid") is not True:
            raise LeaseInvalidError(
                "The lease is not valid for this device.",
                details={"lease_id": lease_id, "device_id": device_id},
            )
        if payload.get("lease_id") != lease_id or payload.get("device_id") != device_id:
            raise LeaseValidationUnavailableError(
                "The Central Server returned mismatched lease-validation data."
            )

        expires_at = _parse_datetime(payload.get("expires_at"))
        if expires_at is None:
            raise LeaseValidationUnavailableError(
                "The Central Server omitted the lease expiration."
            )
        grant = LeaseGrant(
            lease_id=lease_id,
            device_id=device_id,
            user=payload.get("user"),
            expires_at=expires_at,
        )
        if grant.expired:
            raise LeaseInvalidError(
                "The lease has expired.",
                details={"lease_id": lease_id, "device_id": device_id},
            )
        return grant

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()


class StaticLeaseValidator:
    """Deterministic validator for unit tests and offline integration tests.

    The mapping associates each accepted lease ID with either its device ID or
    a complete :class:`LeaseGrant`. Unknown and mismatched leases fail closed.
    """

    def __init__(self, leases: Mapping[str, str | LeaseGrant]) -> None:
        grants: dict[str, LeaseGrant] = {}
        for lease_id, value in leases.items():
            grants[lease_id] = (
                value
                if isinstance(value, LeaseGrant)
                else LeaseGrant(lease_id=lease_id, device_id=value, user="test")
            )
            if grants[lease_id].lease_id != lease_id:
                raise InvalidConfigurationError(
                    "A static lease mapping key must match its LeaseGrant lease_id."
                )
        self._grants = grants

    async def validate(self, *, lease_id: str, device_id: str) -> LeaseGrant:
        _validate_identifiers(lease_id=lease_id, device_id=device_id)
        grant = self._grants.get(lease_id)
        if grant is None or grant.device_id != device_id or grant.expired:
            raise LeaseInvalidError(
                "The lease is not valid for this device.",
                details={"lease_id": lease_id, "device_id": device_id},
            )
        return grant


class DevelopmentLeaseValidator:
    """Explicitly insecure validator for loopback-only development servers."""

    def __init__(self, *, allow_insecure: bool) -> None:
        if allow_insecure is not True:
            raise InvalidConfigurationError(
                "DevelopmentLeaseValidator requires allow_insecure=True."
            )

    async def validate(self, *, lease_id: str, device_id: str) -> LeaseGrant:
        _validate_identifiers(lease_id=lease_id, device_id=device_id)
        return LeaseGrant(
            lease_id=lease_id,
            device_id=device_id,
            user="development",
            metadata={"insecure_development_grant": True},
        )


def _validate_identifiers(*, lease_id: str, device_id: str) -> None:
    if not isinstance(lease_id, str) or not lease_id.strip():
        raise LeaseInvalidError("lease_id must be a non-empty string.")
    if not isinstance(device_id, str) or not device_id.strip():
        raise LeaseInvalidError("device_id must be a non-empty string.")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LeaseValidationUnavailableError(
            "The Central Server returned an invalid lease expiration."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LeaseValidationUnavailableError(
            "The Central Server returned an invalid lease expiration."
        ) from exc
    if parsed.tzinfo is None:
        raise LeaseValidationUnavailableError(
            "The Central Server returned an expiration without a timezone."
        )
    return parsed.astimezone(timezone.utc)


__all__ = [
    "DevelopmentLeaseValidator",
    "HttpLeaseValidator",
    "LeaseGrant",
    "LeaseValidator",
    "StaticLeaseValidator",
]
