"""Declare Dataset Registry integration-event contracts used by owner-local persistence."""

from resource_registry.event_contracts import (
    EventCategory,
    EventClassification,
    EventPayloadField,
    EventSchemaDeclaration,
    EventSource,
    EventSourceRegistry,
    EventSubjectProfile,
    EventType,
    EventVersion,
)
from resource_registry.registry_core import MetadataClassification

DATASET_VERSION_PUBLISHED_V1 = EventSchemaDeclaration(
    event_type=EventType("atlas.dataset.version_published"),
    version=EventVersion(1),
    source=EventSource("dataset", "registry", "application"),
    classification=EventClassification(EventCategory.INTEGRATION, MetadataClassification.INTERNAL),
    subject_profiles=(EventSubjectProfile.RESOURCE_VERSION,),
    payload_fields=(
        EventPayloadField("dataset_version_id", "string", True),
        EventPayloadField("version_number", "integer", True),
        EventPayloadField("schema_hash", "string", True),
        EventPayloadField("profile_hash", "string", True),
    ),
    ordering_guarantee="dataset_stream",
)

DATASET_EVENT_SOURCE_REGISTRY = EventSourceRegistry((DATASET_VERSION_PUBLISHED_V1,))
