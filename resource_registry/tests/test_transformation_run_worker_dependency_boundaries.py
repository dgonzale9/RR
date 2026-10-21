from __future__ import annotations

import inspect

from resource_registry.services import (
    transformation_run_execution_coordinator,
    transformation_run_job_service,
    transformation_run_worker_runner,
)


def test_story_six_excludes_story_seven_and_public_interface_dependencies() -> None:
    source = "\n".join(
        inspect.getsource(module)
        for module in (
            transformation_run_execution_coordinator,
            transformation_run_job_service,
            transformation_run_worker_runner,
        )
    ).lower()
    prohibited = (
        "artifact_lifecycle",
        "dataset_registry_service",
        "lineage",
        "resource_registry.storage",
        "serializers",
        "views",
        "resource_registry.bifs",
        "tabular_transformation_execution_service",
        "importlib",
    )

    for fragment in prohibited:
        assert fragment not in source
