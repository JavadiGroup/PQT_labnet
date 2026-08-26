# Client example

Install the base package from `Labnet/`, set `LABNET_API_KEY`, and run from the
repository root:

```powershell
$env:LABNET_SERVER_URL = "http://127.0.0.1:8008"
$env:LABNET_API_KEY = "<researcher API key>"
python .\examples\client_test\client.py
```

The equivalent asyncio example performs a write, read, and short streamed
acquisition while lease renewal remains non-blocking:

```powershell
python .\examples\client_test\async_client.py
```

For a remote TLS deployment, also set `SSL_CERT_FILE` for Central HTTPS and
`LABNET_GRPC_CA_FILE` for the Lab-PC gRPC certificate authority. The optional
`LABNET_GRPC_CLIENT_KEY_FILE` and `LABNET_GRPC_CLIENT_CERT_FILE` values enable
mTLS.
