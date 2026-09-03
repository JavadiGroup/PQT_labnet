"""Shared synchronous Central-lease + gRPC remote device binding."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
import threading
from typing import Any

from ...client.core.exceptions import (
    AuthenticationError,
    LeaseAuthorityError,
    NoAvailableDeviceError,
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServerResponseError,
)
from ...client.core.grpc_client import ContractDeviceGrpcClient, GrpcTlsConfig
from ...client.core.lab_client import (
    LabClient,
    _validate_granted_device_type,
    _validate_renewal_policy,
)
from ...client.core.models import Measurement
from ...grpc.generated.device_contracts import (
    DUMMY_DEVICE_CONTRACT,
    DeviceContract,
    RpcSpec,
)
from ...lease_policy import (
    DEFAULT_LEASE_DURATION_SECONDS,
    DEFAULT_RENEWAL_INTERVAL_SECONDS,
)


_SCALAR_READ = "RPC_PATTERN_SCALAR_READ"
_SCALAR_WRITE = "RPC_PATTERN_SCALAR_WRITE"
_UNARY_COMMAND = "RPC_PATTERN_UNARY_COMMAND"
_POLLED_SCALAR_STREAM = "RPC_PATTERN_POLLED_SCALAR_STREAM"


def _contract_method(contract: DeviceContract, name: str) -> RpcSpec | None:
    return next(
        (method for method in contract.methods if method.python_name == name),
        None,
    )


def _require_pattern(
    contract: DeviceContract,
    name: str,
    expected_pattern: str,
) -> RpcSpec:
    spec = _contract_method(contract, name)
    if spec is None:
        raise AttributeError(
            f"Contract {contract.device_type!r} does not define RPC method "
            f"{name!r}."
        )
    if spec.pattern != expected_pattern:
        raise RuntimeError(
            f"Contract RPC {spec.rpc_full_name} now uses {spec.pattern}; the "
            f"{name!r} convenience API requires {expected_pattern}."
        )
    return spec


def _dispatch_kind(spec: RpcSpec) -> str:
    if spec.pattern in {_SCALAR_READ, _SCALAR_WRITE, _UNARY_COMMAND}:
        if spec.client_streaming or spec.server_streaming:
            raise RuntimeError(
                f"Generated RPC {spec.rpc_full_name} has incompatible "
                f"{spec.cardinality} cardinality for {spec.pattern}."
            )
        return "unary"
    if spec.pattern == _POLLED_SCALAR_STREAM:
        if spec.client_streaming or not spec.server_streaming:
            raise RuntimeError(
                f"Generated RPC {spec.rpc_full_name} has incompatible "
                f"{spec.cardinality} cardinality for {spec.pattern}."
            )
        return "stream"
    raise RuntimeError(
        f"Generated RPC {spec.rpc_full_name} uses unsupported remote pattern "
        f"{spec.pattern}."
    )


class RemoteDeviceBinding:
    """Own one Central lease and one contract-driven synchronous gRPC channel."""

    device_type = "unknown"

    def __init__(
        self,
        client: LabClient,
        lease_duration: int = DEFAULT_LEASE_DURATION_SECONDS,
        device_id: str | None = None,
        *,
        auto_renew: bool = True,
        renewal_interval_seconds: float = DEFAULT_RENEWAL_INTERVAL_SECONDS,
        tls: GrpcTlsConfig | None = None,
        connection_timeout_seconds: float = 10.0,
        default_rpc_timeout_seconds: float = 10.0,
        idempotency_key: str | None = None,
        _contract: DeviceContract = DUMMY_DEVICE_CONTRACT,
    ) -> None:
        self._contract = _contract
        self.device_type = _contract.device_type
        self.client = client
        self._channel_closed = False
        self._lease_released = False
        self._closing = False
        self._renewal_stop = threading.Event()
        self._renewal_thread: threading.Thread | None = None
        self._lease_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._authority_error: LeaseAuthorityError | None = None
        if not isinstance(auto_renew, bool):
            raise TypeError("auto_renew must be a boolean.")
        if auto_renew:
            lease_duration, renewal_interval_seconds = _validate_renewal_policy(
                lease_duration, renewal_interval_seconds
            )

        if device_id is None:
            available = client.list_available_devices(device_type=self.device_type)
            if not available:
                raise NoAvailableDeviceError(
                    f"No available {self.device_type} is available."
                )
            device_id = available[0].device_id

        grant = client.request_lease(
            device_id=device_id,
            duration_seconds=lease_duration,
            idempotency_key=idempotency_key,
        )
        try:
            _validate_granted_device_type(grant.device, self.device_type)
        except BaseException as exc:
            # This binding performed the acquisition, so it also owns rollback
            # when Central returns a device from the wrong contract family.
            try:
                client.release_lease(grant.lease.lease_id)
            except BaseException as cleanup_exc:
                exc.add_note(
                    "Releasing the mismatched device lease also failed: "
                    f"{cleanup_exc}"
                )
            raise
        self.lease = grant.lease
        self.device = grant.device
        try:
            self.grpc = ContractDeviceGrpcClient(
                grant,
                tls=tls,
                default_timeout_seconds=default_rpc_timeout_seconds,
                _contract=_contract,
            )
            self.grpc.connect(connection_timeout_seconds)
        except BaseException as exc:
            grpc_client = getattr(self, "grpc", None)
            if grpc_client is not None:
                try:
                    grpc_client.close()
                    self._channel_closed = True
                except BaseException as cleanup_exc:
                    exc.add_note(
                        "Closing the gRPC channel after setup failure also failed: "
                        f"{cleanup_exc}"
                    )
            try:
                self.client.release_lease(self.lease.lease_id)
                self._lease_released = True
            except BaseException as cleanup_exc:
                exc.add_note(
                    "Lease rollback also failed after device-client setup: "
                    f"{cleanup_exc}"
                )
            raise
        if auto_renew:
            self._lease_duration = lease_duration
            self._renewal_interval = renewal_interval_seconds
            self._renewal_thread = threading.Thread(
                target=self._renewal_worker,
                name=f"labnet-lease-renew-{self.lease.lease_id}",
                daemon=True,
            )
            self._renewal_thread.start()

    @property
    def device_id(self) -> str:
        return self.device.device_id

    @property
    def lease_id(self) -> str:
        return self.lease.lease_id

    @property
    def lease_expired(self) -> bool:
        with self._lease_lock:
            return self.lease.expired

    @property
    def closed(self) -> bool:
        return self._channel_closed and self._lease_released

    def connect(self, timeout_seconds: float | None = None) -> None:
        self._ensure_authority()
        self.grpc.connect(timeout_seconds)

    def metadata(self) -> dict[str, Any]:
        return dict(self.device.metadata)

    def read_measurement(self, *args: Any, **kwargs: Any) -> Measurement:
        spec = _require_pattern(self._contract, "read_value", _SCALAR_READ)
        self._ensure_authority()
        return getattr(self.grpc, spec.python_name)(*args, **kwargs)

    def write_measurement(self, *args: Any, **kwargs: Any) -> Measurement:
        spec = _require_pattern(self._contract, "write_value", _SCALAR_WRITE)
        self._ensure_authority()
        return getattr(self.grpc, spec.python_name)(*args, **kwargs)

    def close(self) -> None:
        with self._close_lock:
            self._closing = True
            first_error: BaseException | None = None
            renewal_stopped = True
            try:
                self._stop_renewal()
            except BaseException as exc:
                first_error = exc
                renewal_stopped = False
            if not self._channel_closed:
                try:
                    self.grpc.close()
                except BaseException as exc:
                    first_error = first_error or exc
                finally:
                    self._channel_closed = True
            if self._lease_released:
                if first_error is not None:
                    raise first_error
                return
            # Never race a DELETE against an in-flight renewal. If the bounded
            # join failed, Central will reclaim authority at its deadline.
            if renewal_stopped:
                try:
                    self.client.release_lease(self.lease.lease_id)
                except ResourceNotFoundError:
                    self._lease_released = True
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                    else:
                        first_error.add_note(f"Releasing the lease also failed: {exc}")
                else:
                    self._lease_released = True
            if first_error is not None:
                raise first_error

    def _stop_renewal(self) -> None:
        self._renewal_stop.set()
        thread = self._renewal_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.client._request_timeout_seconds + 1.0)
            if thread.is_alive():
                raise RuntimeError(
                    "Timed out waiting for the lease-renewal worker; the gRPC "
                    "transport was closed and Central will expire the lease."
                )
        self._renewal_thread = None

    def _renewal_worker(self) -> None:
        wait_seconds = self._renewal_interval
        while True:
            with self._lease_lock:
                remaining = (
                    self.lease.expires_at - datetime.now(timezone.utc)
                ).total_seconds()
            if remaining <= 0:
                self._mark_authority_lost(
                    LeaseAuthorityError(
                        f"Lease {self.lease.lease_id!r} reached its authority deadline."
                    )
                )
                return
            if self._renewal_stop.wait(min(wait_seconds, remaining)):
                return
            if self.lease_expired:
                self._mark_authority_lost(
                    LeaseAuthorityError(
                        f"Lease {self.lease.lease_id!r} reached its authority deadline."
                    )
                )
                return
            try:
                renewed = self.client.renew_lease(
                    self.lease.lease_id, self._lease_duration
                )
                if renewed.state != "active" or renewed.expired:
                    raise LeaseAuthorityError(
                        f"Lease {renewed.lease_id!r} is no longer active."
                    )
            except BaseException as exc:
                if self._renewal_stop.is_set():
                    return
                if self._renewal_is_terminal(exc) or self.lease_expired:
                    self._mark_authority_lost(exc)
                    return
                with self._lease_lock:
                    remaining = (
                        self.lease.expires_at - datetime.now(timezone.utc)
                    ).total_seconds()
                wait_seconds = min(
                    self._renewal_interval, max(0.001, remaining / 2)
                )
                continue
            with self._lease_lock:
                self.lease = renewed
            wait_seconds = self._renewal_interval

    @staticmethod
    def _renewal_is_terminal(exc: BaseException) -> bool:
        if isinstance(
            exc,
            (
                LeaseAuthorityError,
                AuthenticationError,
                PermissionDeniedError,
                ResourceConflictError,
                ResourceNotFoundError,
            ),
        ):
            return True
        return (
            isinstance(exc, ServerResponseError)
            and 400 <= exc.status_code < 500
            and exc.status_code not in {408, 429}
        )

    def _mark_authority_lost(self, cause: BaseException) -> None:
        error = (
            cause
            if isinstance(cause, LeaseAuthorityError)
            else LeaseAuthorityError(
                f"Automatic renewal failed and lease {self.lease.lease_id!r} "
                f"can no longer be used: {cause}"
            )
        )
        self._authority_error = error
        self._closing = True
        try:
            self.grpc.close()
        finally:
            self._channel_closed = True

    def _ensure_authority(self) -> None:
        if self._authority_error is not None:
            raise self._authority_error
        if self._closing or self._channel_closed:
            raise RuntimeError("The remote device binding is closing or closed.")
        if self.lease_expired:
            error = LeaseAuthorityError(
                f"Lease {self.lease.lease_id!r} expired before it could be renewed."
            )
            self._mark_authority_lost(error)
            raise error

    def __getattr__(self, name: str) -> Any:
        """Expose newly generated RPCs without a handwritten remote method."""

        grpc_client = self.__dict__.get("grpc")
        contract = self.__dict__.get("_contract")
        if grpc_client is None or contract is None:
            raise AttributeError(name)
        spec = _contract_method(contract, name)
        if spec is None:
            raise AttributeError(name)
        dispatch_kind = _dispatch_kind(spec)
        if spec.pattern == _SCALAR_READ:
            def scalar_read(*args: Any, **kwargs: Any) -> float:
                self._ensure_authority()
                return getattr(grpc_client, name)(*args, **kwargs).value

            return scalar_read
        if spec.pattern == _SCALAR_WRITE:
            def scalar_write(*args: Any, **kwargs: Any) -> None:
                self._ensure_authority()
                getattr(grpc_client, name)(*args, **kwargs)

            return scalar_write
        if spec.pattern == _UNARY_COMMAND:
            def unary_command(*args: Any, **kwargs: Any) -> Any:
                self._ensure_authority()
                return getattr(grpc_client, name)(*args, **kwargs)

            return unary_command
        if dispatch_kind == "stream":
            def scalar_stream(*args: Any, **kwargs: Any) -> Iterator[Measurement]:
                self._ensure_authority()
                return getattr(grpc_client, name)(*args, **kwargs)

            return scalar_stream
        raise AttributeError(name)

    def __enter__(self) -> "RemoteDeviceBinding":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise


__all__ = ["RemoteDeviceBinding"]
