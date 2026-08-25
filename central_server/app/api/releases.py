from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request

from ..models.common import ErrorCode, ServiceError
from ..models.releases import (
    ReleaseCreateRequest,
    ReleaseListResponse,
    ReleasePublic,
)
from ..models.users import AuthenticatedUser
from ..services.releases import ReleaseService
from .auth import require_admin


router = APIRouter(prefix="/api/v1/releases", tags=["releases"])


def get_release_service(request: Request) -> ReleaseService:
    return request.app.state.release_service


@router.post("", response_model=ReleasePublic, status_code=201)
def create_release(
    body: ReleaseCreateRequest,
    actor: AuthenticatedUser = Depends(require_admin),
    service: ReleaseService = Depends(get_release_service),
) -> ReleasePublic:
    return service.create(body, actor)


@router.get("", response_model=ReleaseListResponse)
def list_releases(
    _actor: AuthenticatedUser = Depends(require_admin),
    service: ReleaseService = Depends(get_release_service),
) -> ReleaseListResponse:
    return ReleaseListResponse(releases=service.list())


@router.get("/{release_id}", response_model=ReleasePublic)
def get_release(
    release_id: str,
    _actor: AuthenticatedUser = Depends(require_admin),
    service: ReleaseService = Depends(get_release_service),
) -> ReleasePublic:
    return service.get(release_id, include_upload_url=True)


@router.put("/{release_id}/artifact", response_model=ReleasePublic)
async def upload_release_artifact(
    release_id: str,
    request: Request,
    actor: AuthenticatedUser = Depends(require_admin),
    service: ReleaseService = Depends(get_release_service),
) -> ReleasePublic:
    temporary_path, maximum = service.prepare_upload(release_id, actor)
    written = 0
    try:
        with temporary_path.open("xb") as stream:
            async for chunk in request.stream():
                written += len(chunk)
                if written > maximum:
                    raise ServiceError(
                        ErrorCode.RELEASE_INVALID,
                        "Uploaded release exceeded its declared or configured size.",
                        status_code=413,
                        metadata={"maximum_bytes": maximum},
                    )
                stream.write(chunk)
            stream.flush()
        return service.complete_upload(release_id, temporary_path, actor)
    except BaseException:
        Path(temporary_path).unlink(missing_ok=True)
        raise


@router.post("/{release_id}/promote", response_model=ReleasePublic)
def promote_release(
    release_id: str,
    actor: AuthenticatedUser = Depends(require_admin),
    service: ReleaseService = Depends(get_release_service),
) -> ReleasePublic:
    return service.promote(release_id, actor)


__all__ = ["router"]
