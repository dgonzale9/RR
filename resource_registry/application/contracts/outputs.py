from __future__ import annotations

from dataclasses import dataclass

from resource_registry.application.contracts.base import ApplicationContract
from resource_registry.application.contracts.enums import OutputKind, OutputStatus


@dataclass(frozen=True, kw_only=True)
class TransformationOutputReference(ApplicationContract):
    """Expose one output through public registry identifiers only."""

    output_id: str
    logical_name: str
    kind: OutputKind
    status: OutputStatus
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        """Require the public identifiers implied by the selected output kind."""
        if self.kind in {OutputKind.DATASET, OutputKind.DATASET_VERSION} and not self.dataset_id:
            raise ValueError("Dataset-backed outputs require a dataset identifier.")
        if self.kind == OutputKind.DATASET_VERSION and not self.dataset_version_id:
            raise ValueError("Dataset-version outputs require a dataset version identifier.")
