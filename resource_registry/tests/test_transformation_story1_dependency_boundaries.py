from __future__ import annotations

import inspect

from resource_registry.transformations.binding import (
    immutable_json,
    input_binder,
    parameter_binder,
    results,
    service,
)
from resource_registry.transformations.validation import semantic_results, semantic_validator


def test_story_one_core_has_no_runtime_or_django_dependencies() -> None:
    modules = (
        semantic_results,
        semantic_validator,
        immutable_json,
        input_binder,
        parameter_binder,
        results,
        service,
    )
    prohibited_fragments = (
        "django",
        "resource_registry.models",
        "resource_registry.providers",
        "tabular_transformation_execution_service",
        "worker_runner",
        "resource_registry.storage",
        "artifact_lifecycle_service",
        "lineage_service",
        "resource_registry.bifs",
        "serializers",
        "views",
    )

    combined_source = "\n".join(inspect.getsource(module) for module in modules).lower()

    for prohibited_fragment in prohibited_fragments:
        assert prohibited_fragment not in combined_source
