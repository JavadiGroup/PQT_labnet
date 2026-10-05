"""01 - Lease one device, write a value, read it back, release it.

Uses:   the first available DummyDevice (the dev network has three).
Run:    source .devnet/researcher.env && python examples/01_hello_device.py
Prints: dev01-dummy-a 12.5
"""

from __future__ import annotations

from labnet import DummyDevice, LabClient


def main() -> None:
    # Reads LABNET_SERVER_URL, LABNET_API_KEY and (in the lab) LABNET_CA_FILE.
    with LabClient.from_env() as central:
        # Leases the first available dummy_device and connects to its Lab PC.
        # Leaving the block releases the lease immediately.
        with DummyDevice(client=central) as device:
            device.write_value(12.5)
            print(device.device_id, device.read_value())


if __name__ == "__main__":
    main()
