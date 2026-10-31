"""Expose stable, sanitized dataset management failures."""


class DatasetManagementError(ValueError):
    """Describe a rejected operation without exposing storage or foreign resources."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        """Initialize the error.

        Args:
            code: Stable machine-readable failure code.
            message: Safe explanation for the current user.
            status: Corresponding HTTP status.
        """
        super().__init__(message)
        self.code: str = code
        self.status: int = status
