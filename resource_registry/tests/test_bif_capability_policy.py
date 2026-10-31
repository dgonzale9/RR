from __future__ import annotations

import pytest

from resource_registry.bifs.capability_context import BifCapabilityContext
from resource_registry.bifs.capability_policy import DataRegistryBifCapabilityPolicy
from resource_registry.bifs.exceptions import (
    CapabilityDeniedError,
    MissingCapabilityContextError,
    UnknownBifFunctionError,
)
from resource_registry.bifs.manifest import (
    RESOURCE_REGISTRY_BIF_MANIFEST,
    BifCapabilityManifestEntry,
)


def _context(
    *,
    tenant_id: str = "tenant-a",
    roles: tuple[str, ...] = (),
    scopes: tuple[str, ...] = (),
    enabled: frozenset[str] | None = None,
    disabled: frozenset[str] | None = None,
) -> BifCapabilityContext:
    return BifCapabilityContext(
        tenant_id=tenant_id,
        actor="alice",
        roles=roles,
        scopes=scopes,
        enabled_capabilities=enabled or frozenset(),
        disabled_capabilities=disabled or frozenset(),
    )


def test_manifest_covers_only_supported_registry_operations() -> None:
    """Exclude retired agent dataset replacement operations from the Registry manifest."""
    expected_functions = {
        "resource_registry_list_datasets",
        "resource_registry_get_dataset",
        "resource_registry_get_dataset_context",
        "resource_registry_get_dataset_schema",
        "resource_registry_get_dataset_profile",
        "resource_registry_list_dataset_versions",
        "resource_registry_list_external_jobs",
        "resource_registry_query_dataset",
        "resource_registry_start_query_job",
        "resource_registry_create_filtered_dataset",
        "resource_registry_get_query_job_status",
        "resource_registry_get_query_job_result",
        "resource_registry_start_intelligence_job",
        "resource_registry_get_intelligence_job_status",
        "resource_registry_get_intelligence_job_result",
        "resource_registry_plan_query",
        "resource_registry_analyze_dataset",
        "resource_registry_explore_dataset",
        "resource_registry_create_chart",
        "resource_registry_plan_export",
        "resource_registry_get_query_run",
        "resource_registry_get_analysis_artifact",
        "resource_registry_get_chart_artifact",
        "resource_registry_get_export_plan",
        "resource_registry_generate_artifact",
        "resource_registry_start_transformation_run",
        "resource_registry_get_transformation_run",
        "resource_registry_list_transformation_runs",
        "resource_registry_list_transformation_steps",
        "resource_registry_list_transformation_outputs",
        "resource_registry_get_transformation_diagnostics",
    }
    assert set(RESOURCE_REGISTRY_BIF_MANIFEST) == expected_functions
    assert RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_start_query_job"].is_read_only is False
    assert (
        RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_create_filtered_dataset"].is_read_only
        is False
    )
    assert (
        RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_start_intelligence_job"].is_read_only
        is False
    )
    assert RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_analyze_dataset"].is_read_only is False
    assert RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_query_dataset"].is_read_only is False
    assert RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_create_chart"].is_read_only is False
    assert RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_plan_export"].is_read_only is False
    assert (
        RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_generate_artifact"].is_read_only is False
    )
    assert (
        RESOURCE_REGISTRY_BIF_MANIFEST["resource_registry_start_transformation_run"].is_read_only
        is False
    )
    assert all(
        entry.is_read_only
        for name, entry in RESOURCE_REGISTRY_BIF_MANIFEST.items()
        if name
        not in {
            "resource_registry_start_query_job",
            "resource_registry_create_filtered_dataset",
            "resource_registry_start_intelligence_job",
            "resource_registry_analyze_dataset",
            "resource_registry_query_dataset",
            "resource_registry_create_chart",
            "resource_registry_plan_export",
            "resource_registry_generate_artifact",
            "resource_registry_start_transformation_run",
        }
    )


def test_unknown_function_rejected() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    with pytest.raises(UnknownBifFunctionError):
        policy.get_manifest_entry("resource_registry_unknown")


def test_missing_tenant_rejected() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    with pytest.raises(MissingCapabilityContextError):
        policy.assert_can_call(
            "resource_registry_list_datasets",
            _context(tenant_id="", roles=("admin",)),
        )


def test_disabled_capability_denies_even_admin() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    with pytest.raises(CapabilityDeniedError):
        policy.assert_can_call(
            "resource_registry_get_dataset",
            _context(
                roles=("admin",),
                disabled=frozenset({"resource_registry.dataset.read"}),
            ),
        )


def test_capability_policy_rechecks_a_changed_context_for_each_tool_call() -> None:
    """Reject the next call when a previously allowed capability becomes disabled."""
    policy = DataRegistryBifCapabilityPolicy()
    function_name = "resource_registry_get_dataset"

    policy.assert_can_call(function_name, _context(roles=("admin",)))

    with pytest.raises(CapabilityDeniedError):
        policy.assert_can_call(
            function_name,
            _context(
                roles=("admin",),
                disabled=frozenset({"resource_registry.dataset.read"}),
            ),
        )


def test_admin_allowed_for_read_bifs() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    policy.assert_can_call("resource_registry_get_dataset_profile", _context(roles=("admin",)))


def test_reader_allowed_for_read_bifs() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    policy.assert_can_call("resource_registry_get_dataset_context", _context(roles=("reader",)))


def test_restricted_reader_allowed_for_safe_read_bifs() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    policy.assert_can_call(
        "resource_registry_get_dataset_schema",
        _context(roles=("restricted_reader",)),
    )


def test_restricted_reader_can_query_and_plan_export_but_cannot_materialize() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    context = _context(roles=("restricted_reader",))
    for function_name in (
        "resource_registry_plan_query",
        "resource_registry_query_dataset",
        "resource_registry_explore_dataset",
        "resource_registry_start_query_job",
        "resource_registry_get_query_job_status",
        "resource_registry_get_query_job_result",
        "resource_registry_plan_export",
        "resource_registry_get_export_plan",
    ):
        policy.assert_can_call(function_name, context)
    for function_name in (
        "resource_registry_create_filtered_dataset",
        "resource_registry_generate_artifact",
        "resource_registry_start_intelligence_job",
        "resource_registry_start_transformation_run",
    ):
        with pytest.raises(CapabilityDeniedError):
            policy.assert_can_call(function_name, context)


def test_restricted_reader_can_analyze_chart_and_read_saved_results() -> None:
    """Allow insight BIFs for normal readers while honoring an explicit denial."""
    policy = DataRegistryBifCapabilityPolicy()
    context = _context(roles=("restricted_reader",))

    for function_name in (
        "resource_registry_analyze_dataset",
        "resource_registry_create_chart",
        "resource_registry_get_query_run",
        "resource_registry_get_analysis_artifact",
        "resource_registry_get_chart_artifact",
    ):
        policy.assert_can_call(function_name, context)

    with pytest.raises(CapabilityDeniedError):
        policy.assert_can_call(
            "resource_registry_create_chart",
            _context(
                roles=("restricted_reader",),
                disabled=frozenset({"resource_registry.dataset.chart.read"}),
            ),
        )


def test_restricted_reader_can_read_transformation_summaries_and_job_results() -> None:
    """Expose scoped operational reads while retaining critical operation denials."""
    policy = DataRegistryBifCapabilityPolicy()
    context = _context(roles=("restricted_reader",))

    for function_name in (
        "resource_registry_get_transformation_run",
        "resource_registry_list_transformation_runs",
        "resource_registry_list_transformation_steps",
        "resource_registry_list_transformation_outputs",
        "resource_registry_get_intelligence_job_status",
        "resource_registry_get_intelligence_job_result",
    ):
        policy.assert_can_call(function_name, context)

    for function_name in (
        "resource_registry_get_transformation_diagnostics",
        "resource_registry_list_external_jobs",
    ):
        with pytest.raises(CapabilityDeniedError):
            policy.assert_can_call(function_name, context)


def test_restricted_reader_explicit_query_denial_wins() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    with pytest.raises(CapabilityDeniedError):
        policy.assert_can_call(
            "resource_registry_query_dataset",
            _context(
                roles=("restricted_reader",),
                disabled=frozenset({"resource_registry.dataset.query.read"}),
            ),
        )


def test_external_jobs_require_reader_admin_or_explicit_scope() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    with pytest.raises(CapabilityDeniedError):
        policy.assert_can_call(
            "resource_registry_list_external_jobs",
            _context(roles=("restricted_reader",)),
        )

    policy.assert_can_call("resource_registry_list_external_jobs", _context(roles=("reader",)))
    policy.assert_can_call(
        "resource_registry_list_external_jobs",
        _context(roles=("restricted_reader",), scopes=("resource_registry.external_jobs.read",)),
    )


def test_unknown_function_denied() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    with pytest.raises(UnknownBifFunctionError):
        policy.assert_can_call("unknown_func", _context(roles=("admin",)))


def test_non_read_only_function_can_be_allowed_by_policy() -> None:
    write_entry = BifCapabilityManifestEntry(
        function_name="resource_registry_write_attempt",
        capability_key="resource_registry.write",
        allowed_roles=("admin",),
        is_read_only=False,
        risk_level="LOW",
    )
    policy = DataRegistryBifCapabilityPolicy(
        manifest={"resource_registry_write_attempt": write_entry},
    )
    policy.assert_can_call("resource_registry_write_attempt", _context(roles=("admin",)))


def test_list_allowed_functions_respects_explicit_enable_and_disable() -> None:
    policy = DataRegistryBifCapabilityPolicy()
    context = _context(
        roles=("restricted_reader",),
        enabled=frozenset({"resource_registry.external_jobs.read"}),
        disabled=frozenset({"resource_registry.dataset.profile.read"}),
    )
    allowed = policy.list_allowed_functions(context)
    assert "resource_registry_list_external_jobs" in allowed
    assert "resource_registry_get_dataset_profile" not in allowed
