"""02 - See what Central offers, then lease a specific device.

Uses:   any device; by default the first dummy device on lab-pc-dev-02.
Run:    python examples/02_choose_device.py                       # list + default pick
        python examples/02_choose_device.py --device dev01-dummy-b
        python examples/02_choose_device.py --lab-pc lab-pc-dev-01
Prints: Central's device table, then which device was leased and a read.
"""

from __future__ import annotations

import argparse

from labnet import LabClient, LabClientError
from labnet_sim import DummyDevice


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", help="Lease this device id.")
    parser.add_argument("--lab-pc", default="lab-pc-dev-02", help="Else: first dummy device on this Lab PC.")
    args = parser.parse_args(argv)

    central = None
    device = None
    try:
        central = LabClient.from_env()

        print(f"{'DEVICE':<16} {'TYPE':<20} {'LAB PC':<15} LEASABLE")
        for info in central.list_devices():
            leasable = "yes" if info.effective_available else "no"
            print(f"{info.device_id:<16} {info.device_type:<20} {info.manager_id:<15} {leasable}")

        device_id = args.device
        if device_id is None:
            on_lab_pc = [
                info.device_id
                for info in central.list_available_devices("dummy_device")
                if info.manager_id == args.lab_pc
            ]
            if not on_lab_pc:
                raise SystemExit(f"No available dummy device on {args.lab_pc}.")
            device_id = on_lab_pc[0]

        device = DummyDevice(device_id=device_id, client=central)
        print(f"\nLeased {device.device_id}; it reads {device.read_value()}")
    except LabClientError as error:
        raise SystemExit(f"LabNet error: {error}")
    finally:
        if device is not None:
            device.close()
        if central is not None:
            central.close()


if __name__ == "__main__":
    main()
