from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict


UserRole = Literal["admin", "user", "device_manager"]


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: str
    username: str
    role: UserRole

    @property
    def user(self) -> str:
        return self.username


class UserPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str
    role: UserRole
