from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from ..models.common import ErrorCode, ServiceError
from ..models.updates import (
    DeploymentAssignRequest,
    DeploymentListResponse,
    DeploymentPublic,
    DrainStatusResponse,
    UpdateAbortRequest,
    UpdateAttemptPublic,
    UpdateCompleteRequest,
    UpdateDirectiveResponse,
    UpdatePhaseRequest,
)
from ..models.users import AuthenticatedUser
from ..services.deployments import DeploymentService
from ..services.releases import ReleaseService
from .auth import get_current_user, require_admin, require_device_manager


agent_router = APIRouter(prefix="/api/v1/updates", tags=["updates"])
deployment_router = APIRouter(
    prefix="/api/v1/deployments", tags=["deployments"]
)


def get_deployment_service(request: Request) -> DeploymentService:
    return request.app.state.deployment_service


def get_release_service(request: Request) -> ReleaseService:
    return request.app.state.release_service


@deployment_router.put("/{manager_id}", response_model=DeploymentPublic)
def assign_release(
    manager_id: str,
    body: DeploymentAssignRequest,
    actor: AuthenticatedUser = Depends(require_admin),
    service: DeploymentService = Depends(get_deployment_service),
) -> DeploymentPublic:
    return service.assign(
        manager_id=manager_id,
        version=body.version,
        allow_downgrade=body.allow_downgrade,
        actor=actor,
    )


@deployment_router.get("", response_model=DeploymentListResponse)
def list_deployments(
    _actor: AuthenticatedUser = Depends(require_admin),
    service: DeploymentService = Depends(get_deployment_service),
) -> DeploymentListResponse:
    return DeploymentListResponse(deployments=service.list_deployments())


@deployment_router.post(
    "/attempts/{attempt_id}/abort", response_model=UpdateAttemptPublic
)
def admin_abort_update(
    attempt_id: str,
    body: UpdateAbortRequest,
    actor: AuthenticatedUser = Depends(require_admin),
    service: DeploymentService = Depends(get_deployment_service),
) -> UpdateAttemptPublic:
    """Recover a fleet member whose updater cannot clear its own drain."""
    return service.abort(attempt_id, body, actor)


@agent_router.get("/directive", response_model=UpdateDirectiveResponse)
def get_directive(
    actor: AuthenticatedUser = Depends(require_device_manager),
    service: DeploymentService = Depends(get_deployment_service),
) -> UpdateDirectiveResponse:
    return service.directive(actor)


@agent_router.get("/artifacts/{release_id}")
def download_artifact(
    release_id: str,
    actor: AuthenticatedUser = Depends(get_current_user),
    deployments: DeploymentService = Depends(get_deployment_service),
    releases: ReleaseService = Depends(get_release_service),
) -> FileResponse:
    release = releases.get(release_id)
    if actor.role == "device_manager":
        directive = deployments.directive(actor)
        if (
            not directive.update_available
            or directive.desired_version != release.version
        ):
            raise ServiceError(
                ErrorCode.PERMISSION_DENIED,
                "The release is not assigned to this Device Manager.",
                status_code=403,
            )
    elif actor.role != "admin":
        raise ServiceError(
            ErrorCode.PERMISSION_DENIED,
            "The authenticated principal cannot download release artifacts.",
            status_code=403,
        )
    row = releases.get_by_version(release.version)
    path = releases.artifact_path(row)
    if not path.is_file():
        raise ServiceError(
            ErrorCode.RELEASE_INVALID,
            "The release artifact is missing from Central storage.",
            status_code=503,
        )
    return FileResponse(
        path,
        filename=row.artifact_filename,
        media_type="application/zip",
        headers={"X-LabNet-SHA256": row.artifact_sha256},
    )


@agent_router.post("/{attempt_id}/phase", response_model=UpdateAttemptPublic)
def report_phase(
    attempt_id: str,
    body: UpdatePhaseRequest,
    actor: AuthenticatedUser = Depends(require_device_manager),
    service: DeploymentService = Depends(get_deployment_service),
) -> UpdateAttemptPublic:
    return service.report_phase(attempt_id, body, actor)


@agent_router.post("/{attempt_id}/begin-drain", response_model=DrainStatusResponse)
def begin_drain(
    attempt_id: str,
    actor: AuthenticatedUser = Depends(require_device_manager),
    service: DeploymentService = Depends(get_deployment_service),
) -> DrainStatusResponse:
    return service.begin_drain(attempt_id, actor)


@agent_router.get("/{attempt_id}/drain", response_model=DrainStatusResponse)
def drain_status(
    attempt_id: str,
    actor: AuthenticatedUser = Depends(require_device_manager),
    service: DeploymentService = Depends(get_deployment_service),
) -> DrainStatusResponse:
    return service.drain_status(attempt_id, actor)


@agent_router.post("/{attempt_id}/complete", response_model=UpdateAttemptPublic)
def complete_update(
    attempt_id: str,
    body: UpdateCompleteRequest,
    actor: AuthenticatedUser = Depends(require_device_manager),
    service: DeploymentService = Depends(get_deployment_service),
) -> UpdateAttemptPublic:
    return service.complete(attempt_id, body, actor)


@agent_router.post("/{attempt_id}/abort", response_model=UpdateAttemptPublic)
def abort_update(
    attempt_id: str,
    body: UpdateAbortRequest,
    actor: AuthenticatedUser = Depends(require_device_manager),
    service: DeploymentService = Depends(get_deployment_service),
) -> UpdateAttemptPublic:
    return service.abort(attempt_id, body, actor)


__all__ = ["agent_router", "deployment_router"]
