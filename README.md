# LabNet repository

LabNet is a lease-controlled network for laboratory devices. The installable
Python project is self-contained in [`Labnet/`](Labnet/); runnable compositions
that consume the installed package live in [`examples/`](examples/).

| Path | Purpose |
| --- | --- |
| `Labnet/labnet/` | Installed `labnet` package, including reusable Central functionality |
| `Labnet/protos/` | Source protobuf contracts |
| `Labnet/tools/` | Development-time protobuf generation |
| `Labnet/tests/` | Package, Central, integration, and example tests |
| `examples/central_server/` | Minimal Central deployment composition |
| `examples/lab_pc/` | Lab-PC configuration and physical inventory example |
| `examples/client_test/` | Direct client/lease/gRPC example |

Install a development environment from the Python-project directory:

```powershell
cd Labnet
python -m pip install -e ".[dev,central,lab-pc]"
python tools/generate_protos.py --check
python -m pytest
```

See [`Labnet/README.md`](Labnet/README.md) for architecture, deployment,
static-IP setup, and device-development instructions.
