"""03 - Stream measurements with the asyncio client.

Uses:   the SimulatedWavemeter on lab-pc-dev-02.
Run:    python examples/03_stream_async.py
Prints: one line per sample (sequence, wavelength in nm), 20 samples at 10 Hz.

Use asyncio when several devices, streams, or monitoring tasks must run at
the same time. Calls to one instrument still run one at a time on its Lab PC.
"""

from __future__ import annotations

import asyncio

from labnet import AsyncLabClient, SimulatedWavemeter


async def run() -> None:
    async with AsyncLabClient.from_env() as central:
        device = await central.acquire_device(SimulatedWavemeter.contract)
        try:
            samples = device.stream_wavelength(sample_rate_hz=10, sample_count=20)
            try:
                async for sample in samples:
                    print(f"{sample.sequence:3d}  {sample.value:.6f} {sample.unit}")
            finally:
                await samples.aclose()   # 1. close the stream
        finally:
            await device.aclose()        # 2. release the lease
    # 3. leaving the block closes the Central client


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
