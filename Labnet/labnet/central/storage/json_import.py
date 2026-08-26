from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from ..services.auth import UserService
from ..models.common import LABNET_IDENTIFIER_PATTERN
from .database import Database
from .orm import ApiKeyRow, AuditEventRow, UserRow


def import_legacy_users(database: Database, data_dir: Path) -> tuple[int, int]:
    """Import hashed users from legacy JSON without reading raw API keys.

    Legacy WebSocket devices and active leases are intentionally not imported:
    their connection contracts cannot be represented safely as gRPC endpoints.
    """

    users_path = data_dir / "users.json"
    if not users_path.is_file():
        raise FileNotFoundError(f"Legacy users file not found: {users_path}")
    decoded = json.loads(users_path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Legacy users.json must contain an object.")

    imported = 0
    skipped = 0
    with database.transaction() as session:
        for key, record in decoded.items():
            if not isinstance(record, dict):
                skipped += 1
                continue
            username = str(record.get("user", key)).strip()
            role = str(record.get("role", "user"))
            key_hash = str(record.get("api_key_hash", "")).lower()
            if (
                not username
                or len(username) > 128
                or role not in UserService.VALID_ROLES
                or len(key_hash) != 64
                or any(character not in "0123456789abcdef" for character in key_hash)
                or (
                    role == "device_manager"
                    and re.fullmatch(LABNET_IDENTIFIER_PATTERN, username) is None
                )
            ):
                skipped += 1
                continue
            if session.scalar(select(UserRow).where(UserRow.username == username)):
                skipped += 1
                continue
            user_id = f"usr_{uuid4().hex}"
            session.add(
                UserRow(
                    user_id=user_id,
                    username=username,
                    role=role,
                    enabled=True,
                )
            )
            session.flush()
            session.add(
                ApiKeyRow(
                    api_key_id=f"key_{uuid4().hex}",
                    user_id=user_id,
                    key_hash=key_hash,
                )
            )
            session.flush()
            session.add(
                AuditEventRow(
                    event_type="LEGACY_USER_IMPORTED",
                    actor_user_id=user_id,
                    details={"source": "users.json"},
                )
            )
            imported += 1
    return imported, skipped
