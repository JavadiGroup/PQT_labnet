from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..models.devices import Availability, DeviceListResponse, DevicePublic, RuntimeState
from ..models.common import LabNetIdentifier
from ..models.users import AuthenticatedUser
from ..services.registry import DeviceRegistryService
from .auth import get_current_user


router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


def get_registry(request: Request) -> DeviceRegistryService:
    return request.app.state.registry_service


@router.get("", response_model=DeviceListResponse)
def list_devices(
    device_type: str | None = Query(default=None),
    availability: Availability | None = Query(default=None),
    runtime_state: RuntimeState | None = Query(default=None),
    effective_available: bool | None = Query(default=None),
    _actor: AuthenticatedUser = Depends(get_current_user),
    registry: DeviceRegistryService = Depends(get_registry),
) -> DeviceListResponse:
    return DeviceListResponse(
        devices=registry.list_devices(
            device_type=device_type,
            availability=availability,
            runtime_state=runtime_state,
            effective_available=effective_available,
        )
    )


@router.get("/{device_id}", response_model=DevicePublic)
def get_device(
    device_id: LabNetIdentifier,
    _actor: AuthenticatedUser = Depends(get_current_user),
    registry: DeviceRegistryService = Depends(get_registry),
) -> DevicePublic:
    return registry.get_device(device_id)
