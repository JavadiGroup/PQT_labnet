from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

# Auth Import
from app.api.auth import get_current_user, require_device_manager

# API Imports
from app.api.devices import get_device_registry

# Lease Models
from app.models.leases import (
    LeaseDeviceInfo,
    LeaseRecord,
    LeaseReleaseRequest,
    LeaseReleaseResponse,
    LeaseRequest,
    LeaseRequestResponse,
    LeaseValidationRequest,
    LeaseValidationResponse,
)

# User Models
from app.models.users import AuthenticatedUser

# Services
from app.services.DeviceRegistry import DeviceRegistry
from app.services.LeaseManager import (
    DeviceBusyError,
    DeviceDisconnectedError,
    DeviceNotFoundError,
    LeaseManager,
    LeaseNotFoundError,
    LeaseExpiredError,
    PermissionDeniedError,
)


router = APIRouter(
    prefix="/leases",
    tags=["leases"],
)


def get_lease_manager(request: Request) -> LeaseManager:
    """Retrieve the shared LeaseManager from application state."""
    return request.app.state.lease_manager


@router.post(
    "/request",
    response_model=LeaseRequestResponse,
)
def request_lease(
    body: LeaseRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    lease_manager: LeaseManager = Depends(get_lease_manager),
    device_registry: DeviceRegistry = Depends(get_device_registry),
) -> LeaseRequestResponse:
    """Request exclusive access to one device."""

    try:
        lease = lease_manager.request_lease(
            user=current_user,
            device_id=body.device_id,
            duration_seconds=body.duration_seconds,
        )
    except DeviceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device not found: {body.device_id}",
        ) from exc
    except DeviceDisconnectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Device is disconnected: {body.device_id}",
        ) from exc
    except DeviceBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Device is already in use: {body.device_id}",
        ) from exc

    device = device_registry.get_device(body.device_id)

    return LeaseRequestResponse(
        success=True,
        lease=LeaseRecord.model_validate(lease),
        device=LeaseDeviceInfo(
            device_id=device["device_id"],
            host=device["host"],
            rpc_url=device["rpc_url"],
        ),
    )


@router.post(
    "/release",
    response_model=LeaseReleaseResponse,
)
def release_lease(
    body: LeaseReleaseRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> LeaseReleaseResponse:
    """Release a lease owned by the authenticated user."""

    try:
        lease_manager.release_lease(
            user=current_user,
            lease_id=body.lease_id,
        )
    except LeaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lease not found: {body.lease_id}",
        ) from exc
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The authenticated user does not own this lease.",
        ) from exc

    return LeaseReleaseResponse(
        success=True,
        released=True,
    )

@router.post(
    "/{lease_id}/validate",
    response_model=LeaseValidationResponse,
)
def validate_lease(
    lease_id: str,
    body: LeaseValidationRequest,
    current_user: AuthenticatedUser = Depends(require_device_manager),
    lease_manager: LeaseManager = Depends(get_lease_manager),
) -> LeaseValidationResponse:
    """
    Validate that a lease is active for a specific device.

    Called by the Lab PC before executing an RPC method.
    """

    try:
        result = lease_manager.validate_lease(
            lease_id=lease_id,
            device_id=body.device_id,
        )
    except LeaseNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lease not found.",
        ) from exc
    except LeaseExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lease expired.",
        ) from exc
    except PermissionDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lease is not valid for this device.",
        ) from exc

    return LeaseValidationResponse.model_validate(result)