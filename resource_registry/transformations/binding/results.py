from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from resource_registry.transformations.validation.semantic_results import (
    TransformationSemanticValidationResult,
)


class ParameterValueClassification(StrEnum):
    """Classify bound values without defining a serialized secret-reference grammar."""

    LITERAL = "literal"
    SECRET_REFERENCE = "secret_reference"


@dataclass(frozen=True)
class TransformationBindingFinding:
    """Describe one deterministic and sanitized parameter or input binding finding."""

    code: str
    path: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation of the finding.

        Returns:
            Finding fields containing only JSON string values.
        """
        return {
            "code": self.code,
            "path": self.path,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True)
class BoundTransformationParameter:
    """Represent one recursively immutable bound transformation parameter."""

    parameter_key: str
    value: object
    classification: ParameterValueClassification = ParameterValueClassification.LITERAL


@dataclass(frozen=True)
class ParameterBindingResult:
    """Represent deterministic parameter binding findings and values."""

    is_valid: bool
    findings: tuple[TransformationBindingFinding, ...] = field(default_factory=tuple)
    bound_parameters: tuple[BoundTransformationParameter, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TransformationInputBindingRequest:
    """Request one exact dataset-version and optional artifact input reference."""

    input_name: str
    dataset_id: str
    dataset_version_id: str
    artifact_id: str | None = None


@dataclass(frozen=True)
class BoundDatasetVersionReference:
    """Represent one exact, tenant-safe dataset-version reference."""

    tenant_id: str
    dataset_id: str
    dataset_version_id: str
    version_number: int
    status: str
    source_uri: str
    content_hash: str


@dataclass(frozen=True)
class BoundDatasetArtifactReference:
    """Represent one exact active dataset artifact reference."""

    artifact_id: str
    dataset_version_id: str
    uri: str
    artifact_role: str
    artifact_format: str
    size_bytes: int
    checksum: str | None
    storage_provider: str | None
    lifecycle_status: str
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class BoundTransformationInput:
    """Represent one exact bound top-level transformation input."""

    input_name: str
    dataset_version: BoundDatasetVersionReference
    artifact: BoundDatasetArtifactReference | None = None


@dataclass(frozen=True)
class TransformationInputBindingResult:
    """Represent deterministic exact input binding findings and references."""

    is_valid: bool
    findings: tuple[TransformationBindingFinding, ...] = field(default_factory=tuple)
    bound_inputs: tuple[BoundTransformationInput, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TransformationSpecificationBindingResult:
    """Aggregate semantic, parameter, and exact input binding results."""

    is_valid: bool
    semantic_validation: TransformationSemanticValidationResult
    parameter_binding: ParameterBindingResult
    input_binding: TransformationInputBindingResult


def order_binding_findings(
    findings: list[TransformationBindingFinding],
) -> tuple[TransformationBindingFinding, ...]:
    """Return findings ordered by path, code, severity, and message.

    Args:
        findings: Findings collected during one binding phase.

    Returns:
        Deterministically ordered findings.
    """
    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.path,
                finding.code,
                finding.severity,
                finding.message,
            ),
        ),
    )
