from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_registry.policies.exceptions import InvalidPolicyConfigurationError
from resource_registry.policies.provenance import PolicySource
from resource_registry.security.policies import (
    ARTIFACT_MATERIALIZATION_POLICY_KEY,
    ArtifactMaterializationPolicyResolver,
)

SYSTEM_DEFAULT_MAX_SIZE_BYTES = 104_857_600
TENANT_OVERRIDE_MAX_SIZE_BYTES = 1000
DATASET_OVERRIDE_MAX_SIZE_BYTES = 2048
TENANT_DEFAULT_WITH_PROVENANCE_MAX_SIZE_BYTES = 1111


def _resolver_with_service(service: MagicMock) -> ArtifactMaterializationPolicyResolver:
    return ArtifactMaterializationPolicyResolver(policy_resolution_service=service)


def test_materialization_policy_resolver_system_default_resolution() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = None
    service.resolve_tenant_policy.return_value = None
    resolver = _resolver_with_service(service)

    policy = resolver.resolve_policy(tenant_id="tenant-a")

    assert policy.max_size_bytes == SYSTEM_DEFAULT_MAX_SIZE_BYTES
    assert policy.allow_local_files is True
    assert policy.allow_remote_uris is True
    assert policy.allowed_formats == frozenset({"csv", "json", "jsonl", "parquet", "xlsx"})


def test_materialization_policy_resolver_tenant_override() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="tenant_default",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            revision=1,
            policy_id="policy-1",
            rules_json={
                "max_size_bytes": 1000,
                "allowed_formats": ["csv"],
                "require_checksum": True,
                "allowed_storage_providers": ["azure_blob"],
                "allow_local_files": False,
                "allow_remote_uris": True,
            },
        ),
        provenance=SimpleNamespace(
            source=PolicySource.TENANT_DEFAULT,
            revision=1,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            governance_policy_id="policy-1",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["tenant_default", "system_default"],
        ),
    )
    service.resolve_tenant_policy.return_value = SimpleNamespace(
        rules_json={
            "max_size_bytes": 1000,
            "allowed_formats": ["csv"],
            "require_checksum": True,
            "allowed_storage_providers": ["azure_blob"],
            "allow_local_files": False,
            "allow_remote_uris": True,
        },
    )
    resolver = _resolver_with_service(service)

    policy = resolver.resolve_policy(tenant_id="tenant-a")

    assert policy.max_size_bytes == TENANT_OVERRIDE_MAX_SIZE_BYTES
    assert policy.allowed_formats == frozenset({"csv"})
    assert policy.require_checksum is True
    assert policy.allowed_storage_providers == frozenset({"azure_blob"})


def test_materialization_policy_resolver_dataset_override_wins() -> None:
    service = MagicMock()
    service.resolve_effective_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="dataset_override",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id="dataset-1",
            revision=4,
            policy_id="policy-4",
            rules_json={
                "max_size_bytes": 2048,
                "allowed_formats": ["parquet"],
                "require_checksum": False,
                "allowed_storage_providers": ["local"],
                "allow_local_files": True,
                "allow_remote_uris": False,
            },
        ),
        provenance=SimpleNamespace(
            source=PolicySource.DATASET_OVERRIDE,
            revision=4,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id="dataset-1",
            governance_policy_id="policy-4",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["dataset_override", "tenant_default", "system_default"],
        ),
    )
    service.resolve_effective_policy.return_value = SimpleNamespace(
        source="dataset_override",
        rules_json={
            "max_size_bytes": 2048,
            "allowed_formats": ["parquet"],
            "require_checksum": False,
            "allowed_storage_providers": ["local"],
            "allow_local_files": True,
            "allow_remote_uris": False,
        },
    )
    resolver = _resolver_with_service(service)
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-a")
    with patch("resource_registry.security.policies.Dataset.objects.filter") as mocked_filter:
        mocked_filter.return_value.first.return_value = dataset
        policy = resolver.resolve_policy(tenant_id="tenant-a", dataset_id="dataset-1")

    assert policy.max_size_bytes == DATASET_OVERRIDE_MAX_SIZE_BYTES
    assert policy.allow_local_files is True
    assert policy.allow_remote_uris is False


def test_materialization_policy_resolver_partial_merge_with_defaults() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="tenant_default",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            revision=1,
            policy_id="policy-1",
            rules_json={"allow_local_files": True},
        ),
        provenance=SimpleNamespace(
            source=PolicySource.TENANT_DEFAULT,
            revision=1,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            governance_policy_id="policy-1",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["tenant_default", "system_default"],
        ),
    )
    service.resolve_tenant_policy.return_value = SimpleNamespace(
        rules_json={"allow_local_files": True},
    )
    resolver = _resolver_with_service(service)

    policy = resolver.resolve_policy(tenant_id="tenant-a")

    assert policy.allow_local_files is True
    assert policy.max_size_bytes == SYSTEM_DEFAULT_MAX_SIZE_BYTES
    assert policy.allowed_formats == frozenset({"csv", "json", "jsonl", "parquet", "xlsx"})


def test_materialization_policy_resolver_invalid_max_size_rejected() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="tenant_default",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            revision=1,
            policy_id="policy-1",
            rules_json={"max_size_bytes": 0},
        ),
        provenance=SimpleNamespace(
            source=PolicySource.TENANT_DEFAULT,
            revision=1,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            governance_policy_id="policy-1",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["tenant_default", "system_default"],
        ),
    )
    service.resolve_tenant_policy.return_value = SimpleNamespace(
        rules_json={"max_size_bytes": 0},
    )
    resolver = _resolver_with_service(service)
    with pytest.raises(InvalidPolicyConfigurationError):
        resolver.resolve_policy(tenant_id="tenant-a")


def test_materialization_policy_resolver_invalid_allowed_formats_rejected() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="tenant_default",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            revision=1,
            policy_id="policy-1",
            rules_json={"allowed_formats": ["exe"]},
        ),
        provenance=SimpleNamespace(
            source=PolicySource.TENANT_DEFAULT,
            revision=1,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            governance_policy_id="policy-1",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["tenant_default", "system_default"],
        ),
    )
    service.resolve_tenant_policy.return_value = SimpleNamespace(
        rules_json={"allowed_formats": ["exe"]},
    )
    resolver = _resolver_with_service(service)
    with pytest.raises(InvalidPolicyConfigurationError):
        resolver.resolve_policy(tenant_id="tenant-a")


def test_materialization_policy_resolver_invalid_boolean_rejected() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="tenant_default",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            revision=1,
            policy_id="policy-1",
            rules_json={"allow_local_files": "yes"},
        ),
        provenance=SimpleNamespace(
            source=PolicySource.TENANT_DEFAULT,
            revision=1,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            governance_policy_id="policy-1",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["tenant_default", "system_default"],
        ),
    )
    service.resolve_tenant_policy.return_value = SimpleNamespace(
        rules_json={"allow_local_files": "yes"},
    )
    resolver = _resolver_with_service(service)
    with pytest.raises(InvalidPolicyConfigurationError):
        resolver.resolve_policy(tenant_id="tenant-a")


def test_materialization_policy_resolver_unknown_key_rejected() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="tenant_default",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            revision=1,
            policy_id="policy-1",
            rules_json={"unknown_field": True},
        ),
        provenance=SimpleNamespace(
            source=PolicySource.TENANT_DEFAULT,
            revision=1,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            governance_policy_id="policy-1",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["tenant_default", "system_default"],
        ),
    )
    service.resolve_tenant_policy.return_value = SimpleNamespace(
        rules_json={"unknown_field": True},
    )
    resolver = _resolver_with_service(service)
    with pytest.raises(InvalidPolicyConfigurationError):
        resolver.resolve_policy(tenant_id="tenant-a")


def test_materialization_policy_resolver_dataset_lookup_tenant_boundary() -> None:
    service = MagicMock()
    resolver = _resolver_with_service(service)
    with patch("resource_registry.security.policies.Dataset.objects.filter") as mocked_filter:
        mocked_filter.return_value.first.return_value = None
        with pytest.raises(InvalidPolicyConfigurationError):
            resolver.resolve_policy(tenant_id="tenant-a", dataset_id="dataset-x")


def test_materialization_policy_key_constant() -> None:
    assert ARTIFACT_MATERIALIZATION_POLICY_KEY == "artifact_materialization_default"


def test_resolve_with_provenance_system_default_source() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = None
    service.resolve_tenant_policy.return_value = None
    resolver = _resolver_with_service(service)

    resolved = resolver.resolve_with_provenance(tenant_id="tenant-a")

    assert resolved.provenance.source == PolicySource.SYSTEM_DEFAULT


def test_resolve_with_provenance_dataset_override_beats_tenant_default() -> None:
    service = MagicMock()
    service.resolve_effective_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="dataset_override",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id="dataset-1",
            revision=5,
            policy_id="policy-5",
            rules_json={
                "max_size_bytes": 1024,
                "allowed_formats": ["csv"],
                "require_checksum": False,
                "allowed_storage_providers": ["local"],
                "allow_local_files": True,
                "allow_remote_uris": False,
            },
        ),
        provenance=SimpleNamespace(
            source=PolicySource.DATASET_OVERRIDE,
            revision=5,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id="dataset-1",
            governance_policy_id="policy-5",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["dataset_override", "tenant_default", "system_default"],
        ),
    )
    resolver = _resolver_with_service(service)
    with patch("resource_registry.security.policies.Dataset.objects.filter") as mocked_filter:
        mocked_filter.return_value.first.return_value = SimpleNamespace(
            id="dataset-1",
            tenant_id="tenant-a",
        )
        resolved = resolver.resolve_with_provenance(tenant_id="tenant-a", dataset_id="dataset-1")

    assert resolved.provenance.source == PolicySource.DATASET_OVERRIDE
    assert resolved.policy.allow_local_files is True


def test_resolve_with_provenance_tenant_default_beats_system_default() -> None:
    service = MagicMock()
    service.resolve_tenant_policy_with_provenance.return_value = SimpleNamespace(
        resolved_policy=SimpleNamespace(
            source="tenant_default",
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            revision=2,
            policy_id="policy-2",
            rules_json={
                "max_size_bytes": 1111,
                "allowed_formats": ["csv"],
                "require_checksum": False,
                "allowed_storage_providers": ["local"],
                "allow_local_files": True,
                "allow_remote_uris": True,
            },
        ),
        provenance=SimpleNamespace(
            source=PolicySource.TENANT_DEFAULT,
            revision=2,
            policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            tenant_id="tenant-a",
            dataset_id=None,
            governance_policy_id="policy-2",
            resolved_at=None,
            rules_hash="hash",
            resolution_path=["tenant_default", "system_default"],
        ),
    )
    resolver = _resolver_with_service(service)

    resolved = resolver.resolve_with_provenance(tenant_id="tenant-a")

    assert resolved.provenance.source == PolicySource.TENANT_DEFAULT
    assert resolved.policy.max_size_bytes == TENANT_DEFAULT_WITH_PROVENANCE_MAX_SIZE_BYTES
