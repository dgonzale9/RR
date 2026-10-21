from __future__ import annotations

from pathlib import Path

import pytest

from resource_registry.security.artifact_guardrails import ArtifactGuardrailValidator
from resource_registry.security.exceptions import (
    ArtifactTooLargeError,
    UnsafeArtifactUriError,
    UnsupportedArtifactFormatError,
    UnsupportedStorageProviderError,
)
from resource_registry.security.policies import ArtifactMaterializationPolicy

POLICY_REVISION = 3


def test_guardrails_allow_csv_json_parquet_and_xlsx() -> None:
    validator = ArtifactGuardrailValidator()
    policy = ArtifactMaterializationPolicy(allow_local_files=True)
    validator.validate_format("csv", policy=policy)
    validator.validate_format("json", policy=policy)
    validator.validate_format("jsonl", policy=policy)
    validator.validate_format("parquet", policy=policy)
    validator.validate_format("xlsx", policy=policy)


def test_guardrails_reject_unsupported_format() -> None:
    validator = ArtifactGuardrailValidator()
    policy = ArtifactMaterializationPolicy(allow_local_files=True)
    with pytest.raises(UnsupportedArtifactFormatError) as exc_info:
        validator.validate_format(
            "exe",
            policy=policy,
            policy_source="TENANT_DEFAULT",
            policy_revision=POLICY_REVISION,
        )
    metadata = exc_info.value.violation_metadata
    assert metadata["violated_rule"] == "allowed_formats"
    assert metadata["policy_source"] == "TENANT_DEFAULT"
    assert metadata["policy_revision"] == POLICY_REVISION


def test_guardrails_reject_oversized_artifact() -> None:
    validator = ArtifactGuardrailValidator()
    policy = ArtifactMaterializationPolicy(max_size_bytes=10, allow_local_files=True)
    with pytest.raises(ArtifactTooLargeError) as exc_info:
        validator.validate_size(
            size_bytes=11,
            uri="/tmp/a.csv",
            policy=policy,
            policy_source="SYSTEM_DEFAULT",
            policy_revision=None,
        )
    assert exc_info.value.violation_metadata["violated_rule"] == "max_size_bytes"


def test_guardrails_reject_sas_uri() -> None:
    validator = ArtifactGuardrailValidator()
    with pytest.raises(UnsafeArtifactUriError):
        validator.validate_uri(
            "https://example.blob.core.windows.net/c/data.csv?sig=secret",
            policy=ArtifactMaterializationPolicy(),
        )


def test_guardrails_reject_unsupported_storage_provider() -> None:
    validator = ArtifactGuardrailValidator()
    policy = ArtifactMaterializationPolicy(
        allowed_storage_providers=frozenset({"azure_blob"}),
    )
    with pytest.raises(UnsupportedStorageProviderError):
        validator.validate_provider(
            storage_provider="local",
            uri="local://datasets/data.csv",
            policy=policy,
        )


def test_guardrails_reject_raw_local_path_by_default(tmp_path: Path) -> None:
    validator = ArtifactGuardrailValidator()
    source = tmp_path / "data.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    with pytest.raises(UnsafeArtifactUriError):
        validator.validate_uri(str(source), policy=ArtifactMaterializationPolicy())


def test_guardrails_allow_raw_local_path_when_enabled(tmp_path: Path) -> None:
    validator = ArtifactGuardrailValidator()
    source = tmp_path / "data.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    validator.validate_uri(
        str(source),
        policy=ArtifactMaterializationPolicy(allow_local_files=True),
    )
