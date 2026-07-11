import os
from dotenv import load_dotenv
import httpx
import time

from labnet import (
    AuthenticationError,
    LabClient,
    ServerConnectionError,
    RemoteFakeDevice,
    ResourceConflictError,
    retrieve_client_info,
    setup_client
)


def main() -> None:

    url, api_key = retrieve_client_info()
    client = setup_client(url, api_key)

    try:
        # ----------------------------------
        # ---- Test 1: Device Discovery ----
        # ----------------------------------
        print("\nTest 1: Device Discovery...")
        available = client.list_available_devices(
            device_type = "fake_device"
        )

        assert available, "No test device discovered"

        selected_id = available[0].device_id

        print(f"Test Device ID: {selected_id}")


        # -----------------------------------
        # ---- Test 2: Lease Acquisition ----
        # -----------------------------------
        print("\nTest 2: Lease Acquisition...")
        test_device = RemoteFakeDevice(
            client=client,
            device_id=selected_id,
            lease_duration=30
        )

        print(f"Lease created: {test_device.lease_id}")
        print(f"Lease expires: {test_device.lease_expires_at}")

        device_info = client.get_device(selected_id)

        assert device_info.status == "in_use"
        print("Device status changed to in_use")


        # --------------------------------------------------
        # ---- Test 3: Verify Duplicate Lease Rejection ----
        # --------------------------------------------------
        print("\nTest 3: Verify Duplicate Lease Rejection...")
        try:
            RemoteFakeDevice(
                client=client,
                device_id=selected_id,
                lease_duration=30
            )
        except ResourceConflictError:
            print("Duplicate Lease correctly rejected")
        else:
            raise AssertionError(
                "Server incorrectly handled duplicate lease error"
            )
        

        # ----------------------------------------
        # ---- Test 4: Explicit Lease Release ----
        # ----------------------------------------
        print("\nTest 4: Explicit lease release...")
        test_device.close()
        device_info = client.get_device(selected_id)
        assert device_info.status == "idle"
        print("Leased device returned to idle")


        # --------------------------------------------
        # ---- Test 5: Automatic Lease Expiration ----
        # --------------------------------------------
        print("\nTest 5: Automatic Lease Expiration...")
        test_device = RemoteFakeDevice(
            client=client,
            lease_duration=3,
            device_id=selected_id
        )

        print(f"Created short lease: {test_device.lease_id}")
        print("Waiting for lease cleanup...")

        # Lease lasts 3 seconds and cleanup runs every 5 seconds.
        time.sleep(9)

        device = client.get_device(selected_id)

        assert device.status == "idle"
        print("Expired lease returned device to idle.")

        # close() tolerates a lease already removed by cleanup.
        test_device.close()    


        # ---------------------------------------------------
        # ---- Test 6: Verify Device can be leased again ----
        # ---------------------------------------------------
        test_device = RemoteFakeDevice(
            client=client,
            lease_duration=3,
            device_id=selected_id
        )

        print("Degvice was successfully leased again after expiration")

        test_device.close()
        print()
        print("All tests passed")
    finally:
        client.close()


if __name__ == "__main__":
    main()