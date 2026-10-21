from __future__ import annotations

import inspect

from resource_registry.transformations.output_finalization import service


def test_story_seven_has_one_orchestrator_and_excludes_deferred_dependencies() -> None:
    source = inspect.getsource(service).lower()
    prohibited = (
        "transformation_run_worker_runner",
        "transformation_run_job_service",
        "checkpoint_persistence",
        "transformationexecutionplancompiler",
        "serializers",
        "views",
        "resource_registry.bifs",
        "provider_registry",
        "execute_provider",
    )
    local_service_classes = [
        candidate
        for _, candidate in inspect.getmembers(service, inspect.isclass)
        if candidate.__module__ == service.__name__ and candidate.__name__.endswith("Service")
    ]

    assert local_service_classes == [service.TransformationRunOutputFinalizationService]
    for fragment in prohibited:
        assert fragment not in source
