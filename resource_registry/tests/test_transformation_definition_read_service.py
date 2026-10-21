from __future__ import annotations

import inspect
import uuid

import pytest
from django.utils import timezone

from resource_registry.models import TransformationDefinition, TransformationDefinitionVersion
from resource_registry.read.dtos import (
    TransformationDefinitionDetails,
    TransformationDefinitionSummary,
)
from resource_registry.read.exceptions import TransformationDefinitionNotFoundError
from resource_registry.read.transformation_definition_read_service import (
    TransformationDefinitionReadService,
)

pytestmark = pytest.mark.django_db

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
TOTAL_DEFINITION_COUNT = 105
OFFSET_VALUE = 2
OFFSET_EXPECTED_COUNT = 2


def _create_definition(
    *,
    tenant_id: uuid.UUID | None = None,
    definition_key: str = "audience-workbook",
    name: str = "Audience Workbook",
    archived: bool = False,
    description: str | None = None,
) -> TransformationDefinition:
    return TransformationDefinition.objects.create(
        tenant_id=tenant_id or uuid.uuid4(),
        definition_key=definition_key,
        name=name,
        description=description,
        archived_at=timezone.now() if archived else None,
    )


def test_get_definition_by_id_returns_correct_tenant_definition() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    definition = _create_definition(tenant_id=tenant_id, description="Useful definition")

    result = service.get_definition_by_id(
        tenant_id=str(tenant_id),
        definition_id=str(definition.id),
    )

    assert isinstance(result, TransformationDefinitionDetails)
    assert result.id == str(definition.id)
    assert result.tenant_id == str(tenant_id)
    assert result.description == "Useful definition"
    assert result.created_by_id is None


def test_get_definition_by_key_returns_correct_tenant_definition() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    definition = _create_definition(
        tenant_id=tenant_id,
        definition_key="normalizer",
        name="Normalizer",
    )

    result = service.get_definition_by_key(
        tenant_id=str(tenant_id),
        definition_key="normalizer",
    )

    assert result.id == str(definition.id)
    assert result.definition_key == "normalizer"
    assert result.name == "Normalizer"


def test_same_definition_key_is_tenant_scoped() -> None:
    service = TransformationDefinitionReadService()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    definition_a = _create_definition(tenant_id=tenant_a, definition_key="shared")
    definition_b = _create_definition(tenant_id=tenant_b, definition_key="shared")

    result_a = service.get_definition_by_key(tenant_id=str(tenant_a), definition_key="shared")
    result_b = service.get_definition_by_key(tenant_id=str(tenant_b), definition_key="shared")

    assert result_a.id == str(definition_a.id)
    assert result_b.id == str(definition_b.id)


def test_wrong_tenant_returns_not_found() -> None:
    service = TransformationDefinitionReadService()
    definition = _create_definition()

    with pytest.raises(TransformationDefinitionNotFoundError):
        service.get_definition_by_id(
            tenant_id=str(uuid.uuid4()),
            definition_id=str(definition.id),
        )


def test_missing_definition_returns_not_found() -> None:
    service = TransformationDefinitionReadService()

    with pytest.raises(TransformationDefinitionNotFoundError):
        service.get_definition_by_id(
            tenant_id=str(uuid.uuid4()),
            definition_id=str(uuid.uuid4()),
        )


def test_archived_definition_is_returned_by_explicit_lookup_by_default() -> None:
    service = TransformationDefinitionReadService()
    definition = _create_definition(archived=True)

    result = service.get_definition_by_id(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
    )

    assert result.id == str(definition.id)
    assert result.archived_at is not None


def test_archived_definition_hidden_when_lookup_excludes_archived() -> None:
    service = TransformationDefinitionReadService()
    definition = _create_definition(archived=True)

    with pytest.raises(TransformationDefinitionNotFoundError):
        service.get_definition_by_id(
            tenant_id=str(definition.tenant_id),
            definition_id=str(definition.id),
            include_archived=False,
        )


def test_list_definitions_excludes_archived_by_default() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    active_definition = _create_definition(
        tenant_id=tenant_id,
        definition_key="active-definition",
    )
    _create_definition(
        tenant_id=tenant_id,
        definition_key="archived-definition",
        archived=True,
    )

    results = service.list_definitions(tenant_id=str(tenant_id))

    assert [result.id for result in results] == [str(active_definition.id)]


def test_list_definitions_includes_archived_when_requested() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    _create_definition(tenant_id=tenant_id, definition_key="active-definition")
    _create_definition(tenant_id=tenant_id, definition_key="archived-definition", archived=True)

    results = service.list_definitions(tenant_id=str(tenant_id), include_archived=True)

    assert {result.definition_key for result in results} == {
        "active-definition",
        "archived-definition",
    }


def test_list_definitions_orders_by_key_and_id() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    _create_definition(tenant_id=tenant_id, definition_key="zulu")
    _create_definition(tenant_id=tenant_id, definition_key="alpha")
    _create_definition(tenant_id=tenant_id, definition_key="middle")

    results = service.list_definitions(tenant_id=str(tenant_id))

    assert [result.definition_key for result in results] == ["alpha", "middle", "zulu"]


def test_list_definitions_uses_default_pagination() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    for index in range(TOTAL_DEFINITION_COUNT):
        _create_definition(
            tenant_id=tenant_id,
            definition_key=f"definition-{index:03d}",
        )

    results = service.list_definitions(tenant_id=str(tenant_id))

    assert len(results) == DEFAULT_PAGE_SIZE


def test_list_definitions_caps_maximum_limit() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    for index in range(TOTAL_DEFINITION_COUNT):
        _create_definition(
            tenant_id=tenant_id,
            definition_key=f"definition-{index:03d}",
        )

    results = service.list_definitions(tenant_id=str(tenant_id), limit=TOTAL_DEFINITION_COUNT)

    assert len(results) == MAX_PAGE_SIZE


def test_list_definitions_applies_offset() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    for definition_key in ("alpha", "beta", "gamma", "omega"):
        _create_definition(tenant_id=tenant_id, definition_key=definition_key)

    results = service.list_definitions(
        tenant_id=str(tenant_id),
        limit=OFFSET_EXPECTED_COUNT,
        offset=OFFSET_VALUE,
    )

    assert [result.definition_key for result in results] == ["gamma", "omega"]


def test_list_definitions_clamps_negative_offset() -> None:
    service = TransformationDefinitionReadService()
    tenant_id = uuid.uuid4()
    _create_definition(tenant_id=tenant_id, definition_key="alpha")

    results = service.list_definitions(tenant_id=str(tenant_id), offset=-1)

    assert [result.definition_key for result in results] == ["alpha"]


def test_list_definitions_empty_result() -> None:
    service = TransformationDefinitionReadService()

    results = service.list_definitions(tenant_id=str(uuid.uuid4()))

    assert results == []


def test_list_definitions_returns_summary_dtos() -> None:
    service = TransformationDefinitionReadService()
    definition = _create_definition()

    result = service.list_definitions(tenant_id=str(definition.tenant_id))[0]

    assert isinstance(result, TransformationDefinitionSummary)
    assert result.id == str(definition.id)
    assert result.definition_key == definition.definition_key
    assert result.archived_at is None


def test_definition_read_service_does_not_resolve_versions() -> None:
    service = TransformationDefinitionReadService()
    definition = _create_definition()

    with pytest.MonkeyPatch.context() as monkeypatch:

        def fail_version_query(*args: object, **kwargs: object) -> None:
            raise AssertionError("Definition read service must not resolve versions.")

        monkeypatch.setattr(TransformationDefinitionVersion.objects, "filter", fail_version_query)
        result = service.get_definition_by_id(
            tenant_id=str(definition.tenant_id),
            definition_id=str(definition.id),
        )

    assert result.id == str(definition.id)


def test_definition_read_service_has_no_lifecycle_dependency() -> None:
    source = inspect.getsource(TransformationDefinitionReadService)

    assert "TransformationDefinitionLifecycleService" not in source
    assert "publish_version" not in source
    assert "deprecate_version" not in source
    assert "select_for_update" not in source
    assert "transaction.atomic" not in source
