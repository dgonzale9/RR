class ArtifactSecurityError(Exception):
    """Base error for artifact security checks."""


class ArtifactGuardrailViolation(ArtifactSecurityError):  # noqa: N818
    """Raised when artifact guardrail policy blocks materialization."""

    def __init__(  # noqa: PLR0913
        self,
        message: str,
        *,
        violated_rule: str | None = None,
        expected_value: object | None = None,
        actual_value: object | None = None,
        policy_source: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        super().__init__(message)
        self.violation_metadata = {
            "violated_rule": violated_rule,
            "expected_value": expected_value,
            "actual_value": actual_value,
            "policy_source": policy_source,
            "policy_revision": policy_revision,
        }


class ArtifactIntegrityError(ArtifactSecurityError):
    """Raised when artifact checksum verification fails."""


class UnsupportedArtifactFormatError(ArtifactGuardrailViolation):
    """Raised when artifact format is not allowed by policy."""


class ArtifactTooLargeError(ArtifactGuardrailViolation):
    """Raised when artifact exceeds allowed materialization size."""


class UnsafeArtifactUriError(ArtifactGuardrailViolation):
    """Raised when artifact URI fails safety checks."""


class UnsupportedStorageProviderError(ArtifactGuardrailViolation):
    """Raised when storage provider is not allowed by policy."""
