"""Audit and upgrade exact copies of prior Registry reader and visibility policies."""

from __future__ import annotations

from typing import Literal, cast

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from resource_registry.models import DatasetPolicyAssignment, GovernancePolicy
from resource_registry.policies.configuration import PolicyConfigurationService
from resource_registry.policies.defaults import get_system_default_policy

_POLICY_KEY = "resource_registry_bif_capabilities_default"
_VISIBILITY_POLICY_KEY = "schema_profile_visibility_default"
_INTERACTIVE_CAPABILITIES = frozenset(
    {
        "resource_registry.dataset.query.read",
        "resource_registry.dataset.query.job.start",
        "resource_registry.dataset.query.job.read",
        "resource_registry.dataset.query.plan",
        "resource_registry.dataset.exploration.read",
    }
)
_NORMAL_ANALYSIS_CAPABILITIES = frozenset(
    {
        "resource_registry.dataset.analysis.read",
        "resource_registry.dataset.chart.read",
        "resource_registry.dataset.query_run.read",
        "resource_registry.dataset.analysis_artifact.read",
        "resource_registry.dataset.chart_artifact.read",
    }
)
_SAFE_READ_AND_PLANNING_CAPABILITIES = frozenset(
    {
        "resource_registry.dataset.intelligence.job.read",
        "resource_registry.dataset.export.plan",
        "resource_registry.dataset.export_plan.read",
        "resource_registry.transformation.run.read",
        "resource_registry.transformation.step.read",
        "resource_registry.transformation.output.read",
    }
)
PolicyClassification = Literal["legacy_copy", "explicit_denial", "unaffected"]


def _normalize_policy_lists(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _normalize_policy_lists(item) for key, item in value.items()}
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return value


def _build_prior_release_reader_default() -> dict[str, object]:
    previous_default = get_system_default_policy(_POLICY_KEY)
    role_overrides = previous_default["role_overrides"]
    assert isinstance(role_overrides, dict)
    restricted = role_overrides["restricted_reader"]
    assert isinstance(restricted, dict)
    enabled = restricted["enabled_capabilities"]
    disabled = restricted["disabled_capabilities"]
    assert isinstance(enabled, list) and isinstance(disabled, list)
    for capability in _SAFE_READ_AND_PLANNING_CAPABILITIES:
        enabled.remove(capability)
        disabled.append(capability)
    return previous_default


def _build_previous_reader_default() -> dict[str, object]:
    previous_default = _build_prior_release_reader_default()
    role_overrides = previous_default["role_overrides"]
    assert isinstance(role_overrides, dict)
    restricted = role_overrides["restricted_reader"]
    assert isinstance(restricted, dict)
    enabled = restricted["enabled_capabilities"]
    disabled = restricted["disabled_capabilities"]
    assert isinstance(enabled, list) and isinstance(disabled, list)
    for capability in _NORMAL_ANALYSIS_CAPABILITIES:
        enabled.remove(capability)
        disabled.append(capability)
    return previous_default


def _build_legacy_default() -> dict[str, object]:
    legacy_default = _build_previous_reader_default()
    role_overrides = legacy_default["role_overrides"]
    assert isinstance(role_overrides, dict)
    restricted = role_overrides["restricted_reader"]
    assert isinstance(restricted, dict)
    enabled = restricted["enabled_capabilities"]
    disabled = restricted["disabled_capabilities"]
    assert isinstance(enabled, list) and isinstance(disabled, list)
    for capability in _INTERACTIVE_CAPABILITIES:
        enabled.remove(capability)
        disabled.append(capability)
    return legacy_default


def _build_previous_visibility_default() -> dict[str, object]:
    previous_default = get_system_default_policy(_VISIBILITY_POLICY_KEY)
    roles = previous_default["roles"]
    assert isinstance(roles, dict)
    for role_name in ("reader", "restricted_reader"):
        role = roles[role_name]
        assert isinstance(role, dict)
        role["can_view_all_profile"] = False
    return previous_default


def _build_legacy_visibility_default() -> dict[str, object]:
    legacy_default = _build_previous_visibility_default()
    roles = legacy_default["roles"]
    assert isinstance(roles, dict)
    restricted = roles["restricted_reader"]
    assert isinstance(restricted, dict)
    restricted["can_view_all_schema"] = False
    return legacy_default


def classify_reader_policy(rules: dict[str, object]) -> PolicyClassification:
    """Identify only unmodified copies of prior restricted-reader defaults.

    Args:
        rules: Persisted GovernancePolicy rules.

    Returns:
        Legacy copy, explicit denial, or unaffected classification.
    """
    normalized = _normalize_policy_lists(rules)
    for previous_default in (
        _build_legacy_default(),
        _build_previous_reader_default(),
        _build_prior_release_reader_default(),
    ):
        previous_roles = previous_default["role_overrides"]
        assert isinstance(previous_roles, dict)
        previous_restricted = previous_roles["restricted_reader"]
        role_only = {"role_overrides": {"restricted_reader": previous_restricted}}
        role_only_with_empty_lists = {
            **role_only,
            "enabled_capabilities": [],
            "disabled_capabilities": [],
        }
        if normalized in (
            _normalize_policy_lists(previous_default),
            _normalize_policy_lists(role_only),
            _normalize_policy_lists(role_only_with_empty_lists),
        ):
            return "legacy_copy"

    overrides = rules.get("role_overrides")
    restricted = overrides.get("restricted_reader") if isinstance(overrides, dict) else None
    disabled = restricted.get("disabled_capabilities", []) if isinstance(restricted, dict) else []
    expanded_capabilities = (
        _INTERACTIVE_CAPABILITIES
        | _NORMAL_ANALYSIS_CAPABILITIES
        | _SAFE_READ_AND_PLANNING_CAPABILITIES
    )
    if isinstance(disabled, list) and expanded_capabilities.intersection(disabled):
        return "explicit_denial"
    global_disabled = rules.get("disabled_capabilities", [])
    if isinstance(global_disabled, list) and expanded_capabilities.intersection(
        global_disabled,
    ):
        return "explicit_denial"
    return "unaffected"


def build_upgraded_reader_rules(rules: dict[str, object]) -> dict[str, object]:
    """Replace an exact legacy copy with the current normal-reader policy.

    Args:
        rules: Persisted rules already classified as a legacy copy.

    Returns:
        Equivalent rules with the normal-reader capabilities enabled.

    Raises:
        ValueError: If the rules contain custom policy decisions.
    """
    if classify_reader_policy(rules) != "legacy_copy":
        raise ValueError("Only an exact legacy reader policy copy can be upgraded.")
    current_default = get_system_default_policy(_POLICY_KEY)
    if "enabled_capabilities" in rules and rules.get("enabled_capabilities"):
        return current_default
    current_roles = current_default["role_overrides"]
    assert isinstance(current_roles, dict)
    return {
        "role_overrides": {
            "restricted_reader": current_roles["restricted_reader"],
        },
        **(
            {"enabled_capabilities": [], "disabled_capabilities": []}
            if "enabled_capabilities" in rules
            else {}
        ),
    }


def classify_visibility_policy(rules: dict[str, object]) -> PolicyClassification:
    """Identify prior visibility defaults without changing custom restrictions.

    Args:
        rules: Persisted schema and profile visibility rules.

    Returns:
        Legacy copy, explicit denial, or unaffected classification.
    """
    normalized_rules = _normalize_policy_lists(rules)
    if normalized_rules in (
        _normalize_policy_lists(_build_legacy_visibility_default()),
        _normalize_policy_lists(_build_previous_visibility_default()),
    ):
        return "legacy_copy"
    roles = rules.get("roles")
    reader = roles.get("reader") if isinstance(roles, dict) else None
    restricted = roles.get("restricted_reader") if isinstance(roles, dict) else None
    if (
        isinstance(restricted, dict)
        and (
            restricted.get("can_view_all_schema") is False
            or restricted.get("can_view_all_profile") is False
        )
    ) or (isinstance(reader, dict) and reader.get("can_view_all_profile") is False):
        return "explicit_denial"
    return "unaffected"


def build_upgraded_visibility_rules(rules: dict[str, object]) -> dict[str, object]:
    """Replace only an exact legacy visibility policy copy.

    Args:
        rules: Persisted schema and profile visibility rules.

    Returns:
        Current schema and profile visibility defaults.

    Raises:
        ValueError: If the rules contain custom policy decisions.
    """
    if classify_visibility_policy(rules) != "legacy_copy":
        raise ValueError("Only an exact legacy visibility policy copy can be upgraded.")
    return get_system_default_policy(_VISIBILITY_POLICY_KEY)


class Command(BaseCommand):
    """Report policy copies and optionally create safe upgraded revisions."""

    help = "Audit Registry capability and visibility policies; --apply upgrades exact copies."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register a tenant filter and an explicit apply switch.

        Args:
            parser: Django command argument parser.
        """
        parser.add_argument("--tenant-id", type=str, default=None)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: object, **options: object) -> None:
        """Audit active policies and revise only exact legacy copies.

        Args:
            args: Unused positional command arguments.
            options: Parsed command options.
        """
        _ = args
        queryset = GovernancePolicy.objects.filter(
            policy_key__in=(_POLICY_KEY, _VISIBILITY_POLICY_KEY),
            is_active=True,
        )
        tenant_id = options.get("tenant_id")
        if tenant_id:
            queryset = queryset.filter(tenant_id=str(tenant_id))
        for policy_object in queryset.order_by("tenant_id", "revision"):
            policy = cast(GovernancePolicy, policy_object)
            rules = dict(policy.rules_json or {})
            classification = (
                classify_reader_policy(rules)
                if policy.policy_key == _POLICY_KEY
                else classify_visibility_policy(rules)
            )
            action = "retained"
            if classification == "legacy_copy":
                action = "candidate"
                if options.get("apply") is True:
                    self._upgrade_policy(policy)
                    action = "upgraded"
            self.stdout.write(
                f"tenant={policy.tenant_id} key={policy.policy_key} "
                f"policy={policy.id} revision={policy.revision} "
                f"classification={classification} action={action}",
            )

    @staticmethod
    def _upgrade_policy(policy: GovernancePolicy) -> None:
        """Create and activate a new revision while retaining policy history.

        Args:
            policy: Exact legacy policy copy to upgrade.

        Raises:
            ValueError: If the policy changed after it was classified.
        """
        with transaction.atomic():
            locked_policy = cast(
                GovernancePolicy,
                GovernancePolicy.objects.select_for_update().get(pk=policy.pk),
            )
            rules = dict(locked_policy.rules_json or {})
            upgraded_rules = (
                build_upgraded_reader_rules(rules)
                if locked_policy.policy_key == _POLICY_KEY
                else build_upgraded_visibility_rules(rules)
            )
            service = PolicyConfigurationService()
            new_policy = service.create_policy_revision(
                base_policy=locked_policy,
                rules_json=upgraded_rules,
                activate=True,
            )
            DatasetPolicyAssignment.objects.filter(governance_policy=locked_policy).update(
                governance_policy=new_policy,
            )
            if not locked_policy.is_tenant_default:
                service.deactivate_policy(policy=locked_policy)
