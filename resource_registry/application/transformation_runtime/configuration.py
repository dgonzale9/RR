from __future__ import annotations

from collections.abc import Mapping, Sequence

from django.conf import settings

from resource_registry.application.transformation_runtime.contracts import (
    EnabledTransformationProvider,
    RuntimeConfiguration,
    TransformationAuditRuntimeConfiguration,
    TransformationHealthRuntimeConfiguration,
    TransformationMetricsRuntimeConfiguration,
    TransformationOperationalLimitsConfiguration,
    TransformationOutputFinalizationRuntimeConfiguration,
    TransformationWorkerRuntimeConfiguration,
)


class RuntimeConfigurationLoader:
    """Load transformation runtime settings at the composition boundary only."""

    def load(
        self,
        *,
        worker_overrides: Mapping[str, object] | None = None,
    ) -> RuntimeConfiguration:
        """Load and normalize one immutable runtime configuration."""
        overrides = worker_overrides or {}
        poll_interval_value = overrides.get(
            "poll_interval_seconds",
            settings.RESOURCE_REGISTRY_TRANSFORMATION_RUN_WORKER_POLL_INTERVAL_SECONDS,
        )
        if not isinstance(poll_interval_value, str | int | float):
            raise ValueError("Worker poll interval override must be numeric.")
        providers = _parse_enabled_providers(
            getattr(settings, "RESOURCE_REGISTRY_TRANSFORMATION_ENABLED_PROVIDERS", ()),
        )
        worker = TransformationWorkerRuntimeConfiguration(
            poll_interval_seconds=float(
                poll_interval_value,
            ),
            max_retries=int(settings.RESOURCE_REGISTRY_TRANSFORMATION_RUN_MAX_RETRIES),
            lease_seconds=int(settings.RESOURCE_REGISTRY_TRANSFORMATION_RUN_LEASE_SECONDS),
            heartbeat_seconds=int(settings.RESOURCE_REGISTRY_TRANSFORMATION_RUN_HEARTBEAT_SECONDS),
            execution_timeout_seconds=float(
                settings.RESOURCE_REGISTRY_TRANSFORMATION_RUN_EXECUTION_TIMEOUT_SECONDS,
            ),
            recovery_batch_size=int(
                settings.RESOURCE_REGISTRY_TRANSFORMATION_RUN_RECOVERY_BATCH_SIZE,
            ),
        )
        finalization_poll_interval = overrides.get(
            "finalization_poll_interval_seconds",
            _required_float("RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_POLL_INTERVAL_SECONDS"),
        )
        if not isinstance(finalization_poll_interval, str | int | float):
            raise ValueError("Finalization poll interval override must be numeric.")
        return RuntimeConfiguration(
            enabled_providers=providers,
            worker=worker,
            operational_limits=TransformationOperationalLimitsConfiguration(
                maximum_input_bytes=_required_int("RESOURCE_REGISTRY_TRANSFORMATION_MAX_INPUT_BYTES"),
                maximum_output_bytes=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_MAX_OUTPUT_BYTES",
                ),
                maximum_row_count=_required_int("RESOURCE_REGISTRY_TRANSFORMATION_MAX_ROW_COUNT"),
                maximum_source_columns=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_MAX_SOURCE_COLUMNS",
                ),
                maximum_selected_columns=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_MAX_SELECTED_COLUMNS",
                ),
                provider_timeout_seconds=_required_float(
                    "RESOURCE_REGISTRY_TRANSFORMATION_PROVIDER_TIMEOUT_SECONDS",
                ),
                materialization_timeout_seconds=_required_float(
                    "RESOURCE_REGISTRY_TRANSFORMATION_MATERIALIZATION_TIMEOUT_SECONDS",
                ),
                workspace_quota_bytes=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_WORKSPACE_QUOTA_BYTES",
                ),
                workspace_maximum_bytes=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_WORKSPACE_MAXIMUM_BYTES",
                ),
                minimum_free_disk_bytes=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_MINIMUM_FREE_DISK_BYTES",
                ),
            ),
            output_finalization=TransformationOutputFinalizationRuntimeConfiguration(
                poll_interval_seconds=float(finalization_poll_interval),
                max_retries=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_MAX_RETRIES",
                    allow_zero=True,
                ),
                lease_seconds=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_LEASE_SECONDS",
                ),
                heartbeat_seconds=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_HEARTBEAT_SECONDS",
                ),
                timeout_seconds=_required_float(
                    "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_TIMEOUT_SECONDS",
                ),
                recovery_batch_size=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_RECOVERY_BATCH_SIZE",
                ),
                per_process_concurrency=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_PER_PROCESS_CONCURRENCY",
                ),
                per_tenant_concurrency=_required_int(
                    "RESOURCE_REGISTRY_TRANSFORMATION_FINALIZATION_PER_TENANT_CONCURRENCY",
                ),
            ),
            metrics=TransformationMetricsRuntimeConfiguration(
                adapter_key=str(
                    getattr(
                        settings,
                        "RESOURCE_REGISTRY_TRANSFORMATION_METRICS_ADAPTER",
                        "production",
                    ),
                ),
            ),
            audit=TransformationAuditRuntimeConfiguration(
                publisher_key=str(
                    getattr(
                        settings,
                        "RESOURCE_REGISTRY_TRANSFORMATION_AUDIT_PUBLISHER",
                        "production",
                    ),
                ),
            ),
            health=TransformationHealthRuntimeConfiguration(
                require_provider=bool(
                    getattr(
                        settings,
                        "RESOURCE_REGISTRY_TRANSFORMATION_REQUIRE_PROVIDER",
                        True,
                    ),
                ),
                allow_degraded_observability=bool(
                    getattr(
                        settings,
                        "RESOURCE_REGISTRY_TRANSFORMATION_ALLOW_DEGRADED_OBSERVABILITY",
                        True,
                    ),
                ),
            ),
            expected_registry_manifest_checksum=getattr(
                settings,
                "RESOURCE_REGISTRY_TRANSFORMATION_EXPECTED_REGISTRY_MANIFEST_CHECKSUM",
                None,
            ),
        )


def _parse_enabled_providers(value: object) -> tuple[EnabledTransformationProvider, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise ValueError("Enabled transformation providers must be a sequence.")
    providers: list[EnabledTransformationProvider] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"provider_key", "provider_version"}:
            raise ValueError(
                "Each enabled provider requires exact provider_key and provider_version fields.",
            )
        providers.append(
            EnabledTransformationProvider(
                provider_key=_require_string(item["provider_key"]),
                provider_version=_require_string(item["provider_version"]),
            ),
        )
    return tuple(providers)


def _required_int(name: str, *, allow_zero: bool = False) -> int:
    value = getattr(settings, name, None)
    if value is None or value == "":
        raise ValueError(f"Required transformation setting {name} is absent.")
    parsed = int(value)
    if parsed < 0 or (parsed == 0 and not allow_zero):
        raise ValueError(f"Required transformation setting {name} is invalid.")
    return parsed


def _required_float(name: str) -> float:
    value = getattr(settings, name, None)
    if value is None or value == "":
        raise ValueError(f"Required transformation setting {name} is absent.")
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"Required transformation setting {name} is invalid.")
    return parsed


def _require_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Enabled provider identity values must be strings.")
    return value
