from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

UserRole = Literal["admin", "user", "device_manager"]


class UserRecord(BaseModel):
    """
    Internal representation of one user stored in users.json.

    This model includes api_key_hash because it represents storage state.
    Do not return this model directly from public API endpoints.
    """

    user: str = Field(..., min_length=1)
    role: UserRole
    api_key_hash: str = Field(..., min_length=1)


class UserPublic(BaseModel):
    """
    Safe user representation for API responses.

    This model excludes api_key_hash.
    """

    user: str
    role: UserRole


class UserCreateRequest(BaseModel):
    """
    Request body for creating a new user.

    The server generates the raw API key and stores only its hash.
    """

    user: str = Field(..., min_length=1)
    role: UserRole = "user"


class UserCreateResponse(BaseModel):
    """
    Response returned after creating a user.

    The raw API key is returned once and should not be stored by the server.
    """

    user: str
    role: UserRole
    api_key: str


class UserRoleUpdateRequest(BaseModel):
    """
    Request body for changing a user's role.
    """

    role: UserRole


class UserApiKeyRotateResponse(BaseModel):
    """
    Response returned after rotating a user's API key.

    The raw API key is returned once.
    """

    user: str
    api_key: str


class AuthenticatedUser(BaseModel):
    """
    Lightweight model representing the user resolved from an API key.

    This is useful inside API dependencies and services.
    """

    user: str
    role: UserRole

    @property
    def username(self) -> str:
        """
        Compatibility alias for code that expects user.username.
        """
        return self.user