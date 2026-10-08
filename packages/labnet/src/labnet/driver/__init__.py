"""Building device drivers: compile protos, validate them, render contracts.

``labnet driver build`` is the command line; :func:`build` the library. Needs
``labnet[driver-dev]`` (grpcio-tools).
"""

from __future__ import annotations

from .build import BuildTarget, build, core_proto_root, core_target, import_check
from .validate import ContractValidationError, ForwardedFieldSpec, RpcContractSpec, validate_descriptor_set

__all__ = [
    "BuildTarget",
    "ContractValidationError",
    "ForwardedFieldSpec",
    "RpcContractSpec",
    "build",
    "core_proto_root",
    "core_target",
    "import_check",
    "validate_descriptor_set",
]
