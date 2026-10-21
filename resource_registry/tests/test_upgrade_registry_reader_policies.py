"""Regression tests for safe reader policy upgrades."""

from __future__ import annotations

from copy import deepcopy
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command

from resource_registry.management.commands.upgrade_registry_reader_policies import (
    _build_legacy_default,
    _build_legacy_visibility_default,
    _build_previous_reader_default,
    _build_prior_release_reader_default,
    _build_previous_visibility_default,
    build_upgraded_reader_rules,
    build_upgraded_visibility_rules,
    classify_reader_policy,
    classify_visibility_policy,
)
from resource_registry.models import GovernancePolicy, GovernancePolicyType
from resource_registry.policies.defaults import get_system_default_policy

_POLICY_KEY = "resource_registry_bif_capabilities_default"


def test_exact_legacy_copy_is_upgraded_to_current_default() -> None:
    old_rules = _build_legacy_default()

    assert classify_reader_policy(old_rules) == "legacy_copy"
    assert build_upgraded_reader_rules(old_rules) == get_system_default_policy(_POLICY_KEY)


def test_previous_reader_default_is_upgraded_for_analysis_and_charts() -> None:
    """Upgrade an exact previous capability policy while retaining custom denials."""
    previous_default = _build_previous_reader_default()

    assert classify_reader_policy(previous_default) == "legacy_copy"
    upgraded = build_upgraded_reader_rules(previous_default)
    restricted = upgraded["role_overrides"]["restricted_reader"]
    assert "resource_registry.dataset.analysis.read" in restricted["enabled_capabilities"]
    assert "resource_registry.dataset.chart.read" in restricted["enabled_capabilities"]

    customized = deepcopy(previous_default)
    custom_restricted = customized["role_overrides"]["restricted_reader"]
    custom_restricted["disabled_capabilities"].append("resource_registry.dataset.schema.read")
    assert classify_reader_policy(customized) == "explicit_denial"
    with pytest.raises(ValueError):
        build_upgraded_reader_rules(customized)


def test_prior_release_reader_default_is_upgraded_for_safe_planning_and_reads() -> None:
    """Upgrade the immediately prior exact copy without changing custom denials."""
    previous_default = _build_prior_release_reader_default()

    assert classify_reader_policy(previous_default) == "legacy_copy"
    upgraded = build_upgraded_reader_rules(previous_default)
    restricted = upgraded["role_overrides"]["restricted_reader"]
    assert "resource_registry.dataset.export.plan" in restricted["enabled_capabilities"]
    assert "resource_registry.dataset.intelligence.job.read" in restricted["enabled_capabilities"]
    assert "resource_registry.transformation.run.read" in restricted["enabled_capabilities"]

    customized = deepcopy(previous_default)
    custom_restricted = customized["role_overrides"]["restricted_reader"]
    custom_restricted["disabled_capabilities"].append("resource_registry.dataset.schema.read")
    assert classify_reader_policy(customized) == "explicit_denial"
    with pytest.raises(ValueError):
        build_upgraded_reader_rules(customized)


def test_explicit_extra_denial_is_retained() -> None:
    old_rules = _build_legacy_default()
    custom_rules = deepcopy(old_rules)
    custom_roles = custom_rules["role_overrides"]
    assert isinstance(custom_roles, dict)
    restricted = custom_roles["restricted_reader"]
    assert isinstance(restricted, dict)
    disabled = restricted["disabled_capabilities"]
    assert isinstance(disabled, list)
    disabled.append("resource_registry.dataset.schema.read")

    assert classify_reader_policy(custom_rules) == "explicit_denial"
    with pytest.raises(ValueError):
        build_upgraded_reader_rules(custom_rules)


def test_partial_legacy_role_copy_is_upgraded_without_global_grants() -> None:
    old_roles = _build_legacy_default()["role_overrides"]
    assert isinstance(old_roles, dict)
    old_rules = {"role_overrides": {"restricted_reader": old_roles["restricted_reader"]}}

    upgraded = build_upgraded_reader_rules(old_rules)

    assert set(upgraded) == {"role_overrides"}
    current_roles = get_system_default_policy(_POLICY_KEY)["role_overrides"]
    assert isinstance(current_roles, dict)
    assert upgraded["role_overrides"] == {
        "restricted_reader": current_roles["restricted_reader"],
    }


def test_only_exact_legacy_visibility_copy_is_upgraded() -> None:
    old_rules = _build_legacy_visibility_default()
    assert classify_visibility_policy(old_rules) == "legacy_copy"
    assert build_upgraded_visibility_rules(old_rules) == get_system_default_policy(
        "schema_profile_visibility_default",
    )

    custom_rules = deepcopy(old_rules)
    schema = custom_rules["schema"]
    assert isinstance(schema, dict)
    schema["hidden_columns"] = ["business_secret"]
    assert classify_visibility_policy(custom_rules) == "explicit_denial"
    with pytest.raises(ValueError):
        build_upgraded_visibility_rules(custom_rules)


def test_previous_profile_visibility_default_is_upgraded() -> None:
    """Upgrade an exact previous profile policy while retaining custom metrics rules."""
    previous_default = _build_previous_visibility_default()

    assert classify_visibility_policy(previous_default) == "legacy_copy"
    upgraded = build_upgraded_visibility_rules(previous_default)
    upgraded_roles = upgraded["roles"]
    assert isinstance(upgraded_roles, dict)
    assert upgraded_roles["reader"]["can_view_all_profile"] is True
    assert upgraded_roles["restricted_reader"]["can_view_all_profile"] is True

    custom_rules = deepcopy(previous_default)
    custom_profile = custom_rules["profile"]
    assert isinstance(custom_profile, dict)
    custom_profile["hidden_metrics"] = ["row_count"]
    assert classify_visibility_policy(custom_rules) == "explicit_denial"
    with pytest.raises(ValueError):
        build_upgraded_visibility_rules(custom_rules)


@pytest.mark.django_db
def test_command_upgrades_prior_release_reader_and_visibility_defaults_for_tenant() -> None:
    """Activate revised copies of both prior tenant defaults."""
    tenant_id = uuid4()
    previous_reader_policy = GovernancePolicy.objects.create(
        tenant_id=tenant_id,
        policy_key=_POLICY_KEY,
        revision=1,
        policy_type=GovernancePolicyType.ACCESS,
        rules_json=_build_prior_release_reader_default(),
        is_tenant_default=True,
    )
    previous_policy = GovernancePolicy.objects.create(
        tenant_id=tenant_id,
        policy_key="schema_profile_visibility_default",
        revision=1,
        policy_type=GovernancePolicyType.ACCESS,
        rules_json=_build_previous_visibility_default(),
        is_tenant_default=True,
    )

    call_command(
        "upgrade_registry_reader_policies",
        tenant_id=str(tenant_id),
        apply=True,
        stdout=StringIO(),
    )

    previous_reader_policy.refresh_from_db()
    previous_policy.refresh_from_db()
    upgraded_reader_policy = GovernancePolicy.objects.get(
        tenant_id=tenant_id,
        policy_key=_POLICY_KEY,
        revision=2,
    )
    upgraded_policy = GovernancePolicy.objects.get(
        tenant_id=tenant_id,
        policy_key="schema_profile_visibility_default",
        revision=2,
    )
    assert not previous_reader_policy.is_active
    assert not previous_policy.is_active
    assert upgraded_reader_policy.is_active
    assert upgraded_reader_policy.rules_json == get_system_default_policy(_POLICY_KEY)
    assert upgraded_policy.is_active
    assert upgraded_policy.rules_json == get_system_default_policy(
        "schema_profile_visibility_default",
    )


@pytest.mark.django_db
def test_command_dry_run_and_apply_preserve_explicit_denials() -> None:
    """Upgrade exact policy copies while retaining a custom schema denial."""
    tenant_id = uuid4()
    capability_policy = GovernancePolicy.objects.create(
        tenant_id=tenant_id,
        policy_key=_POLICY_KEY,
        revision=1,
        policy_type=GovernancePolicyType.ACCESS,
        rules_json=_build_legacy_default(),
        is_tenant_default=True,
    )
    custom_visibility = _build_legacy_visibility_default()
    schema = custom_visibility["schema"]
    assert isinstance(schema, dict)
    schema["hidden_columns"] = ["business_secret"]
    visibility_policy = GovernancePolicy.objects.create(
        tenant_id=tenant_id,
        policy_key="schema_profile_visibility_default",
        revision=1,
        policy_type=GovernancePolicyType.ACCESS,
        rules_json=custom_visibility,
        is_tenant_default=True,
    )

    dry_run_output = StringIO()
    call_command(
        "upgrade_registry_reader_policies", tenant_id=str(tenant_id), stdout=dry_run_output
    )
    assert "classification=legacy_copy action=candidate" in dry_run_output.getvalue()
    assert "classification=explicit_denial action=retained" in dry_run_output.getvalue()
    assert GovernancePolicy.objects.filter(tenant_id=tenant_id).count() == 2

    apply_output = StringIO()
    call_command(
        "upgrade_registry_reader_policies",
        tenant_id=str(tenant_id),
        apply=True,
        stdout=apply_output,
    )
    capability_policy.refresh_from_db()
    visibility_policy.refresh_from_db()
    assert "classification=legacy_copy action=upgraded" in apply_output.getvalue()
    assert not capability_policy.is_active
    assert visibility_policy.is_active
    upgraded = GovernancePolicy.objects.get(
        tenant_id=tenant_id,
        policy_key=_POLICY_KEY,
        revision=2,
    )
    assert upgraded.is_active
    assert upgraded.rules_json == get_system_default_policy(_POLICY_KEY)
