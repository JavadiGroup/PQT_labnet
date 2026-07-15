from __future__ import annotations

import asyncio
from typing import Any


class RpcDispatchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class RpcDispatcher:
    def __init__(self, manager):
        self.manager = manager

        self.device_locks = {
            device.device_id: asyncio.Lock()
            for device in manager.devices
        }

    async def dispatch(self, request: dict[str, Any]) -> Any:
        self._validate_request_shape(request)

        device_id = request["device_id"]
        lease_id = request["lease_id"]
        method_name = request["method"]
        args = request.get("args", [])
        kwargs = request.get("kwargs", {})

        device = self.manager.get_device(device_id)

        if device is None:
            raise RpcDispatchError(
                "DEVICE_NOT_FOUND",
                f"Device not found: {device_id}",
            )

        try:
            await self.manager.validate_lease(
                lease_id=lease_id,
                device_id=device_id,
            )
        except Exception as exc:
            raise RpcDispatchError(
                "LEASE_INVALID",
                str(exc),
            ) from exc

        if method_name not in device.allowed_methods():
            raise RpcDispatchError(
                "METHOD_NOT_ALLOWED",
                f"Method is not allowed: {method_name}",
            )

        method = getattr(device, method_name, None)

        if method is None or not callable(method):
            raise RpcDispatchError(
                "METHOD_NOT_ALLOWED",
                f"Method is not callable: {method_name}",
            )

        lock = self.device_locks[device_id]

        async with lock:
            try:
                # Runs blocking hardware calls in a worker thread so the
                # WebSocket event loop does not freeze.
                result = await asyncio.to_thread(
                    method,
                    *args,
                    **kwargs,
                )

            except Exception as exc:
                raise RpcDispatchError(
                    "DEVICE_METHOD_ERROR",
                    str(exc),
                ) from exc

        return result

    def _validate_request_shape(self, request: dict[str, Any]) -> None:
        required = [
            "request_id",
            "lease_id",
            "device_id",
            "method",
        ]

        for field in required:
            if field not in request:
                raise RpcDispatchError(
                    "BAD_REQUEST",
                    f"Missing RPC field: {field}",
                )

        if request.get("type") != "rpc_request":
            raise RpcDispatchError(
                "BAD_REQUEST",
                "RPC message type must be 'rpc_request'.",
            )

        if not isinstance(request.get("args", []), list):
            raise RpcDispatchError(
                "BAD_REQUEST",
                "RPC args must be a list.",
            )

        if not isinstance(request.get("kwargs", {}), dict):
            raise RpcDispatchError(
                "BAD_REQUEST",
                "RPC kwargs must be a dictionary.",
            )