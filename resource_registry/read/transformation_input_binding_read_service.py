from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from resource_registry.models import ArtifactLifecycleStatus, DatasetVersionStatus
from resource_registry.read.dtos import DatasetVersionSummary
from resource_registry.read.exceptions import (
    ArtifactNotFoundError,
    DatasetNotFoundError,
    DatasetVersionNotFoundError,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade
from resource_registry.transformations.binding.exceptions import (
    TransformationInputReferenceResolutionError,
)
from resource_registry.transformations.binding.immutable_json import freeze_json_value
from resource_registry.transformations.binding.results import (
    BoundDatasetArtifactReference,
    BoundDatasetVersionReference,
    BoundTransformationInput,
    TransformationInputBindingRequest,
)


class TransformationInputBindingReadService:
    """Resolve exact transformation input references through authorized read services."""

    def __init__(
        self,
        *,
        context: ReadAuthorizationContext,
        read_facade: AuthorizedDataRegistryReadFacade | None = None,
    ) -> None:
        self._context = context
        self._read_facade = read_facade or AuthorizedDataRegistryReadFacade()

    def resolve_input_reference(
        self,
        *,
        request: TransformationInputBindingRequest,
    ) -> BoundTransformationInput:
        """Resolve one exact tenant-safe dataset-version and optional artifact reference.

        Args:
            request: Explicit input, dataset, version, and optional artifact identifiers.

        Returns:
            Immutable sanitized reference metadata.

        Raises:
            TransformationInputReferenceResolutionError: If authorization, ownership, or lifecycle
                validation fails.
        """
        self._assert_dataset_is_readable(dataset_id=request.dataset_id)
        version = self._get_exact_version(request=request)
        if version.status != DatasetVersionStatus.READY:
            raise TransformationInputReferenceResolutionError(
                code="dataset_version_not_ready",
                message="Dataset version must be READY for transformation binding.",
            )

        artifact_reference = None
        if request.artifact_id is not None:
            artifact_reference = self._get_exact_artifact(request=request)

        return BoundTransformationInput(
            input_name=request.input_name,
            dataset_version=BoundDatasetVersionReference(
                tenant_id=str(version.tenant_id),
                dataset_id=str(version.dataset_id),
                dataset_version_id=str(version.id),
                version_number=int(version.version_number),
                status=str(version.status),
                source_uri=str(version.source_uri),
                content_hash=str(version.content_hash),
            ),
            artifact=artifact_reference,
        )

    def _assert_dataset_is_readable(self, *, dataset_id: str) -> None:
        """Verify exact dataset existence and authorization without leaking cross-tenant state."""
        try:
            self._read_facade.get_dataset(self._context, dataset_id)
        except (DatasetNotFoundError, DataRegistryAuthorizationError) as error:
            raise TransformationInputReferenceResolutionError(
                code="dataset_not_found_or_unauthorized",
                message="Dataset was not found or is not authorized.",
            ) from error

    def _get_exact_version(
        self,
        *,
        request: TransformationInputBindingRequest,
    ) -> DatasetVersionSummary:
        """Fetch an exact dataset version through tenant and parent dataset scope."""
        try:
            return self._read_facade.get_version(
                self._context,
                request.dataset_id,
                request.dataset_version_id,
            )
        except (DatasetVersionNotFoundError, DataRegistryAuthorizationError) as error:
            raise TransformationInputReferenceResolutionError(
                code="dataset_version_not_found_or_unauthorized",
                message="Dataset version was not found or is not authorized for the dataset.",
            ) from error

    def _get_exact_artifact(
        self,
        *,
        request: TransformationInputBindingRequest,
    ) -> BoundDatasetArtifactReference:
        """Fetch and validate one explicitly selected artifact."""
        assert request.artifact_id is not None
        try:
            artifact = self._read_facade.get_artifact(
                self._context,
                request.dataset_id,
                request.artifact_id,
            )
        except (ArtifactNotFoundError, DataRegistryAuthorizationError) as error:
            raise TransformationInputReferenceResolutionError(
                code="artifact_not_found_or_unauthorized",
                message="Artifact was not found or is not authorized.",
            ) from error
        if str(artifact.dataset_version_id) != str(request.dataset_version_id):
            raise TransformationInputReferenceResolutionError(
                code="artifact_wrong_dataset_version",
                message="Artifact does not belong to the exact bound dataset version.",
            )
        if artifact.lifecycle_status != ArtifactLifecycleStatus.ACTIVE:
            raise TransformationInputReferenceResolutionError(
                code="artifact_not_active",
                message="Artifact must be ACTIVE for transformation binding.",
            )
        frozen_metadata = freeze_json_value(artifact.metadata_json)
        return BoundDatasetArtifactReference(
            artifact_id=str(artifact.id),
            dataset_version_id=str(artifact.dataset_version_id),
            uri=str(artifact.uri),
            artifact_role=str(artifact.artifact_role),
            artifact_format=str(artifact.format),
            size_bytes=int(artifact.size_bytes),
            checksum=str(artifact.checksum) if artifact.checksum else None,
            storage_provider=(
                str(artifact.storage_provider) if artifact.storage_provider else None
            ),
            lifecycle_status=str(artifact.lifecycle_status),
            metadata=cast(Mapping[str, object], frozen_metadata),
        )
