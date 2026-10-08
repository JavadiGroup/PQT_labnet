from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..models.common import ErrorCode, ServiceError
from ..models.users import AuthenticatedUser, UserPublic
from ..services.auth import UserService


router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
bearer = HTTPBearer(auto_error=False)


def get_user_service(request: Request) -> UserService:
    return request.app.state.user_service


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    service: UserService = Depends(get_user_service),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ServiceError(
            ErrorCode.AUTH_FAILED,
            "Authentication failed.",
            status_code=401,
        )
    return service.authenticate(credentials.credentials)


def require_roles(*roles: str) -> Callable[..., AuthenticatedUser]:
    def dependency(
        actor: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if actor.role not in roles:
            raise ServiceError(
                ErrorCode.PERMISSION_DENIED,
                "The authenticated principal cannot perform this operation.",
                status_code=403,
            )
        return actor

    return dependency


require_device_manager = require_roles("device_manager")
require_admin = require_roles("admin")


@router.get("/me", response_model=UserPublic)
def me(actor: AuthenticatedUser = Depends(get_current_user)) -> UserPublic:
    return UserPublic(user=actor.username, role=actor.role)
