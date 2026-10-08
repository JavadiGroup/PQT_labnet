from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..models.devices import (
    DeviceManagerHeartbeatRequest,
    DeviceManagerListResponse,
    DeviceManagerRegistrationRequest,
    DeviceManagerResponse,
)
from ..models.users import AuthenticatedUser
from ..services.registry import DeviceRegistryService
from .auth import get_current_user, require_device_manager


router = APIRouter(prefix="/api/v1/device-managers", tags=["device-managers"])


def get_registry(request: Request) -> DeviceRegistryService:
    return request.app.state.registry_service


@router.get("", response_model=DeviceManagerListResponse)
def list_device_managers(
    _actor: AuthenticatedUser = Depends(get_current_user),
    registry: DeviceRegistryService = Depends(get_registry),
) -> DeviceManagerListResponse:
    """List every Lab PC and its devices (read-only; any authenticated user)."""

    return DeviceManagerListResponse(device_managers=registry.list_managers())


@router.post("/register", response_model=DeviceManagerResponse)
def register(
    body: DeviceManagerRegistrationRequest,
    actor: AuthenticatedUser = Depends(require_device_manager),
    registry: DeviceRegistryService = Depends(get_registry),
) -> DeviceManagerResponse:
    return DeviceManagerResponse.model_validate(registry.register(body, actor))


@router.post("/heartbeat", response_model=DeviceManagerResponse)
def heartbeat(
    body: DeviceManagerHeartbeatRequest,
    actor: AuthenticatedUser = Depends(require_device_manager),
    registry: DeviceRegistryService = Depends(get_registry),
) -> DeviceManagerResponse:
    return DeviceManagerResponse.model_validate(registry.heartbeat(body, actor))

