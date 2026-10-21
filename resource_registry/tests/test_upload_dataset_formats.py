"""Verify new upload formats without relying on MIME guesses or file suffixes alone."""

import pytest
from django.test import override_settings

from resource_registry.models import LargeFileUploadSession
from resource_registry.uploads.contracts import SupportedUploadFormat, UploadedObjectMetadata
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.format_policy import UploadFileFormatPolicy
from resource_registry.uploads.local_provider import LocalLargeFileUploadProvider
from resource_registry.uploads.services import LargeFileUploadService


@pytest.mark.parametrize("extension", ["jsonl", "ndjson", "parquet"])
@override_settings(RESOURCE_REGISTRY_UPLOAD_ALLOWED_EXTENSIONS=(".csv", ".json", ".xlsx"))
def test_new_upload_formats_honor_explicit_extension_allowlist(extension: str) -> None:
    """Keep explicitly configured deployments closed to new upload formats."""
    with pytest.raises(UploadApplicationError) as error:
        UploadFileFormatPolicy().resolve(
            filename=f"records.{extension}", declared_content_type="application/json"
        )
    assert error.value.code == "UPLOAD_UNSUPPORTED_FILE_FORMAT"


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("records.jsonl", "application/octet-stream"), ("records.parquet", "text/csv")],
)
def test_new_upload_formats_reject_incompatible_mime(filename: str, content_type: str) -> None:
    """Require a MIME type from the format's configured allowlist."""
    with pytest.raises(UploadApplicationError) as error:
        UploadFileFormatPolicy().resolve(filename=filename, declared_content_type=content_type)
    assert error.value.code == "UPLOAD_EXTENSION_CONTENT_TYPE_MISMATCH"


@override_settings(RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_JSONL=("application/jsonl",))
def test_json_lines_alias_uses_configured_mime_policy() -> None:
    """Share the configured MIME policy across both JSON Lines extensions."""
    policy = UploadFileFormatPolicy()
    assert (
        policy.resolve(filename="data.NDJSON", declared_content_type="application/jsonl")
        == SupportedUploadFormat.JSONL
    )
    assert policy.extension_for(SupportedUploadFormat.JSONL) == ".jsonl"
    with pytest.raises(UploadApplicationError) as error:
        policy.resolve(filename="data.jsonl", declared_content_type="application/x-ndjson")
    assert error.value.code == "UPLOAD_EXTENSION_CONTENT_TYPE_MISMATCH"


@pytest.mark.parametrize("initial_bytes", [b"name\nvalue\n", b"PAREencrypted", b""])
def test_parquet_completion_requires_header_evidence(initial_bytes: bytes) -> None:
    """Reject false or encrypted Parquet headers before admitting completed uploads."""
    session = LargeFileUploadSession(
        normalized_filename="data.parquet",
        storage_object_key="uploads/data.parquet",
        resolved_file_format="PARQUET",
        declared_size_bytes=100,
    )
    metadata = UploadedObjectMetadata(
        object_key=session.storage_object_key,
        size_bytes=100,
        content_type="application/octet-stream",
        initial_bytes=initial_bytes,
    )
    service = LargeFileUploadService(provider=LocalLargeFileUploadProvider())
    with pytest.raises(UploadApplicationError) as error:
        service._validate_provider_metadata(session=session, metadata=metadata, expected_etag=None)
    assert error.value.code == "UPLOAD_FORMAT_EVIDENCE_MISMATCH"


@pytest.mark.parametrize("format_name", ["JSONL", "PARQUET"])
def test_upload_completion_preserves_etag_verification(format_name: str) -> None:
    """Apply the existing integrity boundary to both new formats."""
    suffix = format_name.lower()
    session = LargeFileUploadSession(
        normalized_filename=f"data.{suffix}",
        storage_object_key=f"uploads/data.{suffix}",
        resolved_file_format=format_name,
        declared_size_bytes=100,
    )
    metadata = UploadedObjectMetadata(
        object_key=session.storage_object_key,
        size_bytes=100,
        content_type=(
            "application/x-ndjson" if format_name == "JSONL" else "application/octet-stream"
        ),
        initial_bytes=b'{"a":1}' if format_name == "JSONL" else b"PAR1",
        etag="actual",
    )
    service = LargeFileUploadService(provider=LocalLargeFileUploadProvider())
    with pytest.raises(UploadApplicationError) as error:
        service._validate_provider_metadata(
            session=session, metadata=metadata, expected_etag="other"
        )
    assert error.value.code == "UPLOAD_ETAG_MISMATCH"
