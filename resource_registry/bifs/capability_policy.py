from __future__ import annotations

from resource_registry.bifs.capability_context import BifCapabilityContext
from resource_registry.bifs.exceptions import (
    CapabilityDeniedError,
    MissingCapabilityContextError,
    UnknownBifFunctionError,
)
from resource_registry.bifs.manifest import (
    RESOURCE_REGISTRY_BIF_MANIFEST,
    BifCapabilityManifestEntry,
)


class DataRegistryBifCapabilityPolicy:
    """Centralized allow/deny policy for Resource Registry BIF capability checks."""

    def __init__(
        self,
        *,
        manifest: dict[str, BifCapabilityManifestEntry] | None = None,
    ) -> None:
        self._manifest = manifest or RESOURCE_REGISTRY_BIF_MANIFEST

    def get_manifest_entry(self, function_name: str) -> BifCapabilityManifestEntry:
        entry = self._manifest.get(function_name)
        if entry is None:
            raise UnknownBifFunctionError(
                f"Unknown Resource Registry built-in function: {function_name}.",
            )
        return entry

    def assert_can_call(self, function_name: str, context: BifCapabilityContext) -> None:
        entry = self.get_manifest_entry(function_name)
        self._assert_valid_context(context)

        if entry.capability_key in context.disabled_capabilities:
            raise CapabilityDeniedError(
                f"Capability {entry.capability_key} is disabled for this context.",
            )

        if self._is_explicitly_enabled(entry, context):
            return

        if self._is_allowed_by_role_scope(entry, context):
            return

        raise CapabilityDeniedError(f"Capability denied for function {function_name}.")

    def list_allowed_functions(self, context: BifCapabilityContext) -> tuple[str, ...]:
        self._assert_valid_context(context)
        allowed: list[str] = []
        for function_name in sorted(self._manifest):
            try:
                self.assert_can_call(function_name, context)
            except CapabilityDeniedError:
                continue
            except UnknownBifFunctionError:
                continue
            allowed.append(function_name)
        return tuple(allowed)

    def _assert_valid_context(self, context: BifCapabilityContext) -> None:
        if not context.tenant_id:
            raise MissingCapabilityContextError("tenant_id is required.")

    def _is_explicitly_enabled(
        self,
        entry: BifCapabilityManifestEntry,
        context: BifCapabilityContext,
    ) -> bool:
        return entry.capability_key in context.enabled_capabilities

    def _is_allowed_by_role_scope(
        self,
        entry: BifCapabilityManifestEntry,
        context: BifCapabilityContext,
    ) -> bool:
        role_set = set(context.roles)

        if "admin" in role_set and entry.risk_level in {"LOW", "MEDIUM"}:
            return True

        if "reader" in role_set and self._is_reader_allowed(entry):
            return True

        if "restricted_reader" in role_set and self._is_restricted_reader_allowed(entry):
            return True

        if entry.required_scopes and set(entry.required_scopes).issubset(set(context.scopes)):
            return True

        return False

    @staticmethod
    def _is_reader_allowed(entry: BifCapabilityManifestEntry) -> bool:
        return entry.function_name in entry_map_reader_allowlist()

    @staticmethod
    def _is_restricted_reader_allowed(entry: BifCapabilityManifestEntry) -> bool:
        return entry.function_name in entry_map_restricted_allowlist()


def entry_map_reader_allowlist() -> frozenset[str]:
    return frozenset(RESOURCE_REGISTRY_BIF_MANIFEST)


def entry_map_restricted_allowlist() -> frozenset[str]:
    return frozenset(
        {
            "resource_registry_list_datasets",
            "resource_registry_get_dataset",
            "resource_registry_get_dataset_context",
            "resource_registry_get_dataset_schema",
            "resource_registry_get_dataset_profile",
            "resource_registry_list_dataset_versions",
            "resource_registry_plan_query",
            "resource_registry_query_dataset",
            "resource_registry_explore_dataset",
            "resource_registry_start_query_job",
            "resource_registry_get_query_job_status",
            "resource_registry_get_query_job_result",
            "resource_registry_analyze_dataset",
            "resource_registry_create_chart",
            "resource_registry_get_query_run",
            "resource_registry_get_analysis_artifact",
            "resource_registry_get_chart_artifact",
            "resource_registry_get_intelligence_job_status",
            "resource_registry_get_intelligence_job_result",
            "resource_registry_plan_export",
            "resource_registry_get_export_plan",
            "resource_registry_get_transformation_run",
            "resource_registry_list_transformation_runs",
            "resource_registry_list_transformation_steps",
            "resource_registry_list_transformation_outputs",
        },
    )
