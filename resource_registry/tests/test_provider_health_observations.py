"""Verify ERR-5.5 pure expiring Provider health-observation contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from resource_registry.provider_contracts import (
    ProviderHealthObservation,
    ProviderHealthObservationCategory,
    ProviderHealthObservationScope,
    ProviderHealthObservationSet,
    ProviderHealthObservationValidationError,
    ProviderIdentity,
    ProviderKey,
    ProviderVersion,
)

_HEALTH_PATH = Path(__file__).resolve().parents[1] / "provider_contracts" / "health.py"


def _build_scope(region_reference: str = "region.us_central") -> ProviderHealthObservationScope:
    """Build one exact opaque Provider operational observation scope."""
    return ProviderHealthObservationScope(
        ProviderIdentity(
            ProviderKey("atlas.transformation.duckdb"), ProviderVersion("semver", "1.0.0")
        ),
        "atlas.duckdb.implementation.1",
        "atlas.deployment.primary",
        region_reference,
    )


def _build_observation(
    observed_at: datetime = datetime(2026, 7, 30, 12, tzinfo=UTC),
    scope: ProviderHealthObservationScope | None = None,
) -> ProviderHealthObservation:
    """Build one exact expiring sanitized health observation."""
    return ProviderHealthObservation(
        scope or _build_scope(),
        ProviderHealthObservationCategory.READY,
        observed_at,
        observed_at + timedelta(minutes=5),
        "probe_succeeded",
    )


def test_provider_health_observation_is_immutable_and_expiring() -> None:
    """Preserve exact time-bounded observation facts without computing availability."""
    observation = _build_observation()
    assert observation.expires_at > observation.observed_at
    with pytest.raises(FrozenInstanceError):
        observation.category = ProviderHealthObservationCategory.DEGRADED  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ProviderHealthObservationScope(
            _build_scope().provider_identity,
            "atlas.duckdb.implementation.1",
            "https.deployment.example",
            "region.us_central",
        ),
        lambda: ProviderHealthObservationScope(
            _build_scope().provider_identity,
            "atlas.duckdb.implementation.1",
            "tenant.policy",
            "region.us_central",
        ),
        lambda: ProviderHealthObservation(
            _build_scope(),
            ProviderHealthObservationCategory.READY,
            datetime(2026, 7, 30, 12),
            datetime(2026, 7, 30, 12, tzinfo=UTC),
            "probe_succeeded",
        ),
        lambda: ProviderHealthObservation(
            _build_scope(),
            ProviderHealthObservationCategory.READY,
            datetime(2026, 7, 30, 12, tzinfo=UTC),
            datetime(2026, 7, 30, 11, tzinfo=UTC),
            "probe_succeeded",
        ),
        lambda: ProviderHealthObservation(
            _build_scope(),
            ProviderHealthObservationCategory.READY,
            datetime(2026, 7, 30, 12, tzinfo=UTC),
            datetime(2026, 7, 30, 13, tzinfo=UTC),
            "raw_vendor_payload",
        ),
    ),
)
def test_provider_health_observation_rejects_sensitive_and_non_expiring_shapes(
    factory: object,
) -> None:
    """Reject endpoint, policy, raw payload, naive, and reversed observation facts."""
    with pytest.raises(ProviderHealthObservationValidationError):
        factory()  # type: ignore[operator]


def test_provider_health_observation_set_rejects_duplicate_scope_and_time() -> None:
    """Reject duplicate exact scope/time facts while preserving distinct observations."""
    first = _build_observation()
    second = _build_observation(first.observed_at + timedelta(minutes=1))
    assert ProviderHealthObservationSet((first, second)).observations == (first, second)
    with pytest.raises(ProviderHealthObservationValidationError, match="duplicate"):
        ProviderHealthObservationSet((first, _build_observation()))


def test_provider_health_observations_have_no_runtime_dependencies() -> None:
    """Keep ERR-5.5 contracts independent from framework and runtime boundaries."""
    syntax_tree = ast.parse(_HEALTH_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }

    assert imported_modules.isdisjoint(
        {
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "celery",
            "django",
            "execution",
            "network",
            "persistence",
            "registry_core",
            "router",
            "secrets",
            "socket",
            "telemetry",
            "transport",
            "workers",
        }
    )
