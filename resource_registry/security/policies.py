from __future__ import annotations

from dataclasses import dataclass, field

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import Dataset
from resource_registry.policies.defaults import get_system_default_policy
from resource_registry.policies.exceptions import InvalidPolicyConfigurationError
from resource_registry.policies.models import ResolvedPolicy
from resource_registry.policies.provenance import (
    PolicyProvenance,
    ResolvedPolicyWithProvenance,
    build_policy_provenance,
)
from resource_registry.policies.resolution import PolicyResolutionService

ARTIFACT_MATERIALIZATION_POLICY_KEY = "artifact_materialization_default"
SUPPORTED_ARTIFACT_FORMATS = frozenset({"csv", "json", "jsonl", "parquet", "xlsx"})
SUPPORTED_STORAGE_PROVIDERS = frozenset({"local", "azure_blob"})


@dataclass(frozen=True)
class ArtifactMaterializationPolicy:
    """Policy contract for safe artifact materialization."""

    max_size_bytes: int = 1_073_741_824
    allowed_formats: frozenset[str] = field(
        default_factory=lambda: frozenset({"csv", "json", "jsonl", "parquet", "xlsx"}),
    )
    require_checksum: bool = False
    allowed_storage_providers: frozenset[str] = field(
        default_factory=lambda: frozenset({"local", "azure_blob"}),
    )
    allow_local_files: bool = False
    allow_remote_uris: bool = True


class ArtifactMaterializationPolicyResolver:
    """Resolve tenant/dataset materialization rules with safe defaults."""

    def __init__(
        self,
        *,
        policy_resolution_service: PolicyResolutionService | None = None,
    ) -> None:
        self._policy_resolution_service = policy_resolution_service or PolicyResolutionService()

    def resolve_policy(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None = None,
    ) -> ArtifactMaterializationPolicy:
        resolved = self.resolve_with_provenance(tenant_id=tenant_id, dataset_id=dataset_id)
        return resolved.policy

    def resolve_with_provenance(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None = None,
    ) -> ResolvedArtifactMaterializationPolicy:
        resolved_with_provenance = self._resolve_policy_with_provenance(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
        )
        merged_rules = {
            **get_system_default_policy(ARTIFACT_MATERIALIZATION_POLICY_KEY),
            **resolved_with_provenance.resolved_policy.rules_json,
        }
        policy = self._parse_rules(merged_rules)
        return ResolvedArtifactMaterializationPolicy(
            policy=policy,
            provenance=resolved_with_provenance.provenance,
        )

    def _resolve_policy_with_provenance(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
    ) -> ResolvedPolicyWithProvenance:
        default_rules = get_system_default_policy(ARTIFACT_MATERIALIZATION_POLICY_KEY)
        system_default_policy = _build_system_default_resolved_policy(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            rules_json=default_rules,
        )
        if dataset_id:
            dataset = Dataset.objects.filter(
                build_dataset_visibility_filter(tenant_id=tenant_id),
                id=dataset_id,
            ).first()
            if dataset is None:
                raise InvalidPolicyConfigurationError(
                    "Dataset not found or tenant mismatch for policy resolution.",
                )
            if hasattr(self._policy_resolution_service, "resolve_effective_policy_with_provenance"):
                resolved = self._policy_resolution_service.resolve_effective_policy_with_provenance(
                    dataset=dataset,
                    policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
                )
            else:
                legacy_resolved = self._policy_resolution_service.resolve_effective_policy(
                    dataset=dataset,
                    policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
                )
                resolved = ResolvedPolicyWithProvenance(
                    resolved_policy=legacy_resolved,
                    provenance=build_policy_provenance(
                        resolved_policy=legacy_resolved,
                        resolution_path=["dataset_override", "tenant_default", "system_default"],
                    ),
                )
            return resolved

        resolved_tenant = None
        if hasattr(self._policy_resolution_service, "resolve_tenant_policy_with_provenance"):
            resolved_tenant = self._policy_resolution_service.resolve_tenant_policy_with_provenance(
                tenant_id=tenant_id,
                policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            )
        if resolved_tenant is None and hasattr(
            self._policy_resolution_service,
            "resolve_tenant_policy",
        ):
            legacy_tenant = self._policy_resolution_service.resolve_tenant_policy(
                tenant_id=tenant_id,
                policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
            )
            if legacy_tenant is not None:
                resolved_tenant = ResolvedPolicyWithProvenance(
                    resolved_policy=legacy_tenant,
                    provenance=build_policy_provenance(
                        resolved_policy=legacy_tenant,
                        resolution_path=["tenant_default", "system_default"],
                    ),
                )
        if resolved_tenant is not None:
            return resolved_tenant
        return ResolvedPolicyWithProvenance(
            resolved_policy=system_default_policy,
            provenance=build_policy_provenance(
                resolved_policy=system_default_policy,
                resolution_path=["tenant_default", "system_default"],
            ),
        )

    def _parse_rules(self, rules: dict[str, object]) -> ArtifactMaterializationPolicy:
        allowed_keys = {
            "max_size_bytes",
            "allowed_formats",
            "require_checksum",
            "allowed_storage_providers",
            "allow_local_files",
            "allow_remote_uris",
        }
        unknown_keys = set(rules.keys()) - allowed_keys
        if unknown_keys:
            unknown = ", ".join(sorted(unknown_keys))
            raise InvalidPolicyConfigurationError(
                f"Unknown artifact materialization policy keys: {unknown}.",
            )

        max_size_bytes = self._parse_positive_int(
            rules.get("max_size_bytes"),
            field_name="max_size_bytes",
        )
        allowed_formats = self._parse_supported_values(
            rules.get("allowed_formats"),
            field_name="allowed_formats",
            allowed_values=SUPPORTED_ARTIFACT_FORMATS,
        )
        allowed_storage_providers = self._parse_supported_values(
            rules.get("allowed_storage_providers"),
            field_name="allowed_storage_providers",
            allowed_values=SUPPORTED_STORAGE_PROVIDERS,
        )
        require_checksum = self._parse_bool(
            rules.get("require_checksum"),
            field_name="require_checksum",
        )
        allow_local_files = self._parse_bool(
            rules.get("allow_local_files"),
            field_name="allow_local_files",
        )
        allow_remote_uris = self._parse_bool(
            rules.get("allow_remote_uris"),
            field_name="allow_remote_uris",
        )

        return ArtifactMaterializationPolicy(
            max_size_bytes=max_size_bytes,
            allowed_formats=allowed_formats,
            require_checksum=require_checksum,
            allowed_storage_providers=allowed_storage_providers,
            allow_local_files=allow_local_files,
            allow_remote_uris=allow_remote_uris,
        )

    def _parse_positive_int(self, value: object, *, field_name: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise InvalidPolicyConfigurationError(
                f"'{field_name}' must be a positive integer.",
            )
        return int(value)

    def _parse_supported_values(
        self,
        value: object,
        *,
        field_name: str,
        allowed_values: frozenset[str],
    ) -> frozenset[str]:
        if not isinstance(value, list) or not value:
            raise InvalidPolicyConfigurationError(
                f"'{field_name}' must be a non-empty list.",
            )
        normalized: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise InvalidPolicyConfigurationError(
                    f"'{field_name}' values must be strings.",
                )
            normalized_item = item.strip().lower()
            if normalized_item not in allowed_values:
                raise InvalidPolicyConfigurationError(
                    f"Unsupported '{field_name}' value '{item}'.",
                )
            normalized.add(normalized_item)
        if not normalized:
            raise InvalidPolicyConfigurationError(
                f"'{field_name}' must contain at least one value.",
            )
        return frozenset(normalized)

    def _parse_bool(self, value: object, *, field_name: str) -> bool:
        if not isinstance(value, bool):
            raise InvalidPolicyConfigurationError(f"'{field_name}' must be a boolean.")
        return value


@dataclass(frozen=True)
class ResolvedArtifactMaterializationPolicy:
    policy: ArtifactMaterializationPolicy
    provenance: PolicyProvenance | None


def _build_system_default_resolved_policy(
    *,
    tenant_id: str,
    dataset_id: str | None,
    rules_json: dict[str, object],
) -> ResolvedPolicy:
    return ResolvedPolicy(
        source="system_default",
        policy_key=ARTIFACT_MATERIALIZATION_POLICY_KEY,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        revision=None,
        policy_id=None,
        rules_json=dict(rules_json),
    )
