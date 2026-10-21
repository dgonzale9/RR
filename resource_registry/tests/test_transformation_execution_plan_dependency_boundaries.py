from __future__ import annotations

import inspect

from resource_registry.transformations.execution_plan import checksum, compiler, contracts, results


def test_execution_plan_package_has_no_runtime_or_orm_dependencies() -> None:
    combined_source = "\n".join(
        inspect.getsource(module)
        for module in (checksum, compiler, contracts, results)
    ).lower()
    prohibited_fragments = (
        "django",
        "resource_registry.models",
        "transformationrun",
        "job_service",
        "worker_runner",
        "resource_registry.storage",
        "artifact_lifecycle",
        "dataset_registry_service",
        "lineage_service",
        "resource_registry.bifs",
        "serializers",
        "views",
        "tabular_transformation_execution_service",
        ".execute(",
        "vault",
        "secrets_manager",
    )

    for prohibited_fragment in prohibited_fragments:
        assert prohibited_fragment not in combined_source
