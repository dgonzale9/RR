from __future__ import annotations

from types import SimpleNamespace

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_publisher import InMemoryEventPublisher
from resource_registry.events.event_types import EventType
from resource_registry.read.dtos import (
    DatasetContextSummary,
    DatasetDetails,
    DatasetProfileSummary,
    DatasetSchemaSummary,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.field_filtering import FieldFilterService
from resource_registry.read_auth.visibility import (
    ResolvedVisibilityPolicy,
    SchemaProfileVisibilityPolicy,
)


class StubVisibilityResolver:
    def __init__(
        self,
        policy: SchemaProfileVisibilityPolicy,
        source: str = "tenant_default",
    ) -> None:
        self._resolved = ResolvedVisibilityPolicy(policy=policy, source=source, revision=2)

    def resolve_policy(self, *, tenant_id: str, dataset_id: str | None) -> ResolvedVisibilityPolicy:
        _ = tenant_id, dataset_id
        return self._resolved


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


def _schema() -> DatasetSchemaSummary:
    return DatasetSchemaSummary(
        id="s1",
        dataset_version_id="v1",
        tenant_id="tenant-a",
        schema_hash="h",
        column_count=3,
        schema_json=[
            {"name": "id", "type": "BIGINT", "classification": "PUBLIC"},
            {"name": "email", "type": "VARCHAR", "classification": "PII"},
            {"name": "mystery", "type": "VARCHAR", "classification": "unknown_x"},
        ],
        created_at="2026-06-24T00:00:00+00:00",
    )


def _profile() -> DatasetProfileSummary:
    return DatasetProfileSummary(
        id="p1",
        dataset_version_id="v1",
        tenant_id="tenant-a",
        profile_type="BASIC",
        profile_hash="h",
        metrics_json={
            "row_count": 10,
            "email_null_rate": {"value": 0.01, "classification": "PII"},
            "preview": [{"id": 1}],
        },
        computed_at="2026-06-24T00:00:00+00:00",
    )


def _dataset_details() -> DatasetDetails:
    return DatasetDetails(
        id="d1",
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


def test_schema_hidden_by_column_name_explanation() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(hidden_columns=("email",))),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
    )

    decisions = {d.field_name: d for d in result.explanation.decisions}
    assert decisions["email"].action == "hidden"
    assert decisions["email"].reason == "hidden_by_column_name"


def test_schema_masked_by_classification_explanation() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("PII",)),
        ),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
    )
    email = next(d for d in result.explanation.decisions if d.field_name == "email")
    assert email.action == "masked"
    assert email.reason == "masked_by_classification"


def test_unknown_classification_reason_treated_sensitive() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("SENSITIVE",)),
        ),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
    )
    mystery = next(d for d in result.explanation.decisions if d.field_name == "mystery")
    assert mystery.reason == "unknown_classification_treated_sensitive"


def test_profile_hidden_by_classification_explanation() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(hidden_profile_classifications=("PII",)),
        ),
    )
    result = filter_service.filter_profile_with_explanation(
        _profile(),
        _context(roles=("admin",)),
        dataset_id="d1",
    )
    metric = next(d for d in result.explanation.decisions if d.field_name == "email_null_rate")
    assert metric.action == "hidden"
    assert metric.reason in {"hidden_by_classification", "unknown_classification_treated_sensitive"}


def test_allowed_columns_restriction_explanation() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(allowed_columns=("id",))),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("reader",)),
        dataset_id="d1",
    )
    hidden = [
        d for d in result.explanation.decisions
        if d.reason == "restricted_by_allowed_columns"
    ]
    assert hidden


def test_full_read_scope_explanation_reason() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("PII",)),
        ),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("restricted_reader",), scopes=("DATASET_SCHEMA_FULL_READ",)),
        dataset_id="d1",
    )
    visible = next(d for d in result.explanation.decisions if d.field_name == "email")
    assert visible.action == "visible"
    assert visible.reason == "allowed_by_scope"


def test_explainability_does_not_change_output_and_does_not_mutate() -> None:
    schema = _schema()
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("PII",)),
        ),
    )

    plain = filter_service.filter_dataset_schema_summary(
        schema,
        _context(roles=("admin",)),
        dataset_id="d1",
    )
    explained = filter_service.filter_schema_with_explanation(
        schema,
        _context(roles=("admin",)),
        dataset_id="d1",
    )

    assert plain.schema_json == explained.filtered.schema_json
    assert schema.schema_json[1]["type"] == "VARCHAR"


def test_context_with_explanation_and_audit_event_emitted() -> None:
    publisher = InMemoryEventPublisher()
    audit = AuditService(publisher=publisher)
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("PII",)),
        ),
        audit_service=audit,
    )
    context_summary = DatasetContextSummary(
        dataset=_dataset_details(),
        latest_version=None,
        schema=_schema(),
        profile=_profile(),
        artifact=None,
        policy_provenance=None,
    )

    result = filter_service.filter_context_with_explanation(
        context_summary,
        _context(roles=("admin",)),
    )

    assert result.explanation.summary["masked_count"] >= 1
    assert EventType.VISIBILITY_EXPLANATION_GENERATED in [
        event.event_type for event in publisher.list_events()
    ]


def test_diagnostics_hidden_beats_allow_and_conflict_detected() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(hidden_columns=("email",), allowed_columns=("email", "id")),
        ),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
        include_diagnostics=True,
    )
    assert result.explanation.diagnostics is not None
    trace = next(t for t in result.explanation.diagnostics.traces if t.field_name == "email")
    assert trace.final_action == "hidden"
    assert trace.winning_rule.rule_name == "explicit_hidden"
    assert trace.conflict_detected is True


def test_diagnostics_hidden_classification_beats_masked_classification() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(
                hidden_schema_classifications=("PII",),
                masked_schema_classifications=("PII",),
            ),
        ),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
        include_diagnostics=True,
    )
    trace = next(t for t in result.explanation.diagnostics.traces if t.field_name == "email")
    assert trace.final_action == "hidden"
    assert trace.winning_rule.rule_name == "hidden_classification"


def test_diagnostics_explicit_masked_public_field_wins() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(masked_columns=("id",))),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
        include_diagnostics=True,
    )
    trace = next(t for t in result.explanation.diagnostics.traces if t.field_name == "id")
    assert trace.final_action == "masked"
    assert trace.winning_rule.rule_name == "explicit_masked"


def test_diagnostics_full_read_scope_suppresses_masked_classification() -> None:
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(
            _policy(masked_schema_classifications=("PII",)),
        ),
    )
    result = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("restricted_reader",), scopes=("DATASET_SCHEMA_FULL_READ",)),
        dataset_id="d1",
        include_diagnostics=True,
    )
    trace = next(t for t in result.explanation.diagnostics.traces if t.field_name == "email")
    assert trace.final_action == "visible"
    assert trace.winning_rule.rule_name == "full_read_scope"


def test_diagnostics_security_removal_beats_everything() -> None:
    filter_service = FieldFilterService(visibility_resolver=StubVisibilityResolver(_policy()))
    result = filter_service.filter_profile_with_explanation(
        _profile(),
        _context(roles=("admin",), scopes=("DATASET_PROFILE_FULL_READ",)),
        dataset_id="d1",
        include_diagnostics=True,
    )
    trace = next(t for t in result.explanation.diagnostics.traces if t.field_name == "preview")
    assert trace.final_action == "hidden"
    assert trace.winning_rule.rule_name == "security_removal"


def test_diagnostics_ordering_deterministic_and_disabled_by_default() -> None:
    filter_service = FieldFilterService(visibility_resolver=StubVisibilityResolver(_policy()))
    enabled = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
        include_diagnostics=True,
    )
    disabled = filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
    )
    assert enabled.explanation.diagnostics is not None
    first_trace = enabled.explanation.diagnostics.traces[0]
    precedences = [rule.precedence for rule in first_trace.matched_rules]
    assert precedences == sorted(precedences)
    assert disabled.explanation.diagnostics is None


def test_diagnostics_audit_event_emitted() -> None:
    publisher = InMemoryEventPublisher()
    audit = AuditService(publisher=publisher)
    filter_service = FieldFilterService(
        visibility_resolver=StubVisibilityResolver(_policy(masked_schema_classifications=("PII",))),
        audit_service=audit,
    )
    filter_service.filter_schema_with_explanation(
        _schema(),
        _context(roles=("admin",)),
        dataset_id="d1",
        include_diagnostics=True,
    )
    event_types = [event.event_type for event in publisher.list_events()]
    assert EventType.VISIBILITY_DIAGNOSTICS_GENERATED in event_types
