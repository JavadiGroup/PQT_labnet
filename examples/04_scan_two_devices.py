"""04 - A small experiment across two Lab PCs: step a source, average a meter.

Uses:   a DummyDevice as the "source" (lab-pc-dev-01) and the SimulatedWavemeter
        as the "meter" (lab-pc-dev-02). Both are leased for the whole scan.
Run:    python examples/04_scan_two_devices.py [--output scan.csv]
Writes: one CSV row per setpoint: setpoint, mean wavelength, standard deviation.

The two simulated devices are independent, so the wavelength does not follow
the setpoint; with real instruments the same pattern measures a response.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from contextlib import ExitStack

from labnet import DummyDevice, LabClient, SimulatedWavemeter


SETPOINTS = [0.0, 0.25, 0.5, 0.75, 1.0]
SAMPLES_PER_POINT = 10


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default="scan.csv")
    args = parser.parse_args(argv)

    with LabClient.from_env() as central, ExitStack() as leases:
        # ExitStack releases the leases in reverse order, even after an error.
        source = leases.enter_context(DummyDevice(client=central))
        meter = leases.enter_context(SimulatedWavemeter(client=central))
        print(f"source: {source.device_id}   meter: {meter.device_id}")

        with open(args.output, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["setpoint", "wavelength_nm", "std_nm"])
            for setpoint in SETPOINTS:
                source.write_value(setpoint)
                readings = [
                    sample.value
                    for sample in meter.stream_wavelength(
                        sample_rate_hz=50, sample_count=SAMPLES_PER_POINT
                    )
                ]
                mean, spread = statistics.fmean(readings), statistics.stdev(readings)
                writer.writerow([setpoint, f"{mean:.6f}", f"{spread:.6f}"])
                print(f"setpoint {setpoint:4.2f} -> {mean:.6f} nm (+/- {spread:.6f})")

    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
