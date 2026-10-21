from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from resource_registry.application.contracts.base import ApplicationContract
from resource_registry.application.contracts.immutable import (
    FrozenJsonMapping,
    freeze_json_mapping,
)


@dataclass(frozen=True, kw_only=True, order=True)
class TransformationInputBinding(ApplicationContract):
    """Bind one named transformation input to explicit public registry identifiers."""

    input_name: str
    dataset_id: str
    dataset_version_id: str
    artifact_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class TransformationExecutionRequest(ApplicationContract):
    """Request asynchronous execution of one exact transformation definition version."""

    tenant_id: str
    definition_id: str
    definition_version_id: str
    parameters: FrozenJsonMapping
    input_bindings: tuple[TransformationInputBinding, ...]
    caller_idempotency_key: str
    correlation_id: str

    def __init__(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        definition_id: str,
        definition_version_id: str,
        parameters: Mapping[str, object] | None,
        input_bindings: tuple[TransformationInputBinding, ...],
        caller_idempotency_key: str,
        correlation_id: str,
    ) -> None:
        """Normalize mappings and input order into a deterministic immutable request."""
        ordered_bindings = tuple(sorted(input_bindings, key=lambda binding: binding.input_name))
        if len({binding.input_name for binding in ordered_bindings}) != len(ordered_bindings):
            raise ValueError("Transformation input binding names must be unique.")
        object.__setattr__(self, "contract_version", self.contract_version)
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "definition_id", definition_id)
        object.__setattr__(self, "definition_version_id", definition_version_id)
        object.__setattr__(self, "parameters", freeze_json_mapping(parameters))
        object.__setattr__(self, "input_bindings", ordered_bindings)
        object.__setattr__(self, "caller_idempotency_key", caller_idempotency_key)
        object.__setattr__(self, "correlation_id", correlation_id)
