"""Archive immutable outbox event bytes in Azure Blob Storage through Managed Identity."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from django.conf import settings


@dataclass(frozen=True, slots=True)
class AzureBlobEventArchive:
    """Store canonical event bytes once in a dedicated Azure Blob container."""

    account_name: str
    container: str

    @classmethod
    def from_environment(cls) -> AzureBlobEventArchive:
        """Build the archive adapter from non-secret deployment configuration.

        Raises:
            RuntimeError: If the Azure Blob archive configuration is absent.
        """
        account_name = getattr(
            settings, "RESOURCE_REGISTRY_OUTBOX_ARCHIVE_STORAGE_ACCOUNT_NAME", None
        )
        container = getattr(settings, "RESOURCE_REGISTRY_OUTBOX_ARCHIVE_CONTAINER", None)
        if not account_name or not container:
            raise RuntimeError("Azure Blob event archive configuration must be configured.")
        return cls(account_name=account_name, container=container)

    def archive(
        self,
        *,
        event_id: str,
        canonical_event: str,
        checksum: str,
        namespace_id: UUID,
        dataset_id: UUID,
        sequence: int,
    ) -> str:
        """Archive immutable event bytes and return their stable, non-secret URI.

        Args:
            event_id: Immutable global event identifier.
            canonical_event: Canonical serialized event body to preserve.
            checksum: Canonical event checksum used to detect path collisions.
            namespace_id: Owner-derived Namespace identifier.
            dataset_id: Dataset stream owner identifier.
            sequence: Monotonic Dataset stream sequence.

        Returns:
            The stable archive URI for the immutable event bytes.

        Raises:
            ValueError: If an existing archive object has a different immutable identity.
        """
        blob_name = (
            f"resource-registry/outbox/{namespace_id}/{dataset_id}/{sequence:020d}-{event_id}.json"
        )
        credential = DefaultAzureCredential()
        client = BlobServiceClient(
            account_url=f"https://{self.account_name}.blob.core.windows.net",
            credential=credential,
        )
        blob_client = client.get_blob_client(container=self.container, blob=blob_name)
        metadata = {"event_id": event_id, "checksum": checksum}
        try:
            blob_client.upload_blob(
                canonical_event.encode("utf-8"),
                overwrite=False,
                content_type="application/json",
                metadata=metadata,
            )
        except ResourceExistsError:
            existing_metadata = getattr(blob_client.get_blob_properties(), "metadata", {}) or {}
            if (
                existing_metadata.get("event_id") != event_id
                or existing_metadata.get("checksum") != checksum
            ):
                raise ValueError("Existing event archive object identity does not match.")
        return f"azure://{self.container}/{blob_name}"
