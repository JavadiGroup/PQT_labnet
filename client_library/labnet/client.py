from __future__ import annotations

from typing import Any

import httpx

from .exceptions import (
    AuthenticationError,
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServerConnectionError,
    ServerResponseError,
)
from .models import (
    AuthenticatedUser,
    DeviceInfo,
    LeaseGrant,
    LeaseInfo
)


class LabClient:
    """
    HTTP client for the Lab Network Central Server.

    The API key is automatically included in every protected request.
    """

    def __init__(
        self,
        server_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        authenticate_on_start: bool = True,
    ):
        if not server_url:
            raise ValueError("server_url cannot be empty.")

        if not api_key:
            raise ValueError("api_key cannot be empty.")

        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.current_user: AuthenticatedUser | None = None

        self._http = httpx.Client(
            base_url=self.server_url,
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )

        if authenticate_on_start:
            self.authenticate()

    def authenticate(self) -> AuthenticatedUser:
        """
        Validate the API key and retrieve the associated user.

        Calls GET /auth/me.
        """
        data = self._request(
            method="GET",
            path="/auth/me",
        )

        user = AuthenticatedUser(
            user=data["user"],
            role=data["role"],
        )

        self.current_user = user
        return user

    def health(self) -> dict[str, Any]:
        """
        Check whether the Central Server is running.

        Requires GET /health to be implemented later.
        """
        return self._request(
            method="GET",
            path="/health",
        )

    def list_devices(
        self,
        device_type: str | None = None,
        status: str | None = None,
    ) -> list[DeviceInfo]:
        """List devices, optionally filtered by type and status."""

        params = {}

        if device_type is not None:
            params["device_type"] = device_type

        if status is not None:
            params["status"] = status

        data = self._request(
            method="GET",
            path="/devices",
            params=params,
        )

        return [
            DeviceInfo.from_dict(device)
            for device in data["devices"]
        ]
    
    def list_available_devices(
        self,
        device_type: str | None = None,
    ) -> list[DeviceInfo]:
        """Return idle devices available for leasing."""

        return self.list_devices(
            device_type=device_type,
            status="idle",
        )

    def get_device(self, device_id: str) -> DeviceInfo:
        """Retrieve one device record."""

        data = self._request(
            method="GET",
            path=f"/devices/{device_id}",
        )

        return DeviceInfo.from_dict(data)

    def request_lease(
        self,
        device_id: str,
        duration_seconds: int,
    ) -> LeaseGrant:
        """Request exclusive access to one device."""

        if duration_seconds <= 0:
            raise ValueError(
                "duration_seconds must be greater than zero."
            )

        data = self._request(
            method="POST",
            path="/leases/request",
            json={
                "device_id": device_id,
                "duration_seconds": duration_seconds,
            },
        )

        # Fetch the full device record after the lease changes its status.
        device = self.get_device(device_id)

        return LeaseGrant(
            lease=LeaseInfo.from_dict(data["lease"]),
            device=device,
        )

    def release_lease(self, lease_id: str) -> None:
        """Release one active lease."""

        self._request(
            method="POST",
            path="/leases/release",
            json={
                "lease_id": lease_id,
            },
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> "LabClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request and translate HTTP errors into client exceptions."""
        try:
            response = self._http.request(
                method=method,
                url=path,
                json=json,
                params=params,
            )
        except httpx.RequestError as exc:
            raise ServerConnectionError(
                f"Could not connect to Central Server at "
                f"{self.server_url}: {exc}"
            ) from exc

        if response.is_success:
            if not response.content:
                return None

            try:
                return response.json()
            except ValueError as exc:
                raise ServerResponseError(
                    response.status_code,
                    "Server returned invalid JSON.",
                ) from exc

        message = self._extract_error_message(response)

        if response.status_code == 401:
            raise AuthenticationError(message)

        if response.status_code == 403:
            raise PermissionDeniedError(message)

        if response.status_code == 404:
            raise ResourceNotFoundError(message)

        if response.status_code == 409:
            raise ResourceConflictError(message)

        raise ServerResponseError(
            response.status_code,
            message,
        )

    def _extract_error_message(
        self,
        response: httpx.Response,
    ) -> str:
        """Extract a useful message from a FastAPI error response."""
        try:
            data = response.json()
        except ValueError:
            return response.text or "Unknown server error."

        detail = data.get("detail")

        if isinstance(detail, str):
            return detail

        if detail is not None:
            return str(detail)

        error = data.get("error")

        if isinstance(error, dict):
            return error.get("message", str(error))

        if isinstance(error, str):
            return error

        return f"Request failed with HTTP {response.status_code}."