"""AzureBlockBlobLargeFileUploadProvider tests -- no real Azure/Azurite calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings

from resource_registry.uploads.azure_provider import AzureBlockBlobLargeFileUploadProvider
from resource_registry.uploads.exceptions import UploadApplicationError

ACCOUNT_NAME = "devstoreaccount1"
ACCOUNT_KEY = "test-account-key"
CONTAINER_NAME = "media"


def _configure_required_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    custom_domain: str | None,
    retry_total: int = 3,
) -> None:
    monkeypatch.setattr(settings, "AZ_STORAGE_ACCOUNT_NAME", ACCOUNT_NAME, raising=False)
    monkeypatch.setattr(settings, "AZ_STORAGE_ACCOUNT_KEY", ACCOUNT_KEY, raising=False)
    monkeypatch.setattr(settings, "AZ_STORAGE_MEDIA_CONTAINER", CONTAINER_NAME, raising=False)
    monkeypatch.setattr(settings, "AZURE_CUSTOM_DOMAIN", custom_domain, raising=False)
    if custom_domain and custom_domain.startswith("http://"):
        connection_string = (
            f"DefaultEndpointsProtocol=http;AccountName={ACCOUNT_NAME};"
            f"AccountKey={ACCOUNT_KEY};BlobEndpoint={custom_domain}/{ACCOUNT_NAME};"
        )
    else:
        connection_string = (
            f"DefaultEndpointsProtocol=https;AccountName={ACCOUNT_NAME};AccountKey={ACCOUNT_KEY};"
        )
    monkeypatch.setattr(settings, "AZURE_CONNECTION_STRING", connection_string, raising=False)
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_UPLOAD_PROVIDER_CONNECT_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_UPLOAD_PROVIDER_READ_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "RESOURCE_REGISTRY_UPLOAD_PROVIDER_RETRY_TOTAL", retry_total)


def test_missing_account_settings_raise_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incomplete account/container config fails loudly, not with a client-side crash.

    _setting() falls back to the real process environment when the Django setting
    is unset, so the env vars must be cleared too -- not just the settings attrs.
    """
    for name in ("AZ_STORAGE_ACCOUNT_NAME", "AZ_STORAGE_ACCOUNT_KEY", "AZ_STORAGE_MEDIA_CONTAINER"):
        monkeypatch.setattr(settings, name, None, raising=False)
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(UploadApplicationError) as exc_info:
        AzureBlockBlobLargeFileUploadProvider()

    assert exc_info.value.code == "UPLOAD_PROVIDER_UNAVAILABLE"


def test_missing_connection_string_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Account/container set but no resolved connection string still fails loudly."""
    _configure_required_settings(monkeypatch, custom_domain=None)
    monkeypatch.setattr(settings, "AZURE_CONNECTION_STRING", None, raising=False)

    with pytest.raises(UploadApplicationError) as exc_info:
        AzureBlockBlobLargeFileUploadProvider()

    assert exc_info.value.code == "UPLOAD_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    ("configured_retry_total", "expected_retry_total"),
    [(-1, 0), (3, 3), (9, 5)],
)
def test_client_built_from_settings_connection_string_with_bounded_retries(
    monkeypatch: pytest.MonkeyPatch,
    configured_retry_total: int,
    expected_retry_total: int,
) -> None:
    """Reuse the resolved connection string, timeouts, and bounded retry configuration."""
    _configure_required_settings(
        monkeypatch,
        custom_domain=None,
        retry_total=configured_retry_total,
    )

    with patch(
        "resource_registry.uploads.azure_provider.BlobServiceClient.from_connection_string"
    ) as mock_from_conn_str:
        AzureBlockBlobLargeFileUploadProvider()

    mock_from_conn_str.assert_called_once_with(
        settings.AZURE_CONNECTION_STRING,
        connection_timeout=10,
        read_timeout=30,
        retry_total=expected_retry_total,
    )


def test_injected_client_bypasses_connection_string_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied client (e.g. for other tests) skips from_connection_string entirely."""
    _configure_required_settings(monkeypatch, custom_domain=None)
    injected_client = MagicMock()

    provider = AzureBlockBlobLargeFileUploadProvider(blob_service_client=injected_client)

    assert provider._blob_service_client is injected_client


def test_upload_authorization_uses_https_against_real_azure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truthy real-Azure domain still restricts SAS transport to HTTPS."""
    azure_domain = f"{ACCOUNT_NAME}.blob.core.windows.net"
    _configure_required_settings(monkeypatch, custom_domain=azure_domain)
    blob_client = MagicMock()
    blob_client.url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/some/key"
    injected_client = MagicMock()
    injected_client.get_blob_client.return_value = blob_client
    provider = AzureBlockBlobLargeFileUploadProvider(blob_service_client=injected_client)

    with patch(
        "resource_registry.uploads.azure_provider.generate_blob_sas", return_value="sig=abc"
    ) as mock_sas:
        authorization = provider.create_upload_authorization(
            object_key="some/key",
            content_type="text/csv",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            recommended_chunk_size_bytes=16 * 1024 * 1024,
            max_parallel_chunks=4,
        )

    assert mock_sas.call_args.kwargs["protocol"] == "https"
    assert authorization.upload_url == f"{blob_client.url}?sig=abc"
    injected_client.get_blob_client.assert_called_once_with(
        container=CONTAINER_NAME, blob="some/key"
    )


def test_upload_authorization_uses_http_against_azurite(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTP Azurite client restricts SAS transport to HTTP and exposes its SDK URL."""
    custom_domain = "http://127.0.0.1:10000"
    _configure_required_settings(monkeypatch, custom_domain=custom_domain)
    blob_client = MagicMock()
    blob_client.url = f"{custom_domain}/{ACCOUNT_NAME}/{CONTAINER_NAME}/some/key"
    injected_client = MagicMock()
    injected_client.get_blob_client.return_value = blob_client
    provider = AzureBlockBlobLargeFileUploadProvider(blob_service_client=injected_client)

    with patch(
        "resource_registry.uploads.azure_provider.generate_blob_sas", return_value="sig=xyz"
    ) as mock_sas:
        authorization = provider.create_upload_authorization(
            object_key="some/key",
            content_type="text/csv",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            recommended_chunk_size_bytes=16 * 1024 * 1024,
            max_parallel_chunks=4,
        )

    assert mock_sas.call_args.kwargs["protocol"] == "http"
    assert authorization.upload_url == f"{blob_client.url}?sig=xyz"


def _mock_copy_clients(url: str) -> tuple[MagicMock, MagicMock]:
    """A source/destination client pair that completes copy_object() in one poll."""
    source_client = MagicMock()
    source_client.url = url
    destination_client = MagicMock()
    destination_client.get_blob_properties.return_value = MagicMock(
        copy=MagicMock(status="success"),
        size=24,
        content_settings=MagicMock(content_type="text/csv", content_md5=None),
    )
    return source_client, destination_client


def test_copy_object_uses_https_against_real_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same scheme restriction as the upload SAS -- a real-Azure source stays HTTPS-only."""
    _configure_required_settings(
        monkeypatch, custom_domain=f"{ACCOUNT_NAME}.blob.core.windows.net"
    )
    source_client, destination_client = _mock_copy_clients(
        f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/some/source"
    )
    injected_client = MagicMock()
    injected_client.get_blob_client.side_effect = [source_client, destination_client]
    provider = AzureBlockBlobLargeFileUploadProvider(blob_service_client=injected_client)

    with patch(
        "resource_registry.uploads.azure_provider.generate_blob_sas", return_value="sig=abc"
    ) as mock_sas:
        provider.copy_object(source_object_key="some/source", destination_object_key="some/dest")

    assert mock_sas.call_args.kwargs["protocol"] == "https"


def test_copy_object_uses_http_against_azurite(monkeypatch: pytest.MonkeyPatch) -> None:
    """This is the fix -- copy_object() used to hardcode "https" regardless of the
    actual endpoint, so Azurite (plain http) rejected the copy source as
    unverifiable (CannotVerifyCopySource) on every publish."""
    custom_domain = "http://127.0.0.1:10000"
    _configure_required_settings(monkeypatch, custom_domain=custom_domain)
    source_client, destination_client = _mock_copy_clients(
        f"{custom_domain}/{ACCOUNT_NAME}/{CONTAINER_NAME}/some/source"
    )
    injected_client = MagicMock()
    injected_client.get_blob_client.side_effect = [source_client, destination_client]
    provider = AzureBlockBlobLargeFileUploadProvider(blob_service_client=injected_client)

    with patch(
        "resource_registry.uploads.azure_provider.generate_blob_sas", return_value="sig=xyz"
    ) as mock_sas:
        provider.copy_object(source_object_key="some/source", destination_object_key="some/dest")

    assert mock_sas.call_args.kwargs["protocol"] == "http"
