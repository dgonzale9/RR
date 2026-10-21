"""Stable application-service failures for Registry Namespace operations."""


class RegistryNamespaceServiceError(Exception):
    """Report a Registry Namespace application-service failure."""


class RegistryNamespaceInvalidIdentifierError(RegistryNamespaceServiceError):
    """Report a malformed Registry Namespace UUID."""


class RegistryNamespaceWorkspaceNotFoundError(RegistryNamespaceServiceError):
    """Report that the requested Workspace does not exist."""


class RegistryNamespaceWorkspaceConflictError(RegistryNamespaceServiceError):
    """Report that a Workspace already owns a Registry Namespace."""


class RegistryNamespaceOrganizationNotFoundError(RegistryNamespaceServiceError):
    """Report that the requested Organization does not exist."""


class RegistryNamespaceOrganizationConflictError(RegistryNamespaceServiceError):
    """Report that an Organization already owns a Registry Namespace."""


class RegistryNamespaceIdentifierConflictError(RegistryNamespaceServiceError):
    """Report that a Registry Namespace UUID is already mapped."""


class RegistryNamespaceOrganizationMismatchError(RegistryNamespaceServiceError):
    """Report that a Workspace is outside the expected Organization."""


class RegistryNamespacePersistenceConflictError(RegistryNamespaceServiceError):
    """Report an unclassified concurrent Registry Namespace persistence conflict."""
