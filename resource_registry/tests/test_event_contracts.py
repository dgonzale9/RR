"""Verify ERR-6.1 pure Event schema and source registry contracts."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from resource_registry.event_contracts import (
    EventCategory,
    EventClassification,
    EventCompatibilityDeclaration,
    EventCompatibilityOutcome,
    EventContractValidationError,
    EventEnvelope,
    EventPayloadField,
    EventSchemaDeclaration,
    EventSource,
    EventSourceRegistry,
    EventSubject,
    EventSubjectProfile,
    EventType,
    EventVersion,
    assess_event_compatibility,
)
from resource_registry.registry_core import (
    ArtifactKey,
    MetadataClassification,
    NamespaceKey,
    ResourceKey,
    ResourceTypeKey,
    ResourceVersionKey,
)

_NAMESPACE = NamespaceKey(UUID("00000000-0000-4000-8000-000000000001"))
_OTHER_NAMESPACE = NamespaceKey(UUID("00000000-0000-4000-8000-000000000002"))
_TIMESTAMP = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "event_contracts"


def _subject(
    *, namespace: NamespaceKey = _NAMESPACE, include_artifact: bool = False
) -> EventSubject:
    """Build one exact event subject for a test fixture.

    Args:
        namespace: Namespace identity for the resource subject.
        include_artifact: Whether to add an exact artifact identity.

    Returns:
        Exact immutable Registry event subject.
    """
    resource = ResourceKey(namespace, ResourceTypeKey("atlas.dataset.table"), "dataset-1")
    version = ResourceVersionKey(resource, "version-1")
    artifact = ArtifactKey(resource=resource, version=version, artifact_id="artifact-1")
    return EventSubject(resource, version, artifact if include_artifact else None)


def _schema(
    *,
    version: int = 1,
    fields: tuple[EventPayloadField, ...] | None = None,
    source: EventSource | None = None,
    ordering_guarantee: str = "per_subject",
) -> EventSchemaDeclaration:
    """Build one exact pure event schema declaration.

    Args:
        version: Exact positive schema version.
        fields: Optional declarative payload fields.
        source: Optional owner-controlled source declaration.
        ordering_guarantee: Exact bounded ordering declaration.

    Returns:
        Immutable event schema declaration.
    """
    return EventSchemaDeclaration(
        EventType("atlas.dataset.version_published"),
        EventVersion(version),
        source or EventSource("dataset", "registry", "test"),
        EventClassification(EventCategory.INTEGRATION, MetadataClassification.INTERNAL),
        (EventSubjectProfile.RESOURCE_VERSION, EventSubjectProfile.ARTIFACT),
        fields or (EventPayloadField("row_count", "integer", True),),
        ordering_guarantee,
    )


def _envelope(schema: EventSchemaDeclaration | None = None) -> EventEnvelope:
    """Build one exact immutable event envelope.

    Args:
        schema: Optional exact schema bound to the event.

    Returns:
        Valid immutable in-memory event fact.
    """
    event_schema = schema or _schema()
    subject = _subject()
    return EventEnvelope(
        "event-1",
        event_schema,
        event_schema.source,
        subject,
        subject.namespace,
        event_schema.classification,
        _TIMESTAMP,
        _TIMESTAMP,
        "correlation-1",
        None,
        {"row_count": 5},
    )


def test_event_registry_resolves_exact_declarations_and_rejects_conflicting_sources() -> None:
    """Resolve only exact declarations and reject shared event-type ownership."""
    first = _schema(version=1)
    second = _schema(version=2)
    registry = EventSourceRegistry((first, second))

    assert registry.resolve(EventType("atlas.dataset.version_published"), EventVersion(2)) == second

    conflicting_source = EventSchemaDeclaration(
        first.event_type,
        EventVersion(3),
        EventSource("other", "registry", "test"),
        first.classification,
        first.subject_profiles,
        first.payload_fields,
        first.ordering_guarantee,
    )
    with pytest.raises(EventContractValidationError, match="conflicting event schema source"):
        EventSourceRegistry((first, conflicting_source))

    with pytest.raises(EventContractValidationError, match="duplicate event schema declaration"):
        EventSourceRegistry((first, first))

    with pytest.raises(EventContractValidationError, match="unknown event schema declaration"):
        registry.resolve(first.event_type, EventVersion(99))


def test_event_envelope_is_immutable_and_checksum_is_deterministic() -> None:
    """Freeze a declared event fact and calculate stable semantic evidence."""
    first = _envelope()
    second = _envelope()

    assert first.checksum == second.checksum
    with pytest.raises(TypeError):
        first.payload["row_count"] = 6  # type: ignore[index]
    with pytest.raises(AttributeError):
        first.event_id = "event-2"  # type: ignore[misc]


def test_event_envelope_rejects_namespace_and_subject_profile_mismatches() -> None:
    """Fail closed on a cross-Namespace subject or undeclared profile."""
    schema = _schema()
    subject = _subject()
    with pytest.raises(EventContractValidationError, match="invalid event envelope"):
        EventEnvelope(
            "event-1",
            schema,
            schema.source,
            subject,
            _OTHER_NAMESPACE,
            schema.classification,
            _TIMESTAMP,
            _TIMESTAMP,
            None,
            None,
            {"row_count": 5},
        )

    resource_only_schema = EventSchemaDeclaration(
        schema.event_type,
        schema.version,
        schema.source,
        schema.classification,
        (EventSubjectProfile.RESOURCE,),
        schema.payload_fields,
        schema.ordering_guarantee,
    )
    with pytest.raises(EventContractValidationError, match="invalid event envelope"):
        _envelope(resource_only_schema)

    artifact_subject = _subject(include_artifact=True)
    artifact_envelope = EventEnvelope(
        "event-artifact-1",
        schema,
        schema.source,
        artifact_subject,
        artifact_subject.namespace,
        schema.classification,
        _TIMESTAMP,
        _TIMESTAMP,
        None,
        None,
        {"row_count": 5},
    )
    assert artifact_envelope.subject.profile is EventSubjectProfile.ARTIFACT

    selector_resource = ResourceKey(
        _NAMESPACE,
        ResourceTypeKey("atlas.dataset.table"),
        "latest",
    )
    with pytest.raises(EventContractValidationError, match="non-exact event subject identity"):
        EventSubject(selector_resource)

    with pytest.raises(EventContractValidationError, match="invalid event subject resource"):
        EventSubject(None)  # type: ignore[arg-type]


def test_event_contracts_reuse_only_internal_core_classification() -> None:
    """Reject classification values outside the approved Core vocabulary."""
    with pytest.raises(EventContractValidationError, match="invalid event classification"):
        EventClassification(EventCategory.INTEGRATION, "public")  # type: ignore[arg-type]

    with pytest.raises(EventContractValidationError, match="invalid event type"):
        EventType("atlas.secret.rotation")


@pytest.mark.parametrize("field_name", ("secret", "credential_ref", "policy_result", "url"))
def test_event_payload_fields_reject_protected_and_runtime_shaped_names(field_name: str) -> None:
    """Reject protected, authorization, and runtime-shaped payload declarations."""
    with pytest.raises(EventContractValidationError, match="invalid event payload field"):
        EventPayloadField(field_name, "string", False, "redacted")


def test_compatibility_allows_only_additive_optional_fields_with_defaults() -> None:
    """Accept explicit additive optional compatibility and reject breaking additions."""
    predecessor = _schema()
    successor = _schema(
        version=2,
        fields=(
            EventPayloadField("row_count", "integer", True),
            EventPayloadField("source_name", "string", False, "unknown"),
        ),
    )
    assessment = assess_event_compatibility(
        EventCompatibilityDeclaration(
            predecessor,
            successor,
            EventCompatibilityOutcome.BACKWARD_COMPATIBLE,
        )
    )
    assert assessment.outcome is EventCompatibilityOutcome.BACKWARD_COMPATIBLE

    breaking = _schema(
        version=2,
        fields=(
            EventPayloadField("row_count", "integer", True),
            EventPayloadField("source_name", "string", True),
        ),
    )
    with pytest.raises(EventContractValidationError, match="does not match"):
        assess_event_compatibility(
            EventCompatibilityDeclaration(
                predecessor,
                breaking,
                EventCompatibilityOutcome.BACKWARD_COMPATIBLE,
            )
        )


def test_compatibility_requires_an_explicit_replacement_for_changed_event_type() -> None:
    """Classify a changed semantic event type as an exact replacement only."""
    predecessor = _schema()
    replacement = EventSchemaDeclaration(
        EventType("atlas.dataset.version_republished"),
        EventVersion(1),
        predecessor.source,
        predecessor.classification,
        predecessor.subject_profiles,
        predecessor.payload_fields,
        predecessor.ordering_guarantee,
    )

    assessment = assess_event_compatibility(
        EventCompatibilityDeclaration(
            predecessor,
            replacement,
            EventCompatibilityOutcome.REPLACEMENT,
        )
    )

    assert assessment.outcome is EventCompatibilityOutcome.REPLACEMENT


def test_compatibility_marks_expired_predecessor_windows_as_unsupported() -> None:
    """Fail closed when a predecessor is outside its explicit support window."""
    assessment = assess_event_compatibility(
        EventCompatibilityDeclaration(
            _schema(),
            _schema(version=2),
            EventCompatibilityOutcome.UNSUPPORTED,
            predecessor_supported=False,
        )
    )

    assert assessment.outcome is EventCompatibilityOutcome.UNSUPPORTED


def test_event_contracts_keep_the_pure_dependency_boundary() -> None:
    """Import only Registry Core and standard-library modules from event contracts."""
    prohibited_prefixes = (
        "django",
        "BotPlatform",
        "BotServer",
        "resource_registry.api",
        "resource_registry.application",
        "resource_registry.events",
        "resource_registry.providers",
        "resource_registry.services",
        "resource_registry.transformations",
    )
    for source_path in _PACKAGE_ROOT.glob("*.py"):
        source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(source_tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None
        }
        assert not any(
            imported_module.startswith(prohibited_prefixes) for imported_module in imported_modules
        ), source_path
