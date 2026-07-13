from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.auth import (
    get_current_user,
    require_device_manager,
)
from app.models.devices import (
    DeviceHeartbeatRequest,
    DeviceListResponse,
    DeviceRecord,
    DeviceRegistrationRequest,
    DeviceStatus,
    DeviceStatusUpdateRequest,
)
from app.models.users import AuthenticatedUser
from app.services.DeviceRegistry import (
    DeviceNotFoundError,
    DeviceRegistry,
)


router = APIRouter(
    prefix="/devices",
    tags=["devices"],
)


def get_device_registry(request: Request) -> DeviceRegistry:
    """Retrieve the shared DeviceRegistry from application state."""
    return request.app.state.device_registry


@router.get(
    "",
    response_model=DeviceListResponse,
)
def list_devices(
    device_type: str | None = Query(default=None),
    device_status: DeviceStatus | None = Query(
        default=None,
        alias="status",
    ),
    current_user: AuthenticatedUser = Depends(get_current_user),
    registry: DeviceRegistry = Depends(get_device_registry),
) -> DeviceListResponse:
    """List devices, optionally filtered by type and status."""

    devices = registry.list_devices(
        device_type=device_type,
        status=device_status,
    )

    return DeviceListResponse(devices=devices)


@router.get(
    "/{device_id}",
    response_model=DeviceRecord,
)
def get_device(
    device_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    registry: DeviceRegistry = Depends(get_device_registry),
) -> DeviceRecord:
    """Return one device record."""

    try:
        return DeviceRecord.model_validate(
            registry.get_device(device_id)
        )
    except DeviceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device not found: {device_id}",
        ) from exc


@router.post(
    "/status",
    response_model=DeviceRecord,
)
def update_device_status(
    body: DeviceStatusUpdateRequest,
    current_user: AuthenticatedUser = Depends(
        require_device_manager
    ),
    registry: DeviceRegistry = Depends(get_device_registry),
) -> DeviceRecord:
    """
    Update a device's reported status.

    Only a Device Manager or administrator may call this endpoint.
    """

    try:
        device = registry.update_status(
            device_id=body.device_id,
            status=body.status,
        )
    except DeviceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device not found: {body.device_id}",
        ) from exc

    return DeviceRecord.model_validate(device)


@router.post(
    "/register",
    response_model=DeviceListResponse,
)
def register_devices(
    body: DeviceRegistrationRequest,
    current_user: AuthenticatedUser = Depends(require_device_manager),
    registry: DeviceRegistry = Depends(get_device_registry),
) -> DeviceListResponse:
    """
    Register all devices reported by one lab PC / Device Manager.

    This is called when the lab PC script starts.
    """

    devices = registry.register_host_devices(
        host=body.host,
        rpc_url=body.rpc_url,
        devices_payload=[
            device.model_dump()
            for device in body.devices
        ],
    )

    return DeviceListResponse(devices=devices)


@router.post(
    "/heartbeat",
    response_model=DeviceListResponse,
)
def heartbeat(
    body: DeviceHeartbeatRequest,
    current_user: AuthenticatedUser = Depends(require_device_manager),
    registry: DeviceRegistry = Depends(get_device_registry),
) -> DeviceListResponse:
    """
    Update last_seen and status for all devices on one lab PC.

    This is called periodically by the lab PC script.
    """

    updated_devices = []

    for device in body.devices:
        payload = {
            "device_id": device.device_id,
            "device_type": "unknown",
            "host": body.host,
            "rpc_url": body.rpc_url,
            "status": device.status,
            "metadata": device.metadata.model_dump() if device.metadata else {},
        }

        # Prefer updating existing devices.
        try:
            existing = registry.get_device(device.device_id)

            payload["device_type"] = existing["device_type"]

            updated = registry.update_status(
                device_id=device.device_id,
                status=device.status,
            )

            # Keep host/rpc_url/current metadata fresh.
            devices = registry.storage.load_devices()
            devices[device.device_id]["host"] = body.host
            devices[device.device_id]["rpc_url"] = body.rpc_url
            devices[device.device_id]["last_seen"] = registry._utc_now_iso()

            if device.metadata is not None:
                devices[device.device_id]["metadata"] = device.metadata.model_dump()

            registry.storage.save_devices(devices)

            updated_devices.append(devices[device.device_id])

        except DeviceNotFoundError:
            # If the device is not known yet, register it.
            updated_devices.append(
                registry.register_device(payload)
            )

    return DeviceListResponse(devices=updated_devices)