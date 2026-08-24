from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Protocol, Sequence, runtime_checkable

import httpx

from .errors import CentralCommunicationError, InvalidConfigurationError
from .models import DeviceSnapshot, EndpointDescriptor
from .security import validate_central_server_url


@runtime_checkable
class CentralRegistryClient(Protocol):
    async def register(
        self,
        *,
        host: str,
        endpoint: EndpointDescriptor,
        devices: Sequence[DeviceSnapshot],
    ) -> None:
        ...

    async def heartbeat(
        self,
        *,
        host: str,
        endpoint: EndpointDescriptor,
        devices: Sequence[DeviceSnapshot],
        timestamp: datetime,
    ) -> None:
        ...

    async def close(self) -> None:
        ...


class HttpCentralRegistryClient:
    """HTTP management-plane client using versioned endpoint descriptors."""

    def __init__(
        self,
        *,
        server_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
        registration_path: str = "/devices/register",
        heartbeat_path: str = "/devices/heartbeat",
    ) -> None:
        server_url = validate_central_server_url(server_url)
        if not api_key.strip():
            raise InvalidConfigurationError("Central Server API key must be non-empty.")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise InvalidConfigurationError("timeout_seconds must be positive.")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=server_url,
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self._registration_path = registration_path
        self._heartbeat_path = heartbeat_path
        self._closed = False

    async def register(
        self,
        *,
        host: str,
        endpoint: EndpointDescriptor,
        devices: Sequence[DeviceSnapshot],
    ) -> None:
        await self._post(
            self._registration_path,
            {
                "host": host,
                "endpoint": endpoint.to_dict(),
                "devices": [item.to_registration_dict() for item in devices],
            },
        )

    async def heartbeat(
        self,
        *,
        host: str,
        endpoint: EndpointDescriptor,
        devices: Sequence[DeviceSnapshot],
        timestamp: datetime,
    ) -> None:
        await self._post(
            self._heartbeat_path,
            {
                "host": host,
                "endpoint": endpoint.to_dict(),
                "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
                "devices": [item.to_heartbeat_dict() for item in devices],
            },
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, object]) -> None:
        if self._closed:
            raise CentralCommunicationError(
                "The Central Server registry client is closed."
            )
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            raise CentralCommunicationError(
                "Central Server registration or heartbeat failed.",
                details={
                    "exception_type": type(exc).__name__,
                    "status_code": status_code,
                },
            ) from exc


__all__ = ["CentralRegistryClient", "HttpCentralRegistryClient"]
