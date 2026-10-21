from __future__ import annotations


class TransformationInputReferenceResolutionError(Exception):
    """Raise a sanitized, stable input-reference resolution failure."""

    def __init__(self, *, code: str, message: str) -> None:
        """Initialize a resolution failure without resource identifiers.

        Args:
            code: Stable machine-readable failure code.
            message: Sanitized human-readable failure message.
        """
        super().__init__(message)
        self.code = code
        self.message = message
