from __future__ import annotations

import inspect

from resource_registry.transformations.execution import checksum, contracts, ports, references, runtime
from resource_registry.transformations.execution import service as execution_service
from resource_registry.transformations.execution_plan import deserializer


def test_core_execution_and_deserialization_are_orm_and_worker_independent() -> None:
    combined_source = "\n".join(
        inspect.getsource(module)
        for module in (
            checksum,
            contracts,
            ports,
            references,
            runtime,
            execution_service,
            deserializer,
        )
    ).lower()
    prohibited_fragments = (
        "import django",
        "from django",
        "resource_registry.models",
        "worker_runner",
        "skip_locked",
        "lease_expires_at",
        "heartbeat_at",
        "resource_registry.storage",
        "artifact_lifecycle",
        "dataset_registry_service",
        "lineage_service",
        "resource_registry.bifs",
        "serializers",
        "views",
        "tabular_transformation_execution_service",
        "importlib",
    )

    for prohibited_fragment in prohibited_fragments:
        assert prohibited_fragment not in combined_source
