"""05 - The same device classes without the network.

Uses:   local simulated devices; no Central, no Lab PC, no API key.
Run:    python examples/05_local_hardware.py
Prints: a write/read on a local DummyDevice and three local wavemeter samples.

Leaving out ``client=`` drives the physical driver in this process. This is
how you develop and test a driver on the Lab PC it is attached to: the method
names are the ones remote experiments will use. Nothing is leased, so stop
labnet-lab-pc first if it serves the same instrument.
"""

from __future__ import annotations

from labnet_sim import DummyDevice, SimulatedWavemeter


def main() -> None:
    source = None
    meter = None
    try:
        source = DummyDevice(device_id="local-dummy")
        source.write_value(2.5)
        print("local dummy reads", source.read_value())

        meter = SimulatedWavemeter(device_id="local-wavemeter")
        for sample in meter.stream_wavelength(sample_rate_hz=20, sample_count=3):
            print(f"local wavemeter {sample.sequence}: {sample.value:.6f} {sample.unit}")
    finally:
        # Disconnect the drivers even if a call fails.
        if meter is not None:
            meter.close()
        if source is not None:
            source.close()


if __name__ == "__main__":
    main()
