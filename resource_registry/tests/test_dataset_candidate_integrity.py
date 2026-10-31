"""Verify publication integrity with real Azure adapter semantics and simulated blob I/O."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO
from unittest.mock import MagicMock
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.worker import DatasetChangeWorker
from resource_registry.models import DatasetChange, OutboxEntry
from resource_registry.storage.azure_blob import AzureBlobStorageProvider
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.tests.test_dataset_changes import create_verified_intake
from resource_registry.tests.test_dataset_maintenance_flows import Scope, prepare_csv
from resource_registry.tests.test_dataset_management import management_scope  # noqa: F401


@dataclass
class AzureCandidateStorage:
    """Keep fake blob bytes and ETags behind the real storage adapter."""

    provider: AzureBlobStorageProvider
    objects: dict[str, bytes] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    downloads: list[str] = field(default_factory=list)
    include_md5: bool = True
    fail_downloads: bool = False


@pytest.fixture
def azure_candidate_storage(monkeypatch: pytest.MonkeyPatch) -> AzureCandidateStorage:
    """Simulate Azure's binary MD5 and ETags without contacting a real account."""
    client = MagicMock()
    provider = AzureBlobStorageProvider(
        account_name="fixture",
        account_key="fixture",
        container_name="media",
        blob_service_client=client,
    )
    storage = AzureCandidateStorage(provider)

    def get_blob_client(blob_path: str) -> MagicMock:
        blob = MagicMock()

        def upload_blob(stream: BinaryIO, *, overwrite: bool, content_type: str) -> None:
            storage.objects[blob_path] = stream.read()
            storage.etags[blob_path] = str(uuid4())

        def get_blob_properties() -> SimpleNamespace:
            content = storage.objects[blob_path]
            return SimpleNamespace(
                size=len(content),
                etag=storage.etags[blob_path],
                last_modified=None,
                metadata={},
                content_settings=SimpleNamespace(
                    content_type="application/octet-stream",
                    content_md5=(
                        bytearray(hashlib.md5(content, usedforsecurity=False).digest())
                        if storage.include_md5
                        else None
                    ),
                ),
            )

        def download_blob(*, timeout: int) -> SimpleNamespace:
            if storage.fail_downloads:
                raise OSError("Simulated download failure")
            storage.downloads.append(blob_path)

            def readinto(stream: BinaryIO) -> int:
                return stream.write(storage.objects[blob_path])

            return SimpleNamespace(readinto=readinto)

        blob.upload_blob.side_effect = upload_blob
        blob.get_blob_properties.side_effect = get_blob_properties
        blob.download_blob.side_effect = download_blob
        return blob

    client.get_container_client.return_value.get_blob_client.side_effect = get_blob_client
    monkeypatch.setattr(
        "resource_registry.storage.factory.AzureBlobStorageProvider", lambda: provider
    )
    return storage


def _prepare_azure_candidate(
    scope: Scope, path: Path, storage: AzureCandidateStorage
) -> DatasetChange:
    manager, _, organization, namespace, dataset = scope
    path.write_text("id,amount\n1,10\n")
    intake = create_verified_intake(manager, path)
    key = f"fixtures/{uuid4()}/source.csv"
    uploaded = storage.provider.put(str(path), key)
    intake.storage_provider = "azure_blob"
    intake.storage_object_key = key
    intake.storage_etag = uploaded.etag
    intake.save()
    change = DatasetChangeService(manager, organization).create(
        namespace_id=namespace.id,
        intake_id=intake.id,
        idempotency_key=str(uuid4()),
        specification={"dataset_id": str(dataset.id), "expected_revision": dataset.revision},
    )
    DatasetChangeWorker().run_once(batch_size=1, change_id=change.id)
    change.refresh_from_db()
    assert change.status == "READY"
    return change


@pytest.mark.parametrize("legacy", [False, True])
def test_unchanged_azure_candidate_publishes_with_binary_md5_metadata(
    management_scope: Scope,
    tmp_path: Path,
    azure_candidate_storage: AzureCandidateStorage,
    legacy: bool,
) -> None:
    """Publish valid Azure data without comparing provider MD5 to the canonical SHA-256."""
    manager, _, organization, _, dataset = management_scope
    storage = azure_candidate_storage
    change = _prepare_azure_candidate(management_scope, tmp_path / "valid.csv", storage)
    if legacy:
        change.prepared["items"][0].pop("storage_etag", None)
        change.save(update_fields=["prepared"])
    before_downloads = len(storage.downloads)
    service = DatasetChangeService(manager, organization)
    result = service.publish(change.id, expected_revision=change.revision)
    assert result.status == "PUBLISHED"
    dataset.refresh_from_db()
    assert dataset.current_version is not None
    assert dataset.current_version.content_hash == change.prepared["items"][0]["checksum"]
    assert len(storage.downloads) == before_downloads + int(legacy)
    service.publish(change.id, expected_revision=change.revision)
    assert dataset.versions.count() == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_changed_azure_candidate_is_rejected_even_without_provider_checksum(
    management_scope: Scope,
    tmp_path: Path,
    azure_candidate_storage: AzureCandidateStorage,
    legacy: bool,
) -> None:
    """Detect same-size changes through ETags or legacy SHA-256 verification."""
    manager, _, organization, _, dataset = management_scope
    storage = azure_candidate_storage
    storage.include_md5 = False
    change = _prepare_azure_candidate(management_scope, tmp_path / "changed.csv", storage)
    item = change.prepared["items"][0]
    if legacy:
        item.pop("storage_etag", None)
        change.save(update_fields=["prepared"])
    key = urlparse(item["uri"]).path.lstrip("/")
    content = storage.objects[key]
    storage.objects[key] = bytes([content[0] ^ 1]) + content[1:]
    storage.etags[key] = str(uuid4())
    original_events = OutboxEntry.objects.count()
    with pytest.raises(DatasetManagementError) as failure:
        DatasetChangeService(manager, organization).publish(
            change.id, expected_revision=change.revision
        )
    assert failure.value.code == "candidate_changed"
    change.refresh_from_db()
    assert change.status == "FAILED"
    assert not dataset.versions.exists()
    assert OutboxEntry.objects.count() == original_events


def test_legacy_verification_download_failure_preserves_unpublished_state(
    management_scope: Scope,
    tmp_path: Path,
    azure_candidate_storage: AzureCandidateStorage,
) -> None:
    """Treat failed legacy byte verification as unavailable without publishing."""
    manager, _, organization, _, dataset = management_scope
    storage = azure_candidate_storage
    change = _prepare_azure_candidate(management_scope, tmp_path / "unavailable.csv", storage)
    change.prepared["items"][0].pop("storage_etag", None)
    change.save(update_fields=["prepared"])
    storage.fail_downloads = True
    with pytest.raises(DatasetManagementError) as failure:
        DatasetChangeService(manager, organization).publish(
            change.id, expected_revision=change.revision
        )
    assert failure.value.code == "candidate_unavailable"
    assert not dataset.versions.exists()


def test_local_same_size_change_is_still_rejected(management_scope: Scope, tmp_path: Path) -> None:
    """Preserve local SHA-256 validation when metadata ETags are unavailable."""
    _, editor, organization, _, dataset = management_scope
    change = prepare_csv(management_scope, tmp_path / "local.csv", "id\n1\n")
    item = change.prepared["items"][0]
    provider = DataRegistryStorageFactory().create_provider("local")
    path = tmp_path / "altered.parquet"
    provider.download_to_file(item["uri"], str(path))
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    # Simulate out-of-band storage corruption, bypassing application write protection.
    provider._resolve_target_path(item["uri"]).write_bytes(path.read_bytes())
    with pytest.raises(DatasetManagementError) as failure:
        DatasetChangeService(editor, organization).publish(
            change.id, expected_revision=change.revision
        )
    assert failure.value.code == "candidate_changed"
    assert not dataset.versions.exists()
