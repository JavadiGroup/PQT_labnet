"""Synchronous and async-first clients for the versioned Central API."""

from __future__ import annotations

import asyncio
import ipaddress
import math
import os
import ssl
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

import httpx

from labnet.envfile import load_env_file
from labnet.lease_policy import (
    DEFAULT_LEASE_DURATION_SECONDS,
    DEFAULT_RENEWAL_INTERVAL_SECONDS,
)

from .exceptions import (
    AuthenticationError,
    NoAvailableDeviceError,
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServerConnectionError,
    ServerResponseError,
)
from .models import AuthenticatedUser, DeviceInfo, LeaseGrant, LeaseInfo

if TYPE_CHECKING:
    from .grpc_client import AsyncContractDeviceClient, GrpcTlsConfig


API_PREFIX = "/api/v1"


def _validate_central_url(server_url: str) -> str:
    if not server_url:
        raise ValueError("server_url cannot be empty.")
    parsed = urlsplit(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("server_url must be an absolute HTTP or HTTPS URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("server_url cannot contain credentials, a query, or a fragment.")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ValueError("Plaintext Central HTTP is allowed only on loopback.")
    return server_url.rstrip("/")


def _is_loopback(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_duration(duration_seconds: int) -> int:
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int):
        raise TypeError("duration_seconds must be an integer.")
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be greater than zero.")
    return duration_seconds


def _validate_timeout(timeout_seconds: float) -> float:
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be finite and greater than zero.")
    return timeout


def _validate_renewal_policy(
    duration_seconds: int,
    renewal_interval_seconds: float,
) -> tuple[int, float]:
    duration = _validate_duration(duration_seconds)
    interval = float(renewal_interval_seconds)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError(
            "renewal_interval_seconds must be finite and greater than zero."
        )
    if interval >= duration:
        raise ValueError(
            "renewal_interval_seconds must be less than duration_seconds."
        )
    return duration, interval


def _idempotency_key(value: str | None) -> str:
    key = str(uuid.uuid4()) if value is None else value.strip()
    if (
        not key
        or len(key) > 128
        or any(not 0x21 <= ord(character) <= 0x7E for character in key)
    ):
        raise ValueError("idempotency_key must be 1-128 visible ASCII characters.")
    return key


def _resource_path(resource: str) -> str:
    value = resource.strip()
    if not value:
        raise ValueError("resource identifier cannot be empty.")
    return quote(value, safe="")


def _response_json(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise ServerResponseError(
            response.status_code, "Server returned invalid JSON."
        ) from exc


def _error_detail(response: httpx.Response) -> tuple[str, str | None]:
    try:
        data = response.json()
    except ValueError:
        return response.text or "Unknown server error.", None
    if not isinstance(data, Mapping):
        return str(data), None
    detail = data.get("detail", data.get("error"))
    if isinstance(detail, Mapping):
        message = detail.get("message", detail.get("detail", str(detail)))
        code = detail.get("code")
        return str(message), str(code) if code is not None else None
    if detail is not None:
        return str(detail), None
    return f"Request failed with HTTP {response.status_code}.", None


def _raise_http_error(response: httpx.Response) -> None:
    message, error_code = _error_detail(response)
    if response.status_code == 401:
        raise AuthenticationError(message, error_code=error_code)
    if response.status_code == 403:
        raise PermissionDeniedError(message, error_code=error_code)
    if response.status_code == 404:
        raise ResourceNotFoundError(message, error_code=error_code)
    if response.status_code == 409:
        raise ResourceConflictError(message, error_code=error_code)
    raise ServerResponseError(response.status_code, message, error_code=error_code)


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ServerResponseError(200, f"Central returned an invalid {description}.")
    return value


def _parse_devices(data: Any) -> list[DeviceInfo]:
    payload = _mapping(data, "device-list response")
    records = payload.get("devices")
    if not isinstance(records, list):
        raise ServerResponseError(200, "Central device list is missing 'devices'.")
    try:
        return [DeviceInfo.from_dict(_mapping(record, "device record")) for record in records]
    except (KeyError, TypeError, ValueError) as exc:
        raise ServerResponseError(200, f"Central returned an invalid device record: {exc}") from exc


def _parse_device(data: Any) -> DeviceInfo:
    payload = _mapping(data, "device response")
    record = payload.get("device", payload)
    try:
        return DeviceInfo.from_dict(_mapping(record, "device record"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ServerResponseError(200, f"Central returned an invalid device record: {exc}") from exc


def _parse_grant(data: Any) -> LeaseGrant:
    try:
        return LeaseGrant.from_dict(_mapping(data, "lease response"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ServerResponseError(200, f"Central returned an invalid lease grant: {exc}") from exc


def _validate_granted_device_type(
    device: DeviceInfo,
    expected_device_type: str,
) -> None:
    """Reject a lease grant that does not match its generated device contract."""

    if device.device_type == expected_device_type:
        return
    raise ValueError(
        f"LeaseGrant device_type {device.device_type!r} does not match "
        f"requested contract device_type {expected_device_type!r}."
    )


def _parse_lease(data: Any) -> LeaseInfo:
    payload = _mapping(data, "lease response")
    record = payload.get("lease", payload)
    try:
        return LeaseInfo.from_dict(_mapping(record, "lease record"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ServerResponseError(200, f"Central returned an invalid lease record: {exc}") from exc


def _trust_from_ca_file(
    ca_file: str | Path | None,
) -> tuple[ssl.SSLContext | bool, "GrpcTlsConfig | None"]:
    """Build HTTPS verification and gRPC trust from one public CA file."""

    if ca_file is None:
        return True, None
    from .grpc_client import GrpcTlsConfig

    path = Path(ca_file).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"CA file not found: {path}")
    return (
        ssl.create_default_context(cafile=str(path)),
        GrpcTlsConfig.from_files(root_certificates=path),
    )


def _client_settings_from_env(
    env_file: str | Path | None,
) -> tuple[str, str, str | None]:
    # Clients have no command line, so LABNET_ENV_FILE plays --env-file's role.
    load_env_file(env_file or os.getenv("LABNET_ENV_FILE") or None, search_cwd=True)
    if os.getenv("LABNET_GRPC_CA_FILE"):
        raise ValueError(
            "LABNET_GRPC_CA_FILE was removed in LabNet 2.0. Set LABNET_CA_FILE; "
            "it verifies both Central and the Lab PCs."
        )
    api_key = os.getenv("LABNET_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "LABNET_API_KEY is not set. Put your Central API key in the "
            "environment or a .env file (see `labnet-central-admin setup`)."
        )
    server_url = os.getenv("LABNET_SERVER_URL", "").strip() or "http://127.0.0.1:8008"
    ca_file = os.getenv("LABNET_CA_FILE", "").strip() or None
    return server_url, api_key, ca_file


class LabClient:
    """Blocking Central client for scripts and synchronous device wrappers."""

    def __init__(
        self,
        server_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        authenticate_on_start: bool = True,
        *,
        ca_file: str | Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.server_url = _validate_central_url(server_url)
        if not api_key:
            raise ValueError("api_key cannot be empty.")
        self.api_key = api_key
        # One CA file verifies both Central (HTTPS) and every Lab PC (gRPC).
        verify, self.grpc_tls = _trust_from_ca_file(ca_file)
        timeout_seconds = _validate_timeout(timeout_seconds)
        self._request_timeout_seconds = timeout_seconds
        self.current_user: AuthenticatedUser | None = None
        self._http = httpx.Client(
            base_url=self.server_url,
            timeout=timeout_seconds,
            transport=transport,
            verify=verify,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        if authenticate_on_start:
            try:
                self.authenticate()
            except BaseException as exc:
                try:
                    self._http.close()
                except BaseException as cleanup_exc:
                    exc.add_note(
                        "Closing the HTTP client after authentication failure also "
                        f"failed: {cleanup_exc}"
                    )
                raise


    @classmethod
    def from_env(cls, env_file: str | Path | None = None, **options: Any) -> "LabClient":
        """Connect using LABNET_SERVER_URL, LABNET_API_KEY, and LABNET_CA_FILE.

        Values come from the process environment, then from ``env_file``,
        the file named by ``LABNET_ENV_FILE``, or ``./.env``, in that order.
        """

        server_url, api_key, ca_file = _client_settings_from_env(env_file)
        return cls(server_url, api_key, ca_file=ca_file, **options)

    def authenticate(self) -> AuthenticatedUser:
        data = _mapping(self._request("GET", f"{API_PREFIX}/auth/me"), "auth response")
        try:
            user = AuthenticatedUser.from_dict(data)
        except ValueError as exc:
            raise ServerResponseError(200, f"Central returned an invalid user: {exc}") from exc
        self.current_user = user
        return user

    def health(self) -> dict[str, Any]:
        """Return Central readiness (compatibility alias for ``health_ready``)."""

        return self.health_ready()

    def health_live(self) -> dict[str, Any]:
        return dict(
            _mapping(self._request("GET", "/health/live"), "liveness response")
        )

    def health_ready(self) -> dict[str, Any]:
        return dict(
            _mapping(self._request("GET", "/health/ready"), "readiness response")
        )

    def list_devices(
        self,
        device_type: str | None = None,
        *,
        runtime_state: str | None = None,
        availability: str | None = None,
        effective_available: bool | None = None,
    ) -> list[DeviceInfo]:
        params: dict[str, str] = {}
        if device_type is not None:
            params["device_type"] = device_type
        if runtime_state is not None:
            params["runtime_state"] = runtime_state
        if availability is not None:
            params["availability"] = availability
        if effective_available is not None:
            params["effective_available"] = str(effective_available).lower()
        return _parse_devices(
            self._request("GET", f"{API_PREFIX}/devices", params=params)
        )

    def list_available_devices(self, device_type: str | None = None) -> list[DeviceInfo]:
        return [
            device
            for device in self.list_devices(device_type, availability="available")
            if device.effective_available
        ]

    def get_device(self, device_id: str) -> DeviceInfo:
        return _parse_device(
            self._request(
                "GET", f"{API_PREFIX}/devices/{_resource_path(device_id)}"
            )
        )

    def request_lease(
        self,
        device_id: str,
        duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        *,
        idempotency_key: str | None = None,
    ) -> LeaseGrant:
        return _parse_grant(
            self._request(
                "POST",
                f"{API_PREFIX}/leases",
                json={
                    "device_id": device_id,
                    "duration_seconds": _validate_duration(duration_seconds),
                },
                headers={"Idempotency-Key": _idempotency_key(idempotency_key)},
            )
        )

    def renew_lease(self, lease_id: str, duration_seconds: int) -> LeaseInfo:
        return _parse_lease(
            self._request(
                "POST",
                f"{API_PREFIX}/leases/{_resource_path(lease_id)}/renew",
                json={"duration_seconds": _validate_duration(duration_seconds)},
            )
        )

    def release_lease(self, lease_id: str) -> None:
        self._request(
            "DELETE", f"{API_PREFIX}/leases/{_resource_path(lease_id)}"
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "LabClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = self._http.request(
                method, path, json=json, params=params, headers=headers
            )
        except httpx.RequestError as exc:
            raise ServerConnectionError(
                f"Could not connect to Central Server at {self.server_url}: {exc}"
            ) from exc
        if not response.is_success:
            _raise_http_error(response)
        return _response_json(response)


class AsyncLabClient:
    """Async-first Central client with managed lease/device context cleanup."""

    def __init__(
        self,
        server_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        authenticate_on_start: bool = True,
        *,
        ca_file: str | Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.server_url = _validate_central_url(server_url)
        if not api_key:
            raise ValueError("api_key cannot be empty.")
        self.api_key = api_key
        # One CA file verifies both Central (HTTPS) and every Lab PC (gRPC).
        verify, self.grpc_tls = _trust_from_ca_file(ca_file)
        timeout_seconds = _validate_timeout(timeout_seconds)
        self.current_user: AuthenticatedUser | None = None
        self._authenticate_on_start = authenticate_on_start
        self._http = httpx.AsyncClient(
            base_url=self.server_url,
            timeout=timeout_seconds,
            transport=transport,
            verify=verify,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        self._devices: set[Any] = set()
        self._closed = False
        self._closing = False
        self._active_acquires = 0
        self._lifecycle_condition = asyncio.Condition()
        self._close_lock = asyncio.Lock()

    @classmethod
    def from_env(cls, env_file: str | Path | None = None, **options: Any) -> "AsyncLabClient":
        """Connect using LABNET_SERVER_URL, LABNET_API_KEY, and LABNET_CA_FILE.

        Values come from the process environment, then from ``env_file``,
        the file named by ``LABNET_ENV_FILE``, or ``./.env``, in that order.
        """

        server_url, api_key, ca_file = _client_settings_from_env(env_file)
        return cls(server_url, api_key, ca_file=ca_file, **options)

    async def authenticate(self) -> AuthenticatedUser:
        data = _mapping(
            await self._request("GET", f"{API_PREFIX}/auth/me"), "auth response"
        )
        try:
            user = AuthenticatedUser.from_dict(data)
        except ValueError as exc:
            raise ServerResponseError(200, f"Central returned an invalid user: {exc}") from exc
        self.current_user = user
        return user

    async def health(self) -> dict[str, Any]:
        """Return Central readiness (compatibility alias for ``health_ready``)."""

        return await self.health_ready()

    async def health_live(self) -> dict[str, Any]:
        return dict(
            _mapping(
                await self._request("GET", "/health/live"), "liveness response"
            )
        )

    async def health_ready(self) -> dict[str, Any]:
        return dict(
            _mapping(
                await self._request("GET", "/health/ready"), "readiness response"
            )
        )

    async def list_devices(
        self,
        device_type: str | None = None,
        *,
        runtime_state: str | None = None,
        availability: str | None = None,
        effective_available: bool | None = None,
    ) -> list[DeviceInfo]:
        params: dict[str, str] = {}
        if device_type is not None:
            params["device_type"] = device_type
        if runtime_state is not None:
            params["runtime_state"] = runtime_state
        if availability is not None:
            params["availability"] = availability
        if effective_available is not None:
            params["effective_available"] = str(effective_available).lower()
        return _parse_devices(
            await self._request("GET", f"{API_PREFIX}/devices", params=params)
        )

    async def list_available_devices(
        self, device_type: str | None = None
    ) -> list[DeviceInfo]:
        return [
            device
            for device in await self.list_devices(
                device_type, availability="available"
            )
            if device.effective_available
        ]

    async def get_device(self, device_id: str) -> DeviceInfo:
        return _parse_device(
            await self._request(
                "GET", f"{API_PREFIX}/devices/{_resource_path(device_id)}"
            )
        )

    async def request_lease(
        self,
        device_id: str,
        duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        *,
        idempotency_key: str | None = None,
    ) -> LeaseGrant:
        return _parse_grant(
            await self._request(
                "POST",
                f"{API_PREFIX}/leases",
                json={
                    "device_id": device_id,
                    "duration_seconds": _validate_duration(duration_seconds),
                },
                headers={"Idempotency-Key": _idempotency_key(idempotency_key)},
            )
        )

    async def renew_lease(self, lease_id: str, duration_seconds: int) -> LeaseInfo:
        return _parse_lease(
            await self._request(
                "POST",
                f"{API_PREFIX}/leases/{_resource_path(lease_id)}/renew",
                json={"duration_seconds": _validate_duration(duration_seconds)},
            )
        )

    async def release_lease(self, lease_id: str) -> None:
        await self._request(
            "DELETE", f"{API_PREFIX}/leases/{_resource_path(lease_id)}"
        )

    async def acquire_dummy(
        self,
        device_id: str | None = None,
        *,
        duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        auto_renew: bool = True,
        renewal_interval_seconds: float = DEFAULT_RENEWAL_INTERVAL_SECONDS,
        idempotency_key: str | None = None,
        tls: GrpcTlsConfig | None = None,
        connection_timeout_seconds: float = 10.0,
        default_rpc_timeout_seconds: float = 10.0,
    ) -> AsyncContractDeviceClient:
        """Lease and connect a typed async DummyDevice in one safe operation."""

        from labnet.grpc.generated.device_contracts import DUMMY_DEVICE_CONTRACT

        return await self.acquire_device(
            DUMMY_DEVICE_CONTRACT,
            device_id,
            duration_seconds=duration_seconds,
            auto_renew=auto_renew,
            renewal_interval_seconds=renewal_interval_seconds,
            idempotency_key=idempotency_key,
            tls=tls,
            connection_timeout_seconds=connection_timeout_seconds,
            default_rpc_timeout_seconds=default_rpc_timeout_seconds,
        )

    async def acquire_device(
        self,
        contract: Any,
        device_id: str | None = None,
        *,
        duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        auto_renew: bool = True,
        renewal_interval_seconds: float = DEFAULT_RENEWAL_INTERVAL_SECONDS,
        idempotency_key: str | None = None,
        tls: GrpcTlsConfig | None = None,
        connection_timeout_seconds: float = 10.0,
        default_rpc_timeout_seconds: float = 10.0,
    ) -> AsyncContractDeviceClient:
        """Lease any generated device contract using shared async plumbing."""

        from labnet.grpc.generated.device_contracts import DeviceContract
        from .grpc_client import AsyncContractDeviceClient

        if not isinstance(contract, DeviceContract):
            raise TypeError("contract must be a generated DeviceContract.")

        acquire_started = False
        try:
            await self._begin_acquire()
            acquire_started = True
            if not isinstance(auto_renew, bool):
                raise TypeError("auto_renew must be a boolean.")
            if auto_renew:
                duration_seconds, renewal_interval_seconds = _validate_renewal_policy(
                    duration_seconds, renewal_interval_seconds
                )
            else:
                duration_seconds = _validate_duration(duration_seconds)

            if device_id is None:
                available = await self.list_available_devices(contract.device_type)
                if not available:
                    raise NoAvailableDeviceError(
                        f"No available {contract.device_type} was found."
                    )
                device_id = available[0].device_id
            grant = await self.request_lease(
                device_id,
                duration_seconds,
                idempotency_key=idempotency_key,
            )
            try:
                _validate_granted_device_type(
                    grant.device,
                    contract.device_type,
                )
                device = AsyncContractDeviceClient(
                    grant,
                    central=self,
                    lease_duration_seconds=duration_seconds,
                    auto_renew=auto_renew,
                    renewal_interval_seconds=renewal_interval_seconds,
                    tls=tls if tls is not None else self.grpc_tls,
                    default_timeout_seconds=default_rpc_timeout_seconds,
                    _contract=contract,
                )
            except BaseException as exc:
                try:
                    await self.release_lease(grant.lease.lease_id)
                except BaseException as cleanup_exc:
                    exc.add_note(
                        "Lease rollback also failed after device-client construction: "
                        f"{cleanup_exc}"
                    )
                raise
            self._devices.add(device)
            try:
                await device.connect(connection_timeout_seconds)
            except BaseException:
                try:
                    await device.aclose()
                except Exception:
                    pass
                raise
            device._start_auto_renewal()
            return device
        finally:
            if acquire_started:
                await asyncio.shield(self._end_acquire())

    async def _begin_acquire(self) -> None:
        async with self._lifecycle_condition:
            if self._closing or self._closed:
                raise RuntimeError("The async LabClient is closing or closed.")
            self._active_acquires += 1

    async def _end_acquire(self) -> None:
        async with self._lifecycle_condition:
            if self._active_acquires > 0:
                self._active_acquires -= 1
                if self._active_acquires == 0:
                    self._lifecycle_condition.notify_all()

    def _discard_device(self, device: object) -> None:
        self._devices.discard(device)

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            async with self._lifecycle_condition:
                self._closing = True
                await self._lifecycle_condition.wait_for(
                    lambda: self._active_acquires == 0
                )
            first_error: BaseException | None = None
            for device in list(self._devices):
                try:
                    await device.aclose()
                except BaseException as exc:
                    first_error = first_error or exc
            if first_error is not None:
                # Keep the Central HTTP transport open so a later aclose()
                # can retry any lease release that failed transiently.
                raise first_error
            await self._http.aclose()
            self._closed = True

    async def __aenter__(self) -> "AsyncLabClient":
        if self._authenticate_on_start and self.current_user is None:
            try:
                await self.authenticate()
            except BaseException as exc:
                try:
                    await self.aclose()
                except BaseException as cleanup_exc:
                    exc.add_note(
                        "Closing the HTTP client after authentication failure also "
                        f"failed: {cleanup_exc}"
                    )
                raise
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        try:
            await self.aclose()
        except Exception:
            if exc_type is None:
                raise

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if self._closed:
            raise RuntimeError("The async LabClient is closed.")
        try:
            response = await self._http.request(
                method, path, json=json, params=params, headers=headers
            )
        except httpx.RequestError as exc:
            raise ServerConnectionError(
                f"Could not connect to Central Server at {self.server_url}: {exc}"
            ) from exc
        if not response.is_success:
            _raise_http_error(response)
        return _response_json(response)


__all__ = ["API_PREFIX", "AsyncLabClient", "LabClient"]
