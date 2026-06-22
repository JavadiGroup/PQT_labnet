"""
This service resolves and authorizes users

Available methods:
> get user
> validate api key
> require role
> create user
> set role
> rotate api key
> generate api key
> hash api key

"""

from __future__ import annotations
import hashlib
import hmac
import secrets
from dataclasses import dataclass

# ---- Exceptions ----
class AuthenticationError(Exception):
    pass

class UserNotFoundError(Exception):
    pass

class PermissionDeniedError(Exception):
    pass

class InvalidRoleError(Exception):
    pass


@dataclass(frozen=True)
class User:
    user: str
    role: str

    @property
    def username(self) -> str:
        """Compatibility alias for code that expects user.username."""
        return self.user


class UserManager:
    VALID_ROLES = {"admin", "user", "device_manager"}

    def __init__(self, storage):
        self.storage = storage

    def get_user(self, user: str) -> User:
        """Return one user record by username."""
        users = self.storage.load_users()

        if user not in users:
            raise UserNotFoundError(user)

        record = users[user]

        return User(
            user=record["user"],
            role=record["role"],
        )

    def validate_api_key(self, api_key: str) -> User:
        """Return the user associated with a valid API key."""
        if not api_key:
            raise AuthenticationError("Missing API key.")

        api_key_hash = self.hash_api_key(api_key)
        users = self.storage.load_users()

        for record in users.values():
            stored_hash = record.get("api_key_hash", "")

            if hmac.compare_digest(api_key_hash, stored_hash):
                return User(
                    user=record["user"],
                    role=record["role"],
                )

        raise AuthenticationError("Invalid API key.")

    def require_role(
        self,
        user: User,
        allowed_roles: set[str] | list[str] | tuple[str, ...],
    ) -> None:
        """Raise if the user does not have one of the allowed roles."""
        if user.role not in allowed_roles:
            raise PermissionDeniedError(
                f"User '{user.user}' does not have required role."
            )

    def create_user(
        self,
        user: str,
        role: str = "user",
    ) -> dict:
        """Create a user and return the raw API key once."""
        self._validate_role(role)

        users = self.storage.load_users()

        if user in users:
            raise ValueError(f"User already exists: {user}")

        raw_api_key = self.generate_api_key()

        users[user] = {
            "user": user,
            "role": role,
            "api_key_hash": self.hash_api_key(raw_api_key),
        }

        self.storage.save_users(users)

        self.storage.append_log({
            "event": "USER_CREATED",
            "user": user,
            "metadata": {
                "role": role,
            },
        })

        return {
            "user": user,
            "role": role,
            "api_key": raw_api_key,
        }

    def set_role(self, user: str, role: str) -> None:
        """Update a user's role."""
        self._validate_role(role)

        users = self.storage.load_users()

        if user not in users:
            raise UserNotFoundError(user)

        users[user]["role"] = role
        self.storage.save_users(users)

        self.storage.append_log({
            "event": "USER_ROLE_UPDATED",
            "user": user,
            "metadata": {
                "role": role,
            },
        })

    def rotate_api_key(self, user: str) -> dict:
        """Generate a new API key and store only its hash."""
        users = self.storage.load_users()

        if user not in users:
            raise UserNotFoundError(user)

        raw_api_key = self.generate_api_key()
        users[user]["api_key_hash"] = self.hash_api_key(raw_api_key)

        self.storage.save_users(users)

        self.storage.append_log({
            "event": "USER_API_KEY_ROTATED",
            "user": user,
        })

        return {
            "user": user,
            "api_key": raw_api_key,
        }

    def generate_api_key(self) -> str:
        """Generate a long random API key."""
        return f"labnet_{secrets.token_urlsafe(32)}"

    def hash_api_key(self, api_key: str) -> str:
        """Hash an API key for storage."""
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def _validate_role(self, role: str) -> None:
        """Reject unsupported roles."""
        if role not in self.VALID_ROLES:
            raise InvalidRoleError(role)