from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from device_manager import LabPCDeviceManager
from devices.registry import create_devices
from rpc_dispatcher import RpcDispatchError, RpcDispatcher

def get_env(name: str, default: str | None = None) -> str:
    load_dotenv()
    value = os.getenv(name, default)

    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Start and stop the Lab PC Device Manager.

    Startup:
    - create local devices
    - connect devices
    - create RPC dispatcher
    - start heartbeat background task

    Shutdown:
    - stop heartbeat task
    - disconnect devices
    - close HTTP client
    """
    
    # ---- CONFIG ----
    server_url = get_env(
        "LABNET_SERVER_URL",
        "http://127.0.0.1:8008",
    )

    api_key = get_env("LABNET_DEVICE_MANAGER_API_KEY")

    host = get_env(
        "LABNET_LAB_PC_HOST",
        "lab-pc-test",
    )

    rpc_url = get_env(
        "LABNET_RPC_URL",
        "ws://127.0.0.1:9001/ws/rpc",
    )

    heartbeat_interval_seconds = float(
        get_env("LABNET_HEARTBEAT_INTERVAL_SECONDS", "5")
    )

    # ---- INSTANTIATE DEVICES ----
    devices = create_devices()

    # ---- INSTANTIATE DEVICE MANAGER ----
    manager = LabPCDeviceManager(
        server_url=server_url,
        api_key=api_key,
        host=host,
        rpc_url=rpc_url,
        devices=devices,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )

    # ---- INSTANTIATE RPC DISPATCHER ----
    dispatcher = RpcDispatcher(manager)

    # ---- START DEVICE MANAGER ----
    # This runs the connection routine for each device
    await manager.start()

    # ---- START HEARTBEAT LOOP ----
    # This allows the central server to monitor
    # what computers are connected to the network
    # the expected behavior is a regular check in
    # also helps with debugging
    heartbeat_task = asyncio.create_task(
        manager.heartbeat_loop()
    )

    # ---- ADD OBJECTS TO APP STATE (request.app.state.manager/etc.) ----
    app.state.manager = manager
    app.state.dispatcher = dispatcher
    app.state.heartbeat_task = heartbeat_task

    print("Lab PC app started.")
    print(f"Central Server: {server_url}")
    print(f"Host: {host}")
    print(f"RPC URL: {rpc_url}")

    try:
        yield

    finally:
        print("Lab PC app shutting down...")

        heartbeat_task.cancel()

        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        await manager.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lab PC Device Manager",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "service": "lab-pc-device-manager",
        }

    @app.websocket("/ws/rpc")
    async def rpc_websocket(websocket: WebSocket):
        await websocket.accept()

        dispatcher: RpcDispatcher = websocket.app.state.dispatcher

        try:
            while True:
                request = await websocket.receive_json()
                request_id = request.get("request_id")

                try:
                    result = await dispatcher.dispatch(request)

                    await websocket.send_json({
                        "type": "rpc_response",
                        "request_id": request_id,
                        "ok": True,
                        "result": result,
                    })

                except RpcDispatchError as exc:
                    await websocket.send_json({
                        "type": "rpc_response",
                        "request_id": request_id,
                        "ok": False,
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                        },
                    })

                except Exception as exc:
                    await websocket.send_json({
                        "type": "rpc_response",
                        "request_id": request_id,
                        "ok": False,
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": str(exc),
                        },
                    })

        except WebSocketDisconnect:
            print("RPC client disconnected.")

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=9001,
        reload=False,
    )


if __name__ == "__main__":
    main()