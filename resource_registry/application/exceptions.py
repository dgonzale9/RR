class DataRegistryApplicationError(Exception):
    """Base error for application-layer data registry workflows."""


class ValidationError(DataRegistryApplicationError):
    """Raised when command/application validation fails."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str = "validation_error",
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)


class DuplicateDatasetError(DataRegistryApplicationError):
    """Raised when dataset uniqueness constraints are violated."""


class UnsupportedDatasetSourceError(ValidationError):
    """Raised when a dataset source configuration is unsupported."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            status=400,
            code="unsupported_dataset_format",
        )


class DatasetRegistrationError(DataRegistryApplicationError):
    """Raised when dataset registration fails unexpectedly."""
