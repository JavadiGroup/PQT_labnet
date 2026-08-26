# Lab-PC example

This example composes the installed `labnet` device-manager and gRPC APIs with
a machine-specific configuration and physical-device inventory. The checked-in
inventory creates one `PhysicalDummyDevice`.

Install `Labnet[lab-pc]`, copy `.env.example` to `.env`, edit the inventory as
needed before deployment, and run from the repository root:

```powershell
python -m examples.lab_pc
```
