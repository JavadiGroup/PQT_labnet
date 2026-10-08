"""Generated-contract-driven local-or-remote device facades."""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from typing import Any, ClassVar

from labnet.client.core.grpc_client import GrpcTlsConfig
from labnet.client.core.lab_client import LabClient
from labnet.client.core.models import Measurement, MeasurementQuality
from labnet.contracts import DeviceContract, RpcSpec
from labnet.lease_policy import (
    DEFAULT_LEASE_DURATION_SECONDS,
    DEFAULT_RENEWAL_INTERVAL_SECONDS,
)


class ContractDeviceFacade:
    """Use one public class for either local hardware or a remote lease.

    Family modules provide only two class attributes: their generated
    ``contract`` and their physical driver type. All API methods are resolved
    from the generated RpcSpecs, so changing the proto and regenerating changes
    both the local and remote public surface without another adapter file.
    """

    contract: ClassVar[DeviceContract]
    physical_type: ClassVar[Callable[..., Any]]
    device_type: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        contract = getattr(cls, "contract", None)
        if contract is not None:
            cls.device_type = contract.device_type

    def __init__(
        self,
        device_id: str | None = None,
        *,
        client: LabClient | None = None,
        lease_duration: int = DEFAULT_LEASE_DURATION_SECONDS,
        auto_renew: bool = True,
        renewal_interval_seconds: float = DEFAULT_RENEWAL_INTERVAL_SECONDS,
        tls: GrpcTlsConfig | None = None,
        connection_timeout_seconds: float = 10.0,
        default_rpc_timeout_seconds: float = 10.0,
        idempotency_key: str | None = None,
        physical_options: Mapping[str, Any] | None = None,
    ) -> None:
        self._is_remote = client is not None
        self._closed = False
        if client is None:
            options = dict(physical_options or {})
            if "device_id" in options:
                raise ValueError(
                    "Pass device_id directly, not inside physical_options."
                )
            if device_id is not None:
                options["device_id"] = device_id
            backend = self.physical_type(**options)
            try:
                backend.connect()
            except BaseException as exc:
                try:
                    backend.disconnect()
                except BaseException as cleanup_exc:
                    exc.add_note(
                        "Disconnecting the local physical device after its "
                        f"connection failed also failed: {cleanup_exc}"
                    )
                raise
        else:
            if physical_options:
                raise ValueError(
                    "physical_options are valid only for a local device."
                )
            # Lease lifecycle is shared, contract-driven framework code and is
            # not edited when a device family gains or changes RPC methods.
            from labnet.devices.core.remote import RemoteDeviceBinding

            backend = RemoteDeviceBinding(
                client=client,
                lease_duration=lease_duration,
                device_id=device_id,
                auto_renew=auto_renew,
                renewal_interval_seconds=renewal_interval_seconds,
                tls=tls,
                connection_timeout_seconds=connection_timeout_seconds,
                default_rpc_timeout_seconds=default_rpc_timeout_seconds,
                idempotency_key=idempotency_key,
                _contract=self.contract,
            )
        self._backend = backend

    @property
    def is_remote(self) -> bool:
        return self._is_remote

    @property
    def device_id(self) -> str:
        return self._backend.device_id

    def metadata(self) -> dict[str, Any]:
        metadata = dict(self._backend.metadata())
        metadata["capabilities"] = [
            method.python_name for method in self.contract.methods
        ]
        return metadata

    def connect(self, timeout_seconds: float | None = None) -> None:
        if self._is_remote:
            self._backend.connect(timeout_seconds)
        else:
            self._backend.connect()
        self._closed = False

    def disconnect(self) -> None:
        self.close()

    def status(self) -> str:
        if self._is_remote:
            if self._backend._channel_closed:
                return "disconnected"
            return self._backend.device.runtime_state
        return self._backend.status()

    def allowed_methods(self) -> list[str]:
        return [method.python_name for method in self.contract.methods]

    def close(self) -> None:
        if self._closed:
            return
        if self._is_remote:
            self._backend.close()
        else:
            self._backend.disconnect()
        self._closed = True

    def __getattr__(self, name: str) -> Any:
        """Resolve the public RPC name using the generated family contract."""

        spec = next(
            (method for method in self.contract.methods if method.python_name == name),
            None,
        )
        if spec is None:
            raise AttributeError(
                f"{type(self).__name__!s} has no RPC method {name!r}."
            )
        if self._closed:
            raise RuntimeError(f"{type(self).__name__} is closed.")
        if self._is_remote:
            return getattr(self._backend, name)
        if spec.pattern == "RPC_PATTERN_POLLED_SCALAR_STREAM":
            return self._local_stream_method(spec)
        return self._local_unary_method(spec)

    def _local_unary_method(self, spec: RpcSpec) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            forwarded = _bind_physical_arguments(spec, args, kwargs)
            if spec.pattern == "RPC_PATTERN_SCALAR_WRITE":
                unit = kwargs.pop("unit", None)
                if unit is not None and str(unit).strip() != spec.default_unit:
                    raise ValueError(
                        f"unit must be {spec.default_unit!r}; automatic unit "
                        "conversion is not enabled."
                    )
            _discard_local_transport_options(kwargs)
            if kwargs:
                unexpected = next(iter(kwargs))
                raise TypeError(
                    f"{spec.python_name}() got an unexpected keyword argument "
                    f"{unexpected!r}."
                )
            physical = getattr(self._backend, spec.physical_method)
            return physical(**forwarded)

        invoke.__name__ = spec.python_name
        return invoke

    def _local_stream_method(
        self,
        spec: RpcSpec,
    ) -> Callable[..., Iterator[Measurement]]:
        def stream(*args: Any, **kwargs: Any) -> Iterator[Measurement]:
            forwarded = _bind_physical_arguments(spec, args, kwargs)
            try:
                sample_rate_hz = _positive_finite(
                    kwargs.pop("sample_rate_hz"), "sample_rate_hz"
                )
            except KeyError as exc:
                raise TypeError(
                    f"{spec.python_name}() is missing required argument "
                    "'sample_rate_hz'."
                ) from exc
            sample_count = kwargs.pop("sample_count", None)
            if sample_count is not None and (
                isinstance(sample_count, bool)
                or not isinstance(sample_count, int)
                or sample_count <= 0
            ):
                raise ValueError("sample_count must be a positive integer.")
            duration_seconds = kwargs.pop("duration_seconds", None)
            if duration_seconds is not None:
                duration_seconds = _positive_finite(
                    duration_seconds, "duration_seconds"
                )
            operation_id = kwargs.pop("operation_id", None) or str(uuid.uuid4())
            kwargs.pop("timeout_seconds", None)
            if kwargs:
                unexpected = next(iter(kwargs))
                raise TypeError(
                    f"{spec.python_name}() got an unexpected keyword argument "
                    f"{unexpected!r}."
                )
            return _poll_local_scalar(
                physical=getattr(self._backend, spec.physical_method),
                forwarded=forwarded,
                sample_rate_hz=sample_rate_hz,
                sample_count=sample_count,
                duration_seconds=duration_seconds,
                operation_id=operation_id,
                default_unit=spec.default_unit,
            )

        stream.__name__ = spec.python_name
        return stream

    def __enter__(self) -> "ContractDeviceFacade":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def _bind_physical_arguments(
    spec: RpcSpec,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    if len(args) > len(spec.forwarded_fields):
        raise TypeError(
            f"{spec.python_name}() accepts at most {len(spec.forwarded_fields)} "
            f"positional argument(s); received {len(args)}."
        )
    forwarded = dict(zip(spec.forwarded_fields, args, strict=False))
    for field in spec.forwarded_field_specs:
        if field.name in kwargs:
            if field.name in forwarded:
                raise TypeError(
                    f"{spec.python_name}() got multiple values for {field.name!r}."
                )
            forwarded[field.name] = kwargs.pop(field.name)
        if not field.optional and field.name not in forwarded:
            raise TypeError(
                f"{spec.python_name}() is missing required argument {field.name!r}."
            )
    return forwarded


def _discard_local_transport_options(kwargs: dict[str, Any]) -> None:
    kwargs.pop("operation_id", None)
    kwargs.pop("timeout_seconds", None)


def _positive_finite(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and greater than zero.")
    return result


def _poll_local_scalar(
    *,
    physical: Callable[..., Any],
    forwarded: dict[str, Any],
    sample_rate_hz: float,
    sample_count: int | None,
    duration_seconds: float | None,
    operation_id: str,
    default_unit: str,
) -> Iterator[Measurement]:
    interval = 1.0 / sample_rate_hz
    started = time.monotonic()
    sequence = 0
    while True:
        if sample_count is not None and sequence >= sample_count:
            return
        if duration_seconds is not None and sequence > 0:
            if time.monotonic() - started >= duration_seconds:
                return
        raw = physical(**forwarded)
        value = float(getattr(raw, "value", raw))
        yield Measurement(
            operation_id=operation_id,
            sequence=sequence,
            timestamp_ns=time.time_ns(),
            value=value,
            unit=str(getattr(raw, "unit", default_unit)),
            quality=MeasurementQuality.GOOD,
            status=str(getattr(raw, "status", "OK")),
            channel=(
                None
                if forwarded.get("channel") is None
                else str(forwarded["channel"])
            ),
        )
        sequence += 1
        delay = started + sequence * interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)


__all__ = ["ContractDeviceFacade"]
