"""
This service enforces exclusive device access

Available methods:
> request lease
> release lease
> validate lease
> cleanup expired leases

"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import uuid4

# ---- Exceptions ----
class DeviceNotFoundError(Exception):
    pass

class DeviceDisconnectedError(Exception):
    pass

class DeviceBusyError(Exception):
    pass

class LeaseNotFoundError(Exception):
    pass

class PermissionDeniedError(Exception):
    pass

class LeaseExpiredError(Exception):
    pass


class LeaseManager:
    def __init__(self, storage):
        self.storage = storage

    def request_lease(
        self,
        user,
        device_id: str,
        duration_seconds: int,
    ) -> dict:
        """Create a lease if the device exists, is connected, and is idle."""
        devices = self.storage.load_devices()
        leases = self.storage.load_leases()

        if device_id not in devices:
            raise DeviceNotFoundError(device_id)

        device = devices[device_id]

        if device["status"] == "disconnected":
            raise DeviceDisconnectedError(device_id)

        if device["status"] == "in_use":
            raise DeviceBusyError(device_id)

        if any(lease["device_id"] == device_id for lease in leases.values()):
            raise DeviceBusyError(device_id)

        lease_id = self._generate_lease_id()
        now = self._utc_now()
        expires_at = now + timedelta(seconds=duration_seconds)

        lease = {
            "lease_id": lease_id,
            "device_id": device_id,
            "user": user.username,
            "created_at": self._to_iso(now),
            "expires_at": self._to_iso(expires_at),
            "status": "active",
        }

        leases[lease_id] = lease
        devices[device_id]["status"] = "in_use"

        self.storage.save_leases(leases)
        self.storage.save_devices(devices)

        self.storage.append_log({
            "event": "LEASE_CREATED",
            "user": user.username,
            "device_id": device_id,
            "lease_id": lease_id,
        })

        return lease

    def release_lease(
        self,
        user,
        lease_id: str,
    ) -> None:
        """Release a lease if the caller owns it or is an admin."""
        leases = self.storage.load_leases()
        devices = self.storage.load_devices()

        if lease_id not in leases:
            raise LeaseNotFoundError(lease_id)

        lease = leases[lease_id]

        if lease["user"] != user.username and user.role != "admin":
            raise PermissionDeniedError()

        device_id = lease["device_id"]

        del leases[lease_id]

        if device_id in devices and devices[device_id]["status"] != "disconnected":
            devices[device_id]["status"] = "idle"

        self.storage.save_leases(leases)
        self.storage.save_devices(devices)

        self.storage.append_log({
            "event": "LEASE_RELEASED",
            "user": user.username,
            "device_id": device_id,
            "lease_id": lease_id,
        })

    def validate_lease(
        self,
        lease_id: str,
        device_id: str,
    ) -> dict:
        """Confirm that a lease exists, matches the device, and has not expired."""
        leases = self.storage.load_leases()

        if lease_id not in leases:
            raise LeaseNotFoundError(lease_id)

        lease = leases[lease_id]

        if lease["device_id"] != device_id:
            raise PermissionDeniedError()

        if self._is_expired(lease):
            raise LeaseExpiredError(lease_id)

        return {
            "valid": True,
            "lease_id": lease_id,
            "device_id": device_id,
            "user": lease["user"],
            "expires_at": lease["expires_at"],
        }

    def cleanup_expired_leases(self) -> list[dict]:
        """Remove expired leases and return affected devices to idle."""
        leases = self.storage.load_leases()
        devices = self.storage.load_devices()

        expired = []

        for lease_id, lease in list(leases.items()):
            if not self._is_expired(lease):
                continue

            device_id = lease["device_id"]

            del leases[lease_id]

            if device_id in devices and devices[device_id]["status"] != "disconnected":
                devices[device_id]["status"] = "idle"

            expired.append(lease)

            self.storage.append_log({
                "event": "LEASE_EXPIRED",
                "user": lease["user"],
                "device_id": device_id,
                "lease_id": lease_id,
            })

        if expired:
            self.storage.save_leases(leases)
            self.storage.save_devices(devices)

        return expired

    def _generate_lease_id(self) -> str:
        """Generate a unique lease identifier."""
        return f"lease_{uuid4().hex}"

    def _utc_now(self) -> datetime:
        """Return current UTC time."""
        return datetime.now(timezone.utc)

    def _to_iso(self, dt: datetime) -> str:
        """Convert datetime to ISO UTC string ending in Z."""
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _from_iso(self, value: str) -> datetime:
        """Parse ISO UTC string."""
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _is_expired(self, lease: dict) -> bool:
        """Return True if the lease expiration time has passed."""
        expires_at = self._from_iso(lease["expires_at"])
        return self._utc_now() >= expires_at