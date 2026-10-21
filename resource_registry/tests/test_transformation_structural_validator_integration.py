from __future__ import annotations

import inspect
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from resource_registry.models import (
    TransformationDefinition,
    TransformationDefinitionVersion,
    TransformationDefinitionVersionStatus,
)
from resource_registry.transformations.validation.structural_validator import (
    TransformationStructuralValidator,
)

pytestmark = pytest.mark.django_db


def _valid_specification() -> dict[str, object]:
    return {
        "dsl_version": "1.0",
        "kind": "transformation",
        "steps": [
            {
                "step_id": "inspect-source",
                "provider": {"key": "any-provider", "version": "1.0"},
                "operation": {"key": "inspect", "version": "1.0"},
                "config": {},
            },
        ],
    }


def _create_definition() -> TransformationDefinition:
    return TransformationDefinition.objects.create(
        tenant_id=uuid.uuid4(),
        definition_key="generic-transformation",
        name="Generic Transformation",
    )


def _create_version(
    *,
    definition: TransformationDefinition,
    specification_json: dict[str, object] | None = None,
) -> TransformationDefinitionVersion:
    version = TransformationDefinitionVersion(
        definition=definition,
        version_number=1,
        status=TransformationDefinitionVersionStatus.DRAFT,
        specification_json=specification_json or _valid_specification(),
    )
    version.refresh_specification_checksum()
    version.save()
    return version


def test_validator_uses_read_service_to_validate_persisted_specification() -> None:
    definition = _create_definition()
    version = _create_version(definition=definition)
    original_checksum = version.specification_checksum
    original_status = version.status

    result = TransformationStructuralValidator().validate_persisted_version(
        tenant_id=str(definition.tenant_id),
        definition_id=str(definition.id),
        version_id=str(version.id),
    )

    version.refresh_from_db()
    assert result.success is True
    assert version.specification_checksum == original_checksum
    assert version.status == original_status


def test_persisted_validation_does_not_write_to_database() -> None:
    definition = _create_definition()
    version = _create_version(definition=definition)

    with CaptureQueriesContext(connection) as captured_queries:
        TransformationStructuralValidator().validate_persisted_version(
            tenant_id=str(definition.tenant_id),
            definition_id=str(definition.id),
            version_id=str(version.id),
        )

    disallowed_statements = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP")
    executed_sql = [query["sql"].lstrip().upper() for query in captured_queries.captured_queries]
    assert not any(sql.startswith(disallowed_statements) for sql in executed_sql)


def test_structural_validator_has_no_provider_or_execution_dependency() -> None:
    source = inspect.getsource(TransformationStructuralValidator)

    assert "resource_registry.providers" not in source
    assert "TabularTransformationExecutionService" not in source
    assert "refresh_specification_checksum" not in source
    assert "publish_version" not in source
