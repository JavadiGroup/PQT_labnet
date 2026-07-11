"""
This service maintains the device registry

Available methods:
> register device
> list devices
> get device
> update status
> mark disconnected
> register host devices
> cleanup heartbeat timeouts


"""

from __future__ import annotations
import threading
from datetime import datetime, timezone

# ---- Exceptions ----
class DeviceNotFoundError(Exception):
    pass

class InvalidDeviceStatusError(Exception):
    pass


class DeviceRegistry:
    VALID_STATUSES = {"disconnected", "idle", "in_use"}

    def __init__(self, storage):
        self.storage = storage
        self._lock = threading.RLock()

    def register_device(self, device: dict) -> dict:
        """Register or update one device record."""
        self._validate_device_payload(device)

        with self._lock:
            devices = self.storage.load_devices()
            leases = self.storage.load_leases()

            device_id = device["device_id"]
            existing = devices.get(device_id, {})

            merged = {
                "device_id": device_id,
                "device_type": device["device_type"],
                "host": device["host"],
                "rpc_url": device["rpc_url"],
                "status": device.get("status", "idle"),
                "last_seen": self._utc_now_iso(),
                "metadata": device.get("metadata", {}),
            }

            # LeaseManager owns in_use state; do not overwrite active leases.
            if self._device_has_active_lease(device_id, leases):
                merged["status"] = "in_use"
            elif existing.get("status") == "in_use":
                merged["status"] = "idle"

            devices[device_id] = merged
            self.storage.save_devices(devices)

            self.storage.append_log({
                "event": "DEVICE_REGISTERED",
                "device_id": device_id,
                "host": merged["host"],
            })

            return merged

    def register_host_devices(
        self,
        host: str,
        rpc_url: str,
        devices_payload: list[dict],
    ) -> list[dict]:
        """Register all devices reported by one Device Manager."""
        registered = []

        for device in devices_payload:
            device = dict(device)
            device["host"] = host
            device["rpc_url"] = rpc_url
            registered.append(self.register_device(device))

        return registered

    def list_devices(
        self,
        device_type: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Return devices, optionally filtered by type or status."""
        devices = self.storage.load_devices()
        results = list(devices.values())

        if device_type is not None:
            results = [
                d for d in results
                if d.get("device_type") == device_type
            ]

        if status is not None:
            self._validate_status(status)
            results = [
                d for d in results
                if d.get("status") == status
            ]

        return results

    def get_device(self, device_id: str) -> dict:
        """Return one device record by device_id."""
        devices = self.storage.load_devices()

        if device_id not in devices:
            raise DeviceNotFoundError(device_id)

        return devices[device_id]

    def update_status(self, device_id: str, status: str) -> dict:
        """Update one device status."""
        self._validate_status(status)

        with self._lock:
            devices = self.storage.load_devices()
            leases = self.storage.load_leases()

            if device_id not in devices:
                raise DeviceNotFoundError(device_id)

            if status == "disconnected":
                return self.mark_disconnected(device_id)

            # Preserve in_use if an active lease exists.
            if self._device_has_active_lease(device_id, leases):
                devices[device_id]["status"] = "in_use"
            else:
                devices[device_id]["status"] = status

            devices[device_id]["last_seen"] = self._utc_now_iso()
            self.storage.save_devices(devices)

            self.storage.append_log({
                "event": "DEVICE_STATUS_UPDATED",
                "device_id": device_id,
                "status": devices[device_id]["status"],
            })

            return devices[device_id]

    def mark_disconnected(
        self,
        device_id: str,
        reason: str | None = None,
    ) -> dict:
        """Mark a device disconnected and remove any active lease."""
        with self._lock:
            devices = self.storage.load_devices()
            leases = self.storage.load_leases()

            if device_id not in devices:
                raise DeviceNotFoundError(device_id)

            devices[device_id]["status"] = "disconnected"
            devices[device_id]["last_seen"] = self._utc_now_iso()

            revoked_lease_ids = []

            for lease_id, lease in list(leases.items()):
                if lease["device_id"] == device_id:
                    revoked_lease_ids.append(lease_id)
                    del leases[lease_id]

            self.storage.save_devices(devices)
            self.storage.save_leases(leases)

            self.storage.append_log({
                "event": "DEVICE_DISCONNECTED",
                "device_id": device_id,
                "reason": reason,
                "revoked_leases": revoked_lease_ids,
            })

            return devices[device_id]

    def cleanup_heartbeat_timeouts(
        self,
        timeout_seconds: int = 30,
    ) -> list[dict]:
        """Mark devices disconnected if last_seen is too old."""
        disconnected = []
        devices = self.storage.load_devices()
        now = self._utc_now()

        for device_id, device in devices.items():
            if device.get("status") == "disconnected":
                continue

            last_seen = device.get("last_seen")

            if not last_seen:
                disconnected.append(
                    self.mark_disconnected(device_id, reason="missing_last_seen")
                )
                continue

            age_seconds = (now - self._from_iso(last_seen)).total_seconds()

            if age_seconds > timeout_seconds:
                disconnected.append(
                    self.mark_disconnected(device_id, reason="heartbeat_timeout")
                )

        return disconnected

    def _validate_device_payload(self, device: dict) -> None:
        """Validate minimum fields required for registration."""
        required = ["device_id", "device_type", "host", "rpc_url"]

        for field in required:
            if field not in device:
                raise ValueError(f"Missing required device field: {field}")

        if "status" in device:
            self._validate_status(device["status"])

    def _validate_status(self, status: str) -> None:
        """Reject invalid device status values."""
        if status not in self.VALID_STATUSES:
            raise InvalidDeviceStatusError(status)

    def _device_has_active_lease(
        self,
        device_id: str,
        leases: dict,
    ) -> bool:
        """Return True if any active lease references the device."""
        return any(
            lease.get("device_id") == device_id
            and lease.get("status") == "active"
            for lease in leases.values()
        )

    def _utc_now(self) -> datetime:
        """Return current UTC datetime."""
        return datetime.now(timezone.utc)

    def _utc_now_iso(self) -> str:
        """Return current UTC timestamp as ISO string ending in Z."""
        return self._utc_now().isoformat().replace("+00:00", "Z")

    def _from_iso(self, value: str) -> datetime:
        """Parse ISO UTC timestamp."""
        return datetime.fromisoformat(value.replace("Z", "+00:00"))