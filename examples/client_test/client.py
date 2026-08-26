"""Authenticate, lease a dummy device, and call it directly over gRPC."""

from __future__ import annotations

import os

from labnet import DummyDevice, GrpcTlsConfig, LabClient


def _tls_config() -> GrpcTlsConfig | None:
    ca_file = os.getenv("LABNET_GRPC_CA_FILE")
    if not ca_file:
        return None
    return GrpcTlsConfig.from_files(root_certificates=ca_file)


def main() -> None:

    server_url = os.environ.get("LABNET_SERVER_URL", "http://127.0.0.1:8008")
    api_key = os.environ["LABNET_API_KEY"]
    central = LabClient(server_url, api_key)

    try:
        # Check Device availability
        available = central.list_available_devices("dummy_device")
        if not available:
            raise RuntimeError("Central reported no available dummy_device.")

        # Instantiate device
        device = DummyDevice(
            device_id=available[0].device_id,
            client=central,
            tls=_tls_config(),
        )

        try:
            # Experiment Logic
            device.write_value(12.5)
            print(device.read_value())



    # Close device and network connections
        finally:
            device.close()
    finally:
        central.close()


if __name__ == "__main__":
    main()
