"""03 - Stream measurements with the asyncio client.

Uses:   the SimulatedWavemeter on lab-pc-dev-02.
Run:    python examples/03_stream_async.py
Prints: one line per sample (sequence, wavelength in nm), 20 samples at 10 Hz.

Use asyncio when several devices, streams, or monitoring tasks must run at
the same time. Calls to one instrument still run one at a time on its Lab PC.
"""

from __future__ import annotations

import asyncio

from labnet import AsyncLabClient, LabClientError
from labnet_sim import SimulatedWavemeter


async def run() -> None:
    central = None
    device = None
    samples = None
    try:
        central = AsyncLabClient.from_env()
        device = await central.acquire_device(SimulatedWavemeter.contract)
        samples = device.stream_wavelength(sample_rate_hz=10, sample_count=20)

        async for sample in samples:
            print(f"{sample.sequence:3d}  {sample.value:.6f} {sample.unit}")
    except LabClientError as error:
        raise SystemExit(f"LabNet error: {error}")
    finally:
        # Reverse order: the stream, then the device (its lease), then Central.
        if samples is not None:
            await samples.aclose()
        if device is not None:
            await device.aclose()
        if central is not None:
            await central.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
