# Troubleshooting

| Symptom | Likely cause | Check |
| --- | --- | --- |
| Central readiness is 503 | Missing migration or unreachable database | `labnet-central-admin --env-file central.env db upgrade`; check `LABNET_DATABASE_URL` |
| Admin CLI says the schema is not ready | CLI and server read different databases | Pass the same `--env-file` to both |
| Lab PC cannot register | Wrong key, or Central's certificate not trusted | Recreate or rotate the device_manager key; check `LABNET_CA_FILE` |
| Lab PC refuses a LAN address | No certificate, or no API key | Set both TLS files and `LABNET_DEVICE_MANAGER_API_KEY` |
| Lab PC refuses to start | No inventory, or no key in production | Set `LABNET_DEVICE_INVENTORY` and `LABNET_DEVICE_MANAGER_API_KEY` |
| Client sees no available devices | Lab PC down, device unhealthy, or every device leased | Both health endpoints and Central's device list |
| Client times out connecting to a TLS Lab PC | CA not configured, or IP not in the Lab PC certificate | `LABNET_CA_FILE`; compare `labnet-certs show` with `LABNET_GRPC_ADVERTISED_TARGET` |
| Proto check reports stale files | Generated bindings do not match the protos | `labnet driver build --core`, then commit every generated change |
| A device shows **Driver mismatch** or **No driver here** | The Lab PC and the Control Center run different driver packages or versions for that device type, or the Control Center lacks it | Install the same release of the driver package (for example `labnet-sim`) on both; the device's details say which. Automation refuses a mismatched device |
| Physical contract validation fails | Driver method missing or with an incompatible signature | Compare `physical_method` and forwarded fields with `physical.py` |
| Lease lost during an experiment | Renewal failure, lease deadline, or idle timeout | Client and Central logs; network stability; timeout policy |
