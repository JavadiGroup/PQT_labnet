class LabClientError(Exception):
    """Base exception for client-library failures."""


class AuthenticationError(LabClientError):
    """Raised when the API key is missing or invalid."""


class PermissionDeniedError(LabClientError):
    """Raised when the authenticated user lacks permission."""


class ResourceNotFoundError(LabClientError):
    """Raised when a requested server resource does not exist."""


class ResourceConflictError(LabClientError):
    """Raised when a device is busy or another conflict occurs."""


class ServerConnectionError(LabClientError):
    """Raised when the Central Server cannot be reached."""


class ServerResponseError(LabClientError):
    """Raised for an unexpected Central Server response."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(
            f"Central Server returned HTTP {status_code}: {message}"
        )


class NoAvailableDeviceError(LabClientError):
    """Raised when no idle device of the requested type exists."""