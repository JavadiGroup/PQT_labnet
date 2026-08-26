"""Async Central discovery, leasing, and direct gRPC device control example."""

from __future__ import annotations

import asyncio
import os

from labnet import AsyncLabClient, GrpcTlsConfig


def _tls_config() -> GrpcTlsConfig | None:
    ca_file = os.getenv("LABNET_GRPC_CA_FILE")
    client_key = os.getenv("LABNET_GRPC_CLIENT_KEY_FILE")
    client_cert = os.getenv("LABNET_GRPC_CLIENT_CERT_FILE")
    if not any((ca_file, client_key, client_cert)):
        return None
    return GrpcTlsConfig.from_files(
        root_certificates=ca_file,
        private_key=client_key,
        certificate_chain=client_cert,
    )


async def run() -> None:
    server_url = os.environ.get("LABNET_SERVER_URL", "http://127.0.0.1:8008")
    api_key = os.environ["LABNET_API_KEY"]
    central = AsyncLabClient(server_url, api_key)

    try:
        available = await central.list_available_devices("dummy_device")
        if not available:
            raise RuntimeError("Central reported no available dummy_device.")

        device = await central.acquire_dummy(
            device_id=available[0].device_id,
            tls=_tls_config(),
        )
        try:
            await device.write_value(12.5)
            measurement = await device.read_value()
            print(measurement.value, measurement.unit)

            samples = device.stream_data(sample_rate_hz=10, sample_count=5)
            try:
                async for sample in samples:
                    print(sample.sequence, sample.value, sample.unit)
            finally:
                await samples.aclose()
        finally:
            await device.aclose()
    finally:
        await central.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
