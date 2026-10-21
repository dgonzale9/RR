from __future__ import annotations

import inspect
import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone

from resource_registry.models import (
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
)
from resource_registry.services.transformation_definition_lifecycle_service import (
    ArchivedTransformationDefinitionError,
    InvalidTransformationDefinitionVersionTransitionError,
    TransformationDefinitionLifecycleService,
    TransformationDefinitionNotFoundError,
    TransformationDefinitionVersionNotFoundError,
    TransformationSpecificationFinalizationError,
)
from resource_registry.utils.json_checksum import (
    TransformationSpecificationChecksumError,
    calculate_transformation_specification_checksum,
)

pytestmark = pytest.mark.django_db

IMMUTABILITY_VERSION_NUMBER = 7
DEPRECATION_VERSION_NUMBER = 3


def _specification(step_key: str = "inspect") -> dict[str, object]:
    return {"spec_version": "1.0", "steps": [{"step_key": step_key}]}


def _create_definition(
    *,
    tenant_id: uuid.UUID | None = None,
    definition_key: str = "audience-workbook",
    archived: bool = False,
) -> TransformationDefinition:
    return TransformationDefinition.objects.create(
        tenant_id=tenant_id or uuid.uuid4(),
        definition_key=definition_key,
        name=definition_key.replace("-", " ").title(),
        archived_at=timezone.now() if archived else None,
    )


def _create_version(
    *,
    definition: TransformationDefinition,
    version_number: int = 1,
    status: str = TransformationDefinitionVersionStatus.DRAFT,
    specification_json: dict[str, object] | None = None,
) -> TransformationDefinitionVersion:
    version = TransformationDefinitionVersion(
        definition=definition,
        version_number=version_number,
        status=status,
        specification_json=specification_json or _specification(f"step-{version_number}"),
    )
    version.refresh_specification_checksum()
    version.save()
    return version


def test_publish_draft_version_persists_published_status_and_current_checksum() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        specification_json=_specification("normalize"),
    )

    result = service.publish_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
    )

    version.refresh_from_db()
    assert result.id == version.id
    assert version.status == TransformationDefinitionVersionStatus.PUBLISHED
    assert version.specification_checksum == calculate_transformation_specification_checksum(
        version.specification_json,
    )


def test_publish_refreshes_stale_checksum_from_current_specification_json() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        specification_json=_specification("before"),
    )
    stale_checksum = version.specification_checksum
    updated_specification = _specification("after")
    TransformationDefinitionVersion.objects.filter(pk=version.pk).update(
        specification_json=updated_specification,
    )

    service.publish_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
    )

    version.refresh_from_db()
    assert version.specification_checksum != stale_checksum
    assert version.specification_checksum == calculate_transformation_specification_checksum(
        updated_specification,
    )


def test_publish_under_archived_definition_fails_without_mutating_version() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition(archived=True)
    version = _create_version(definition=definition)

    with pytest.raises(ArchivedTransformationDefinitionError):
        service.publish_version(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            version_id=version.id,
        )

    version.refresh_from_db()
    assert version.status == TransformationDefinitionVersionStatus.DRAFT


def test_publish_deprecated_target_is_rejected() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        status=TransformationDefinitionVersionStatus.DEPRECATED,
    )

    with pytest.raises(InvalidTransformationDefinitionVersionTransitionError):
        service.publish_version(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            version_id=version.id,
        )


def test_publish_already_published_target_is_idempotent_without_sibling_changes() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    target = _create_version(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )
    duplicate_published_sibling = _create_version(
        definition=definition,
        version_number=2,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    result = service.publish_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=target.id,
    )

    duplicate_published_sibling.refresh_from_db()
    assert result.id == target.id
    assert duplicate_published_sibling.status == TransformationDefinitionVersionStatus.PUBLISHED


def test_publish_rejects_tenant_mismatch_without_exposing_definition() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(definition=definition)

    with pytest.raises(TransformationDefinitionNotFoundError):
        service.publish_version(
            tenant_id=uuid.uuid4(),
            definition_id=definition.id,
            version_id=version.id,
        )


def test_publish_rejects_wrong_definition_version_association() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition(definition_key="target")
    other_definition = _create_definition(
        tenant_id=definition.tenant_id,
        definition_key="other",
    )
    other_version = _create_version(definition=other_definition)

    with pytest.raises(TransformationDefinitionVersionNotFoundError):
        service.publish_version(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            version_id=other_version.id,
        )


def test_publish_deprecates_existing_published_siblings_only() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    published_sibling = _create_version(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )
    draft_sibling = _create_version(
        definition=definition,
        version_number=2,
        status=TransformationDefinitionVersionStatus.DRAFT,
    )
    deprecated_sibling = _create_version(
        definition=definition,
        version_number=3,
        status=TransformationDefinitionVersionStatus.DEPRECATED,
    )
    target = _create_version(
        definition=definition,
        version_number=4,
        status=TransformationDefinitionVersionStatus.DRAFT,
    )

    service.publish_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=target.id,
    )

    published_sibling.refresh_from_db()
    draft_sibling.refresh_from_db()
    deprecated_sibling.refresh_from_db()
    target.refresh_from_db()
    assert published_sibling.status == TransformationDefinitionVersionStatus.DEPRECATED
    assert draft_sibling.status == TransformationDefinitionVersionStatus.DRAFT
    assert deprecated_sibling.status == TransformationDefinitionVersionStatus.DEPRECATED
    assert target.status == TransformationDefinitionVersionStatus.PUBLISHED
    assert (
        TransformationDefinitionVersion.objects.filter(
            definition=definition,
            status=TransformationDefinitionVersionStatus.PUBLISHED,
        ).count()
        == 1
    )


def test_publish_rolls_back_if_checksum_finalization_fails() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    published_sibling = _create_version(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )
    target = _create_version(
        definition=definition,
        version_number=2,
        status=TransformationDefinitionVersionStatus.DRAFT,
    )

    with (
        patch.object(
            TransformationDefinitionVersion,
            "refresh_specification_checksum",
            side_effect=TransformationSpecificationChecksumError("unsupported"),
        ),
        pytest.raises(TransformationSpecificationFinalizationError),
    ):
        service.publish_version(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            version_id=target.id,
        )

    published_sibling.refresh_from_db()
    target.refresh_from_db()
    assert published_sibling.status == TransformationDefinitionVersionStatus.PUBLISHED
    assert target.status == TransformationDefinitionVersionStatus.DRAFT


def test_publish_rolls_back_sibling_deprecation_if_target_persistence_fails() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    published_sibling = _create_version(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )
    target = _create_version(
        definition=definition,
        version_number=2,
        status=TransformationDefinitionVersionStatus.DRAFT,
    )
    original_save = TransformationDefinitionVersion.save

    def fail_target_publication(
        instance: TransformationDefinitionVersion,
        *args: object,
        **kwargs: object,
    ) -> None:
        if (
            instance.pk == target.pk
            and instance.status == TransformationDefinitionVersionStatus.PUBLISHED
        ):
            raise RuntimeError("publication failed")
        original_save(instance, *args, **kwargs)

    with (
        patch.object(TransformationDefinitionVersion, "save", fail_target_publication),
        pytest.raises(RuntimeError),
    ):
        service.publish_version(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            version_id=target.id,
        )

    published_sibling.refresh_from_db()
    target.refresh_from_db()
    assert published_sibling.status == TransformationDefinitionVersionStatus.PUBLISHED
    assert target.status == TransformationDefinitionVersionStatus.DRAFT


def test_deprecate_published_version_persists_deprecated_status() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    result = service.deprecate_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
    )

    version.refresh_from_db()
    assert result.id == version.id
    assert version.status == TransformationDefinitionVersionStatus.DEPRECATED


def test_deprecate_already_deprecated_target_is_idempotent() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        status=TransformationDefinitionVersionStatus.DEPRECATED,
    )

    result = service.deprecate_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
    )

    assert result.id == version.id
    version.refresh_from_db()
    assert version.status == TransformationDefinitionVersionStatus.DEPRECATED


def test_deprecate_draft_version_is_rejected() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(definition=definition)

    with pytest.raises(InvalidTransformationDefinitionVersionTransitionError):
        service.deprecate_version(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            version_id=version.id,
        )


def test_deprecate_under_archived_definition_is_allowed() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition(archived=True)
    version = _create_version(
        definition=definition,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    service.deprecate_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
    )

    version.refresh_from_db()
    assert version.status == TransformationDefinitionVersionStatus.DEPRECATED


def test_deprecate_only_published_version_leaves_zero_published_versions() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    service.deprecate_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
    )

    assert (
        TransformationDefinitionVersion.objects.filter(
            definition=definition,
            status=TransformationDefinitionVersionStatus.PUBLISHED,
        ).count()
        == 0
    )
    assert TransformationDefinitionVersion.objects.get(pk=version.pk).status == (
        TransformationDefinitionVersionStatus.DEPRECATED
    )


def test_deprecate_rejects_tenant_mismatch_and_wrong_parent() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition(definition_key="target")
    other_definition = _create_definition(
        tenant_id=definition.tenant_id,
        definition_key="other",
    )
    version = _create_version(
        definition=definition,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )
    other_version = _create_version(
        definition=other_definition,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    with pytest.raises(TransformationDefinitionNotFoundError):
        service.deprecate_version(
            tenant_id=uuid.uuid4(),
            definition_id=definition.id,
            version_id=version.id,
        )

    with pytest.raises(TransformationDefinitionVersionNotFoundError):
        service.deprecate_version(
            tenant_id=definition.tenant_id,
            definition_id=definition.id,
            version_id=other_version.id,
        )


def test_publish_does_not_mutate_immutable_business_fields() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        version_number=IMMUTABILITY_VERSION_NUMBER,
        specification_json=_specification("derive"),
    )
    original_specification = dict(version.specification_json)
    original_definition_id = version.definition_id
    original_created_by_id = version.created_by_id

    service.publish_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
    )

    version.refresh_from_db()
    assert version.specification_json == original_specification
    assert version.version_number == IMMUTABILITY_VERSION_NUMBER
    assert version.definition_id == original_definition_id
    assert version.created_by_id == original_created_by_id


def test_deprecate_changes_only_lifecycle_fields() -> None:
    service = TransformationDefinitionLifecycleService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        version_number=DEPRECATION_VERSION_NUMBER,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
        specification_json=_specification("rank"),
    )
    original_specification = dict(version.specification_json)
    original_checksum = version.specification_checksum
    original_definition_id = version.definition_id

    service.deprecate_version(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        version_id=version.id,
    )

    version.refresh_from_db()
    assert version.status == TransformationDefinitionVersionStatus.DEPRECATED
    assert version.specification_json == original_specification
    assert version.specification_checksum == original_checksum
    assert version.version_number == DEPRECATION_VERSION_NUMBER
    assert version.definition_id == original_definition_id


def test_lifecycle_service_locking_contract_uses_atomic_and_select_for_update() -> None:
    source = inspect.getsource(TransformationDefinitionLifecycleService)
    definition_lock_index = source.index("TransformationDefinition.objects.select_for_update()")
    version_lock_index = source.index("TransformationDefinitionVersion.objects.select_for_update()")

    assert "transaction.atomic()" in source
    assert definition_lock_index < version_lock_index
    assert "version_id" in source
    assert "definition=definition" in source


def test_lifecycle_service_has_no_arbitrary_published_version_update_api() -> None:
    public_methods = {
        name
        for name, value in inspect.getmembers(
            TransformationDefinitionLifecycleService,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert public_methods == {"publish_version", "deprecate_version"}


def test_lifecycle_service_has_no_out_of_scope_runtime_dependencies() -> None:
    source = inspect.getsource(TransformationDefinitionLifecycleService)

    assert "rest_framework" not in source
    assert "TransformationRun" not in source
    assert "Provider" not in source
    assert "BIF" not in source
    assert "DatasetArtifact" not in source
