"""Define pure validation errors for metadata schema definition contracts."""


class MetadataSchemaValidationError(ValueError):
    """Report invalid input to an Identity/Type metadata schema contract."""


class MetadataSchemaPublicationValidationError(ValueError):
    """Report invalid input to a metadata schema publication contract."""


class MetadataSchemaPersistenceError(ValueError):
    """Report one stable metadata schema persistence-contract failure."""

    @property
    def requires_fresh_read(self) -> bool:
        """Return whether the failed write requires an exact durable reread before retrying."""
        return str(self) == "metadata schema commit outcome unknown"
