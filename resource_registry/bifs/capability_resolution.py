from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ValidationError

from resource_registry.bifs.exceptions import (
    InvalidCapabilityPolicyError,
    MissingCapabilityContextError,
)
from resource_registry.bifs.manifest import RESOURCE_REGISTRY_BIF_MANIFEST
from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.events.audit_service import AuditService
from resource_registry.models import Dataset
from resource_registry.policies.defaults import get_system_default_policy
from resource_registry.policies.resolution import PolicyResolutionService

_POLICY_KEY = "resource_registry_bif_capabilities_default"
_VALID_ROLE_KEYS = frozenset({"admin", "reader", "restricted_reader"})


@dataclass(frozen=True)
class ResolvedBifCapabilities:
    """Resolved capability contract from policy + session context."""

    tenant_id: str
    policy_source: str
    policy_revision: int | None
    enabled_capabilities: frozenset[str]
    disabled_capabilities: frozenset[str]
    effective_roles: tuple[str, ...]
    effective_scopes: tuple[str, ...]
    resolution_path: tuple[str, ...] = field(default_factory=tuple)


class DataRegistryBifCapabilityResolver:
    """Resolves tenant-scoped Resource Registry BIF capabilities."""

    def __init__(
        self,
        *,
        policy_resolution_service: PolicyResolutionService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self._policy_resolution_service = policy_resolution_service or PolicyResolutionService()
        self._audit_service = audit_service or AuditService()
        self._known_capability_keys = frozenset(
            entry.capability_key for entry in RESOURCE_REGISTRY_BIF_MANIFEST.values()
        )

    def resolve(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        actor: str | None = None,
        roles: tuple[str, ...] = (),
        scopes: tuple[str, ...] = (),
        session_enabled_capabilities: frozenset[str] | None = None,
        session_disabled_capabilities: frozenset[str] | None = None,
        dataset_id: str | None = None,
    ) -> ResolvedBifCapabilities:
        if not tenant_id:
            raise MissingCapabilityContextError("tenant_id is required.")

        safe_roles = tuple(str(role) for role in roles)
        safe_scopes = tuple(str(scope) for scope in scopes)
        session_enabled = frozenset(session_enabled_capabilities or frozenset())
        session_disabled = frozenset(session_disabled_capabilities or frozenset())

        system_rules = get_system_default_policy(_POLICY_KEY)
        self._validate_policy_rules(system_rules, source="system_default")

        effective_rules = self._normalize_policy_rules(system_rules)
        policy_source = "system_default"
        policy_revision: int | None = None
        resolution_path: list[str] = ["system_default"]

        tenant_policy = self._policy_resolution_service.resolve_tenant_policy(
            tenant_id=str(tenant_id),
            policy_key=_POLICY_KEY,
        )
        if tenant_policy is not None:
            tenant_rules = dict(tenant_policy.rules_json or {})
            self._validate_policy_rules(tenant_rules, source="tenant_default")
            effective_rules = self._merge_policy_rules(
                base=effective_rules,
                override=self._normalize_policy_rules(tenant_rules),
            )
            policy_source = "tenant_default"
            policy_revision = tenant_policy.revision
            resolution_path.append("tenant_default")

        if dataset_id:
            dataset = self._get_tenant_dataset(tenant_id=str(tenant_id), dataset_id=str(dataset_id))
            if dataset is not None:
                dataset_policy = self._policy_resolution_service.resolve_dataset_policy(
                    dataset=dataset,
                    policy_key=_POLICY_KEY,
                )
                if dataset_policy is not None:
                    dataset_rules = dict(dataset_policy.rules_json or {})
                    self._validate_policy_rules(dataset_rules, source="dataset_override")
                    effective_rules = self._merge_policy_rules(
                        base=effective_rules,
                        override=self._normalize_policy_rules(dataset_rules),
                    )
                    policy_source = "dataset_override"
                    policy_revision = dataset_policy.revision
                    resolution_path.append("dataset_override")

        enabled_capabilities, disabled_capabilities = self._resolve_for_roles(
            normalized_rules=effective_rules,
            roles=safe_roles,
        )

        enabled_capabilities = enabled_capabilities | session_enabled
        disabled_capabilities = disabled_capabilities | session_disabled
        enabled_capabilities = enabled_capabilities - disabled_capabilities

        resolved = ResolvedBifCapabilities(
            tenant_id=str(tenant_id),
            policy_source=policy_source,
            policy_revision=policy_revision,
            enabled_capabilities=enabled_capabilities,
            disabled_capabilities=disabled_capabilities,
            effective_roles=safe_roles,
            effective_scopes=safe_scopes,
            resolution_path=tuple(resolution_path),
        )

        self._safe_audit(
            lambda: self._audit_service.record_bif_capabilities_resolved(
                tenant_id=str(tenant_id),
                actor=actor,
                metadata={
                    "policy_source": resolved.policy_source,
                    "policy_revision": resolved.policy_revision,
                    "resolution_path": list(resolved.resolution_path),
                },
            ),
        )

        return resolved

    def _resolve_for_roles(
        self,
        *,
        normalized_rules: dict[str, Any],
        roles: tuple[str, ...],
    ) -> tuple[frozenset[str], frozenset[str]]:
        enabled = set(normalized_rules["enabled_capabilities"])
        disabled = set(normalized_rules["disabled_capabilities"])
        role_overrides = normalized_rules["role_overrides"]

        for role in roles:
            role_config = role_overrides.get(role)
            if role_config is None:
                continue
            role_enabled = set(role_config["enabled_capabilities"])
            role_disabled = set(role_config["disabled_capabilities"])
            if "*" in role_enabled:
                if role != "admin":
                    raise InvalidCapabilityPolicyError(
                        "Wildcard capability is only valid for admin.",
                    )
                role_enabled.remove("*")
                role_enabled |= set(self._known_capability_keys)
            enabled |= role_enabled
            disabled |= role_disabled

        enabled -= disabled
        return frozenset(enabled), frozenset(disabled)

    def _merge_policy_rules(
        self,
        *,
        base: dict[str, Any],
        override: dict[str, Any],
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {
            "enabled_capabilities": list(base["enabled_capabilities"]),
            "disabled_capabilities": list(base["disabled_capabilities"]),
            "role_overrides": {
                role: {
                    "enabled_capabilities": list(config["enabled_capabilities"]),
                    "disabled_capabilities": list(config["disabled_capabilities"]),
                }
                for role, config in base["role_overrides"].items()
            },
        }

        merged["enabled_capabilities"] = list(
            set(merged["enabled_capabilities"]) | set(override["enabled_capabilities"]),
        )
        merged["disabled_capabilities"] = list(
            set(merged["disabled_capabilities"]) | set(override["disabled_capabilities"]),
        )

        for role, role_config in override["role_overrides"].items():
            current = merged["role_overrides"].get(
                role,
                {"enabled_capabilities": [], "disabled_capabilities": []},
            )
            current["enabled_capabilities"] = list(
                set(current["enabled_capabilities"]) | set(role_config["enabled_capabilities"]),
            )
            current["disabled_capabilities"] = list(
                set(current["disabled_capabilities"]) | set(role_config["disabled_capabilities"]),
            )
            merged["role_overrides"][role] = current

        return merged

    def _normalize_policy_rules(self, rules: dict[str, Any]) -> dict[str, Any]:
        role_overrides = rules.get("role_overrides") or {}
        normalized_role_overrides: dict[str, dict[str, list[str]]] = {}
        for role, role_config in role_overrides.items():
            config = role_config or {}
            normalized_role_overrides[str(role)] = {
                "enabled_capabilities": [
                    str(item) for item in config.get("enabled_capabilities", [])
                ],
                "disabled_capabilities": [
                    str(item) for item in config.get("disabled_capabilities", [])
                ],
            }

        return {
            "enabled_capabilities": [str(item) for item in rules.get("enabled_capabilities", [])],
            "disabled_capabilities": [str(item) for item in rules.get("disabled_capabilities", [])],
            "role_overrides": normalized_role_overrides,
        }

    def _validate_policy_rules(self, rules: dict[str, Any], *, source: str) -> None:
        if not isinstance(rules.get("enabled_capabilities", []), list):
            raise InvalidCapabilityPolicyError(f"{source}: enabled_capabilities must be a list.")
        if not isinstance(rules.get("disabled_capabilities", []), list):
            raise InvalidCapabilityPolicyError(f"{source}: disabled_capabilities must be a list.")

        role_overrides = rules.get("role_overrides", {})
        if not isinstance(role_overrides, dict):
            raise InvalidCapabilityPolicyError(f"{source}: role_overrides must be a dict.")

        self._validate_capability_values(
            rules.get("enabled_capabilities", []),
            allow_wildcard=False,
            source=source,
            role=None,
        )
        self._validate_capability_values(
            rules.get("disabled_capabilities", []),
            allow_wildcard=False,
            source=source,
            role=None,
        )

        for role, role_config in role_overrides.items():
            if role not in _VALID_ROLE_KEYS:
                raise InvalidCapabilityPolicyError(f"{source}: unsupported role override '{role}'.")
            if not isinstance(role_config, dict):
                raise InvalidCapabilityPolicyError(
                    f"{source}: role override '{role}' must be a dict.",
                )
            enabled = role_config.get("enabled_capabilities", [])
            disabled = role_config.get("disabled_capabilities", [])
            if not isinstance(enabled, list):
                raise InvalidCapabilityPolicyError(
                    f"{source}: role '{role}' enabled_capabilities must be a list.",
                )
            if not isinstance(disabled, list):
                raise InvalidCapabilityPolicyError(
                    f"{source}: role '{role}' disabled_capabilities must be a list.",
                )
            self._validate_capability_values(
                enabled,
                allow_wildcard=role == "admin",
                source=source,
                role=role,
            )
            self._validate_capability_values(
                disabled,
                allow_wildcard=False,
                source=source,
                role=role,
            )

    def _validate_capability_values(
        self,
        values: list[Any],
        *,
        allow_wildcard: bool,
        source: str,
        role: str | None,
    ) -> None:
        for value in values:
            if not isinstance(value, str):
                raise InvalidCapabilityPolicyError(
                    f"{source}: capability entries must be strings (role={role}).",
                )
            if value == "*":
                if not allow_wildcard:
                    raise InvalidCapabilityPolicyError(
                        f"{source}: wildcard '*' is only allowed for admin role overrides.",
                    )
                continue
            if value not in self._known_capability_keys:
                raise InvalidCapabilityPolicyError(
                    f"{source}: unknown capability '{value}'.",
                )

    @staticmethod
    def _get_tenant_dataset(*, tenant_id: str, dataset_id: str) -> Dataset | None:
        try:
            return Dataset.objects.get(
                build_dataset_visibility_filter(tenant_id=tenant_id),
                id=dataset_id,
            )
        except (Dataset.DoesNotExist, ValidationError, ValueError):
            return None

    @staticmethod
    def _safe_audit(audit_call: Callable[[], object]) -> None:
        try:
            audit_call()
        except Exception:
            return
