from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest

from resource_registry.models import ArtifactLifecycleStatus, DatasetVersionStatus
from resource_registry.read.exceptions import DatasetNotFoundError, DatasetVersionNotFoundError
from resource_registry.read.transformation_input_binding_read_service import (
    TransformationInputBindingReadService,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.transformations.binding.exceptions import (
    TransformationInputReferenceResolutionError,
)
from resource_registry.transformations.binding.results import TransformationInputBindingRequest


def _request(*, artifact_id: str | None = None) -> TransformationInputBindingRequest:
    return TransformationInputBindingRequest(
        input_name="source",
        dataset_id="dataset-1",
        dataset_version_id="version-1",
        artifact_id=artifact_id,
    )


def _version(*, status: str = DatasetVersionStatus.READY) -> SimpleNamespace:
    return SimpleNamespace(
        id="version-1",
        dataset_id="dataset-1",
        tenant_id="tenant-1",
        version_number=1,
        status=status,
        source_uri="https://blob.example/input.parquet",
        content_hash="content-hash",
    )


def _artifact(
    *,
    version_id: str = "version-1",
    status: str = ArtifactLifecycleStatus.ACTIVE,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="artifact-1",
        dataset_version_id=version_id,
        tenant_id="tenant-1",
        artifact_role="PRIMARY",
        uri="local://artifact.parquet",
        format="PARQUET",
        size_bytes=10,
        checksum="checksum",
        storage_provider="local",
        lifecycle_status=status,
        metadata_json={"safe": {"value": 1}},
    )


def _service(read_facade: MagicMock) -> TransformationInputBindingReadService:
    return TransformationInputBindingReadService(
        context=ReadAuthorizationContext(tenant_id="tenant-1", roles=("admin",)),
        read_facade=read_facade,
    )


def test_exact_ready_dataset_version_resolves_without_fallback_lookup() -> None:
    read_facade = MagicMock()
    read_facade.get_dataset.return_value = SimpleNamespace(id="dataset-1")
    read_facade.get_version.return_value = _version()

    bound = _service(read_facade).resolve_input_reference(request=_request())

    assert bound.dataset_version.dataset_version_id == "version-1"
    assert bound.dataset_version.content_hash == "content-hash"
    read_facade.get_version.assert_called_once_with(
        ANY,
        "dataset-1",
        "version-1",
    )
    read_facade.list_versions.assert_not_called()
    read_facade.get_artifact.assert_not_called()


def test_wrong_tenant_or_missing_dataset_is_sanitized() -> None:
    read_facade = MagicMock()
    read_facade.get_dataset.side_effect = DatasetNotFoundError("private identifier")

    with pytest.raises(TransformationInputReferenceResolutionError) as captured:
        _service(read_facade).resolve_input_reference(request=_request())

    assert captured.value.code == "dataset_not_found_or_unauthorized"
    assert "private identifier" not in captured.value.message


def test_wrong_parent_or_missing_version_is_sanitized() -> None:
    read_facade = MagicMock()
    read_facade.get_dataset.return_value = SimpleNamespace(id="dataset-1")
    read_facade.get_version.side_effect = DatasetVersionNotFoundError("private version")

    with pytest.raises(TransformationInputReferenceResolutionError) as captured:
        _service(read_facade).resolve_input_reference(request=_request())

    assert captured.value.code == "dataset_version_not_found_or_unauthorized"
    assert "private version" not in captured.value.message


def test_dataset_version_must_be_ready() -> None:
    read_facade = MagicMock()
    read_facade.get_dataset.return_value = SimpleNamespace(id="dataset-1")
    read_facade.get_version.return_value = _version(status=DatasetVersionStatus.PENDING)

    with pytest.raises(TransformationInputReferenceResolutionError) as captured:
        _service(read_facade).resolve_input_reference(request=_request())

    assert captured.value.code == "dataset_version_not_ready"


def test_explicit_artifact_must_belong_to_version_and_be_active() -> None:
    read_facade = MagicMock()
    read_facade.get_dataset.return_value = SimpleNamespace(id="dataset-1")
    read_facade.get_version.return_value = _version()
    read_facade.get_artifact.return_value = _artifact(version_id="version-2")

    with pytest.raises(TransformationInputReferenceResolutionError) as wrong_version:
        _service(read_facade).resolve_input_reference(request=_request(artifact_id="artifact-1"))
    assert wrong_version.value.code == "artifact_wrong_dataset_version"

    read_facade.get_artifact.return_value = _artifact(status=ArtifactLifecycleStatus.DEPRECATED)
    with pytest.raises(TransformationInputReferenceResolutionError) as inactive:
        _service(read_facade).resolve_input_reference(request=_request(artifact_id="artifact-1"))
    assert inactive.value.code == "artifact_not_active"


def test_explicit_active_artifact_resolves_with_immutable_metadata() -> None:
    read_facade = MagicMock()
    read_facade.get_dataset.return_value = SimpleNamespace(id="dataset-1")
    read_facade.get_version.return_value = _version()
    read_facade.get_artifact.return_value = _artifact()

    bound = _service(read_facade).resolve_input_reference(
        request=_request(artifact_id="artifact-1"),
    )

    assert bound.artifact is not None
    assert bound.artifact.artifact_id == "artifact-1"
    with pytest.raises(TypeError):
        bound.artifact.metadata["changed"] = True  # type: ignore[index]
