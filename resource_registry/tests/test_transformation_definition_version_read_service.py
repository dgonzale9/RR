from __future__ import annotations

import inspect
import uuid

import pytest
from django.utils import timezone

from resource_registry.models import (
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
)
from resource_registry.read.dtos import (
    TransformationDefinitionVersionDetails,
    TransformationDefinitionVersionSummary,
)
from resource_registry.read.exceptions import (
    InvalidTransformationDefinitionVersionStatusFilterError,
    TransformationDefinitionNotFoundError,
    TransformationDefinitionVersionNotFoundError,
)
from resource_registry.read.transformation_definition_version_read_service import (
    TransformationDefinitionVersionReadService,
)

pytestmark = pytest.mark.django_db

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
TOTAL_VERSION_COUNT = 105
OFFSET_VALUE = 2
OFFSET_LIMIT = 2
HIGHEST_OFFSET_VERSION = 2
LOWEST_OFFSET_VERSION = 1


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


def test_get_version_by_id_resolves_through_tenant_scoped_parent() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    version = _create_version(
        definition=definition,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    result = service.get_version_by_id(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        version_id=str(version.id),
    )

    assert isinstance(result, TransformationDefinitionVersionDetails)
    assert result.id == str(version.id)
    assert result.definition_id == str(definition.id)
    assert result.status == TransformationDefinitionVersionStatus.PUBLISHED


def test_get_version_by_number_resolves_through_tenant_scoped_parent() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    version = _create_version(definition=definition, version_number=3)

    result = service.get_version_by_number(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        version_number=version.version_number,
    )

    assert result.id == str(version.id)
    assert result.version_number == version.version_number


def test_wrong_tenant_returns_not_found() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    version = _create_version(definition=definition)

    with pytest.raises(TransformationDefinitionNotFoundError):
        service.get_version_by_id(
            tenant_id=str(uuid.uuid4()),
            definition_id=str(definition.id),
            version_id=str(version.id),
        )


def test_wrong_parent_version_association_returns_not_found() -> None:
    service = TransformationDefinitionVersionReadService()
    tenant_id = uuid.uuid4()
    definition = _create_definition(tenant_id=tenant_id, definition_key="target")
    other_definition = _create_definition(tenant_id=tenant_id, definition_key="other")
    other_version = _create_version(definition=other_definition)

    with pytest.raises(TransformationDefinitionVersionNotFoundError):
        service.get_version_by_id(
            tenant_id=str(tenant_id),
            definition_id=str(definition.id),
            version_id=str(other_version.id),
        )


def test_missing_version_returns_not_found() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()

    with pytest.raises(TransformationDefinitionVersionNotFoundError):
        service.get_version_by_id(
            tenant_id=str(definition.tenant_id),
            definition_id=str(definition.id),
            version_id=str(uuid.uuid4()),
        )


@pytest.mark.parametrize(
    "status",
    [
        TransformationDefinitionVersionStatus.DRAFT,
        TransformationDefinitionVersionStatus.PUBLISHED,
        TransformationDefinitionVersionStatus.DEPRECATED,
    ],
)
def test_all_version_statuses_are_addressable(status: str) -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    version = _create_version(definition=definition, status=status)

    result = service.get_version_by_id(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        version_id=str(version.id),
    )

    assert result.status == status


def test_list_versions_returns_all_statuses_by_default() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    _create_version(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.DRAFT,
    )
    _create_version(
        definition=definition,
        version_number=2,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )
    _create_version(
        definition=definition,
        version_number=3,
        status=TransformationDefinitionVersionStatus.DEPRECATED,
    )

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
    )

    assert {result.status for result in results} == {
        TransformationDefinitionVersionStatus.DRAFT,
        TransformationDefinitionVersionStatus.PUBLISHED,
        TransformationDefinitionVersionStatus.DEPRECATED,
    }


def test_list_versions_filters_by_one_status() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    _create_version(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.DRAFT,
    )
    published = _create_version(
        definition=definition,
        version_number=2,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    assert [result.id for result in results] == [str(published.id)]


def test_list_versions_rejects_invalid_status_filter() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()

    with pytest.raises(InvalidTransformationDefinitionVersionStatusFilterError):
        service.list_versions(
            tenant_id=str(definition.tenant_id),
            definition_id=str(definition.id),
            status="ACTIVE",
        )


def test_deprecated_versions_remain_listed() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    deprecated = _create_version(
        definition=definition,
        status=TransformationDefinitionVersionStatus.DEPRECATED,
    )

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
    )

    assert [result.id for result in results] == [str(deprecated.id)]


def test_archived_parent_remains_accessible_by_default() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition(archived=True)
    version = _create_version(definition=definition)

    result = service.get_version_by_id(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        version_id=str(version.id),
    )

    assert result.id == str(version.id)


def test_archived_parent_hidden_when_excluded() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition(archived=True)
    version = _create_version(definition=definition)

    with pytest.raises(TransformationDefinitionNotFoundError):
        service.get_version_by_id(
            tenant_id=str(definition.tenant_id),
            definition_id=str(definition.id),
            version_id=str(version.id),
            include_archived_definition=False,
        )

    with pytest.raises(TransformationDefinitionNotFoundError):
        service.list_versions(
            tenant_id=str(definition.tenant_id),
            definition_id=str(definition.id),
            include_archived_definition=False,
        )


def test_list_versions_orders_by_version_number_descending() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    _create_version(definition=definition, version_number=1)
    _create_version(definition=definition, version_number=3)
    _create_version(definition=definition, version_number=2)

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
    )

    assert [result.version_number for result in results] == [3, 2, 1]


def test_list_versions_uses_default_pagination() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    for version_number in range(1, TOTAL_VERSION_COUNT + 1):
        _create_version(definition=definition, version_number=version_number)

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
    )

    assert len(results) == DEFAULT_PAGE_SIZE


def test_list_versions_caps_maximum_limit() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    for version_number in range(1, TOTAL_VERSION_COUNT + 1):
        _create_version(definition=definition, version_number=version_number)

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        limit=TOTAL_VERSION_COUNT,
    )

    assert len(results) == MAX_PAGE_SIZE


def test_list_versions_applies_offset() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    for version_number in range(1, 5):
        _create_version(definition=definition, version_number=version_number)

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        limit=OFFSET_LIMIT,
        offset=OFFSET_VALUE,
    )

    assert [result.version_number for result in results] == [
        HIGHEST_OFFSET_VERSION,
        LOWEST_OFFSET_VERSION,
    ]


def test_list_versions_clamps_negative_offset() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    version = _create_version(definition=definition)

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        offset=-1,
    )

    assert [result.id for result in results] == [str(version.id)]


def test_version_summary_excludes_specification_json() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    _create_version(definition=definition)

    result = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
    )[0]

    assert isinstance(result, TransformationDefinitionVersionSummary)
    assert not hasattr(result, "specification_json")


def test_version_detail_includes_specification_json() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    specification_json = _specification("map")
    version = _create_version(definition=definition, specification_json=specification_json)

    result = service.get_version_by_id(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        version_id=str(version.id),
    )

    assert result.specification_json == specification_json
    assert result.created_by_id is None


def test_multiple_persisted_published_rows_are_returned_without_collapsing() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    first = _create_version(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )
    second = _create_version(
        definition=definition,
        version_number=2,
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    results = service.list_versions(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        status=TransformationDefinitionVersionStatus.PUBLISHED,
    )

    assert {result.id for result in results} == {str(first.id), str(second.id)}


def test_version_read_service_does_not_refresh_checksum() -> None:
    service = TransformationDefinitionVersionReadService()
    definition = _create_definition()
    version = _create_version(definition=definition)

    with pytest.MonkeyPatch.context() as monkeypatch:

        def fail_checksum_refresh(*args: object, **kwargs: object) -> None:
            raise AssertionError("Read service must not refresh checksums.")

        monkeypatch.setattr(
            TransformationDefinitionVersion,
            "refresh_specification_checksum",
            fail_checksum_refresh,
        )
        result = service.get_version_by_id(
            tenant_id=str(definition.tenant_id),
            definition_id=str(definition.id),
            version_id=str(version.id),
        )

    assert result.id == str(version.id)


def test_version_read_service_has_no_lifecycle_or_runtime_dependencies() -> None:
    source = inspect.getsource(TransformationDefinitionVersionReadService)

    assert "TransformationDefinitionLifecycleService" not in source
    assert "publish_version" not in source
    assert "deprecate_version" not in source
    assert "refresh_specification_checksum" not in source
    assert "select_for_update" not in source
    assert "transaction.atomic" not in source
    assert "rest_framework" not in source
    assert "TransformationRun" not in source
    assert "Provider" not in source
    assert "DatasetArtifact" not in source
    assert "DatasetQueryJob" not in source
