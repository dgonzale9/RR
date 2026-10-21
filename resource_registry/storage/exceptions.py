class DataRegistryStorageError(Exception):
    """Base storage abstraction error."""


class StorageConfigurationError(DataRegistryStorageError):
    """Raised when required storage provider configuration is missing/invalid."""


class StorageObjectNotFoundError(DataRegistryStorageError):
    """Raised when a storage object cannot be found."""


class StorageProviderUnsupportedError(DataRegistryStorageError):
    """Raised when an unsupported storage provider key is requested."""


class UnsafeStorageUriError(DataRegistryStorageError):
    """Raised when URI/path input is unsafe for storage operations."""


class UnsupportedArtifactReadError(DataRegistryStorageError):
    """Raised when no artifact read adapter can handle a URI."""


class ArtifactMaterializationError(DataRegistryStorageError):
    """Raised when an adapter cannot materialize an artifact to local temp storage."""
