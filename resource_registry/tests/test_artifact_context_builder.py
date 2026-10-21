from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from resource_registry.application.artifact_context import ArtifactContextBuilder
from resource_registry.security.exceptions import (
    ArtifactGuardrailViolation,
    ArtifactIntegrityError,
    UnsafeArtifactUriError,
    UnsupportedArtifactFormatError,
    UnsupportedStorageProviderError,
)
from resource_registry.security.policies import (
    ArtifactMaterializationPolicy,
    ArtifactMaterializationPolicyResolver,
)
from resource_registry.storage.local import LocalStorageProvider
from resource_registry.storage.read_adapters import LocalArtifactReadAdapter


def test_artifact_context_builder_builds_context_from_local_uri(tmp_path: Path) -> None:
    expected_column_count = 2
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=tmp_path / "store")
    provider.put(str(source_file), "datasets/sample.csv")
    adapter = LocalArtifactReadAdapter(storage_provider=provider)
    builder = ArtifactContextBuilder(adapters=[adapter])

    context = builder.build_context(
        artifact_uri="local://datasets/sample.csv",
        preview_limit=5,
    )

    assert context["summary"]["row_count"] == 1
    assert context["summary"]["column_count"] == expected_column_count


def test_artifact_context_builder_cleans_temp_on_success(tmp_path: Path) -> None:
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=tmp_path / "store")
    provider.put(str(source_file), "datasets/sample.csv")
    adapter = LocalArtifactReadAdapter(storage_provider=provider)
    dataset_context_builder = MagicMock()
    dataset_context_builder.build_context.return_value = {
        "summary": {"row_count": 1, "column_count": 2},
    }
    builder = ArtifactContextBuilder(
        dataset_context_builder=dataset_context_builder,
        adapters=[adapter],
    )

    _ = builder.build_context(artifact_uri="local://datasets/sample.csv")

    local_path = dataset_context_builder.build_context.call_args.args[0]
    assert not Path(local_path).exists()


def test_artifact_context_builder_cleans_temp_on_failure(tmp_path: Path) -> None:
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=tmp_path / "store")
    provider.put(str(source_file), "datasets/sample.csv")
    adapter = LocalArtifactReadAdapter(storage_provider=provider)
    dataset_context_builder = MagicMock()
    dataset_context_builder.build_context.side_effect = RuntimeError("boom")
    builder = ArtifactContextBuilder(
        dataset_context_builder=dataset_context_builder,
        adapters=[adapter],
    )

    with pytest.raises(RuntimeError):
        builder.build_context(artifact_uri="local://datasets/sample.csv")

    local_path = dataset_context_builder.build_context.call_args.args[0]
    assert not Path(local_path).exists()


def test_artifact_context_builder_rejects_unsupported_uri() -> None:
    builder = ArtifactContextBuilder(adapters=[])
    with pytest.raises(UnsupportedStorageProviderError):
        builder.build_context(artifact_uri="s3://bucket/key.parquet")


def test_guardrail_runs_before_materialization() -> None:
    adapter = MagicMock()
    adapter.can_handle.return_value = True
    builder = ArtifactContextBuilder(adapters=[adapter])
    with pytest.raises(UnsupportedArtifactFormatError):
        builder.build_context(
            artifact_uri="local://datasets/sample.xml",
            artifact_metadata={"size_bytes": 10},
        )
    adapter.materialize_to_temp.assert_not_called()


def test_artifact_context_builder_cleans_temp_after_integrity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=tmp_path / "store")
    provider.put(str(source_file), "datasets/sample.csv")
    temp_path = tmp_path / "temp-materialized.csv"
    monkeypatch.setattr(
        "resource_registry.storage.read_adapters._create_temp_path",
        lambda _uri: str(temp_path),
    )
    builder = ArtifactContextBuilder(
        adapters=[LocalArtifactReadAdapter(storage_provider=provider)],
    )
    with pytest.raises(ArtifactIntegrityError):
        builder.build_context(
            artifact_uri="local://datasets/sample.csv",
            artifact_metadata={"size_bytes": source_file.stat().st_size},
            expected_checksum="b" * 64,
        )
    assert not temp_path.exists()


def test_artifact_context_builder_allows_checksum_match(tmp_path: Path) -> None:
    source_file = tmp_path / "sample.csv"
    payload = "id,name\n1,Ada\n"
    source_file.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    provider = LocalStorageProvider(base_directory=tmp_path / "store")
    provider.put(str(source_file), "datasets/sample.csv")
    builder = ArtifactContextBuilder(
        adapters=[LocalArtifactReadAdapter(storage_provider=provider)],
        materialization_policy=ArtifactMaterializationPolicy(require_checksum=True),
    )
    context = builder.build_context(
        artifact_uri="local://datasets/sample.csv",
        artifact_metadata={"size_bytes": source_file.stat().st_size},
        expected_checksum=f"sha256:{digest}",
    )
    assert context["summary"]["row_count"] == 1


def test_artifact_context_builder_generates_enforcement_context(tmp_path: Path) -> None:
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    provider = LocalStorageProvider(base_directory=tmp_path / "store")
    provider.put(str(source_file), "datasets/sample.csv")
    builder = ArtifactContextBuilder(
        adapters=[LocalArtifactReadAdapter(storage_provider=provider)],
    )
    result = builder.build_context_with_integrity(
        artifact_uri="local://datasets/sample.csv",
        artifact_metadata={"size_bytes": source_file.stat().st_size},
    )
    assert result.enforcement_context is not None
    assert result.enforcement_context.enforcement_result == "ALLOW"


def test_artifact_context_builder_uses_explicit_policy(tmp_path: Path) -> None:
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    policy = ArtifactMaterializationPolicy(allow_local_files=True)
    builder = ArtifactContextBuilder(materialization_policy=policy)

    context = builder.build_context(
        artifact_uri=str(source_file),
        artifact_metadata={"size_bytes": source_file.stat().st_size},
    )

    assert context["summary"]["row_count"] == 1


def test_artifact_context_builder_resolves_policy_from_tenant_dataset(tmp_path: Path) -> None:
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    resolver = MagicMock(spec=ArtifactMaterializationPolicyResolver)
    resolver.resolve_with_provenance.return_value = SimpleNamespace(
        policy=ArtifactMaterializationPolicy(allow_local_files=True),
        provenance=None,
    )
    builder = ArtifactContextBuilder(policy_resolver=resolver)

    _ = builder.build_context(
        artifact_uri=str(source_file),
        tenant_id="tenant-a",
        dataset_id="dataset-1",
        artifact_metadata={"size_bytes": source_file.stat().st_size},
    )

    resolver.resolve_with_provenance.assert_called_once_with(
        tenant_id="tenant-a",
        dataset_id="dataset-1",
    )


def test_artifact_context_builder_rejects_local_file_when_policy_disallows(tmp_path: Path) -> None:
    source_file = tmp_path / "sample.csv"
    source_file.write_text("id,name\n1,Ada\n", encoding="utf-8")
    builder = ArtifactContextBuilder(materialization_policy=ArtifactMaterializationPolicy())
    with pytest.raises(UnsafeArtifactUriError):
        builder.build_context(
            artifact_uri=str(source_file),
            artifact_metadata={"size_bytes": source_file.stat().st_size},
        )


def test_artifact_context_builder_rejects_disallowed_provider() -> None:
    builder = ArtifactContextBuilder(
        materialization_policy=ArtifactMaterializationPolicy(
            allowed_storage_providers=frozenset({"local"}),
        ),
    )
    with pytest.raises(UnsupportedStorageProviderError):
        builder.build_context(
            artifact_uri="azure://media/container/data.csv",
            artifact_metadata={"size_bytes": 100},
        )


def test_artifact_context_builder_rejects_disallowed_format() -> None:
    builder = ArtifactContextBuilder(
        materialization_policy=ArtifactMaterializationPolicy(
            allowed_formats=frozenset({"parquet"}),
        ),
    )
    with pytest.raises(UnsupportedArtifactFormatError):
        builder.build_context(
            artifact_uri="local://datasets/sample.csv",
            artifact_metadata={"size_bytes": 100},
        )


def test_artifact_context_builder_missing_checksum_when_required() -> None:
    builder = ArtifactContextBuilder(
        materialization_policy=ArtifactMaterializationPolicy(require_checksum=True),
    )
    with pytest.raises(ArtifactGuardrailViolation):
        builder.build_context(
            artifact_uri="local://datasets/sample.csv",
            artifact_metadata={"size_bytes": 100},
        )
