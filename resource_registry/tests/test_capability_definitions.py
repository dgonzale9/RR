"""Verify ERR-3.1 pure immutable Capability Definition contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from resource_registry.capability_definitions import (
    CapabilityCancellationMode,
    CapabilityCompatibilityDeclaration,
    CapabilityCompatibilityDisposition,
    CapabilityContractRef,
    CapabilityContractVersion,
    CapabilityDeadlineBehavior,
    CapabilityDeadlineClass,
    CapabilityDefinitionAggregate,
    CapabilityDefinitionValidationError,
    CapabilityEffectCategory,
    CapabilityEffectContract,
    CapabilityErrorContract,
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKey,
    CapabilityLifecycleState,
    CapabilityObservabilityContract,
    CapabilityRetryPolicy,
    CapabilitySchema,
    CapabilitySecurityContract,
    CapabilityTimingClass,
    CapabilityTimingContract,
    CapabilityType,
    CapabilityVersion,
)

_CONTRACTS_PATH = Path(__file__).resolve().parents[1] / "capability_definitions" / "contracts.py"


def _build_contract_reference(version: str = "1.0.0") -> CapabilityContractRef:
    """Build one exact capability contract reference fixture.

    Args:
        version: Exact semantic version to include.

    Returns:
        A valid immutable contract reference.
    """
    return CapabilityContractRef(
        CapabilityKey("atlas.dataset.values.read"), CapabilityVersion(version)
    )


def _build_contract_version(
    version: str = "1.0.0",
    *,
    compatibility: CapabilityCompatibilityDeclaration | None = None,
    lifecycle_state: CapabilityLifecycleState = CapabilityLifecycleState.DRAFT,
) -> CapabilityContractVersion:
    """Build one valid provider-neutral contract version fixture.

    Args:
        version: Exact semantic version to include.
        compatibility: Optional explicit compatibility declaration.
        lifecycle_state: Initial immutable lifecycle state.

    Returns:
        A valid immutable contract version.
    """
    return CapabilityContractVersion(
        contract_ref=_build_contract_reference(version),
        capability_type=CapabilityType.READ,
        purpose="Read bounded dataset values.",
        preconditions=("Caller supplies one exact authorized resource reference.",),
        postconditions=("Result observes one bounded immutable resource view.",),
        semantic_invariants=("The contract never grants execution authority.",),
        input_schema=CapabilitySchema(
            profile_key="json-schema",
            profile_version="2020-12",
            schema_json='{"type":"object","properties":{"limit":{"type":"integer"}}}',
        ),
        output_schema=CapabilitySchema(
            profile_key="json-schema",
            profile_version="2020-12",
            schema_json='{"type":"object","properties":{"rows":{"type":"array"}}}',
        ),
        errors=(
            CapabilityErrorContract(
                code="resource.not_found",
                category="not_found",
                retry_policy=CapabilityRetryPolicy.NEVER,
                safe_detail=False,
            ),
        ),
        effect=CapabilityEffectContract(
            categories=(CapabilityEffectCategory.NONE,),
            idempotency=CapabilityIdempotency.INHERENT,
            retry_policy=CapabilityRetryPolicy.SAFE,
            cancellation_mode=CapabilityCancellationMode.BEFORE_START,
            execution_modes=(CapabilityExecutionMode.SYNCHRONOUS,),
            atomicity_boundary="single read observation",
        ),
        timing=CapabilityTimingContract(
            expected_latency_class=CapabilityTimingClass.INTERACTIVE,
            deadline_behavior=CapabilityDeadlineBehavior.HONORED,
            maximum_deadline_class=CapabilityDeadlineClass.SHORT,
        ),
        observability=CapabilityObservabilityContract(
            required_correlation_fields=("correlation_id", "resource_version"),
            required_metric_categories=("request",),
            required_event_categories=("capability.audit",),
        ),
        security=CapabilitySecurityContract(
            input_classification="internal",
            output_classification="internal",
            required_purpose="dataset inspection",
            residency_requirement="approved region",
            prohibited_log_fields=("query",),
        ),
        compatibility=compatibility
        or CapabilityCompatibilityDeclaration(
            disposition=CapabilityCompatibilityDisposition.INITIAL,
            predecessor=None,
            replacement=None,
            reason="Initial immutable semantic contract.",
        ),
        lifecycle_state=lifecycle_state,
        conformance_profile_ref="atlas.capability.conformance.read.v1",
    )


def test_capability_key_and_exact_semantic_version_reject_aliases_and_ambiguity() -> None:
    """Reject non-semantic keys and non-exact version selection vocabulary."""
    assert CapabilityKey("atlas.dataset.values.read").value == "atlas.dataset.values.read"
    assert str(CapabilityVersion("12.3.4")) == "12.3.4"
    assert CapabilityVersion("1.10.0") > CapabilityVersion("1.2.0")

    for invalid_key in ("atlas.read", "Atlas.dataset.read", "atlas.dataset.read/handler", " "):
        with pytest.raises(CapabilityDefinitionValidationError, match="^invalid capability key$"):
            CapabilityKey(invalid_key)
    for invalid_version in ("latest", "1", "1.0", "01.0.0", "1.0.0-rc.1", "1.0.0+build"):
        with pytest.raises(
            CapabilityDefinitionValidationError, match="^invalid capability version$"
        ):
            CapabilityVersion(invalid_version)


def test_schema_is_canonical_immutable_and_bounded() -> None:
    """Canonicalize safe schema JSON and reject non-object or oversized declarations."""
    schema = CapabilitySchema(
        profile_key="json-schema",
        profile_version="2020-12",
        schema_json=' { "properties": { "name": { "type": "string" } }, "type": "object" } ',
    )

    assert schema.schema_json == '{"properties":{"name":{"type":"string"}},"type":"object"}'
    with pytest.raises(CapabilityDefinitionValidationError, match="^invalid capability schema$"):
        CapabilitySchema("json-schema", "2020-12", "[]")
    with pytest.raises(CapabilityDefinitionValidationError, match="^invalid capability schema$"):
        CapabilitySchema("json-schema", "2020-12", "{" + '"x":"' + "x" * 70_000 + '"}')
    with pytest.raises(CapabilityDefinitionValidationError, match="^invalid capability schema$"):
        CapabilitySchema("json-schema", "2020-12", '{"provider":"runtime identity"}')


def test_effect_contract_rejects_incoherent_side_effect_and_retry_declarations() -> None:
    """Reject effect combinations that could falsely imply retry safety."""
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^invalid capability effect categories$"
    ):
        CapabilityEffectContract(
            categories=(CapabilityEffectCategory.NONE, CapabilityEffectCategory.EXTERNAL),
            idempotency=CapabilityIdempotency.KEY_REQUIRED,
            retry_policy=CapabilityRetryPolicy.RECONCILIATION_REQUIRED,
            cancellation_mode=CapabilityCancellationMode.COOPERATIVE,
            execution_modes=(CapabilityExecutionMode.ASYNCHRONOUS,),
            atomicity_boundary="external boundary",
        )


def test_contract_version_rejects_empty_conformance_and_malformed_semantic_declarations() -> None:
    """Reject a contract that lacks portable conformance or semantic contract evidence."""
    contract = _build_contract_version()

    with pytest.raises(
        CapabilityDefinitionValidationError,
        match="^invalid capability conformance profile reference$",
    ):
        replace(contract, conformance_profile_ref="")
    with pytest.raises(
        CapabilityDefinitionValidationError,
        match="^invalid capability semantic invariants$",
    ):
        replace(contract, semantic_invariants=("",))
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^incoherent capability retry policy$"
    ):
        CapabilityEffectContract(
            categories=(CapabilityEffectCategory.EXTERNAL,),
            idempotency=CapabilityIdempotency.NOT_IDEMPOTENT,
            retry_policy=CapabilityRetryPolicy.SAFE,
            cancellation_mode=CapabilityCancellationMode.COOPERATIVE,
            execution_modes=(CapabilityExecutionMode.ASYNCHRONOUS,),
            atomicity_boundary="external boundary",
        )


def test_timing_and_observability_contracts_reject_missing_or_incoherent_declarations() -> None:
    """Reject timing and observability values that cannot express contract obligations."""
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^incoherent capability deadline declaration$"
    ):
        CapabilityTimingContract(
            expected_latency_class=CapabilityTimingClass.INTERACTIVE,
            deadline_behavior=CapabilityDeadlineBehavior.REQUIRED,
            maximum_deadline_class=CapabilityDeadlineClass.NONE,
        )
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^invalid capability metric categories$"
    ):
        CapabilityObservabilityContract(
            required_correlation_fields=("correlation_id",),
            required_metric_categories=(),
            required_event_categories=("capability.audit",),
        )
    contract = _build_contract_version()
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^invalid capability timing contract$"
    ):
        replace(contract, timing="short")
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^invalid capability observability contract$"
    ):
        replace(contract, observability="audit")


def test_aggregate_snapshots_versions_and_resolves_only_exact_versions() -> None:
    """Preserve immutable declared order and reject implicit version fallback."""
    first = _build_contract_version("1.0.0")
    versions = [first]
    aggregate = CapabilityDefinitionAggregate(
        capability_key=first.contract_ref.capability_key,
        definition_authority="atlas capability authority",
        contract_versions=versions,
    )
    versions.clear()

    assert aggregate.contract_versions == (first,)
    assert aggregate.resolve_exact_version(CapabilityVersion("1.0.0")) is first
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^unknown capability contract version$"
    ):
        aggregate.resolve_exact_version(CapabilityVersion("1.0.1"))
    with pytest.raises(FrozenInstanceError):
        aggregate.definition_authority = "other"  # type: ignore[misc]


def test_aggregate_enforces_explicit_compatible_and_breaking_version_rules() -> None:
    """Require compatible versions to retain a major and breaking versions to increment it."""
    initial = _build_contract_version("1.0.0")
    compatible = _build_contract_version(
        "1.1.0",
        compatibility=CapabilityCompatibilityDeclaration(
            disposition=CapabilityCompatibilityDisposition.BACKWARD_COMPATIBLE,
            predecessor=initial.contract_ref,
            replacement=None,
            reason="Adds an optional bounded field.",
        ),
    )
    aggregate = CapabilityDefinitionAggregate(
        capability_key=initial.contract_ref.capability_key,
        definition_authority="atlas capability authority",
        contract_versions=(initial, compatible),
    )
    assert aggregate.resolve_exact_version(CapabilityVersion("1.1.0")) == compatible

    invalid_breaking = _build_contract_version(
        "1.2.0",
        compatibility=CapabilityCompatibilityDeclaration(
            disposition=CapabilityCompatibilityDisposition.BREAKING,
            predecessor=initial.contract_ref,
            replacement=None,
            reason="Changes required output semantics.",
        ),
    )
    with pytest.raises(
        CapabilityDefinitionValidationError,
        match="^breaking capability version requires new major$",
    ):
        CapabilityDefinitionAggregate(
            capability_key=initial.contract_ref.capability_key,
            definition_authority="atlas capability authority",
            contract_versions=(initial, invalid_breaking),
        )


def test_aggregate_rejects_cyclic_exact_replacement_references() -> None:
    """Reject a replacement graph that could make migration semantics ambiguous."""
    first_ref = _build_contract_reference("1.0.0")
    second_ref = _build_contract_reference("2.0.0")
    first = _build_contract_version(
        "1.0.0",
        compatibility=CapabilityCompatibilityDeclaration(
            disposition=CapabilityCompatibilityDisposition.INITIAL,
            predecessor=None,
            replacement=second_ref,
            reason="Superseded by the next major contract.",
        ),
    )
    second = _build_contract_version(
        "2.0.0",
        compatibility=CapabilityCompatibilityDeclaration(
            disposition=CapabilityCompatibilityDisposition.INITIAL,
            predecessor=None,
            replacement=first_ref,
            reason="Invalid reciprocal replacement fixture.",
        ),
    )

    with pytest.raises(
        CapabilityDefinitionValidationError,
        match="^cyclic capability compatibility replacement$",
    ):
        CapabilityDefinitionAggregate(
            capability_key=first_ref.capability_key,
            definition_authority="atlas capability authority",
            contract_versions=(first, second),
        )


def test_lifecycle_transition_returns_new_aggregate_without_runtime_availability_state() -> None:
    """Produce immutable lifecycle successors using only the ADR-007 state graph."""
    draft = _build_contract_version()
    aggregate = CapabilityDefinitionAggregate(
        capability_key=draft.contract_ref.capability_key,
        definition_authority="atlas capability authority",
        contract_versions=(draft,),
    )

    experimental_aggregate, experimental_transition = aggregate.transition_exact_version(
        draft.contract_ref, CapabilityLifecycleState.EXPERIMENTAL
    )
    published_aggregate, published_transition = experimental_aggregate.transition_exact_version(
        draft.contract_ref, CapabilityLifecycleState.PUBLISHED
    )

    assert aggregate.contract_versions[0].lifecycle_state is CapabilityLifecycleState.DRAFT
    assert experimental_transition.next_state is CapabilityLifecycleState.EXPERIMENTAL
    assert published_transition.previous_state is CapabilityLifecycleState.EXPERIMENTAL
    assert (
        published_aggregate.contract_versions[0].lifecycle_state
        is CapabilityLifecycleState.PUBLISHED
    )
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^invalid capability lifecycle transition$"
    ):
        aggregate.transition_exact_version(draft.contract_ref, CapabilityLifecycleState.RETIRED)


def test_disabled_contract_returns_only_to_its_immediately_preceding_state() -> None:
    """Preserve the lifecycle state preceding a temporary fail-closed disablement."""
    published = _build_contract_version(lifecycle_state=CapabilityLifecycleState.PUBLISHED)
    aggregate = CapabilityDefinitionAggregate(
        capability_key=published.contract_ref.capability_key,
        definition_authority="atlas capability authority",
        contract_versions=(published,),
    )
    disabled_aggregate, _ = aggregate.transition_exact_version(
        published.contract_ref, CapabilityLifecycleState.DISABLED
    )

    assert (
        disabled_aggregate.contract_versions[0].disabled_from is CapabilityLifecycleState.PUBLISHED
    )
    with pytest.raises(
        CapabilityDefinitionValidationError, match="^invalid capability lifecycle transition$"
    ):
        disabled_aggregate.transition_exact_version(
            published.contract_ref, CapabilityLifecycleState.DEPRECATED
        )
    restored_aggregate, _ = disabled_aggregate.transition_exact_version(
        published.contract_ref, CapabilityLifecycleState.PUBLISHED
    )
    assert restored_aggregate.contract_versions[0].disabled_from is None


@pytest.mark.parametrize(
    ("initial_state", "next_state"),
    (
        (CapabilityLifecycleState.DRAFT, CapabilityLifecycleState.EXPERIMENTAL),
        (CapabilityLifecycleState.EXPERIMENTAL, CapabilityLifecycleState.PUBLISHED),
        (CapabilityLifecycleState.EXPERIMENTAL, CapabilityLifecycleState.DEPRECATED),
        (CapabilityLifecycleState.PUBLISHED, CapabilityLifecycleState.DEPRECATED),
        (CapabilityLifecycleState.PUBLISHED, CapabilityLifecycleState.DISABLED),
        (CapabilityLifecycleState.DEPRECATED, CapabilityLifecycleState.RETIRED),
        (CapabilityLifecycleState.DEPRECATED, CapabilityLifecycleState.DISABLED),
        (CapabilityLifecycleState.RETIRED, CapabilityLifecycleState.ARCHIVED),
        (CapabilityLifecycleState.ARCHIVED, CapabilityLifecycleState.REMOVED),
    ),
)
def test_lifecycle_allows_every_normal_adr_transition(
    initial_state: CapabilityLifecycleState,
    next_state: CapabilityLifecycleState,
) -> None:
    """Allow every non-return normal transition specified by the approved lifecycle graph."""
    version = _build_contract_version(lifecycle_state=initial_state)

    successor, transition = version.transition_to(next_state)

    assert transition.previous_state is initial_state
    assert successor.lifecycle_state is next_state


def test_capability_definition_module_has_no_framework_or_runtime_dependencies() -> None:
    """Keep Capability Definition contracts independent from forbidden runtime boundaries."""
    syntax_tree = ast.parse(_CONTRACTS_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    )

    assert imported_modules.isdisjoint(
        {
            "django",
            "BotPlatform",
            "BotServer",
            "MCPServer",
            "CloudServices",
            "models",
            "providers",
            "bifs",
            "execution",
            "catalog",
            "search",
            "settings",
        }
    )
