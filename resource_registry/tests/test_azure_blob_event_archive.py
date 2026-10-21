"""Azure Blob immutable event archive tests without external provider calls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from azure.core.exceptions import ResourceExistsError
from django.conf import settings

from resource_registry.services.azure_blob_event_archive import AzureBlobEventArchive


@pytest.mark.parametrize(
    "missing_name",
    [
        "RESOURCE_REGISTRY_OUTBOX_ARCHIVE_STORAGE_ACCOUNT_NAME",
        "RESOURCE_REGISTRY_OUTBOX_ARCHIVE_CONTAINER",
    ],
)
def test_archive_requires_complete_non_secret_configuration(
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    """Reject incomplete Blob archive deployment configuration."""
    monkeypatch.setattr(
        settings,
        "RESOURCE_REGISTRY_OUTBOX_ARCHIVE_STORAGE_ACCOUNT_NAME",
        "storageaccount",
    )
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_OUTBOX_ARCHIVE_CONTAINER", "event-archive")
    monkeypatch.setattr(settings, missing_name, None)

    with pytest.raises(RuntimeError, match="must be configured"):
        AzureBlobEventArchive.from_environment()


def test_archive_stores_canonical_bytes_at_a_deterministic_immutable_path() -> None:
    """Store event bytes once with immutable identity metadata."""
    credential = MagicMock()
    blob_client = MagicMock()
    service_client = MagicMock()
    service_client.get_blob_client.return_value = blob_client
    namespace_id = UUID("1f8076e7-18b3-465e-b299-443c7f0de8a0")
    dataset_id = UUID("7eabb6e1-c6e1-4f80-968c-5d59cb7f3e7d")

    with (
        patch(
            "resource_registry.services.azure_blob_event_archive.DefaultAzureCredential",
            return_value=credential,
        ) as default_credential,
        patch(
            "resource_registry.services.azure_blob_event_archive.BlobServiceClient",
            return_value=service_client,
        ) as blob_service_client,
    ):
        archive_uri = AzureBlobEventArchive(
            account_name="storageaccount",
            container="event-archive",
        ).archive(
            event_id="evt-123",
            canonical_event='{"event_id":"evt-123"}',
            checksum="a" * 64,
            namespace_id=namespace_id,
            dataset_id=dataset_id,
            sequence=7,
        )

    blob_name = (
        f"resource-registry/outbox/{namespace_id}/{dataset_id}/00000000000000000007-evt-123.json"
    )
    default_credential.assert_called_once_with()
    blob_service_client.assert_called_once_with(
        account_url="https://storageaccount.blob.core.windows.net",
        credential=credential,
    )
    service_client.get_blob_client.assert_called_once_with(
        container="event-archive", blob=blob_name
    )
    blob_client.upload_blob.assert_called_once_with(
        b'{"event_id":"evt-123"}',
        overwrite=False,
        content_type="application/json",
        metadata={"event_id": "evt-123", "checksum": "a" * 64},
    )
    assert archive_uri == f"azure://event-archive/{blob_name}"


def test_archive_accepts_an_existing_object_only_when_immutable_identity_matches() -> None:
    """Treat a matching blob as an idempotent retry after a dispatcher crash."""
    blob_client = MagicMock()
    blob_client.upload_blob.side_effect = ResourceExistsError("already exists")
    blob_client.get_blob_properties.return_value = SimpleNamespace(
        metadata={"event_id": "evt-123", "checksum": "a" * 64}
    )
    service_client = MagicMock()
    service_client.get_blob_client.return_value = blob_client

    with (
        patch("resource_registry.services.azure_blob_event_archive.DefaultAzureCredential"),
        patch(
            "resource_registry.services.azure_blob_event_archive.BlobServiceClient",
            return_value=service_client,
        ),
    ):
        archive_uri = AzureBlobEventArchive("storageaccount", "event-archive").archive(
            event_id="evt-123",
            canonical_event='{"event_id":"evt-123"}',
            checksum="a" * 64,
            namespace_id=UUID("1f8076e7-18b3-465e-b299-443c7f0de8a0"),
            dataset_id=UUID("7eabb6e1-c6e1-4f80-968c-5d59cb7f3e7d"),
            sequence=7,
        )

    assert archive_uri.startswith("azure://event-archive/")


def test_archive_rejects_existing_object_with_different_immutable_identity() -> None:
    """Fail closed if a deterministic archive path resolves to different event evidence."""
    blob_client = MagicMock()
    blob_client.upload_blob.side_effect = ResourceExistsError("already exists")
    blob_client.get_blob_properties.return_value = SimpleNamespace(
        metadata={"event_id": "evt-123", "checksum": "different"}
    )
    service_client = MagicMock()
    service_client.get_blob_client.return_value = blob_client

    with (
        patch("resource_registry.services.azure_blob_event_archive.DefaultAzureCredential"),
        patch(
            "resource_registry.services.azure_blob_event_archive.BlobServiceClient",
            return_value=service_client,
        ),
        pytest.raises(ValueError, match="identity does not match"),
    ):
        AzureBlobEventArchive("storageaccount", "event-archive").archive(
            event_id="evt-123",
            canonical_event='{"event_id":"evt-123"}',
            checksum="a" * 64,
            namespace_id=UUID("1f8076e7-18b3-465e-b299-443c7f0de8a0"),
            dataset_id=UUID("7eabb6e1-c6e1-4f80-968c-5d59cb7f3e7d"),
            sequence=7,
        )
