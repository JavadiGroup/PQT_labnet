from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version


def installed_version() -> str:
    """Return the installed LabNet distribution version.

    Source checkouts used by tests may not have distribution metadata, so the
    fallback is deliberately explicit rather than pretending to be a release.
    """

    try:
        return version("labnet")
    except PackageNotFoundError:
        return "0+source"


def contract_digest() -> str:
    """Hash the generated public RPC mapping reported by a Lab-PC runtime."""

    from labnet.grpc.generated.device_contracts import DEVICE_CONTRACTS

    payload = [
        {
            "device_type": contract.device_type,
            "service": contract.service_full_name,
            "methods": [
                {
                    "rpc": method.rpc_full_name,
                    "pattern": method.pattern,
                    "physical_method": method.physical_method,
                    "default_unit": method.default_unit,
                    "forwarded_fields": list(method.forwarded_fields),
                    "client_streaming": method.client_streaming,
                    "server_streaming": method.server_streaming,
                }
                for method in contract.methods
            ],
        }
        for contract in DEVICE_CONTRACTS
    ]
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["contract_digest", "installed_version"]
