"""Address exact managed query-cache objects without accepting arbitrary local paths."""

from resource_registry.storage.local import LocalStorageProvider


class DatasetCacheStorageProvider(LocalStorageProvider):
    """Restrict cache cleanup to the server-configured Registry materialization directory."""

    URI_SCHEME = "dataset-cache://"
    PROVIDER_NAME = "dataset_cache"
