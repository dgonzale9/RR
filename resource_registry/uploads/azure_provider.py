"""Azure Block Blob infrastructure adapter for direct large-file uploads."""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)
from django.conf import settings

from resource_registry.uploads.contracts import DirectUploadAuthorization, UploadedObjectMetadata
from resource_registry.uploads.exceptions import UploadApplicationError


class AzureBlockBlobLargeFileUploadProvider:
    """Issue one-blob scheme-restricted SAS authorization and inspect bounded blob metadata."""

    provider_name = "azure_blob"

    def __init__(self, *, blob_service_client: BlobServiceClient | None = None) -> None:
        """Initialize the adapter from environment-backed Django settings."""
        self._account_name = self._setting("AZ_STORAGE_ACCOUNT_NAME")
        self._account_key = self._setting("AZ_STORAGE_ACCOUNT_KEY")
        self._container_name = self._setting("AZ_STORAGE_MEDIA_CONTAINER")
        if not all((self._account_name, self._account_key, self._container_name)):
            raise UploadApplicationError("UPLOAD_PROVIDER_UNAVAILABLE", 503)
        # settings.py already builds AZURE_CONNECTION_STRING once, for real Azure
        # and Azurite alike -- reuse it instead of rebuilding connection details
        # here, so this file can't drift from what the rest of the project resolves to.
        if blob_service_client is not None:
            self._blob_service_client = blob_service_client
        else:
            connection_string = getattr(settings, "AZURE_CONNECTION_STRING", None)
            if not connection_string:
                raise UploadApplicationError("UPLOAD_PROVIDER_UNAVAILABLE", 503)
            try:
                self._blob_service_client = BlobServiceClient.from_connection_string(
                    connection_string,
                    connection_timeout=int(
                        settings.RESOURCE_REGISTRY_UPLOAD_PROVIDER_CONNECT_TIMEOUT_SECONDS
                    ),
                    read_timeout=int(
                        settings.RESOURCE_REGISTRY_UPLOAD_PROVIDER_READ_TIMEOUT_SECONDS
                    ),
                    retry_total=max(
                        min(int(settings.RESOURCE_REGISTRY_UPLOAD_PROVIDER_RETRY_TOTAL), 5),
                        0,
                    ),
                )
            except (AzureError, ValueError) as error:
                raise UploadApplicationError("UPLOAD_PROVIDER_UNAVAILABLE", 503) from error

    def create_upload_authorization(
        self,
        *,
        object_key: str,
        content_type: str,
        expires_at: datetime,
        recommended_chunk_size_bytes: int,
        max_parallel_chunks: int,
    ) -> DirectUploadAuthorization:
        """Issue create/write-only SAS ( Shared Access Signature ) permission for exactly one Block Blob."""
        try:
            blob_client = self._blob_service_client.get_blob_client(
                container=str(self._container_name), blob=object_key
            )
            endpoint_scheme = urlsplit(str(blob_client.url)).scheme.lower()
            token = generate_blob_sas(
                account_name=str(self._account_name),
                container_name=str(self._container_name),
                blob_name=object_key,
                account_key=str(self._account_key),
                permission=BlobSasPermissions(create=True, write=True),
                start=datetime.now(UTC) - timedelta(minutes=5),
                expiry=expires_at,
                protocol="http" if endpoint_scheme == "http" else "https",
            )
        except (AzureError, ValueError) as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_AUTHORIZATION_FAILED", 503) from error
        # Let the SDK build the URL from how the client was constructed, instead
        # of rebuilding a host here -- same pattern copy_object() uses below.
        url = f"{blob_client.url}?{token}"
        return DirectUploadAuthorization(
            upload_url=url,
            expires_at=expires_at,
            upload_method="block_blob",
            recommended_chunk_size_bytes=recommended_chunk_size_bytes,
            max_parallel_chunks=max_parallel_chunks,
            required_headers={"x-ms-blob-type": "BlockBlob", "Content-Type": content_type},
        )

    def stage_upload_block(self, *, object_key: str, block_id: str, content: bytes) -> None:
        """Upload one bounded block using the server's configured storage credential.

        Args:
            object_key: Server-selected destination key.
            block_id: Server-derived, deterministic block identifier.
            content: Validated block bytes; never an entire large file.

        Raises:
            UploadApplicationError: If Azure rejects or cannot receive the block.
        """
        try:
            client = self._blob_service_client.get_blob_client(
                container=str(self._container_name), blob=object_key
            )
            client.stage_block(block_id=block_id, data=content, length=len(content))
        except AzureError as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_TRANSFER_FAILED", 503) from error

    def commit_upload_blocks(
        self, *, object_key: str, block_sizes: Mapping[str, int], content_type: str
    ) -> None:
        """Verify block lengths and commit the trusted ordered manifest.

        Args:
            object_key: Server-selected destination key.
            block_sizes: Ordered block IDs with exact expected lengths.
            content_type: Validated intake content type.

        Raises:
            UploadApplicationError: If blocks are missing, malformed, or Azure is unavailable.
        """
        try:
            client = self._blob_service_client.get_blob_client(
                container=str(self._container_name), blob=object_key
            )
            committed, uncommitted = client.get_block_list(block_list_type="all")
            # Latest uncommitted data wins, matching Azure's commit semantics on retries.
            available = {block.id: block.size for block in (*committed, *uncommitted)}
            if any(available.get(block_id) != size for block_id, size in block_sizes.items()):
                raise UploadApplicationError("INTAKE_UPLOAD_INCOMPLETE", 409)
            client.commit_block_list(
                list(block_sizes), content_settings=ContentSettings(content_type=content_type)
            )
        except ResourceNotFoundError as error:
            raise UploadApplicationError("INTAKE_UPLOAD_INCOMPLETE", 409) from error
        except AzureError as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_TRANSFER_FAILED", 503) from error

    def inspect_uploaded_object(self, *, object_key: str) -> UploadedObjectMetadata:
        """Read properties and one configured bounded sample without downloading the file."""
        blob_client = self._blob_service_client.get_blob_client(
            container=str(self._container_name),
            blob=object_key,
        )
        try:
            properties = blob_client.get_blob_properties()
            sniff_bytes = min(
                max(int(settings.RESOURCE_REGISTRY_UPLOAD_FORMAT_SNIFF_BYTES), 4),
                64 * 1024,
            )
            initial_bytes = blob_client.download_blob(offset=0, length=sniff_bytes).readall()
        except ResourceNotFoundError as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_OBJECT_NOT_FOUND", 404) from error
        except AzureError as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_METADATA_UNAVAILABLE", 503) from error
        content_settings = getattr(properties, "content_settings", None)
        checksum = getattr(content_settings, "content_md5", None)
        return UploadedObjectMetadata(
            object_key=object_key,
            size_bytes=int(getattr(properties, "size", 0)),
            content_type=str(getattr(content_settings, "content_type", "") or ""),
            etag=str(getattr(properties, "etag", "") or "") or None,
            version_id=str(getattr(properties, "version_id", "") or "") or None,
            provider_checksum=(base64.b64encode(checksum).decode("ascii") if checksum else None),
            initial_bytes=bytes(initial_bytes[:sniff_bytes]),
        )

    def delete_object(self, *, object_key: str) -> None:
        """Delete only the server-selected blob and tolerate absence."""
        from resource_registry.cleanup.storage import (  # noqa: PLC0415
            guarded_deletion,
        )

        blob_client = self._blob_service_client.get_blob_client(
            container=str(self._container_name),
            blob=object_key,
        )
        with guarded_deletion("azure_blob", f"azure://{self._container_name}/{object_key}"):
            try:
                blob_client.delete_blob(timeout=60)
            except ResourceNotFoundError:
                pass
            except AzureError as error:
                raise UploadApplicationError("UPLOAD_PROVIDER_CLEANUP_FAILED", 503) from error

    def copy_object(
        self, *, source_object_key: str, destination_object_key: str
    ) -> UploadedObjectMetadata:
        """Reserve the source and a new managed generation for a server-side copy."""
        from resource_registry.cleanup.storage import (  # noqa: PLC0415
            complete_object_write,
            lease_object,
        )

        source_uri = f"azure://{self._container_name}/{source_object_key}"
        destination_uri = f"azure://{self._container_name}/{destination_object_key}"
        with (
            lease_object("azure_blob", source_uri),
            lease_object(
                "azure_blob", destination_uri, writing=True, location=str(self._account_name)
            ) as stored,
        ):
            metadata = self._copy_object(
                source_object_key=source_object_key, destination_object_key=destination_object_key
            )
            complete_object_write(
                stored, size_bytes=metadata.size_bytes, checksum=None, etag=metadata.etag
            )
            return metadata

    def _copy_object(
        self,
        *,
        source_object_key: str,
        destination_object_key: str,
    ) -> UploadedObjectMetadata:
        """Copy one private blob using the configured account credential.

        Args:
            source_object_key: Server-selected source blob path.
            destination_object_key: Server-selected destination blob path.

        Returns:
            Provider metadata for the copied object.

        Raises:
            UploadApplicationError: If the source is unavailable or the copy cannot complete.
        """
        source_client = self._blob_service_client.get_blob_client(
            container=str(self._container_name),
            blob=source_object_key,
        )
        destination_client = self._blob_service_client.get_blob_client(
            container=str(self._container_name),
            blob=destination_object_key,
        )
        try:
            source_client.get_blob_properties()
            # Hardcoded "https" made Azurite (locally - plain http)
            # reject the copy source as unverifiable -- same
            # scheme-detection create_upload_authorization() already does.
            endpoint_scheme = urlsplit(str(source_client.url)).scheme.lower()
            read_token = generate_blob_sas(
                account_name=str(self._account_name),
                container_name=str(self._container_name),
                blob_name=source_object_key,
                account_key=str(self._account_key),
                permission=BlobSasPermissions(read=True),
                start=datetime.now(UTC) - timedelta(minutes=5),
                expiry=datetime.now(UTC) + timedelta(minutes=10),
                protocol="http" if endpoint_scheme == "http" else "https",
            )
            source_url = f"{source_client.url}?{read_token}"
            destination_client.start_copy_from_url(source_url)
            properties = destination_client.get_blob_properties()
            copy_id = str(getattr(getattr(properties, "copy", None), "id", "") or "")
            for _ in range(50):
                copy_status = str(getattr(getattr(properties, "copy", None), "status", "")).lower()
                if copy_status == "success":
                    break
                if copy_status in {"aborted", "failed"}:
                    raise UploadApplicationError("UPLOAD_PROVIDER_COPY_FAILED", 503)
                time.sleep(0.1)
                properties = destination_client.get_blob_properties()
            else:
                if copy_id:
                    destination_client.abort_copy(copy_id)
                destination_client.delete_blob()
                raise UploadApplicationError("UPLOAD_PROVIDER_COPY_FAILED", 503)
        except ResourceNotFoundError as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_OBJECT_NOT_FOUND", 404) from error
        except AzureError as error:
            raise UploadApplicationError("UPLOAD_PROVIDER_COPY_FAILED", 503) from error
        content_settings = getattr(properties, "content_settings", None)
        checksum = getattr(content_settings, "content_md5", None)
        return UploadedObjectMetadata(
            object_key=destination_object_key,
            size_bytes=int(getattr(properties, "size", 0)),
            content_type=str(getattr(content_settings, "content_type", "") or ""),
            etag=str(getattr(properties, "etag", "") or "") or None,
            version_id=str(getattr(properties, "version_id", "") or "") or None,
            provider_checksum=(base64.b64encode(checksum).decode("ascii") if checksum else None),
        )

    @staticmethod
    def _setting(name: str) -> str | None:
        value = getattr(settings, name, None) or os.environ.get(name)
        return str(value).strip() if value else None
