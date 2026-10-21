from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TransformationMetadata:
    """Represent optional top-level Transformation DSL metadata."""

    values: Mapping[str, object]


@dataclass(frozen=True)
class TransformationParameter:
    """Represent one named Transformation DSL parameter definition."""

    parameter_key: str
    definition: object


@dataclass(frozen=True)
class TransformationInput:
    """Represent one Transformation DSL input declaration."""

    values: Mapping[str, object]


@dataclass(frozen=True)
class TransformationOutput:
    """Represent one Transformation DSL output declaration."""

    values: Mapping[str, object]


@dataclass(frozen=True)
class TransformationStep:
    """Represent one ordered Transformation DSL step declaration."""

    step_id: str
    provider: Mapping[str, object]
    operation: Mapping[str, object]
    config: Mapping[str, object]
    inputs: tuple[TransformationInput, ...] | None = None
    outputs: tuple[TransformationOutput, ...] | None = None
    extensions: Mapping[str, object] | None = None


@dataclass(frozen=True)
class TransformationSpecification:
    """Represent a structurally valid Transformation DSL specification as typed objects."""

    dsl_version: str
    kind: str
    steps: tuple[TransformationStep, ...]
    metadata: TransformationMetadata | None = None
    parameters: tuple[TransformationParameter, ...] | None = None
    inputs: tuple[TransformationInput, ...] | None = None
    outputs: tuple[TransformationOutput, ...] | None = None
    extensions: Mapping[str, object] | None = None
