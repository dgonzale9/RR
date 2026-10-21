"""Stable sanitized upload application failures."""

from __future__ import annotations


class UploadApplicationError(RuntimeError):
    """Carry a stable public error code and HTTP status without provider details."""

    def __init__(
        self,
        code: str,
        http_status: int,
        message: str = "Upload request rejected.",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
