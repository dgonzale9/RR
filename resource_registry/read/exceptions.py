class DataRegistryReadError(Exception):
    """Base read-service error."""


class RegistryNamespaceNotFoundError(DataRegistryReadError):
    """Raised when a Registry Namespace UUID cannot be resolved."""


class WorkspaceRegistryNamespaceNotFoundError(DataRegistryReadError):
    """Raised when a Workspace has no Registry Namespace mapping."""


class DatasetNotFoundError(DataRegistryReadError):
    """Raised when a dataset cannot be found for a tenant."""


class DatasetVersionNotFoundError(DataRegistryReadError):
    """Raised when a dataset version cannot be found for a tenant."""


class ArtifactNotFoundError(DataRegistryReadError):
    """Raised when an artifact cannot be found for a tenant."""


class TransformationDefinitionNotFoundError(DataRegistryReadError):
    """Raised when a transformation definition cannot be found for a tenant."""


class TransformationDefinitionVersionNotFoundError(DataRegistryReadError):
    """Raised when a transformation definition version cannot be found for a tenant."""


class InvalidTransformationDefinitionVersionStatusFilterError(DataRegistryReadError):
    """Raised when a transformation definition version status filter is unsupported."""


class TenantBoundaryError(DataRegistryReadError):
    """Raised when a tenant boundary would be violated."""


class UnsafeReadReferenceError(DataRegistryReadError):
    """Raised when a read reference is unsafe for consumer exposure."""
