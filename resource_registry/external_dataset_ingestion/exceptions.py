"""Sanitized external dataset ingestion failures."""


class ExternalDatasetIngestionError(RuntimeError):
    """Carry one stable API-safe failure code and HTTP status."""

    def __init__(self, code: str, http_status: int, message: str) -> None:
        """Initialize a sanitized failure.

        Args:
            code: Stable client-visible failure code.
            http_status: HTTP status used by the transport adapter.
            message: Sanitized client-visible message.
        """
        super().__init__(message)
        self.code = code
        self.http_status = http_status
