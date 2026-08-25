from __future__ import annotations

from abc import ABC, abstractmethod


class BaseDeviceInterface(ABC):
    """
    Generic interface that every physical and remote device should satisfy. This is meant to
    be minimal in nature and simply ensure that each device class has some minimum functionality
    for diagnostics and testing.
    """

    device_type: str

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Return the unique device ID."""
        raise NotImplementedError

    def allowed_methods(self) -> list[str]:
        """Return the physical methods exposed by this device's proto contract.

        The generated contract is the authority for remote exposure.  A driver
        author therefore never maintains a second, handwritten allow-list in
        ``physical.py``.
        """

        from labnet.grpc.generated.device_contracts import DEVICE_CONTRACT_BY_TYPE

        try:
            contract = DEVICE_CONTRACT_BY_TYPE[self.device_type]
        except KeyError as exc:
            raise RuntimeError(
                f"No generated gRPC contract exists for device_type "
                f"{self.device_type!r}. Add its service proto and run "
                "tools/generate_protos.py."
            ) from exc
        return list(contract.allowed_physical_methods)
