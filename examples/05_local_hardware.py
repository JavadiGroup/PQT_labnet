"""05 - The same device classes without the network.

Uses:   local simulated devices; no Central, no Lab PC, no API key.
Run:    python examples/05_local_hardware.py
Prints: a write/read on a local DummyDevice and three local wavemeter samples.

Leaving out ``client=`` drives the physical driver in this process. This is
how you develop and test a driver on the Lab PC it is attached to: the method
names are the ones remote experiments will use.
"""

from __future__ import annotations

from labnet import DummyDevice, SimulatedWavemeter


def main() -> None:
    with DummyDevice(device_id="local-dummy") as source:
        source.write_value(2.5)
        print("local dummy reads", source.read_value())

    with SimulatedWavemeter(device_id="local-wavemeter") as meter:
        for sample in meter.stream_wavelength(sample_rate_hz=20, sample_count=3):
            print(f"local wavemeter {sample.sequence}: {sample.value:.6f} {sample.unit}")


if __name__ == "__main__":
    main()
