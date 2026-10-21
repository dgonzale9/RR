"""Verify Resource Registry filesystem isolation in the Django test settings."""

from pathlib import Path

from django.conf import settings


def test_resource_registry_test_directories_use_isolated_temporary_root() -> None:
    """Keep local storage, query work, and dataset cache paths outside the repository."""
    isolated_root = Path(settings.TEST_RESOURCE_REGISTRY_ROOT).resolve()
    repository_root = Path(settings.BASE_DIR).resolve()
    configured_directories = (
        Path(settings.RESOURCE_REGISTRY_LOCAL_STORAGE_DIR).resolve(),
        Path(settings.RESOURCE_REGISTRY_QUERY_TEMP_DIR).resolve(),
        Path(settings.RESOURCE_REGISTRY_DATASET_CACHE_DIR).resolve(),
    )

    assert not isolated_root.is_relative_to(repository_root)
    assert all(directory.is_relative_to(isolated_root) for directory in configured_directories)
