# Client API

Experiment scripts use `LabClient` (or `AsyncLabClient`) to talk to Central,
and a driver package's facades (such as `labnet_sim.SimulatedWavemeter`) to
use a device. Everything here is importable from `labnet` directly:
`from labnet import LabClient, LabClientError`.

::: labnet.client.core.lab_client
    options:
      members: [LabClient, AsyncLabClient]

## Results and descriptions

::: labnet.client.core.models

## Errors

::: labnet.client.core.exceptions
