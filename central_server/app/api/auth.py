from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.users import AuthenticatedUser, UserPublic
from app.services.LogService import LogService
from app.services.UserManager import AuthenticationError, UserManager


router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

bearer_scheme = HTTPBearer(auto_error=False)


def get_user_manager(request: Request) -> UserManager:
    """Retrieve the shared UserManager from FastAPI application state."""
    return request.app.state.user_manager


def get_log_service(request: Request) -> LogService:
    """Retrieve the shared LogService from FastAPI application state."""
    return request.app.state.log_service


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    user_manager: UserManager = Depends(get_user_manager),
) -> AuthenticatedUser:
    """Validate the bearer API key and return the authenticated user."""

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization scheme must be Bearer.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user = user_manager.validate_api_key(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return AuthenticatedUser(
        user=user.user,
        role=user.role,
    )


def require_roles(
    *allowed_roles: str,
) -> Callable[..., AuthenticatedUser]:
    """Create a dependency that permits only the specified roles."""

    def role_dependency(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not have permission for this operation.",
            )

        return current_user

    return role_dependency


require_admin = require_roles("admin")

require_device_manager = require_roles(
    "device_manager",
    "admin",
)


@router.get(
    "/me",
    response_model=UserPublic,
)
def get_authenticated_user(
    current_user: AuthenticatedUser = Depends(get_current_user),
    log_service: LogService = Depends(get_log_service),
) -> UserPublic:
    """Validate the API key and return its associated user."""

    log_service.log_event(
        event="USER_LOGIN",
        user=current_user.user,
    )

    return UserPublic(
        user=current_user.user,
        role=current_user.role,
    )