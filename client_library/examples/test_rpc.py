from __future__ import annotations

import os
import time
from dotenv import load_dotenv

from labnet import LabClient
from labnet.devices import RemoteFakeDevice
from labnet.rpc import RpcError, RpcConnectionClosedError

from labnet import (
    LabClient,
    RemoteFakeDevice,
    RpcError,
    RpcConnectionClosedError,
    retrieve_client_info,
    setup_client
)

load_dotenv()
SERVER_URL, API_KEY = retrieve_client_info()


def main() -> None:
    client = setup_client(SERVER_URL, API_KEY)

    try:
        # ----- TEST 1: Device Discovery ----
        print("\nTest 1: Device Discovery...")
        devices = client.list_available_devices(
            device_type="fake_device",
        )

        assert devices, "No idle fake_device found."

        device_id = devices[0].device_id
        print(f"   Found device: {device_id}")

        # ----- TEST 2: Create Lease and Persistent RPC Connection -----
        print("\nTest 2: Creating lease and persistent RPC connection...")
        remote = RemoteFakeDevice(
            client=client,
            device_id=device_id,
            lease_duration=30,
        )

        print(f"   Lease: {remote.lease.lease_id}")
        print(f"   RPC URL: {remote.device.rpc_url}")

        # ----- TEST 3: Repeated RPC call test -----
        print("\n Test 3: Performing repeated RPC calls over same WebSocket...")
        for i in range(5):
            expected = float(i) + 0.25

            remote.write_value(expected)
            actual = remote.read_value()

            assert actual == expected

            print(f"   Round trip {i + 1}: actual = {actual}")

        # ----- Test 4: Closing remote device -----
        print("\n Test 4: Closing remote device...")
        old_rpc = remote.rpc
        remote.close()

        try:
            old_rpc.call(
                lease_id=remote.lease.lease_id,
                device_id=remote.device.device_id,
                method="read_value",
            )
        except RpcConnectionClosedError:
            print("   Closed RPC connection correctly rejected call.")
        else:
            raise AssertionError(
                "RPC call succeeded after connection was closed."
            )
        
        # ----- Test 5: Testing expired lease on open websocket -----
        print("\n Test 5: Testing expired lease on an open WebSocket...")
        expiring = RemoteFakeDevice(
            client=client,
            device_id=device_id,
            lease_duration=2,
        )

        expiring.write_value(99.0)
        assert expiring.read_value() == 99.0

        print("   Waiting for lease to expire...")
        time.sleep(4)

        try:
            expiring.read_value()
        except RpcError as exc:
            assert exc.code == "LEASE_INVALID"
            print("   Expired lease correctly rejected.")
        else:
            raise AssertionError(
                "Expired lease was incorrectly allowed."
            )

        expiring.close()

        print()
        print("All persistent RPC tests passed.")

    finally:
        client.close()


if __name__ == "__main__":
    main()