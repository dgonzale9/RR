"""Domain-level exceptions shared by Resource Registry persistence boundaries."""


class RegistryNamespacePersistenceError(Exception):
    """Report a Registry Namespace persistence invariant violation."""


class RegistryNamespaceImmutableFieldError(RegistryNamespacePersistenceError):
    """Report an attempted mutation of namespace identity or ownership."""


class RegistryNamespaceDeletionNotAllowedError(RegistryNamespacePersistenceError):
    """Report an ordinary attempt to delete a Registry Namespace."""
