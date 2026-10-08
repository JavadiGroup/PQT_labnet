from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ..models.leases import (
    LeaseCreateRequest,
    LeaseGrantResponse,
    LeaseListResponse,
    LeasePublic,
    LeaseReleaseResponse,
    LeaseRenewRequest,
    LeaseValidationRequest,
    LeaseValidationResponse,
)
from ..models.common import LabNetIdentifier
from ..models.users import AuthenticatedUser
from ..services.leases import LeaseService
from .auth import require_admin, require_roles


router = APIRouter(prefix="/api/v1/leases", tags=["leases"])


def get_lease_service(request: Request) -> LeaseService:
    return request.app.state.lease_service


@router.post("", response_model=LeaseGrantResponse, status_code=201)
def create_lease(
    body: LeaseCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    actor: AuthenticatedUser = Depends(require_roles("user", "admin")),
    service: LeaseService = Depends(get_lease_service),
) -> LeaseGrantResponse:
    return service.request_lease(
        actor=actor,
        device_id=body.device_id,
        duration_seconds=body.duration_seconds,
        idempotency_key=idempotency_key,
    )


@router.post("/{lease_id}/renew", response_model=LeasePublic)
def renew_lease(
    lease_id: LabNetIdentifier,
    body: LeaseRenewRequest,
    actor: AuthenticatedUser = Depends(require_roles("user", "admin")),
    service: LeaseService = Depends(get_lease_service),
) -> LeasePublic:
    return service.renew(
        actor=actor,
        lease_id=lease_id,
        duration_seconds=body.duration_seconds,
    )


@router.delete("/{lease_id}", response_model=LeaseReleaseResponse)
def release_lease(
    lease_id: LabNetIdentifier,
    actor: AuthenticatedUser = Depends(require_roles("user", "admin")),
    service: LeaseService = Depends(get_lease_service),
) -> LeaseReleaseResponse:
    return service.release(actor=actor, lease_id=lease_id)


@router.post("/{lease_id}/validate", response_model=LeaseValidationResponse)
def validate_lease(
    lease_id: LabNetIdentifier,
    body: LeaseValidationRequest,
    actor: AuthenticatedUser = Depends(require_roles("device_manager")),
    service: LeaseService = Depends(get_lease_service),
) -> LeaseValidationResponse:
    return service.validate(
        actor=actor,
        lease_id=lease_id,
        device_id=body.device_id,
    )


@router.get("", response_model=LeaseListResponse)
def list_active_leases(
    _actor: AuthenticatedUser = Depends(require_admin),
    service: LeaseService = Depends(get_lease_service),
) -> LeaseListResponse:
    return LeaseListResponse(leases=service.list_active())
