from __future__ import annotations

import json
import threading
from typing import Any
from uuid import uuid4

from websockets.sync.client import ClientConnection, connect


class RpcError(Exception):
    """Raised when the Lab PC returns an RPC-level error."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class RpcConnectionClosedError(RpcError):
    """Raised when using an RPC connection after it has closed."""


class PersistentRpcConnection:
    """
    Persistent WebSocket RPC connection to one Lab PC.

    For MVP this class allows one in-flight request at a time.
    That is simple and reliable for normal experiment scripts.
    """

    def __init__(
        self,
        rpc_url: str,
        timeout_seconds: float = 5.0,
    ):
        self.rpc_url = rpc_url
        self.timeout_seconds = timeout_seconds

        self._websocket: ClientConnection | None = None
        self._lock = threading.RLock()
        self._closed = False

    def connect(self) -> None:
        """Open the WebSocket connection if it is not already open."""

        with self._lock:
            if self._closed:
                raise RpcConnectionClosedError(
                    "RPC_CONNECTION_CLOSED",
                    "RPC connection has already been closed.",
                )

            if self._websocket is not None:
                return

            self._websocket = connect(
                self.rpc_url,
                open_timeout=self.timeout_seconds,
                close_timeout=self.timeout_seconds,
                ping_interval=20,
                ping_timeout=20,
            )

    def call(
        self,
        lease_id: str,
        device_id: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        """
        Send one RPC request over the persistent WebSocket.

        This method is intentionally serialized with a lock so responses
        cannot get mixed up.
        """

        with self._lock:
            if self._closed:
                raise RpcConnectionClosedError(
                    "RPC_CONNECTION_CLOSED",
                    "RPC connection has already been closed.",
                )

            if self._websocket is None:
                self.connect()

            assert self._websocket is not None

            request_id = str(uuid4())

            payload = {
                "type": "rpc_request",
                "request_id": request_id,
                "lease_id": lease_id,
                "device_id": device_id,
                "method": method,
                "args": args or [],
                "kwargs": kwargs or {},
            }

            self._websocket.send(json.dumps(payload))

            raw_response = self._websocket.recv(
                timeout=self.timeout_seconds
            )

            response = json.loads(raw_response)

            if response.get("request_id") != request_id:
                raise RpcError(
                    "BAD_RESPONSE",
                    "RPC response request_id did not match request.",
                )

            if not response.get("ok"):
                error = response.get("error", {})

                raise RpcError(
                    error.get("code", "UNKNOWN_RPC_ERROR"),
                    error.get("message", "Unknown RPC error."),
                )

            return response.get("result")

    def close(self) -> None:
        """Close the WebSocket connection."""

        with self._lock:
            if self._closed:
                return

            self._closed = True

            if self._websocket is not None:
                self._websocket.close()
                self._websocket = None

    def __enter__(self) -> "PersistentRpcConnection":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()