"""
This service writes audit events as JSON lines

Available methods:
> log event (different types, all use same callback)

"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any


class LogService:
    def __init__(self, storage):
        self.storage = storage

    def log_event(
        self,
        event: str,
        user: str | None = None,
        device_id: str | None = None,
        lease_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create one audit log event and append it to logs.jsonl."""
        record = {
            "timestamp": self._utc_now_iso(),
            "event": event,
            "user": user,
            "device_id": device_id,
            "lease_id": lease_id,
            "metadata": metadata or {},
        }

        self.storage.append_log(record)

    def log_user_login(self, user: str) -> None:
        """Log successful user authentication."""
        self.log_event(
            event="USER_LOGIN",
            user=user,
        )

    def log_device_registered(
        self,
        device_id: str,
        host: str,
        metadata: dict | None = None,
    ) -> None:
        """Log device registration or re-registration."""
        self.log_event(
            event="DEVICE_REGISTERED",
            device_id=device_id,
            metadata={
                "host": host,
                **(metadata or {}),
            },
        )

    def log_device_disconnected(
        self,
        device_id: str,
        reason: str | None = None,
        revoked_leases: list[str] | None = None,
    ) -> None:
        """Log device disconnection and any revoked leases."""
        self.log_event(
            event="DEVICE_DISCONNECTED",
            device_id=device_id,
            metadata={
                "reason": reason,
                "revoked_leases": revoked_leases or [],
            },
        )

    def log_lease_created(
        self,
        user: str,
        device_id: str,
        lease_id: str,
        expires_at: str,
    ) -> None:
        """Log successful lease creation."""
        self.log_event(
            event="LEASE_CREATED",
            user=user,
            device_id=device_id,
            lease_id=lease_id,
            metadata={
                "expires_at": expires_at,
            },
        )

    def log_lease_released(
        self,
        user: str,
        device_id: str,
        lease_id: str,
    ) -> None:
        """Log explicit lease release."""
        self.log_event(
            event="LEASE_RELEASED",
            user=user,
            device_id=device_id,
            lease_id=lease_id,
        )

    def log_lease_expired(
        self,
        user: str,
        device_id: str,
        lease_id: str,
    ) -> None:
        """Log automatic lease expiration."""
        self.log_event(
            event="LEASE_EXPIRED",
            user=user,
            device_id=device_id,
            lease_id=lease_id,
        )

    def log_background_task_error(
        self,
        task: str,
        error: str,
    ) -> None:
        """Log cleanup-loop or heartbeat-monitor errors."""
        self.log_event(
            event="BACKGROUND_TASK_ERROR",
            metadata={
                "task": task,
                "error": error,
            },
        )

    def read_recent_logs(self, limit: int = 100) -> list[dict]:
        """Read recent audit log records for the admin /logs endpoint."""
        if not self.storage.logs_path.exists():
            return []

        with open(self.storage.logs_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        records = []

        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({
                    "event": "MALFORMED_LOG_LINE",
                    "raw": line,
                })

        return records

    def _utc_now_iso(self) -> str:
        """Return current UTC time as ISO string ending in Z."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")