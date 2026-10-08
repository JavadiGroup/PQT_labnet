from __future__ import annotations

import hashlib
import secrets
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..models.common import ErrorCode, ServiceError
from ..models.common import LABNET_IDENTIFIER_PATTERN
from ..models.users import AuthenticatedUser, UserRole
from ..storage.database import Database
from ..storage.orm import ApiKeyRow, AuditEventRow, UserRow


class UserService:
    VALID_ROLES = {"admin", "user", "device_manager"}

    def __init__(self, database: Database) -> None:
        self.database = database

    def authenticate(self, raw_api_key: str) -> AuthenticatedUser:
        if not raw_api_key:
            raise _auth_failed()
        key_hash = self.hash_api_key(raw_api_key)
        with self.database.session() as session:
            row = session.execute(
                select(ApiKeyRow, UserRow)
                .join(UserRow, UserRow.user_id == ApiKeyRow.user_id)
                .where(
                    ApiKeyRow.key_hash == key_hash,
                    ApiKeyRow.revoked_at.is_(None),
                    UserRow.enabled.is_(True),
                )
            ).one_or_none()
            if row is None:
                raise _auth_failed()
            _, user = row
            return AuthenticatedUser(
                user_id=user.user_id,
                username=user.username,
                role=user.role,  # type: ignore[arg-type]
            )

    def create_user(self, username: str, role: UserRole = "user") -> dict[str, str]:
        username = username.strip()
        self._validate(username, role)
        raw_key = self.generate_api_key()
        user_id = f"usr_{uuid4().hex}"
        try:
            with self.database.transaction() as session:
                session.add(
                    UserRow(user_id=user_id, username=username, role=role, enabled=True)
                )
                session.flush()
                session.add(
                    ApiKeyRow(
                        api_key_id=f"key_{uuid4().hex}",
                        user_id=user_id,
                        key_hash=self.hash_api_key(raw_key),
                    )
                )
                session.flush()
                session.add(
                    AuditEventRow(
                        event_type="USER_CREATED",
                        actor_user_id=user_id,
                        details={"role": role},
                    )
                )
        except IntegrityError as exc:
            raise ValueError(f"User already exists: {username}") from exc
        return {"user": username, "role": role, "api_key": raw_key}

    def rotate_api_key(self, username: str) -> dict[str, str]:
        raw_key = self.generate_api_key()
        now = datetime.now(timezone.utc)
        with self.database.transaction() as session:
            user = session.scalar(select(UserRow).where(UserRow.username == username))
            if user is None:
                raise ValueError(f"User not found: {username}")
            session.execute(
                update(ApiKeyRow)
                .where(ApiKeyRow.user_id == user.user_id, ApiKeyRow.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            session.add(
                ApiKeyRow(
                    api_key_id=f"key_{uuid4().hex}",
                    user_id=user.user_id,
                    key_hash=self.hash_api_key(raw_key),
                )
            )
            session.add(
                AuditEventRow(
                    event_type="USER_API_KEY_ROTATED",
                    actor_user_id=user.user_id,
                )
            )
        return {"user": username, "api_key": raw_key}

    def list_users(self) -> list[dict[str, object]]:
        """Return every user without any key material."""

        with self.database.session() as session:
            rows = session.scalars(select(UserRow).order_by(UserRow.username)).all()
            return [
                {
                    "user": row.username,
                    "role": row.role,
                    "enabled": row.enabled,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    @staticmethod
    def generate_api_key() -> str:
        return f"labnet_{secrets.token_urlsafe(32)}"

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def _validate(self, username: str, role: str) -> None:
        if not username or len(username) > 128:
            raise ValueError("Username must contain 1 to 128 characters.")
        if role not in self.VALID_ROLES:
            raise ValueError(f"Unsupported role: {role}")
        if role == "device_manager" and re.fullmatch(
            LABNET_IDENTIFIER_PATTERN, username
        ) is None:
            raise ValueError(
                "A device_manager username must be a valid LabNet manager_id."
            )


def _auth_failed() -> ServiceError:
    return ServiceError(
        ErrorCode.AUTH_FAILED,
        "Authentication failed.",
        status_code=401,
    )
