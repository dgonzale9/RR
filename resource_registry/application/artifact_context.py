from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from resource_registry.application.exceptions import ValidationError
from resource_registry.policies.provenance import (
    PolicyEnforcementContext,
    PolicyProvenance,
    enforcement_allow,
)
from resource_registry.security.artifact_guardrails import ArtifactGuardrailValidator
from resource_registry.security.integrity import (
    ArtifactIntegrityVerifier,
    IntegrityVerificationResult,
)
from resource_registry.security.policies import (
    ArtifactMaterializationPolicy,
    ArtifactMaterializationPolicyResolver,
    ResolvedArtifactMaterializationPolicy,
)
from resource_registry.services.dataset_context_builder import DatasetContextBuilder
from resource_registry.services.dataset_formats import normalize_dataset_format
from resource_registry.storage.exceptions import UnsafeStorageUriError, UnsupportedArtifactReadError
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.read_adapters import (
    ArtifactReadAdapter,
    LocalArtifactReadAdapter,
    StorageProviderArtifactReadAdapter,
)


@dataclass(frozen=True)
class ArtifactContextBuildResult:
    context: dict[str, Any]
    integrity_verification: IntegrityVerificationResult | None = None
    policy_provenance: PolicyProvenance | None = None
    enforcement_context: PolicyEnforcementContext | None = None


class ArtifactContextBuilder:
    """Build dataset context from storage-backed artifact URI using read adapters."""

    def __init__(
        self,
        *,
        dataset_context_builder: DatasetContextBuilder | None = None,
        storage_factory: DataRegistryStorageFactory | None = None,
        adapters: list[ArtifactReadAdapter] | None = None,
        guardrail_validator: ArtifactGuardrailValidator | None = None,
        integrity_verifier: ArtifactIntegrityVerifier | None = None,
        materialization_policy: ArtifactMaterializationPolicy | None = None,
        policy_resolver: ArtifactMaterializationPolicyResolver | None = None,
    ) -> None:
        self._dataset_context_builder = dataset_context_builder or DatasetContextBuilder()
        self._storage_factory = storage_factory or DataRegistryStorageFactory()
        self._adapters = adapters
        self._guardrail_validator = guardrail_validator or ArtifactGuardrailValidator()
        self._integrity_verifier = integrity_verifier or ArtifactIntegrityVerifier()
        self._materialization_policy = materialization_policy or ArtifactMaterializationPolicy()
        self._policy_resolver = policy_resolver or ArtifactMaterializationPolicyResolver()

    def build_context(
        self,
        *,
        artifact_uri: str,
        preview_limit: int = 10,
        storage_provider_key: str | None = None,
        artifact_metadata: dict[str, object] | None = None,
        expected_checksum: str | None = None,
        tenant_id: str | None = None,
        dataset_id: str | None = None,
        materialization_policy: ArtifactMaterializationPolicy | None = None,
    ) -> dict[str, Any]:
        result = self.build_context_with_integrity(
            artifact_uri=artifact_uri,
            preview_limit=preview_limit,
            storage_provider_key=storage_provider_key,
            artifact_metadata=artifact_metadata,
            expected_checksum=expected_checksum,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            materialization_policy=materialization_policy,
        )
        return result.context

    def build_context_with_integrity(
        self,
        *,
        artifact_uri: str,
        preview_limit: int = 10,
        storage_provider_key: str | None = None,
        artifact_metadata: dict[str, object] | None = None,
        expected_checksum: str | None = None,
        tenant_id: str | None = None,
        dataset_id: str | None = None,
        materialization_policy: ArtifactMaterializationPolicy | None = None,
    ) -> ArtifactContextBuildResult:
        if "sig=" in artifact_uri.lower():
            raise UnsafeStorageUriError("SAS-bearing URI is unsafe for artifact context reads.")
        artifact_format = self._guess_format(artifact_uri=artifact_uri)
        artifact_size = self._resolve_artifact_size(
            artifact_uri=artifact_uri,
            metadata=artifact_metadata,
        )
        resolved_policy = self._resolve_policy(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            materialization_policy=materialization_policy,
        )
        policy_source = (
            str(resolved_policy.provenance.source)
            if resolved_policy.provenance is not None
            else None
        )
        policy_revision = (
            resolved_policy.provenance.revision if resolved_policy.provenance is not None else None
        )
        self._guardrail_validator.validate_before_materialization(
            uri=artifact_uri,
            artifact_format=artifact_format,
            policy=resolved_policy.policy,
            size_bytes=artifact_size,
            storage_provider=storage_provider_key,
            metadata=artifact_metadata,
            checksum=expected_checksum,
            policy_source=policy_source,
            policy_revision=policy_revision,
        )
        enforcement_context = enforcement_allow(
            policy_key="artifact_materialization_default",
            policy_source=policy_source,
            policy_revision=policy_revision,
            reason="guardrails_passed",
        )
        adapter = self._resolve_adapter(
            artifact_uri=artifact_uri,
            storage_provider_key=storage_provider_key,
        )
        with adapter.materialize_to_temp(artifact_uri) as temporary_artifact:
            integrity_result: IntegrityVerificationResult | None = None
            if expected_checksum:
                integrity_result = self._integrity_verifier.verify_sha256(
                    temporary_artifact.local_path,
                    expected_checksum,
                )
            context = self._dataset_context_builder.build_context(
                temporary_artifact.local_path,
                preview_limit=preview_limit,
            )
            return ArtifactContextBuildResult(
                context=context,
                integrity_verification=integrity_result,
                policy_provenance=resolved_policy.provenance,
                enforcement_context=enforcement_context,
            )

    def _resolve_policy(
        self,
        *,
        tenant_id: str | None,
        dataset_id: str | None,
        materialization_policy: ArtifactMaterializationPolicy | None,
    ) -> ResolvedArtifactMaterializationPolicy:
        if materialization_policy is not None:
            return ResolvedArtifactMaterializationPolicy(
                policy=materialization_policy,
                provenance=None,
            )
        if tenant_id:
            return self._policy_resolver.resolve_with_provenance(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
            )
        return ResolvedArtifactMaterializationPolicy(
            policy=self._materialization_policy,
            provenance=None,
        )

    def _resolve_adapter(
        self,
        *,
        artifact_uri: str,
        storage_provider_key: str | None,
    ) -> ArtifactReadAdapter:
        adapters = self._adapters or self._build_default_adapters(
            storage_provider_key=storage_provider_key,
        )
        for adapter in adapters:
            if adapter.can_handle(artifact_uri):
                return adapter
        raise UnsupportedArtifactReadError(f"No adapter supports artifact URI: {artifact_uri}")

    def _build_default_adapters(
        self,
        *,
        storage_provider_key: str | None,
    ) -> list[ArtifactReadAdapter]:
        adapters: list[ArtifactReadAdapter] = []
        local_provider = self._storage_factory.create_provider("local")
        adapters.append(LocalArtifactReadAdapter(storage_provider=local_provider))

        if storage_provider_key:
            provider = self._storage_factory.create_provider(storage_provider_key)
            schemes = self._schemes_for_provider(provider_key=storage_provider_key)
            adapters.append(
                StorageProviderArtifactReadAdapter(
                    storage_provider=provider,
                    schemes=schemes,
                ),
            )
        return adapters

    def _schemes_for_provider(self, *, provider_key: str) -> tuple[str, ...]:
        normalized = provider_key.strip().lower()
        if normalized == "azure_blob":
            return ("azure://", "https://")
        if normalized == "local":
            return ("local://",)
        raise ValidationError(f"Unsupported storage provider key '{provider_key}'.")

    def _guess_format(self, *, artifact_uri: str) -> str:
        suffix = Path(artifact_uri).suffix.lower()
        if suffix == ".csv":
            return "csv"
        if suffix == ".parquet":
            return "parquet"
        if suffix == ".xlsx":
            return "xlsx"
        return normalize_dataset_format(suffix)

    def _resolve_artifact_size(
        self,
        *,
        artifact_uri: str,
        metadata: dict[str, object] | None,
    ) -> int | None:
        if metadata is not None and "size_bytes" in metadata:
            raw_size = metadata.get("size_bytes")
            if raw_size is None:
                return None
            return int(raw_size)
        path = Path(artifact_uri)
        if path.exists() and path.is_file():
            return int(path.stat().st_size)
        return None
