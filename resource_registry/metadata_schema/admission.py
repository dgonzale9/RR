"""Admit immutable metadata envelopes against exact durable schema selections."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from resource_registry.registry_core import (
    MetadataEnvelope,
    MetadataSchemaKey,
)

from .definitions import MetadataSchemaDefinition
from .payload_measurement import (
    MetadataPayloadMeasurement,
    MetadataPayloadMeasurementError,
    measure_metadata_payload,
)
from .publications import (
    MetadataSchemaActiveSelection,
    MetadataSchemaPublication,
    MetadataSchemaPublicationStatus,
)

__all__ = (  # noqa: RUF022
    "MetadataEnvelopeAdmissionStatus",
    "MetadataEnvelopeAdmissionFieldEvidence",
    "MetadataEnvelopeSchemaBinding",
    "MetadataEnvelopeAdmissionResult",
    "MetadataEnvelopeAdmissionReader",
    "MetadataEnvelopeAdmission",
)

_NO_ACTIVE_SELECTION_ERROR = "unknown metadata schema active selection"
_MISSING_PUBLICATION_ERROR = "unknown metadata schema publication"
_INVALID_DURABLE_STATE_ERRORS = frozenset(
    {
        "invalid metadata schema persistence identity",
        "metadata schema persistence inconsistency",
        "unknown metadata schema definition",
    }
)
_UNAVAILABLE_DEPENDENCY_ERRORS = frozenset(
    {
        "metadata schema transaction failure",
        "metadata schema repository failure",
        "metadata schema commit outcome unknown",
    }
)
_MAXIMUM_FIELD_ERRORS = 32
_RESERVED_PAYLOAD_FIELD_NAMES = frozenset(
    {
        "authorization",
        "classification",
        "lifecycle",
        "namespace",
        "observed_at",
        "owner",
        "principal",
        "provenance",
        "registry_namespace",
        "revision",
        "schema",
        "schema_eligibility",
        "schema_version",
        "scope",
        "subject",
        "subject_identity",
        "tenant",
        "workspace",
    }
)


class MetadataEnvelopeAdmissionStatus(str, Enum):
    """Represent the closed set of metadata-envelope admission outcomes."""

    ADMITTED = "admitted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataEnvelopeAdmissionFieldEvidence:
    """Describe one redacted field-validation failure.

    Args:
        code: Stable failure code without a payload value.
        path: Exact top-level schema field path.

    Raises:
        ValueError: If the evidence is malformed.
    """

    code: str
    path: str

    def __post_init__(self) -> None:
        """Validate redacted field evidence.

        Raises:
            ValueError: If the evidence is malformed.
        """
        if not _is_non_blank_plain_string(self.code) or not _is_non_blank_plain_string(self.path):
            raise ValueError("invalid metadata envelope admission field evidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataEnvelopeSchemaBinding:
    """Bind an admitted envelope to one observed immutable schema publication.

    Args:
        schema: Exact globally unique schema identity.
        schema_version: Selected opaque schema version.
        selection_revision: Observed S4 active-selection revision.

    Raises:
        ValueError: If the binding is malformed.
    """

    schema: MetadataSchemaKey
    schema_version: str
    selection_revision: int

    def __post_init__(self) -> None:
        """Validate immutable binding evidence.

        Raises:
            ValueError: If the binding is malformed.
        """
        if not isinstance(self.schema, MetadataSchemaKey):
            raise ValueError("invalid metadata envelope schema binding")
        if not _is_non_blank_plain_string(self.schema_version):
            raise ValueError("invalid metadata envelope schema binding")
        if type(self.selection_revision) is not int or self.selection_revision < 0:
            raise ValueError("invalid metadata envelope schema binding")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataEnvelopeAdmissionResult:
    """Return one immutable safe metadata-envelope admission outcome.

    Args:
        status: Closed admission outcome classification.
        code: Stable safe outcome code.
        message: Safe outcome message without payload values or durable snapshots.
        retry_classification: Safe caller retry guidance.
        fresh_read_required: Whether a new S5 admission attempt requires fresh S4 reads.
        field_evidence: Ordered redacted field-validation evidence.
        candidate: Original immutable envelope for an admitted result only.
        schema_binding: Exact observed publication binding for an admitted result only.

    Raises:
        ValueError: If result fields violate the public admission contract.
    """

    status: MetadataEnvelopeAdmissionStatus
    code: str
    message: str
    retry_classification: str
    fresh_read_required: bool
    field_evidence: tuple[MetadataEnvelopeAdmissionFieldEvidence, ...] = ()
    candidate: MetadataEnvelope | None = None
    schema_binding: MetadataEnvelopeSchemaBinding | None = None

    def __post_init__(self) -> None:
        """Validate result shape and admitted-result evidence.

        Raises:
            ValueError: If result fields violate the public admission contract.
        """
        if not isinstance(self.status, MetadataEnvelopeAdmissionStatus):
            raise ValueError("invalid metadata envelope admission result")
        if not all(
            _is_non_blank_plain_string(value)
            for value in (self.code, self.message, self.retry_classification)
        ):
            raise ValueError("invalid metadata envelope admission result")
        if type(self.fresh_read_required) is not bool:
            raise ValueError("invalid metadata envelope admission result")
        if not isinstance(self.field_evidence, tuple) or not all(
            isinstance(item, MetadataEnvelopeAdmissionFieldEvidence) for item in self.field_evidence
        ):
            raise ValueError("invalid metadata envelope admission result")
        is_admitted = self.status is MetadataEnvelopeAdmissionStatus.ADMITTED
        if is_admitted != (
            isinstance(self.candidate, MetadataEnvelope)
            and isinstance(self.schema_binding, MetadataEnvelopeSchemaBinding)
        ):
            raise ValueError("invalid metadata envelope admission result")
        if is_admitted and self.field_evidence:
            raise ValueError("invalid metadata envelope admission result")


class MetadataEnvelopeAdmissionReader(Protocol):
    """Read the exact S4 values required by framework-independent admission."""

    def read_active_selection(self, schema: MetadataSchemaKey) -> MetadataSchemaActiveSelection:
        """Read one exact active selection.

        Args:
            schema: Exact globally unique metadata schema identity.

        Returns:
            The immutable S4 selection.
        """
        ...

    def read_publication(
        self, schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaPublication:
        """Read one exact immutable publication.

        Args:
            schema: Exact globally unique metadata schema identity.
            schema_version: Exact opaque selected schema version.

        Returns:
            The immutable S4 publication.
        """
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class MetadataEnvelopeAdmission:
    """Admit one immutable envelope through exact S4 active-selection reads.

    Args:
        reader: Repository-neutral S4 read boundary.

    Raises:
        ValueError: If the reader does not implement the required public reads.
    """

    reader: MetadataEnvelopeAdmissionReader

    def __post_init__(self) -> None:
        """Validate the structural S4 read boundary.

        Raises:
            ValueError: If the reader does not implement the required public reads.
        """
        if not callable(getattr(self.reader, "read_active_selection", None)) or not callable(
            getattr(self.reader, "read_publication", None)
        ):
            raise ValueError("invalid metadata envelope admission reader")

    def admit(self, candidate: object) -> MetadataEnvelopeAdmissionResult:  # noqa: PLR0911
        """Admit one envelope or return a safe fail-closed result.

        Args:
            candidate: One immutable Core metadata envelope candidate.

        Returns:
            An immutable admitted, rejected, unavailable, or indeterminate result.
        """
        if not isinstance(candidate, MetadataEnvelope):
            return _result(
                MetadataEnvelopeAdmissionStatus.REJECTED,
                "invalid_metadata_envelope_admission_request",
                "The metadata envelope admission request is invalid.",
                "correct_input",
                fresh_read_required=False,
            )
        measurement = _measure_candidate_payload(candidate)
        if isinstance(measurement, MetadataEnvelopeAdmissionResult):
            return measurement

        selection = self._read_selection(candidate.schema)
        if isinstance(selection, MetadataEnvelopeAdmissionResult):
            return selection
        publication = self._read_publication(candidate.schema, selection.schema_version)
        if isinstance(publication, MetadataEnvelopeAdmissionResult):
            return publication
        definition = _resolve_exact_definition(candidate, selection, publication)
        if isinstance(definition, MetadataEnvelopeAdmissionResult):
            return definition
        scope_result = _validate_scope_and_classification(candidate, definition)
        if scope_result is not None:
            return scope_result
        if not _has_complete_owner_facts(definition):
            return _result(
                MetadataEnvelopeAdmissionStatus.INDETERMINATE,
                "metadata_envelope_admission_durable_state_invalid",
                "The selected durable schema state is invalid.",
                "external_correction",
                fresh_read_required=True,
            )
        return _validate_payload_and_bounds(candidate, definition, selection, measurement)

    def _read_selection(
        self, schema: MetadataSchemaKey
    ) -> MetadataSchemaActiveSelection | MetadataEnvelopeAdmissionResult:
        """Read and translate one S4 active-selection outcome.

        Args:
            schema: Exact candidate schema identity.

        Returns:
            The selection or a safe terminal admission result.
        """
        try:
            selection = self.reader.read_active_selection(schema)
        except Exception as error:  # S4 implementations expose stable public error messages.
            return _translate_read_error(error, is_selection_read=True)
        if not isinstance(selection, MetadataSchemaActiveSelection):
            return _invalid_durable_state_result()
        return selection

    def _read_publication(
        self, schema: MetadataSchemaKey, schema_version: str
    ) -> MetadataSchemaPublication | MetadataEnvelopeAdmissionResult:
        """Read and translate one exact S4 publication outcome.

        Args:
            schema: Exact candidate schema identity.
            schema_version: Exact selected opaque schema version.

        Returns:
            The publication or a safe terminal admission result.
        """
        try:
            publication = self.reader.read_publication(schema, schema_version)
        except Exception as error:  # S4 implementations expose stable public error messages.
            return _translate_read_error(error, is_selection_read=False)
        if not isinstance(publication, MetadataSchemaPublication):
            return _invalid_durable_state_result()
        return publication


def _resolve_exact_definition(
    candidate: MetadataEnvelope,
    selection: MetadataSchemaActiveSelection,
    publication: MetadataSchemaPublication,
) -> MetadataSchemaDefinition | MetadataEnvelopeAdmissionResult:
    """Verify exact S4/S2 identity binding before candidate validation."""
    definition = publication.definition
    if (
        selection.schema != candidate.schema
        or selection.schema_version != publication.definition.schema_version
        or definition.schema != candidate.schema
        or candidate.schema_version != selection.schema_version
        or publication.status is not MetadataSchemaPublicationStatus.PUBLISHED
    ):
        if candidate.schema_version != selection.schema_version:
            return _result(
                MetadataEnvelopeAdmissionStatus.REJECTED,
                "metadata_envelope_admission_schema_version_mismatch",
                "The candidate schema version is not the selected version.",
                "correct_input_or_reread_selection",
                fresh_read_required=True,
            )
        return _invalid_durable_state_result()
    if not isinstance(definition, MetadataSchemaDefinition):
        return _invalid_durable_state_result()
    return definition


def _validate_scope_and_classification(
    candidate: MetadataEnvelope, definition: MetadataSchemaDefinition
) -> MetadataEnvelopeAdmissionResult | None:
    """Validate candidate attributes that do not need payload measurement."""
    declaration = definition.declaration
    if candidate.scope not in declaration.allowed_scopes:
        return _result(
            MetadataEnvelopeAdmissionStatus.REJECTED,
            "metadata_envelope_admission_scope_mismatch",
            "The candidate scope is not allowed by the selected schema.",
            "correct_input",
            fresh_read_required=False,
        )
    if candidate.classification is not declaration.classification:
        return _result(
            MetadataEnvelopeAdmissionStatus.REJECTED,
            "metadata_envelope_admission_classification_mismatch",
            "The candidate classification does not satisfy the selected schema.",
            "correct_input",
            fresh_read_required=False,
        )
    return None


def _has_complete_owner_facts(definition: MetadataSchemaDefinition) -> bool:
    """Determine whether selected durable definition ownership facts are complete."""
    try:
        return all(
            _is_non_blank_plain_string(owner)
            for field in definition.fields
            for owner in definition.get_field_owner(field.name)
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _measure_candidate_payload(
    candidate: MetadataEnvelope,
) -> MetadataPayloadMeasurement | MetadataEnvelopeAdmissionResult:
    """Measure a candidate before durable reads when its payload is independently invalid."""
    try:
        return measure_metadata_payload(candidate.payload)
    except MetadataPayloadMeasurementError:
        return _result(
            MetadataEnvelopeAdmissionStatus.REJECTED,
            "metadata_envelope_admission_validation_failed",
            "The metadata payload does not satisfy the selected schema.",
            "correct_input",
            fresh_read_required=False,
        )


def _validate_payload_and_bounds(
    candidate: MetadataEnvelope,
    definition: MetadataSchemaDefinition,
    selection: MetadataSchemaActiveSelection,
    measurement: MetadataPayloadMeasurement,
) -> MetadataEnvelopeAdmissionResult:
    """Validate selected-definition payload fields and RFC 8785 structural bounds."""
    bounds = definition.effective_bounds
    if (
        measurement.payload_bytes > bounds.max_payload_bytes
        or measurement.nesting_depth > bounds.max_nesting_depth
        or measurement.object_key_count > bounds.max_object_key_count
        or measurement.collection_item_count > bounds.max_collection_item_count
    ):
        return _result(
            MetadataEnvelopeAdmissionStatus.REJECTED,
            "metadata_envelope_admission_bounds_exceeded",
            "The metadata payload exceeds the selected schema bounds.",
            "correct_input",
            fresh_read_required=False,
        )
    field_evidence = _validate_payload_fields(candidate, definition)
    if field_evidence:
        return _result(
            MetadataEnvelopeAdmissionStatus.REJECTED,
            "metadata_envelope_admission_validation_failed",
            "The metadata payload does not satisfy the selected schema.",
            "correct_input",
            fresh_read_required=False,
            field_evidence=field_evidence,
        )
    return MetadataEnvelopeAdmissionResult(
        status=MetadataEnvelopeAdmissionStatus.ADMITTED,
        code="metadata_envelope_admitted",
        message="The metadata envelope is admitted.",
        retry_classification="none",
        fresh_read_required=False,
        candidate=candidate,
        schema_binding=MetadataEnvelopeSchemaBinding(
            schema=candidate.schema,
            schema_version=selection.schema_version,
            selection_revision=selection.revision,
        ),
    )


def _validate_payload_fields(
    candidate: MetadataEnvelope, definition: MetadataSchemaDefinition
) -> tuple[MetadataEnvelopeAdmissionFieldEvidence, ...]:
    """Return ordered, redacted selected-schema field validation evidence."""
    evidence: list[MetadataEnvelopeAdmissionFieldEvidence] = []
    declared_fields = {field.name: field for field in definition.fields}
    for field in definition.fields:
        value = candidate.payload.get(field.name)
        if field.name in candidate.payload and field.name in _RESERVED_PAYLOAD_FIELD_NAMES:
            evidence.append(_field_evidence("reserved_field", field.name))
        elif field.required and field.name not in candidate.payload:
            evidence.append(_field_evidence("missing_required", field.name))
        elif field.name in candidate.payload and value is None and not field.allows_null:
            evidence.append(_field_evidence("disallowed_null", field.name))
        elif (
            field.name in candidate.payload
            and value is not None
            and not _is_scalar_type(value, field.scalar_type)
        ):
            evidence.append(_field_evidence("invalid_scalar_type", field.name))
        if len(evidence) == _MAXIMUM_FIELD_ERRORS:
            return tuple(evidence)
    for field_name in sorted(candidate.payload.keys() - declared_fields.keys()):
        code = "reserved_field" if field_name in _RESERVED_PAYLOAD_FIELD_NAMES else "unknown_field"
        evidence.append(_field_evidence(code, field_name))
        if len(evidence) == _MAXIMUM_FIELD_ERRORS:
            break
    return tuple(evidence)


def _field_evidence(code: str, path: str) -> MetadataEnvelopeAdmissionFieldEvidence:
    """Build one stable redacted field-validation evidence item."""
    return MetadataEnvelopeAdmissionFieldEvidence(code=code, path=path)


def _is_scalar_type(value: object, scalar_type: str) -> bool:
    """Determine whether one non-null payload value matches an exact S2 scalar type."""
    return (
        (scalar_type == "string" and type(value) is str)
        or (scalar_type == "integer" and type(value) is int)
        or (scalar_type == "number" and type(value) in (int, float))
        or (scalar_type == "boolean" and type(value) is bool)
    )


def _translate_read_error(
    error: Exception, *, is_selection_read: bool
) -> MetadataEnvelopeAdmissionResult:
    """Translate stable S4 public errors without importing its implementation module."""
    error_code = str(error)
    if is_selection_read and error_code == _NO_ACTIVE_SELECTION_ERROR:
        return _result(
            MetadataEnvelopeAdmissionStatus.UNAVAILABLE,
            "metadata_envelope_admission_no_active_selection",
            "No active schema selection is available.",
            "after_activation",
            fresh_read_required=True,
        )
    if not is_selection_read and error_code == _MISSING_PUBLICATION_ERROR:
        return _result(
            MetadataEnvelopeAdmissionStatus.UNAVAILABLE,
            "metadata_envelope_admission_selected_publication_missing",
            "The selected schema publication is unavailable.",
            "operator_correction",
            fresh_read_required=True,
        )
    if error_code in _INVALID_DURABLE_STATE_ERRORS:
        return _invalid_durable_state_result()
    if error_code in _UNAVAILABLE_DEPENDENCY_ERRORS:
        return _result(
            MetadataEnvelopeAdmissionStatus.UNAVAILABLE,
            "metadata_envelope_admission_dependency_unavailable",
            "The schema admission dependency is unavailable.",
            "transient_retry",
            fresh_read_required=True,
        )
    return _result(
        MetadataEnvelopeAdmissionStatus.INDETERMINATE,
        "metadata_envelope_admission_internal_failure",
        "The metadata envelope admission outcome is indeterminate.",
        "operator_investigation",
        fresh_read_required=True,
    )


def _invalid_durable_state_result() -> MetadataEnvelopeAdmissionResult:
    """Build the stable invalid-durable-state result."""
    return _result(
        MetadataEnvelopeAdmissionStatus.INDETERMINATE,
        "metadata_envelope_admission_durable_state_invalid",
        "The selected durable schema state is invalid.",
        "external_correction",
        fresh_read_required=True,
    )


def _result(  # noqa: PLR0913
    status: MetadataEnvelopeAdmissionStatus,
    code: str,
    message: str,
    retry_classification: str,
    *,
    fresh_read_required: bool,
    field_evidence: tuple[MetadataEnvelopeAdmissionFieldEvidence, ...] = (),
) -> MetadataEnvelopeAdmissionResult:
    """Build one non-admitted immutable admission result."""
    return MetadataEnvelopeAdmissionResult(
        status=status,
        code=code,
        message=message,
        retry_classification=retry_classification,
        fresh_read_required=fresh_read_required,
        field_evidence=field_evidence,
    )


def _is_non_blank_plain_string(candidate: object) -> bool:
    """Determine whether a value is an exact non-blank plain string."""
    return type(candidate) is str and bool(candidate.strip())
