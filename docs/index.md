# LabNet

LabNet lets experiment programs anywhere on a lab network use instruments
attached to other computers safely: one user at a time, through typed
operations, with the instrument's own computer staying in charge of the
hardware.

- **New here?** The [Quickstart](quickstart.md) runs a whole simulated lab
  on your computer in two commands.
- **Writing experiments?** See [Clients](clients.md).
- **Adding an instrument?** See [Writing a driver](drivers.md).
- **Setting up a lab?** See [Deploying a lab](deploying/index.md).

## How the network fits together

There are three roles. In the lab each role runs on its own computer; for
development all three run on your laptop. Each role reads one settings file.

| Role | Command | Settings file | Listens on | Job |
| --- | --- | --- | --- | --- |
| **Central** | `labnet-central --env-file central.env` | `central.env` | HTTP `127.0.0.1:8008` (HTTPS `:443` via Caddy in the lab) | Users and API keys, the device directory, exclusive leases |
| **Lab PC** | `labnet-lab-pc --env-file lab-pc.env` | `lab-pc.env` | gRPC `:50051`; health `127.0.0.1:9001` | Owns the attached instruments and serves their operations |
| **Client** | your script, using `LabClient.from_env()` | `client.env` | nothing | Leases a device from Central, then talks directly to its Lab PC |
| **Control Center** | `labnet-control-center --env-file control-center.env` | `control-center.env` | HTTP `127.0.0.1:8080` (HTTPS via Caddy in the lab) | Web interface; a client of Central like any other |

```text
                  ┌─────────────────────────────┐
                  │           Central           │
                  │ API keys · devices · leases │
                  └──▲──────────────────▲────┬──┘
1. register devices, │       2. log in, │    │ 3. Lab PC address
           heartbeat │        request a │    │    + lease id
      5. check lease │            lease │    │
                     │                  │    │
                     │                  │    ▼
              ┌──────┴──────┐         ┌─┴──────────┐
              │    Lab PC   │◄────────│   Client   │
              │  + drivers  │         │(your code) │
              └─────────────┘         └────────────┘
                      4. gRPC + lease id, direct
```

1. At startup a Lab PC connects its instruments and registers them, along with
   its gRPC address, with Central. It keeps sending heartbeats.
2. A client logs in to Central with an API key and requests a lease on one
   device.
3. Central grants the lease only if nobody else holds the device, and returns
   the Lab PC's address and a lease id.
4. The client connects straight to that Lab PC and sends the lease id with
   every call.
5. The Lab PC checks the lease with Central before touching the hardware.

Leases last 60 s and the client renews them automatically every 20 s. If a
client crashes, its lease expires and the device frees itself. Central also
expires a lease after 15 minutes with no device commands.

## License

LabNet is licensed under the [MIT License](https://github.com/mill0851/labnet/blob/main/LICENSE).
