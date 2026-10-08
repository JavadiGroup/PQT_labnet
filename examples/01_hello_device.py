"""01 - Lease one device, write a value, read it back, release it.

Uses:   the first available DummyDevice (the dev network has three).
Run:    LABNET_ENV_FILE=.devnet/researcher.env python examples/01_hello_device.py
Prints: dev01-dummy-a 12.5
"""

from __future__ import annotations

import time

from labnet import LabClient, LabClientError
from labnet_sim import DummyDevice


def main() -> None:
    # Start every LabNet object as None so `finally` knows what to close.
    central = None
    device = None
    try:
        # Reads LABNET_SERVER_URL, LABNET_API_KEY and (in the lab) LABNET_CA_FILE.
        central = LabClient.from_env()
        # Leases the first available dummy_device and connects to its Lab PC.
        device = DummyDevice(client=central)

        device.write_value(12.5)
        print(device.device_id, device.read_value())
        time.sleep(5)
    except LabClientError as error:
        raise SystemExit(f"LabNet error: {error}")
    finally:
        # Runs after success, an error, or Ctrl+C. Close in reverse order:
        # the device first (this releases its lease), then Central.
        if device is not None:
            device.close()
        if central is not None:
            central.close()


if __name__ == "__main__":
    main()
