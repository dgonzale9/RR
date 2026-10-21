from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from resource_registry.bifs.transformation.definitions import (
    TRANSFORMATION_BIF_DEFINITIONS,
    TransformationBifDefinition,
)


def build_transformation_bif_registry(
    definitions: Iterable[TransformationBifDefinition] = TRANSFORMATION_BIF_DEFINITIONS,
) -> Mapping[str, TransformationBifDefinition]:
    """Build an immutable deterministic registry and reject duplicate names."""
    registry: dict[str, TransformationBifDefinition] = {}
    for definition in sorted(definitions, key=lambda item: item.name):
        if definition.name in registry:
            raise ValueError(f"Duplicate transformation BIF name: {definition.name}.")
        registry[definition.name] = definition
    return MappingProxyType(registry)


TRANSFORMATION_BIF_REGISTRY = build_transformation_bif_registry()
