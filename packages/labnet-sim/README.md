# labnet-sim

Simulated LabNet devices, for developing and demonstrating experiments without
hardware: an optics bench (`DummyLaser`, `DummyAOM`, `DummyEOM`, `DummyAWG`),
`DummyDevice`, a motor-tuned `SimulatedTunableLaser` and a `SimulatedWavemeter`.

```
pip install labnet-sim
```

Install it on a Lab PC to serve these devices, and on clients and the Control
Center to use them. A Lab PC inventory creates the drivers:

```python
from labnet_sim import PhysicalDummyAOM, PhysicalSimulatedWavemeter

def create_devices():
    return [PhysicalDummyAOM("bench-aom"), PhysicalSimulatedWavemeter("bench-wavemeter")]
```

and a client uses the facades:

```python
from labnet import LabClient
from labnet_sim import DummyAOM

central = LabClient.from_env()
aom = DummyAOM(client=central)  # leases the first available dummy_aom
try:
    aom.set_frequency(80.0)
finally:
    aom.close()  # releases the lease
    central.close()
```

It is also a complete example of a LabNet driver package: protos in
`protos/labnet_sim/v1/`, drivers in `src/labnet_sim/<family>/physical.py`, and
everything in `src/labnet_sim/_generated/` built by `labnet driver build`.
Ready-made Lab PC inventories for a development network are in
`labnet_sim.inventories`.

Documentation: <https://javadigroup.github.io/labnet/drivers/> (writing a driver
package like this one). LabNet is MIT licensed. Source:
<https://github.com/JavadiGroup/labnet>.
