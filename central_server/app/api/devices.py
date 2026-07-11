from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.auth import (
    get_current_user,
    require_device_manager,
)
from app.models.devices import (
    DeviceListResponse,
    DeviceRecord,
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