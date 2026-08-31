# Client examples

This directory contains synchronous and asyncio programs that authenticate
with Central, discover an available instrument, acquire an exclusive lease,
and connect directly to the selected Lab PC over gRPC.

| File | Programming model | Demonstrates |
| --- | --- | --- |
| `client.py` | Synchronous | Discovery, lease acquisition, write, read, and cleanup |
| `async_client.py` | Asyncio | Discovery, lease acquisition, write, read, streaming, and async cleanup |
| `.env.example` | Environment reference | Central URL, API key, and public CA path |

## Prerequisites

Before running a client:

1. Central must report ready.
2. A Lab PC must be registered and healthy.
3. At least one `dummy_device` must be available.
4. You need an API key for a Central user with role `user` or `admin`.
5. For remote deployment, the client must trust both Central and Lab-PC
   certificates.

## Install

Clients need only the base package:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .\Labnet
```

## Local plaintext setup

Plaintext is accepted only because the local endpoints use loopback.

```powershell
$env:LABNET_SERVER_URL = "http://127.0.0.1:8008"
$env:LABNET_API_KEY = "<researcher API key>"
```

Run the synchronous example:

```powershell
python .\examples\client_test\client.py
```

It prints the value returned by the example device.

Run the asyncio example:

```powershell
python .\examples\client_test\async_client.py
```

It prints one read result followed by five streamed samples. The async client
renews its lease without blocking the event loop.

## TLS setup

The client normally receives only the public CA certificate. It does not need
Central's or the Lab PC's private key. Put the CA certificate in a directory
readable by the user running the client:

```text
C:\ProgramData\LabNet\certs\labnet-ca.pem
```

That one public file can establish both trust paths when the same private CA
issued Central's HTTPS certificate and the Lab PC's gRPC certificate. Configure
Central HTTPS trust through `SSL_CERT_FILE` and Lab-PC gRPC trust through
`LABNET_GRPC_CA_FILE`:

```powershell
$env:LABNET_SERVER_URL = "https://192.168.50.10"
$env:LABNET_API_KEY = "<researcher API key>"
$env:SSL_CERT_FILE = "C:\ProgramData\LabNet\certs\labnet-ca.pem"
$env:LABNET_GRPC_CA_FILE = "C:\ProgramData\LabNet\certs\labnet-ca.pem"
```

`SSL_CERT_FILE` is used when the client makes HTTPS requests to Central.
`LABNET_GRPC_CA_FILE` is read by this example and passed to gRPC when it makes a
direct instrument connection. They refer to the same file here but configure
two different networking libraries. The gRPC server certificate must contain
the exact hostname or IP advertised by Central for the Lab PC.

The client needs no certificate or private key. The CA/private-key mental model
and complete one-CA setup are in the main
[certificate guide](../../Labnet/README.md#generate-the-minimum-tls-certificate-set).

## Synchronous lifecycle

The synchronous pattern is:

```python
from labnet import DummyDevice, LabClient

central = LabClient("https://192.168.50.10", "<API key>")
try:
    device = DummyDevice(device_id="dummy-bench-a", client=central)
    try:
        device.write_value(12.5)
        print(device.read_value())
    finally:
        device.close()
finally:
    central.close()
```

Create one `LabClient` per process and share it across remote device objects.
Each device object owns its own lease and gRPC channel lifecycle.

## Asyncio lifecycle

The async pattern is:

```python
import asyncio

from labnet import AsyncLabClient


async def run() -> None:
    central = AsyncLabClient("https://192.168.50.10", "<API key>")
    try:
        devices = await central.list_available_devices("dummy_device")
        device = await central.acquire_dummy(devices[0].device_id)
        try:
            measurement = await device.read_value()
            print(measurement.value, measurement.unit)
        finally:
            await device.aclose()
    finally:
        await central.aclose()


asyncio.run(run())
```

Use asyncio when independent device operations, monitoring tasks, or streams
must overlap. Commands sent to one physical instrument remain serialized on the
Lab PC.

## Cleanup requirements

Always close resources in the reverse order in which they were acquired:

1. close a stream;
2. close the device, which releases its lease; and
3. close the Central client.

If the client process crashes, automatic lease renewal stops and Central
expires the lease. Explicit cleanup is still preferred because it makes the
instrument immediately available to another user.

## Adapting the examples

For another device family:

- replace the discovery type `dummy_device` with the generated device type;
- use that family's public facade for synchronous code; or
- pass its generated contract to `AsyncLabClient.acquire_device()` for async
  code; and
- call the operations declared by that family's protobuf contract.

Keep API keys out of source files. Use environment variables, an OS credential
store, or a secrets manager.
