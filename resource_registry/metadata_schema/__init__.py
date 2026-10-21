"""Expose immutable Identity/Type metadata schema contracts."""

from .admission import (
    MetadataEnvelopeAdmission,
    MetadataEnvelopeAdmissionFieldEvidence,
    MetadataEnvelopeAdmissionReader,
    MetadataEnvelopeAdmissionResult,
    MetadataEnvelopeAdmissionStatus,
    MetadataEnvelopeSchemaBinding,
)
from .declarations import (
    MetadataNamespaceDeclaration,
    MetadataPayloadBounds,
    MetadataSchemaRegistry,
    MetadataSchemaVersion,
)
from .definitions import (
    MetadataClassificationProfile,
    MetadataCompatibilityAssessment,
    MetadataCompatibilityStatus,
    MetadataFieldClassification,
    MetadataFieldDefinition,
    MetadataSchemaDefinition,
    MetadataSchemaDefinitionRegistry,
    MetadataSchemaValidationError,
)
from .payload_measurement import (
    MetadataPayloadMeasurement,
    MetadataPayloadMeasurementError,
    measure_metadata_payload,
)
from .publications import (
    MetadataSchemaActivationChange,
    MetadataSchemaActivationResult,
    MetadataSchemaActiveSelection,
    MetadataSchemaPublication,
    MetadataSchemaPublicationChange,
    MetadataSchemaPublicationRegistry,
    MetadataSchemaPublicationResult,
    MetadataSchemaPublicationStatus,
    MetadataSchemaPublicationValidationError,
)

__all__ = (  # noqa: RUF022
    "MetadataNamespaceDeclaration",
    "MetadataPayloadBounds",
    "MetadataSchemaVersion",
    "MetadataSchemaRegistry",
    "MetadataSchemaValidationError",
    "MetadataFieldDefinition",
    "MetadataFieldClassification",
    "MetadataClassificationProfile",
    "MetadataSchemaDefinition",
    "MetadataSchemaDefinitionRegistry",
    "MetadataCompatibilityStatus",
    "MetadataCompatibilityAssessment",
    "MetadataSchemaPublicationValidationError",
    "MetadataSchemaPublicationStatus",
    "MetadataSchemaPublication",
    "MetadataSchemaActiveSelection",
    "MetadataSchemaPublicationResult",
    "MetadataSchemaActivationResult",
    "MetadataSchemaPublicationChange",
    "MetadataSchemaActivationChange",
    "MetadataSchemaPublicationRegistry",
    "MetadataEnvelopeAdmissionStatus",
    "MetadataEnvelopeAdmissionFieldEvidence",
    "MetadataEnvelopeSchemaBinding",
    "MetadataEnvelopeAdmissionResult",
    "MetadataEnvelopeAdmissionReader",
    "MetadataEnvelopeAdmission",
    "MetadataPayloadMeasurementError",
    "MetadataPayloadMeasurement",
    "measure_metadata_payload",
)
