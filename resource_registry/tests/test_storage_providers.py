from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import ResourceNotFoundError
from django.conf import settings

from resource_registry.storage.azure_blob import AzureBlobStorageProvider
from resource_registry.storage.exceptions import (
    StorageConfigurationError,
    StorageProviderUnsupportedError,
    UnsafeStorageUriError,
)
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.local import LocalStorageProvider

CHUNK_SIZE = 64 * 1024
BLOB_SIZE_BYTES = 123


def test_local_put_exists_head_get_uri_delete(tmp_path: Path) -> None:
    source_file = tmp_path / "source.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=tmp_path / "store")

    metadata = provider.put(str(source_file), "datasets/sample.csv")

    assert metadata.provider == "local"
    assert metadata.uri.startswith("local://")
    assert metadata.size_bytes == source_file.stat().st_size
    assert provider.exists("datasets/sample.csv")
    head = provider.head("local://datasets/sample.csv")
    assert head.uri == "local://datasets/sample.csv"
    assert provider.get_uri("datasets/sample.csv") == "local://datasets/sample.csv"
    provider.delete("datasets/sample.csv")
    assert provider.exists("datasets/sample.csv") is False


def test_local_path_traversal_rejected(tmp_path: Path) -> None:
    provider = LocalStorageProvider(base_directory=tmp_path / "store")
    with pytest.raises(UnsafeStorageUriError):
        provider.get_uri("../secrets.txt")


def test_local_put_uses_copyfileobj(tmp_path: Path) -> None:
    source_file = tmp_path / "source.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=tmp_path / "store")

    with patch(
        "resource_registry.storage.local.shutil.copyfileobj",
        wraps=shutil.copyfileobj,
    ) as mocked_copy:
        provider.put(str(source_file), "datasets/sample.csv")
    mocked_copy.assert_called_once()


def test_local_download_to_file_uses_copyfileobj(tmp_path: Path) -> None:
    source_file = tmp_path / "source.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=tmp_path / "store")
    provider.put(str(source_file), "datasets/sample.csv")
    target_file = tmp_path / "downloaded.csv"

    with patch(
        "resource_registry.storage.local.shutil.copyfileobj",
        wraps=shutil.copyfileobj,
    ) as mocked_copy:
        metadata = provider.download_to_file("local://datasets/sample.csv", str(target_file))

    assert target_file.exists()
    assert metadata.provider == "local"
    mocked_copy.assert_called_once()


def test_azure_configuration_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AZ_STORAGE_ACCOUNT_NAME", None, raising=False)
    monkeypatch.setattr(settings, "AZ_STORAGE_ACCOUNT_KEY", None, raising=False)
    monkeypatch.setattr(settings, "AZ_STORAGE_MEDIA_CONTAINER", None, raising=False)
    monkeypatch.delenv("AZ_STORAGE_ACCOUNT_NAME", raising=False)
    monkeypatch.delenv("AZ_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("AZ_STORAGE_MEDIA_CONTAINER", raising=False)
    with pytest.raises(StorageConfigurationError):
        AzureBlobStorageProvider()


def test_azure_unsafe_destination_rejected(tmp_path: Path) -> None:
    source_file = tmp_path / "source.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = AzureBlobStorageProvider(
        account_name="acct",
        account_key="key",
        container_name="media",
        blob_service_client=MagicMock(),
    )
    with pytest.raises(UnsafeStorageUriError):
        provider.put(str(source_file), "../unsafe.csv")


def test_azure_sas_uri_rejected() -> None:
    provider = AzureBlobStorageProvider(
        account_name="acct",
        account_key="key",
        container_name="media",
        blob_service_client=MagicMock(),
    )
    with pytest.raises(UnsafeStorageUriError):
        provider.head("https://acct.blob.core.windows.net/media/file.csv?sig=secret")


def test_azure_put_uses_file_stream(tmp_path: Path) -> None:
    source_file = tmp_path / "source.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")

    blob_client = MagicMock()
    blob_properties = SimpleNamespace(
        size=source_file.stat().st_size,
        etag="etag-1",
        last_modified=None,
        metadata={},
        content_settings=SimpleNamespace(content_type="text/csv", content_md5=None),
    )
    blob_client.get_blob_properties.return_value = blob_properties
    container_client = MagicMock()
    container_client.get_blob_client.return_value = blob_client
    service_client = MagicMock()
    service_client.get_container_client.return_value = container_client
    provider = AzureBlobStorageProvider(
        account_name="acct",
        account_key="key",
        container_name="media",
        blob_service_client=service_client,
    )

    provider.put(str(source_file), "datasets/sample.csv")

    upload_call = blob_client.upload_blob.call_args
    assert hasattr(upload_call.args[0], "read")


def test_azure_head_maps_blob_properties() -> None:
    blob_client = MagicMock()
    blob_client.get_blob_properties.return_value = SimpleNamespace(
        size=BLOB_SIZE_BYTES,
        etag="etag-1",
        last_modified=None,
        metadata={"safe": "ok", "token": "REDACTED"},
        content_settings=SimpleNamespace(content_type="text/csv", content_md5="md5"),
    )
    container_client = MagicMock()
    container_client.get_blob_client.return_value = blob_client
    service_client = MagicMock()
    service_client.get_container_client.return_value = container_client
    provider = AzureBlobStorageProvider(
        account_name="acct",
        account_key="key",
        container_name="media",
        blob_service_client=service_client,
    )

    metadata = provider.head("datasets/sample.csv")

    assert metadata.size_bytes == BLOB_SIZE_BYTES
    assert metadata.etag == "etag-1"
    assert metadata.metadata == {"safe": "ok"}
    assert "sig=" not in metadata.uri


def test_azure_exists_found_not_found() -> None:
    blob_client = MagicMock()
    blob_client.exists.return_value = True
    container_client = MagicMock()
    container_client.get_blob_client.return_value = blob_client
    service_client = MagicMock()
    service_client.get_container_client.return_value = container_client
    provider = AzureBlobStorageProvider(
        account_name="acct",
        account_key="key",
        container_name="media",
        blob_service_client=service_client,
    )
    assert provider.exists("datasets/sample.csv") is True

    blob_client.exists.side_effect = ResourceNotFoundError(message="not found")
    assert provider.exists("datasets/missing.csv") is False


def test_azure_delete_calls_client() -> None:
    blob_client = MagicMock()
    container_client = MagicMock()
    container_client.get_blob_client.return_value = blob_client
    service_client = MagicMock()
    service_client.get_container_client.return_value = container_client
    provider = AzureBlobStorageProvider(
        account_name="acct",
        account_key="key",
        container_name="media",
        blob_service_client=service_client,
    )

    provider.delete("datasets/sample.csv")

    blob_client.delete_blob.assert_called_once()


def test_azure_download_to_file_uses_stream(tmp_path: Path) -> None:
    destination_file = tmp_path / "downloaded.csv"
    blob_client = MagicMock()
    download_stream = MagicMock()

    def _readinto(file_obj):
        file_obj.write(b"id,name\n1,Ada\n")

    download_stream.readinto.side_effect = _readinto
    blob_client.download_blob.return_value = download_stream
    blob_client.get_blob_properties.return_value = SimpleNamespace(
        size=14,
        etag="etag-1",
        last_modified=None,
        metadata={},
        content_settings=SimpleNamespace(content_type="text/csv", content_md5=None),
    )
    container_client = MagicMock()
    container_client.get_blob_client.return_value = blob_client
    service_client = MagicMock()
    service_client.get_container_client.return_value = container_client
    provider = AzureBlobStorageProvider(
        account_name="acct",
        account_key="key",
        container_name="media",
        blob_service_client=service_client,
    )

    metadata = provider.download_to_file("datasets/sample.csv", str(destination_file))

    assert destination_file.exists()
    assert metadata.provider == "azure_blob"
    download_stream.readinto.assert_called_once()


def test_factory_returns_local_provider(tmp_path: Path) -> None:
    factory = DataRegistryStorageFactory()
    provider = factory.create_provider("local", local_base_directory=tmp_path / "storage")
    assert isinstance(provider, LocalStorageProvider)


def test_factory_returns_azure_provider() -> None:
    factory = DataRegistryStorageFactory()
    with patch(
        "resource_registry.storage.factory.AzureBlobStorageProvider",
        return_value=MagicMock(),
    ) as mocked_azure:
        provider = factory.create_provider("azure_blob")
    assert provider is not None
    mocked_azure.assert_called_once()


def test_factory_unsupported_provider_rejected() -> None:
    factory = DataRegistryStorageFactory()
    with pytest.raises(StorageProviderUnsupportedError):
        factory.create_provider("unsupported")


def test_factory_missing_azure_config_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = DataRegistryStorageFactory()
    monkeypatch.setattr(settings, "AZ_STORAGE_ACCOUNT_NAME", None, raising=False)
    monkeypatch.setattr(settings, "AZ_STORAGE_ACCOUNT_KEY", None, raising=False)
    monkeypatch.setattr(settings, "AZ_STORAGE_MEDIA_CONTAINER", None, raising=False)
    monkeypatch.delenv("AZ_STORAGE_ACCOUNT_NAME", raising=False)
    monkeypatch.delenv("AZ_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("AZ_STORAGE_MEDIA_CONTAINER", raising=False)
    with pytest.raises(StorageConfigurationError):
        factory.create_provider("azure_blob")


def test_azure_reuses_connection_string_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.py builds AZURE_CONNECTION_STRING once, honoring
    AZURE_CUSTOM_DOMAIN for Azurite -- reused here instead of guessing a host."""
    monkeypatch.setattr(
        settings, "AZURE_CONNECTION_STRING", "UseDevelopmentStorage=true", raising=False
    )
    with patch(
        "resource_registry.storage.azure_blob.BlobServiceClient.from_connection_string"
    ) as mock_from_conn_str:
        AzureBlobStorageProvider(account_name="acct", account_key="key", container_name="media")
    mock_from_conn_str.assert_called_once_with("UseDevelopmentStorage=true")


def test_azure_raises_when_no_connection_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """No silent fallback to a guessed real-Azure host -- fail loudly instead."""
    monkeypatch.setattr(settings, "AZURE_CONNECTION_STRING", None, raising=False)
    with pytest.raises(StorageConfigurationError):
        AzureBlobStorageProvider(account_name="acct", account_key="key", container_name="media")
