from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.policies.models import ResolvedPolicy
from resource_registry.read.dtos import (
    DatasetContextSummary,
    DatasetDetails,
    DatasetProfileSummary,
    DatasetSchemaSummary,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import InvalidVisibilityPolicyError
from resource_registry.read_auth.field_filtering import FieldFilterService
from resource_registry.read_auth.visibility import (
    DATASET_PROFILE_FULL_READ,
    DATASET_PROFILE_MASKED_READ,
    DATASET_SCHEMA_FULL_READ,
    DATASET_SCHEMA_MASKED_READ,
    ResolvedVisibilityPolicy,
    SchemaProfileVisibilityPolicy,
    SchemaProfileVisibilityResolver,
)

DEFAULT_ROW_COUNT = 100


class StubVisibilityResolver:
    def __init__(
        self,
        policy: SchemaProfileVisibilityPolicy,
        source: str = "system_default",
    ) -> None:
        self._policy = policy
        self._source = source

    def resolve_policy(self, *, tenant_id: str, dataset_id: str | None) -> ResolvedVisibilityPolicy:
        _ = tenant_id, dataset_id
        return ResolvedVisibilityPolicy(policy=self._policy, source=self._source, revision=None)


def _context(**overrides) -> ReadAuthorizationContext:
    defaults = {
        "tenant_id": "tenant-a",
        "actor": "user-1",
        "roles": ("reader",),
        "scopes": (),
        "allowed_dataset_ids": None,
        "allow_cross_tenant": False,
        "metadata": {},
    }
    defaults.update(overrides)
    return ReadAuthorizationContext(**defaults)


def _policy(**overrides) -> SchemaProfileVisibilityPolicy:
    defaults = {
        "hidden_columns": (),
        "masked_columns": (),
        "allowed_columns": None,
        "hidden_schema_classifications": (),
        "masked_schema_classifications": (),
        "hidden_metrics": (),
        "masked_metrics": (),
        "allowed_metrics": None,
        "hidden_profile_classifications": (),
        "masked_profile_classifications": (),
        "role_permissions": {
            "admin": SimpleNamespace(can_view_all_schema=True, can_view_all_profile=True),
            "reader": SimpleNamespace(can_view_all_schema=True, can_view_all_profile=False),
            "restricted_reader": SimpleNamespace(
                can_view_all_schema=False,
                can_view_all_profile=False,
            ),
        },
    }
    defaults.update(overrides)
    return SchemaProfileVisibilityPolicy(**defaults)


def _schema_summary() -> DatasetSchemaSummary:
    return DatasetSchemaSummary(
        id="s-1",
        dataset_version_id="v-1",
        tenant_id="tenant-a",
        schema_hash="h",
        column_count=3,
        schema_json=[
            {"name": "id", "type": "BIGINT", "extra": "safe"},
            {"name": "email", "type": "VARCHAR"},
            {"name": "salary", "type": "DECIMAL"},
            {"name": "health_notes", "type": "TEXT", "classification": "phi"},
            "unknown-shape",
        ],
        created_at="2026-06-24T00:00:00+00:00",
    )


def _profile_summary() -> DatasetProfileSummary:
    return DatasetProfileSummary(
        id="p-1",
        dataset_version_id="v-1",
        tenant_id="tenant-a",
        profile_type="BASIC",
        profile_hash="h",
        metrics_json={
            "row_count": DEFAULT_ROW_COUNT,
            "null_rate": {"email": 0.2},
            "salary_percentile_95": 1000,
            "email_null_rate": {"value": 0.01, "classification": "pii"},
            "preview": [{"id": 1}],
            "credentials": {"token": "abc"},
        },
        computed_at="2026-06-24T00:00:00+00:00",
    )


def _profile_summary_with_map() -> DatasetProfileSummary:
    return DatasetProfileSummary(
        id="p-2",
        dataset_version_id="v-1",
        tenant_id="tenant-a",
        profile_type="BASIC",
        profile_hash="h",
        metrics_json={
            "metrics": {
                "email_null_rate": 0.01,
                "row_count": DEFAULT_ROW_COUNT,
            },
            "classifications": {
                "email_null_rate": ["PII", "confidential"],
            },
        },
        computed_at="2026-06-24T00:00:00+00:00",
    )


def _dataset_details() -> DatasetDetails:
    return DatasetDetails(
        id="d-1",
        tenant_id="tenant-a",
        slug="sales",
        name="Sales",
        source_type="FILE_UPLOAD",
        status="ACTIVE",
        description=None,
        owner_team=None,
        tags=[],
        created_at="2026-06-24T00:00:00+00:00",
        updated_at="2026-06-24T00:00:00+00:00",
        archived_at=None,
    )


def test_visibility_resolver_system_default() -> None:
    resolver = SchemaProfileVisibilityResolver(policy_resolution_service=MagicMock())
    result = resolver.resolve_policy(tenant_id="tenant-a", dataset_id=None)
    assert result.source == "system_default"
    assert "admin" in result.policy.role_permissions


@pytest.mark.parametrize("role", ("reader", "restricted_reader"))
def test_system_default_profile_exposes_safe_metrics_to_readers(role: str) -> None:
    """Expose safe profile metrics to both read roles.

    Args:
        role: Registry reader role under test.
    """
    resolver = SchemaProfileVisibilityResolver(policy_resolution_service=MagicMock())
    filter_service = FieldFilterService(visibility_resolver=resolver)

    with patch("resource_registry.read_auth.visibility.Dataset.objects.filter") as mocked_filter:
        mocked_filter.return_value.first.return_value = None
        result = filter_service.filter_dataset_profile_summary(
            _profile_summary(),
            _context(roles=(role,)),
            dataset_id="d-1",
        )

    assert result.metrics_json["row_count"] == DEFAULT_ROW_COUNT
    assert result.metrics_json["null_rate"] == {"email": 0.2}
    assert result.metrics_json["email_null_rate"] == "REDACTED"
    assert "preview" not in result.metrics_json
    assert "credentials" not in result.metrics_json


def test_visibility_resolver_uses_resolved_dataset_policy_source() -> None:
    policy_resolution_service = MagicMock()
    policy_resolution_service.resolve_effective_policy.return_value = ResolvedPolicy(
        source="dataset_override",
        policy_key="schema_profile_visibility_default",
        tenant_id="tenant-a",
        dataset_id="d-1",
        revision=2,
        policy_id="p-1",
        rules_json={
            "schema": {"hidden_columns": [], "masked_columns": [], "allowed_columns": ["id"]},
            "profile": {
                "hidden_metrics": [],
                "masked_metrics": [],
                "allowed_metrics": ["row_count"],
            },
            "roles": {
                "admin": {
                    "can_view_all_schema": True,
                    "can_view_all_profile": True,
                },
                "reader": {
                    "can_view_all_schema": True,
                    "can_view_all_profile": False,
                },
                "restricted_reader": {
                    "can_view_all_schema": False,
                    "can_view_all_profile": False,
                },
            },
        },
    )
    resolver = SchemaProfileVisibilityResolver(policy_resolution_service=policy_resolution_service)

    with patch("resource_registry.read_auth.visibility.Dataset.objects.filter") as mocked_filter:
        mocked_filter.return_value.first.return_value = SimpleNamespace(
            id="d-1",
            tenant_id="tenant-a",
        )
        result = resolver.resolve_policy(tenant_id="tenant-a", dataset_id="d-1")

    assert result.source == "dataset_override"


def test_visibility_resolver_uses_resolved_tenant_default_source() -> None:
    policy_resolution_service = MagicMock()
    policy_resolution_service.resolve_effective_policy.return_value = ResolvedPolicy(
        source="tenant_default",
        policy_key="schema_profile_visibility_default",
        tenant_id="tenant-a",
        dataset_id="d-1",
        revision=1,
        policy_id="p-1",
        rules_json={
            "schema": {"hidden_columns": [], "masked_columns": [], "allowed_columns": None},
            "profile": {"hidden_metrics": [], "masked_metrics": [], "allowed_metrics": None},
            "roles": {
                "admin": {
                    "can_view_all_schema": True,
                    "can_view_all_profile": True,
                },
                "reader": {
                    "can_view_all_schema": True,
                    "can_view_all_profile": False,
                },
                "restricted_reader": {
                    "can_view_all_schema": False,
                    "can_view_all_profile": False,
                },
            },
        },
    )
    resolver = SchemaProfileVisibilityResolver(policy_resolution_service=policy_resolution_service)

    with patch("resource_registry.read_auth.visibility.Dataset.objects.filter") as mocked_filter:
        mocked_filter.return_value.first.return_value = SimpleNamespace(
            id="d-1",
            tenant_id="tenant-a",
        )
        result = resolver.resolve_policy(tenant_id="tenant-a", dataset_id="d-1")

    assert result.source == "tenant_default"


def test_visibility_resolver_invalid_policy_rejected() -> None:
    policy_resolution_service = MagicMock()
    policy_resolution_service.resolve_effective_policy.return_value = ResolvedPolicy(
        source="tenant_default",
        policy_key="schema_profile_visibility_default",
        tenant_id="tenant-a",
        dataset_id="d-1",
        revision=1,
        policy_id="p-1",
        rules_json={"schema": {}, "profile": {}, "roles": {"reader": {"bad": True}}},
    )
    resolver = SchemaProfileVisibilityResolver(policy_resolution_service=policy_resolution_service)

    with (
        patch("resource_registry.read_auth.visibility.Dataset.objects.filter") as mocked_filter,
        pytest.raises(InvalidVisibilityPolicyError),
    ):
        mocked_filter.return_value.first.return_value = SimpleNamespace(
            id="d-1",
            tenant_id="tenant-a",
        )
        resolver.resolve_policy(tenant_id="tenant-a", dataset_id="d-1")


def test_schema_filter_admin_sees_full_schema() -> None:
    filter_service = FieldFilterService(visibility_resolver=StubVisibilityResolver(_policy()))
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    names = {column["name"] for column in result.schema_json}
    assert names == {"id", "email", "salary", "health_notes"}


def test_schema_filter_restricted_reader_enforces_allowed_columns() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(allowed_columns=("id",))),
    )
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("restricted_reader",)),
        dataset_id="d-1",
    )
    assert [column["name"] for column in result.schema_json] == ["id"]


def test_schema_filter_hidden_masked_deny_beats_allow() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(
                hidden_columns=("email",),
                masked_columns=("salary",),
                allowed_columns=("email", "salary", "id"),
            ),
        ),
    )
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("reader",)),
        dataset_id="d-1",
    )
    names = {column["name"] for column in result.schema_json}
    assert "email" not in names
    salary = next(column for column in result.schema_json if column["name"] == "salary")
    assert salary["type"] == "REDACTED"


def test_schema_filter_masked_scope_returns_redacted_output() -> None:
    filter_service = FieldFilterService(visibility_resolver=StubVisibilityResolver(_policy()))
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("restricted_reader",), scopes=(DATASET_SCHEMA_MASKED_READ,)),
        dataset_id="d-1",
    )
    assert result.schema_json
    assert all(column.get("type") == "REDACTED" for column in result.schema_json)


def test_schema_filter_full_scope_respects_explicit_hidden() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(hidden_columns=("salary",))),
    )
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("restricted_reader",), scopes=(DATASET_SCHEMA_FULL_READ,)),
        dataset_id="d-1",
    )
    assert {column["name"] for column in result.schema_json} == {"id", "email", "health_notes"}


def test_profile_filter_admin_full_and_sensitive_removed() -> None:
    filter_service = FieldFilterService(visibility_resolver=StubVisibilityResolver(_policy()))
    result = filter_service.filter_dataset_profile_summary(
        _profile_summary(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    assert "row_count" in result.metrics_json
    assert "email_null_rate" in result.metrics_json
    assert "preview" not in result.metrics_json
    assert "credentials" not in result.metrics_json


def test_profile_filter_reader_restricted_by_default() -> None:
    filter_service = FieldFilterService(visibility_resolver=StubVisibilityResolver(_policy()))
    result = filter_service.filter_dataset_profile_summary(
        _profile_summary(),
        _context(roles=("reader",)),
        dataset_id="d-1",
    )
    assert result.metrics_json == {}


def test_profile_filter_hidden_masked_allowed_enforced() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(
                hidden_metrics=("salary_percentile_95",),
                masked_metrics=("null_rate",),
                allowed_metrics=("row_count", "null_rate", "salary_percentile_95"),
            ),
        ),
    )
    result = filter_service.filter_dataset_profile_summary(
        _profile_summary(),
        _context(roles=("restricted_reader",)),
        dataset_id="d-1",
    )
    assert "salary_percentile_95" not in result.metrics_json
    assert result.metrics_json["null_rate"] == "REDACTED"
    assert "row_count" in result.metrics_json


def test_profile_filter_masked_scope_redacts_output() -> None:
    filter_service = FieldFilterService(visibility_resolver=StubVisibilityResolver(_policy()))
    result = filter_service.filter_dataset_profile_summary(
        _profile_summary(),
        _context(roles=("restricted_reader",), scopes=(DATASET_PROFILE_MASKED_READ,)),
        dataset_id="d-1",
    )
    assert result.metrics_json["row_count"] == "REDACTED"


def test_profile_filter_full_scope_allows_profile_subject_to_hidden() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(hidden_metrics=("null_rate",))),
    )
    result = filter_service.filter_dataset_profile_summary(
        _profile_summary(),
        _context(roles=("restricted_reader",), scopes=(DATASET_PROFILE_FULL_READ,)),
        dataset_id="d-1",
    )
    assert "row_count" in result.metrics_json
    assert "null_rate" not in result.metrics_json


def test_context_filter_applies_schema_and_profile_visibility_and_does_not_mutate() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(allowed_columns=("id",))),
    )
    original_schema = _schema_summary()
    original_profile = _profile_summary()
    context_summary = DatasetContextSummary(
        dataset=_dataset_details(),
        latest_version=None,
        schema=original_schema,
        profile=original_profile,
        artifact=None,
        policy_provenance=None,
    )

    filtered = filter_service.filter_dataset_context_summary(
        context_summary,
        _context(roles=("restricted_reader",)),
    )

    assert [item["name"] for item in filtered.schema.schema_json] == ["id"]
    assert context_summary.schema.schema_json[1]["name"] == "email"
    assert context_summary.profile.metrics_json["row_count"] == DEFAULT_ROW_COUNT


def test_visibility_filter_audit_events_emitted() -> None:
    publisher = InMemoryEventPublisher()
    audit = AuditService(publisher=publisher)
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(allowed_columns=("id",))),
        audit_service=audit,
    )

    filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("reader",)),
        dataset_id="d-1",
    )
    filter_service.filter_dataset_profile_summary(
        _profile_summary(),
        _context(roles=("reader",)),
        dataset_id="d-1",
    )

    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.SCHEMA_VISIBILITY_FILTER_APPLIED in event_types
    assert EventType.PROFILE_VISIBILITY_FILTER_APPLIED in event_types

    schema_event = next(
        event
        for event in publisher.list_events()
        if event.event_type == EventType.SCHEMA_VISIBILITY_FILTER_APPLIED
    )
    profile_event = next(
        event
        for event in publisher.list_events()
        if event.event_type == EventType.PROFILE_VISIBILITY_FILTER_APPLIED
    )
    assert "hidden_by_classification_count" in schema_event.metadata
    assert "masked_by_classification_count" in schema_event.metadata
    assert "hidden_by_classification_count" in profile_event.metadata
    assert "masked_by_classification_count" in profile_event.metadata


def test_schema_classification_default_masking() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("PII", "PHI")),
        ),
    )
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )

    health = next(item for item in result.schema_json if item["name"] == "health_notes")
    assert health["type"] == "REDACTED"


def test_schema_classification_hidden_by_policy() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(hidden_schema_classifications=("PHI",)),
        ),
    )
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    assert "health_notes" not in {item["name"] for item in result.schema_json}


def test_schema_explicit_hidden_beats_classification_mask() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(
                hidden_columns=("health_notes",),
                masked_schema_classifications=("PHI",),
            ),
        ),
    )
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    assert "health_notes" not in {item["name"] for item in result.schema_json}


def test_schema_explicit_masked_column_masks_public_column() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(masked_columns=("id",))),
    )
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    masked_id = next(item for item in result.schema_json if item["name"] == "id")
    assert masked_id["type"] == "REDACTED"


def test_schema_classification_unknown_treated_as_sensitive() -> None:
    schema = _schema_summary()
    schema.schema_json.append({"name": "mystery", "type": "TEXT", "classification": "unknown_x"})
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("SENSITIVE",)),
        ),
    )
    result = filter_service.filter_dataset_schema_summary(
        schema,
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    mystery = next(item for item in result.schema_json if item["name"] == "mystery")
    assert mystery["type"] == "REDACTED"


def test_profile_classification_default_masking_and_map_support() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_profile_classifications=("PII",)),
        ),
    )
    inline_result = filter_service.filter_dataset_profile_summary(
        _profile_summary(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    mapped_result = filter_service.filter_dataset_profile_summary(
        _profile_summary_with_map(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    assert inline_result.metrics_json["email_null_rate"] == "REDACTED"
    assert mapped_result.metrics_json["email_null_rate"] == "REDACTED"


def test_profile_classification_hidden_beats_masked() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(
                hidden_profile_classifications=("PII",),
                masked_profile_classifications=("PII",),
            ),
        ),
    )
    result = filter_service.filter_dataset_profile_summary(
        _profile_summary(),
        _context(roles=("admin",)),
        dataset_id="d-1",
    )
    assert "email_null_rate" not in result.metrics_json


def test_full_read_scope_bypasses_classification_mask_not_hidden() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("PHI",)),
        ),
    )
    result = filter_service.filter_dataset_schema_summary(
        _schema_summary(),
        _context(roles=("restricted_reader",), scopes=(DATASET_SCHEMA_FULL_READ,)),
        dataset_id="d-1",
    )
    health = next(item for item in result.schema_json if item["name"] == "health_notes")
    assert health["type"] == "TEXT"
