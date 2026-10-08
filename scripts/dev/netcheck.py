"""Inspect or exercise a running LabNet network as a researcher client.

    python scripts/dev/netcheck.py status   # what Central knows about each device
    python scripts/dev/netcheck.py smoke    # lease every available device and call it

Connects with LabClient.from_env(); the scripts/dev wrappers point
LABNET_ENV_FILE at .devnet/researcher.env.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from labnet import AsyncLabClient, LabClient
from labnet.contracts import registry


def status() -> int:
    with LabClient.from_env() as central:
        devices = central.list_devices()
    if not devices:
        print("Central has no registered devices.")
        return 1
    header = ("DEVICE", "TYPE", "LAB PC", "gRPC TARGET", "STATE", "LEASABLE")
    rows = [
        (
            device.device_id,
            device.device_type,
            device.manager_id,
            device.endpoint.target,
            device.runtime_state,
            "yes" if device.effective_available else "no",
        )
        for device in sorted(devices, key=lambda item: (item.manager_id, item.device_id))
    ]
    widths = [max(len(str(row[i])) for row in (header, *rows)) for i in range(len(header))]
    for row in (header, *rows):
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))
    return 0


async def _smoke() -> int:
    failures = 0
    async with AsyncLabClient.from_env() as central:
        devices = await central.list_available_devices()
        if not devices:
            print("FAIL no available devices reported by Central.")
            return 1
        for info in sorted(devices, key=lambda item: item.device_id):
            contract = registry.find(info.device_type)
            if contract is None:
                print(f"SKIP {info.device_id}: no generated contract for {info.device_type!r}")
                continue
            # Only reads are attempted, so smoke testing never moves real hardware.
            reads = [m for m in contract.methods if m.pattern == "RPC_PATTERN_SCALAR_READ"]
            try:
                device = await central.acquire_device(contract, info.device_id)
                try:
                    if reads:
                        measurement = await getattr(device, reads[0].python_name)()
                        detail = f"{reads[0].python_name}() = {measurement.value} {measurement.unit}"
                    else:
                        detail = "leased (no scalar read RPC to call)"
                finally:
                    await device.aclose()
            except Exception as exc:  # report every device, then fail overall
                failures += 1
                print(f"FAIL {info.device_id} via {info.endpoint.target}: {type(exc).__name__}: {exc}")
            else:
                print(f"OK   {info.device_id} via {info.manager_id} {info.endpoint.target}: {detail}")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["status", "smoke"])
    command = parser.parse_args().command
    sys.exit(status() if command == "status" else asyncio.run(_smoke()))


if __name__ == "__main__":
    main()
