from __future__ import annotations

from copy import deepcopy

from resource_registry.policies.exceptions import UnsupportedPolicyError

RETENTION_DEFAULT_POLICY: dict[str, object] = {
    "retain_latest_version": True,
    "max_versions": 10,
}

LIFECYCLE_DEFAULT_POLICY: dict[str, object] = {
    "default_action_type": "DEPRECATE",
    "deprecate_before_delete": True,
}

ARTIFACT_MATERIALIZATION_DEFAULT_POLICY: dict[str, object] = {
    "max_size_bytes": 104_857_600,
    "allowed_formats": ["csv", "json", "jsonl", "parquet", "xlsx"],
    "require_checksum": False,
    "allowed_storage_providers": ["local", "azure_blob"],
    "allow_local_files": True,
    "allow_remote_uris": True,
}

SCHEMA_PROFILE_VISIBILITY_DEFAULT_POLICY: dict[str, object] = {
    "schema": {
        "hidden_columns": [],
        "masked_columns": [],
        "allowed_columns": None,
        "hidden_classifications": [],
        "masked_classifications": ["PII", "PHI", "FINANCIAL", "SENSITIVE"],
    },
    "profile": {
        "hidden_metrics": [],
        "masked_metrics": [],
        "allowed_metrics": None,
        "hidden_classifications": [],
        "masked_classifications": ["PII", "PHI", "FINANCIAL", "SENSITIVE"],
    },
    "roles": {
        "admin": {
            "can_view_all_schema": True,
            "can_view_all_profile": True,
        },
        "reader": {
            "can_view_all_schema": True,
            "can_view_all_profile": True,
        },
        "restricted_reader": {
            "can_view_all_schema": True,
            "can_view_all_profile": True,
        },
    },
}

RESOURCE_REGISTRY_BIF_CAPABILITIES_DEFAULT_POLICY: dict[str, object] = {
    "enabled_capabilities": [
        "resource_registry.dataset.list",
        "resource_registry.dataset.read",
        "resource_registry.dataset.context.read",
        "resource_registry.dataset.schema.read",
        "resource_registry.dataset.profile.read",
        "resource_registry.dataset.versions.read",
        "resource_registry.dataset.query.read",
        "resource_registry.dataset.query.job.start",
        "resource_registry.dataset.derived.create",
        "resource_registry.dataset.query.job.read",
        "resource_registry.dataset.intelligence.job.start",
        "resource_registry.dataset.intelligence.job.read",
        "resource_registry.dataset.query.plan",
        "resource_registry.dataset.analysis.read",
        "resource_registry.dataset.exploration.read",
        "resource_registry.dataset.chart.read",
        "resource_registry.dataset.export.plan",
        "resource_registry.dataset.query_run.read",
        "resource_registry.dataset.analysis_artifact.read",
        "resource_registry.dataset.chart_artifact.read",
        "resource_registry.dataset.export_plan.read",
        "resource_registry.dataset.artifact.generate",
        "resource_registry.transformation.run.read",
        "resource_registry.transformation.step.read",
        "resource_registry.transformation.output.read",
        "resource_registry.transformation.diagnostics.read",
    ],
    "disabled_capabilities": [],
    "role_overrides": {
        "admin": {
            "enabled_capabilities": ["*"],
            "disabled_capabilities": [],
        },
        "reader": {
            "enabled_capabilities": [
                "resource_registry.dataset.list",
                "resource_registry.dataset.read",
                "resource_registry.dataset.context.read",
                "resource_registry.dataset.schema.read",
                "resource_registry.dataset.profile.read",
                "resource_registry.dataset.versions.read",
                "resource_registry.external_jobs.read",
                "resource_registry.dataset.query.read",
                "resource_registry.dataset.query.job.start",
                "resource_registry.dataset.derived.create",
                "resource_registry.dataset.query.job.read",
                "resource_registry.dataset.intelligence.job.start",
                "resource_registry.dataset.intelligence.job.read",
                "resource_registry.dataset.query.plan",
                "resource_registry.dataset.analysis.read",
                "resource_registry.dataset.exploration.read",
                "resource_registry.dataset.chart.read",
                "resource_registry.dataset.export.plan",
                "resource_registry.dataset.query_run.read",
                "resource_registry.dataset.analysis_artifact.read",
                "resource_registry.dataset.chart_artifact.read",
                "resource_registry.dataset.export_plan.read",
                "resource_registry.dataset.artifact.generate",
                "resource_registry.transformation.run.read",
                "resource_registry.transformation.step.read",
                "resource_registry.transformation.output.read",
                "resource_registry.transformation.diagnostics.read",
            ],
            "disabled_capabilities": [],
        },
        "restricted_reader": {
            "enabled_capabilities": [
                "resource_registry.dataset.list",
                "resource_registry.dataset.read",
                "resource_registry.dataset.context.read",
                "resource_registry.dataset.schema.read",
                "resource_registry.dataset.profile.read",
                "resource_registry.dataset.versions.read",
                "resource_registry.dataset.query.read",
                "resource_registry.dataset.query.job.start",
                "resource_registry.dataset.query.job.read",
                "resource_registry.dataset.query.plan",
                "resource_registry.dataset.analysis.read",
                "resource_registry.dataset.exploration.read",
                "resource_registry.dataset.chart.read",
                "resource_registry.dataset.intelligence.job.read",
                "resource_registry.dataset.export.plan",
                "resource_registry.dataset.query_run.read",
                "resource_registry.dataset.analysis_artifact.read",
                "resource_registry.dataset.chart_artifact.read",
                "resource_registry.dataset.export_plan.read",
                "resource_registry.transformation.run.read",
                "resource_registry.transformation.step.read",
                "resource_registry.transformation.output.read",
            ],
            "disabled_capabilities": [
                "resource_registry.external_jobs.read",
                "resource_registry.dataset.derived.create",
                "resource_registry.dataset.intelligence.job.start",
                "resource_registry.dataset.artifact.generate",
                "resource_registry.transformation.run.start",
                "resource_registry.transformation.diagnostics.read",
            ],
        },
    },
}

SYSTEM_POLICY_DEFAULTS: dict[str, dict[str, object]] = {
    "retention_default": RETENTION_DEFAULT_POLICY,
    "lifecycle_default": LIFECYCLE_DEFAULT_POLICY,
    "artifact_materialization_default": ARTIFACT_MATERIALIZATION_DEFAULT_POLICY,
    "schema_profile_visibility_default": SCHEMA_PROFILE_VISIBILITY_DEFAULT_POLICY,
    "resource_registry_bif_capabilities_default": RESOURCE_REGISTRY_BIF_CAPABILITIES_DEFAULT_POLICY,
}


def get_system_default_policy(policy_key: str) -> dict[str, object]:
    try:
        return deepcopy(SYSTEM_POLICY_DEFAULTS[policy_key])
    except KeyError as error:
        raise UnsupportedPolicyError(
            f"No system default configured for policy key '{policy_key}'.",
        ) from error
