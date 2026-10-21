from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import Dataset
from resource_registry.policies.defaults import get_system_default_policy
from resource_registry.policies.models import ResolvedPolicy
from resource_registry.policies.resolution import PolicyResolutionService
from resource_registry.read_auth.classifications import DataClassification, normalize_classification
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import InvalidVisibilityPolicyError

VISIBILITY_POLICY_KEY = "schema_profile_visibility_default"

DATASET_SCHEMA_FULL_READ = "DATASET_SCHEMA_FULL_READ"
DATASET_PROFILE_FULL_READ = "DATASET_PROFILE_FULL_READ"
DATASET_SCHEMA_MASKED_READ = "DATASET_SCHEMA_MASKED_READ"
DATASET_PROFILE_MASKED_READ = "DATASET_PROFILE_MASKED_READ"


@dataclass(frozen=True)
class RoleVisibilityPermissions:
    can_view_all_schema: bool
    can_view_all_profile: bool


@dataclass(frozen=True)
class SchemaProfileVisibilityPolicy:
    hidden_columns: tuple[str, ...]
    masked_columns: tuple[str, ...]
    allowed_columns: tuple[str, ...] | None
    hidden_schema_classifications: tuple[str, ...]
    masked_schema_classifications: tuple[str, ...]
    hidden_metrics: tuple[str, ...]
    masked_metrics: tuple[str, ...]
    allowed_metrics: tuple[str, ...] | None
    hidden_profile_classifications: tuple[str, ...]
    masked_profile_classifications: tuple[str, ...]
    role_permissions: dict[str, RoleVisibilityPermissions]


@dataclass(frozen=True)
class ResolvedVisibilityPolicy:
    policy: SchemaProfileVisibilityPolicy
    source: str
    revision: int | None


class SchemaProfileVisibilityResolver:
    """Resolves and validates effective schema/profile visibility policy."""

    def __init__(self, policy_resolution_service: PolicyResolutionService | None = None) -> None:
        self._policy_resolution_service = policy_resolution_service or PolicyResolutionService()

    def resolve_policy(
        self,
        *,
        tenant_id: str,
        dataset_id: str | None,
    ) -> ResolvedVisibilityPolicy:
        dataset = None
        if dataset_id:
            try:
                dataset = Dataset.objects.filter(
                    build_dataset_visibility_filter(tenant_id=tenant_id),
                    id=dataset_id,
                ).first()
            except Exception:
                dataset = None

        resolved: ResolvedPolicy
        if dataset is None:
            resolved = ResolvedPolicy(
                source="system_default",
                policy_key=VISIBILITY_POLICY_KEY,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                revision=None,
                policy_id=None,
                rules_json=get_system_default_policy(VISIBILITY_POLICY_KEY),
            )
        else:
            resolved = self._policy_resolution_service.resolve_effective_policy(
                dataset=dataset,
                policy_key=VISIBILITY_POLICY_KEY,
            )

        return ResolvedVisibilityPolicy(
            policy=self._parse_policy(resolved.rules_json),
            source=resolved.source,
            revision=resolved.revision,
        )

    def _parse_policy(self, rules_json: dict[str, Any]) -> SchemaProfileVisibilityPolicy:
        if not isinstance(rules_json, dict):
            raise InvalidVisibilityPolicyError("Visibility policy rules must be an object.")

        schema_rules = rules_json.get("schema")
        profile_rules = rules_json.get("profile")
        roles_rules = rules_json.get("roles")

        if not isinstance(schema_rules, dict):
            raise InvalidVisibilityPolicyError("Visibility policy requires 'schema' object.")
        if not isinstance(profile_rules, dict):
            raise InvalidVisibilityPolicyError("Visibility policy requires 'profile' object.")
        if not isinstance(roles_rules, dict):
            raise InvalidVisibilityPolicyError("Visibility policy requires 'roles' object.")

        role_permissions: dict[str, RoleVisibilityPermissions] = {}
        for role, role_rule in roles_rules.items():
            if not isinstance(role, str) or not isinstance(role_rule, dict):
                raise InvalidVisibilityPolicyError("Invalid role visibility rule format.")
            schema_flag = role_rule.get("can_view_all_schema")
            profile_flag = role_rule.get("can_view_all_profile")
            if not isinstance(schema_flag, bool) or not isinstance(profile_flag, bool):
                raise InvalidVisibilityPolicyError("Role visibility flags must be boolean.")
            role_permissions[role] = RoleVisibilityPermissions(
                can_view_all_schema=schema_flag,
                can_view_all_profile=profile_flag,
            )

        return SchemaProfileVisibilityPolicy(
            hidden_columns=self._parse_str_list(
                schema_rules.get("hidden_columns"),
                "hidden_columns",
            ),
            masked_columns=self._parse_str_list(
                schema_rules.get("masked_columns"),
                "masked_columns",
            ),
            allowed_columns=self._parse_optional_str_list(
                schema_rules.get("allowed_columns"),
                "allowed_columns",
            ),
            hidden_schema_classifications=self._parse_classification_list(
                schema_rules.get("hidden_classifications"),
                "schema.hidden_classifications",
            ),
            masked_schema_classifications=self._parse_classification_list(
                schema_rules.get("masked_classifications"),
                "schema.masked_classifications",
            ),
            hidden_metrics=self._parse_str_list(
                profile_rules.get("hidden_metrics"),
                "hidden_metrics",
            ),
            masked_metrics=self._parse_str_list(
                profile_rules.get("masked_metrics"),
                "masked_metrics",
            ),
            allowed_metrics=self._parse_optional_str_list(
                profile_rules.get("allowed_metrics"),
                "allowed_metrics",
            ),
            hidden_profile_classifications=self._parse_classification_list(
                profile_rules.get("hidden_classifications"),
                "profile.hidden_classifications",
            ),
            masked_profile_classifications=self._parse_classification_list(
                profile_rules.get("masked_classifications"),
                "profile.masked_classifications",
            ),
            role_permissions=role_permissions,
        )

    def _parse_str_list(self, value: Any, field_name: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise InvalidVisibilityPolicyError(f"{field_name} must be a list of strings.")
        return tuple(value)

    def _parse_optional_str_list(self, value: Any, field_name: str) -> tuple[str, ...] | None:
        if value is None:
            return None
        return self._parse_str_list(value, field_name)

    def _parse_classification_list(self, value: Any, field_name: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise InvalidVisibilityPolicyError(f"{field_name} must be a list of strings.")
        allowed = {item.value for item in DataClassification}
        normalized: list[str] = []
        for item in value:
            normalized_item = normalize_classification(item)
            if normalized_item not in allowed:
                raise InvalidVisibilityPolicyError(
                    f"Unsupported classification in {field_name}: {item}.",
                )
            normalized.append(normalized_item)
        return tuple(normalized)


def can_view_all_schema(
    *,
    context: ReadAuthorizationContext,
    policy: SchemaProfileVisibilityPolicy,
) -> bool:
    if DATASET_SCHEMA_FULL_READ in context.scopes:
        return True
    return any(
        policy.role_permissions.get(
            role,
            RoleVisibilityPermissions(False, False),
        ).can_view_all_schema
        for role in context.roles
    )


def can_view_all_profile(
    *,
    context: ReadAuthorizationContext,
    policy: SchemaProfileVisibilityPolicy,
) -> bool:
    if DATASET_PROFILE_FULL_READ in context.scopes:
        return True
    return any(
        policy.role_permissions.get(
            role,
            RoleVisibilityPermissions(False, False),
        ).can_view_all_profile
        for role in context.roles
    )
