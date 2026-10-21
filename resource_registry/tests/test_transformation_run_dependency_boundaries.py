from __future__ import annotations

import inspect

from resource_registry.transformations.runs import fingerprint, serialization, service


def test_run_creation_modules_exclude_execution_and_runtime_dependencies() -> None:
    combined_source = "\n".join(
        inspect.getsource(module) for module in (fingerprint, serialization, service)
    ).lower()
    prohibited_fragments = (
        "provider.execute",
        ".execute(",
        "worker_runner",
        "resource_registry.storage",
        "artifact_lifecycle_service",
        "dataset_registry_service",
        "lineage_service",
        "serializers",
        "views",
        "resource_registry.bifs",
        "tabular_transformation_execution_service",
        "importlib",
        "vault",
        "secrets_manager",
        "claim_token",
        "claim_owner",
        "heartbeat_at",
        "lease_expires_at",
    )

    for prohibited_fragment in prohibited_fragments:
        assert prohibited_fragment not in combined_source
