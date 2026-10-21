from __future__ import annotations

import inspect

from resource_registry.transformations.providers import base, contracts, exceptions, registry


def test_transformation_provider_package_has_no_prohibited_dependencies() -> None:
    combined_source = "\n".join(
        inspect.getsource(module)
        for module in (base, contracts, exceptions, registry)
    ).lower()
    prohibited_fragments = (
        "django",
        "resource_registry.models",
        "resource_registry.providers",
        "read_auth",
        "resource_registry.read",
        "resource_registry.storage",
        "job_service",
        "worker_runner",
        "artifact_lifecycle",
        "lineage_service",
        "resource_registry.bifs",
        "serializers",
        "views",
        "tabular_transformation_execution_service",
        "importlib",
        "entry_points",
    )

    for prohibited_fragment in prohibited_fragments:
        assert prohibited_fragment not in combined_source
