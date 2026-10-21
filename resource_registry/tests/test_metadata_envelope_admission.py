"""Test ERR-1.5-S5 metadata-envelope admission contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from resource_registry.metadata_schema import (
    MetadataClassificationProfile,
    MetadataEnvelopeAdmission,
    MetadataEnvelopeAdmissionResult,
    MetadataEnvelopeAdmissionStatus,
    MetadataFieldClassification,
    MetadataFieldDefinition,
    MetadataPayloadBounds,
    MetadataPayloadMeasurementError,
    MetadataSchemaActiveSelection,
    MetadataSchemaDefinition,
    MetadataSchemaPublication,
    MetadataSchemaPublicationStatus,
    MetadataSchemaVersion,
    measure_metadata_payload,
)
from resource_registry.registry_core import (
    MetadataClassification,
    MetadataEnvelope,
    MetadataNamespaceKey,
    MetadataProvenance,
    MetadataSchemaKey,
    MetadataScope,
    NamespaceKey,
    ResourceKey,
    ResourceTypeKey,
)

_SELECTION_REVISION = 3


@dataclass(frozen=True, slots=True)
class _Reader:
    """Provide a deterministic S4-shaped reader for admission tests."""

    selection: MetadataSchemaActiveSelection | Exception
    publication: MetadataSchemaPublication | Exception

    def read_active_selection(self, schema: MetadataSchemaKey) -> MetadataSchemaActiveSelection:
        """Return or raise the configured active-selection outcome.

        Args:
            schema: Exact requested schema.

        Returns:
            Configured active selection.

        Raises:
            Exception: Configured safe S4 read failure.
        """
        if isinstance(self.selection, Exception):
            raise self.selection
        return self.selection

    def read_publication(
        self, schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaPublication:
        """Return or raise the configured publication outcome.

        Args:
            schema: Exact requested schema.
            schema_version: Exact requested opaque version.

        Returns:
            Configured immutable publication.

        Raises:
            Exception: Configured safe S4 read failure.
        """
        if isinstance(self.publication, Exception):
            raise self.publication
        return self.publication


def _build_definition(
    *,
    allowed_scopes: tuple[MetadataScope, ...],
    field_name: str = "title",
    scalar_type: str = "string",
) -> MetadataSchemaDefinition:
    """Build one complete S1/S2 definition for admission tests.

    Args:
        allowed_scopes: Exact declaration scopes for the test definition.
        field_name: Exact field identity for the test definition.
        scalar_type: Exact S2 scalar type for the test field.

    Returns:
        Complete immutable schema definition.
    """
    namespace = MetadataNamespaceKey(value="atlas.metadata.example")
    schema = MetadataSchemaKey(namespace=namespace, name="summary")
    declaration = MetadataSchemaVersion(
        namespace=namespace,
        schema=schema,
        schema_version="opaque/v1",
        semantic_owner="metadata-team",
        persistence_owner="registry-store",
        allowed_scopes=allowed_scopes,
        classification=MetadataClassification.INTERNAL,
        bounds=MetadataPayloadBounds(
            max_payload_bytes=100,
            max_nesting_depth=4,
            max_object_key_count=10,
            max_collection_item_count=20,
        ),
    )
    return MetadataSchemaDefinition(
        declaration=declaration,
        fields=(
            MetadataFieldDefinition(
                name=field_name,
                scalar_type=scalar_type,
                required=False,
                allows_null=True,
            ),
        ),
        classification_profile=MetadataClassificationProfile(
            schema_classification=MetadataClassification.INTERNAL,
            field_classifications=(
                MetadataFieldClassification(
                    field_name=field_name, classification=MetadataClassification.INTERNAL
                ),
            ),
        ),
    )


def _build_candidate(definition: MetadataSchemaDefinition) -> MetadataEnvelope:
    """Build one valid immutable envelope for the supplied definition.

    Args:
        definition: Definition that supplies exact schema identity.

    Returns:
        A Core-valid metadata envelope.
    """
    return MetadataEnvelope(
        scope=MetadataScope.RESOURCE,
        subject=ResourceKey(
            NamespaceKey("8dbf68a1-9567-4d74-931f-c36c4247f80b"),
            ResourceTypeKey("atlas.data.dataset"),
            "dataset-1",
        ),
        schema=definition.schema,
        schema_version=definition.schema_version,
        revision=1,
        classification=MetadataClassification.INTERNAL,
        provenance=MetadataProvenance(source_kind="catalog", producer="test"),
        observed_at=datetime(2026, 7, 23, tzinfo=UTC),
        payload={"title": "example"},
    )


def _build_reader(definition: MetadataSchemaDefinition) -> _Reader:
    """Build a reader with one matching selected publication.

    Args:
        definition: Definition to expose through the S4-shaped reader.

    Returns:
        A matching deterministic reader.
    """
    return _Reader(
        selection=MetadataSchemaActiveSelection(
            schema=definition.schema,
            schema_version=definition.schema_version,
            revision=_SELECTION_REVISION,
        ),
        publication=MetadataSchemaPublication(
            definition=definition, status=MetadataSchemaPublicationStatus.PUBLISHED
        ),
    )


def test_admission_returns_an_exact_binding_after_rfc8785_bound_measurement() -> None:
    """Admit a payload that satisfies selected RFC 8785 structural bounds."""
    definition = _build_definition(allowed_scopes=(MetadataScope.RESOURCE,))

    result = MetadataEnvelopeAdmission(reader=_build_reader(definition)).admit(
        _build_candidate(definition)
    )

    assert result.status is MetadataEnvelopeAdmissionStatus.ADMITTED
    assert result.code == "metadata_envelope_admitted"
    assert result.fresh_read_required is False
    assert result.candidate == _build_candidate(definition)
    assert result.schema_binding is not None
    assert result.schema_binding.schema_version == "opaque/v1"
    assert result.schema_binding.selection_revision == _SELECTION_REVISION


def test_admission_translates_missing_active_selection_without_django_dependency() -> None:
    """Translate the stable S4 missing-selection error through the structural read boundary."""
    definition = _build_definition(allowed_scopes=(MetadataScope.RESOURCE,))
    reader = _Reader(
        selection=ValueError("unknown metadata schema active selection"),
        publication=MetadataSchemaPublication(
            definition=definition, status=MetadataSchemaPublicationStatus.PUBLISHED
        ),
    )

    result = MetadataEnvelopeAdmission(reader=reader).admit(_build_candidate(definition))

    assert result.status is MetadataEnvelopeAdmissionStatus.UNAVAILABLE
    assert result.code == "metadata_envelope_admission_no_active_selection"
    assert result.fresh_read_required is True


def test_admission_reads_selected_publication_before_version_mismatch() -> None:
    """Reject candidate opaque-version mismatch only after the exact selected publication read."""
    definition = _build_definition(allowed_scopes=(MetadataScope.RESOURCE,))
    candidate = _build_candidate(definition)
    mismatched = MetadataEnvelope(
        scope=candidate.scope,
        subject=candidate.subject,
        schema=candidate.schema,
        schema_version="opaque/v2",
        revision=candidate.revision,
        classification=candidate.classification,
        provenance=candidate.provenance,
        observed_at=candidate.observed_at,
        payload=candidate.payload,
    )

    result = MetadataEnvelopeAdmission(reader=_build_reader(definition)).admit(mismatched)

    assert result.status is MetadataEnvelopeAdmissionStatus.REJECTED
    assert result.code == "metadata_envelope_admission_schema_version_mismatch"
    assert result.fresh_read_required is True


def test_admission_rejects_scope_before_undefined_bounds() -> None:
    """Reject a determinable scope mismatch before payload bound measurement."""
    definition = _build_definition(allowed_scopes=(MetadataScope.VERSION,))

    result = MetadataEnvelopeAdmission(reader=_build_reader(definition)).admit(
        _build_candidate(definition)
    )

    assert result.status is MetadataEnvelopeAdmissionStatus.REJECTED
    assert result.code == "metadata_envelope_admission_scope_mismatch"
    assert result.fresh_read_required is False


def test_admission_rejects_unknown_fields_and_exceeded_rfc8785_bounds() -> None:
    """Return redacted field evidence and exact structural-bound failures."""
    definition = _build_definition(allowed_scopes=(MetadataScope.RESOURCE,))
    candidate = _build_candidate(definition)
    unknown_field_candidate = MetadataEnvelope(
        scope=candidate.scope,
        subject=candidate.subject,
        schema=candidate.schema,
        schema_version=candidate.schema_version,
        revision=candidate.revision,
        classification=candidate.classification,
        provenance=candidate.provenance,
        observed_at=candidate.observed_at,
        payload={"extra": "value"},
    )

    validation_result = MetadataEnvelopeAdmission(reader=_build_reader(definition)).admit(
        unknown_field_candidate
    )

    assert validation_result.code == "metadata_envelope_admission_validation_failed"
    assert validation_result.field_evidence[0].code == "unknown_field"
    assert validation_result.field_evidence[0].path == "extra"

    constrained_definition = MetadataSchemaDefinition(
        declaration=definition.declaration,
        fields=definition.fields,
        bounds=MetadataPayloadBounds(
            max_payload_bytes=1,
            max_nesting_depth=4,
            max_object_key_count=10,
            max_collection_item_count=20,
        ),
        classification_profile=definition.classification_profile,
    )
    bounds_result = MetadataEnvelopeAdmission(reader=_build_reader(constrained_definition)).admit(
        _build_candidate(constrained_definition)
    )

    assert bounds_result.code == "metadata_envelope_admission_bounds_exceeded"


def test_admission_measures_before_s4_reads_and_rejects_reserved_payload_fields() -> None:
    """Reject non-JCS and reserved inputs without allowing S4 reads to reinterpret them."""
    integer_definition = _build_definition(
        allowed_scopes=(MetadataScope.RESOURCE,), scalar_type="integer"
    )
    unsafe_integer_candidate = MetadataEnvelope(
        scope=MetadataScope.RESOURCE,
        subject=_build_candidate(integer_definition).subject,
        schema=integer_definition.schema,
        schema_version=integer_definition.schema_version,
        revision=1,
        classification=MetadataClassification.INTERNAL,
        provenance=MetadataProvenance(source_kind="catalog", producer="test"),
        observed_at=datetime(2026, 7, 23, tzinfo=UTC),
        payload={"title": 2**80},
    )
    unavailable_reader = _Reader(
        selection=AssertionError("S4 reads must not follow an invalid payload measurement"),
        publication=AssertionError("S4 reads must not follow an invalid payload measurement"),
    )

    unsafe_result = MetadataEnvelopeAdmission(reader=unavailable_reader).admit(
        unsafe_integer_candidate
    )

    assert unsafe_result.code == "metadata_envelope_admission_validation_failed"

    owner_definition = _build_definition(
        allowed_scopes=(MetadataScope.RESOURCE,), field_name="owner"
    )
    owner_candidate = MetadataEnvelope(
        scope=MetadataScope.RESOURCE,
        subject=_build_candidate(owner_definition).subject,
        schema=owner_definition.schema,
        schema_version=owner_definition.schema_version,
        revision=1,
        classification=MetadataClassification.INTERNAL,
        provenance=MetadataProvenance(source_kind="catalog", producer="test"),
        observed_at=datetime(2026, 7, 23, tzinfo=UTC),
        payload={"owner": "payload-owner"},
    )

    reserved_result = MetadataEnvelopeAdmission(reader=_build_reader(owner_definition)).admit(
        owner_candidate
    )

    assert reserved_result.code == "metadata_envelope_admission_validation_failed"
    assert reserved_result.field_evidence[0].code == "reserved_field"
    assert reserved_result.field_evidence[0].path == "owner"


def test_rfc8785_measurement_handles_frozen_core_payloads_and_rejects_unsafe_integers() -> None:
    """Measure frozen mappings with JCS and reject values outside its numeric domain."""
    definition = _build_definition(allowed_scopes=(MetadataScope.RESOURCE,))
    measurement = measure_metadata_payload(_build_candidate(definition).payload)

    assert measurement.payload_bytes == len(b'{"title":"example"}')
    assert measurement.nesting_depth == 1
    assert measurement.object_key_count == 1
    assert measurement.collection_item_count == 0

    unsafe_integer_candidate = MetadataEnvelope(
        scope=MetadataScope.RESOURCE,
        subject=_build_candidate(definition).subject,
        schema=definition.schema,
        schema_version=definition.schema_version,
        revision=1,
        classification=MetadataClassification.INTERNAL,
        provenance=MetadataProvenance(source_kind="catalog", producer="test"),
        observed_at=datetime(2026, 7, 23, tzinfo=UTC),
        payload={"title": 2**80},
    )
    try:
        measure_metadata_payload(unsafe_integer_candidate.payload)
    except MetadataPayloadMeasurementError as error:
        assert str(error) == "metadata payload is not representable by RFC 8785"
    else:
        raise AssertionError("RFC 8785 accepted an unsafe integer")


def test_admission_results_require_complete_admitted_evidence() -> None:
    """Reject malformed admitted result construction without exposing mutable state."""
    try:
        MetadataEnvelopeAdmissionResult(
            status=MetadataEnvelopeAdmissionStatus.ADMITTED,
            code="admitted",
            message="Admitted.",
            retry_classification="none",
            fresh_read_required=False,
        )
    except ValueError as error:
        assert str(error) == "invalid metadata envelope admission result"
    else:
        raise AssertionError("admitted result without immutable evidence was accepted")


def test_admission_module_does_not_import_django() -> None:
    """Keep the S5 admission contract independent of Django and S4 implementation modules."""
    from resource_registry.metadata_schema import admission

    source = admission.__file__
    assert source is not None
    assert "django" not in open(source, encoding="utf-8").read()
