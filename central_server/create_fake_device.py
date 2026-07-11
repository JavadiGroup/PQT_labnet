from app.services.DeviceRegistry import DeviceRegistry
from app.storage.json_storage import JsonStorage


DEVICE_ID = "fake_device_1"


def main() -> None:
    storage = JsonStorage(data_dir="data")
    registry = DeviceRegistry(storage)

    # Remove any stale lease from an earlier test.
    leases = storage.load_leases()

    stale_lease_ids = [
        lease_id
        for lease_id, lease in leases.items()
        if lease.get("device_id") == DEVICE_ID
    ]

    for lease_id in stale_lease_ids:
        del leases[lease_id]

    storage.save_leases(leases)

    device = registry.register_device({
        "device_id": DEVICE_ID,
        "device_type": "fake_device",
        "host": "fake-lab-pc",
        "rpc_url": "ws://127.0.0.1:9001/ws/rpc",
        "status": "idle",
        "metadata": {
            "manufacturer": "LabNet",
            "model": "FakeDeviceV1",
            "capabilities": [
                "read_value",
                "write_value",
            ],
        },
    })

    print("Fake device created:")
    print(device)


if __name__ == "__main__":
    main()