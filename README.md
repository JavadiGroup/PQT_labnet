# LabNet

LabNet lets experiment programs anywhere on a lab network use instruments
attached to other computers safely: one user at a time, through typed
operations, with the instrument's own computer staying in charge of the
hardware.

- **Central** keeps users and API keys, the device directory, and exclusive
  leases.
- **Lab PCs** serve their instruments' operations over gRPC. They check the
  lease and the instrument's limits on every call, from any client.
- **Clients** (your experiment scripts) lease a device from Central, then talk
  directly to its Lab PC: `meter = SimulatedWavemeter(client=central)`.
- **The Control Center** is a web interface for the whole network: live
  device pages, saved experiments, and optionally an AI assistant and
  supervised automation that a person approves step by step.
- **Drivers** are ordinary Python packages. Describe an instrument in a
  `.proto` file, wrap its SDK in one class, and `labnet driver build`
  generates the typed client, the server side and the checks.

## Try it

A whole simulated lab on your computer (Python 3.11 or newer):

```bash
pip install "labnet[central,lab-pc]" labnet-sim labnet-control-center
labnet dev up
```

Then open <http://127.0.0.1:8080/status>. The
[Quickstart](docs/quickstart.md) continues from there.

## Documentation

| | |
| --- | --- |
| [Quickstart](docs/quickstart.md) | A simulated lab on your computer in two commands |
| [Concepts](docs/concepts.md) | Central, Lab PCs, leases, and what happens on one call |
| [Clients](docs/clients.md) | Writing experiment scripts |
| [Writing a driver](docs/drivers.md) | Your instrument as a driver package |
| [Deploying a lab](docs/deploying/index.md) | Computers, certificates, settings, bundles and updates |
| [Control Center](docs/control-center/index.md) | The web interface, its logins, and Photo-Agent |
| [Automation](docs/automation/index.md) | Agent-run experiments, and the [security model](docs/automation/security.md) that keeps people in charge |
| [Development](docs/development.md) | Working on LabNet itself |
| [Migrating to 3.0](docs/migration-3.0.md) | For labs running LabNet 2.x |

The same pages, with the API reference, build into a site with
`uv run --group docs mkdocs serve`.

## Packages

| Package | What it is |
| --- | --- |
| [`labnet`](packages/labnet/) | Central, the Lab PC runtime, the client library, the driver tooling, and the `labnet` command |
| [`labnet-control-center`](packages/labnet-control-center/) | The web interface, and the safe-state watchdog for automation |
| [`labnet-automation`](packages/labnet-automation/) | The Automation runner and its container images |
| [`labnet-sim`](packages/labnet-sim/) | Simulated instruments, and a complete example of a driver package |

## Working on LabNet

```bash
git clone https://github.com/JavadiGroup/labnet && cd labnet
uv sync                       # every package, editable, with the test tools
uv run labnet dev up          # the development network
uv run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for how changes are made and checked.
A lab's own configuration lives in a site folder; [`sites/pqt/`](sites/pqt/README.md)
is one lab's.

## Security

Report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md).
Plaintext is allowed only on loopback: LabNet refuses plaintext Central or
gRPC endpoints on any other address.

## License

LabNet is licensed under the [MIT License](LICENSE).
