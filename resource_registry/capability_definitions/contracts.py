"""Define immutable provider-neutral capability definition contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from .errors import CapabilityDefinitionValidationError

__all__ = (  # noqa: RUF022
    "CapabilityKey",
    "CapabilityVersion",
    "CapabilityContractRef",
    "CapabilityType",
    "CapabilityEffectCategory",
    "CapabilityIdempotency",
    "CapabilityRetryPolicy",
    "CapabilityCancellationMode",
    "CapabilityExecutionMode",
    "CapabilityTimingClass",
    "CapabilityDeadlineBehavior",
    "CapabilityDeadlineClass",
    "CapabilityLifecycleState",
    "CapabilityCompatibilityDisposition",
    "CapabilitySchema",
    "CapabilityErrorContract",
    "CapabilityEffectContract",
    "CapabilityTimingContract",
    "CapabilityObservabilityContract",
    "CapabilitySecurityContract",
    "CapabilityCompatibilityDeclaration",
    "CapabilityLifecycleTransition",
    "CapabilityContractVersion",
    "CapabilityDefinitionAggregate",
)

_CAPABILITY_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*){2,}\Z", re.ASCII)
_SEMANTIC_VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_ERROR_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_.-]*\Z", re.ASCII)
_MAXIMUM_CAPABILITY_KEY_LENGTH = 128
_MAXIMUM_SCHEMA_BYTES = 65_536
_MAXIMUM_SCHEMA_DEPTH = 16
_MAXIMUM_SCHEMA_NODES = 512
_PROHIBITED_SCHEMA_KEYS = frozenset(
    {
        "class_path",
        "credential",
        "endpoint",
        "handler",
        "provider",
        "secret",
        "sdk_type",
        "table_name",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityKey:
    """Identify a stable technology-neutral capability semantic outcome.

    Args:
        value: Lowercase dotted semantic key with at least authority, domain, and outcome segments.

    Raises:
        CapabilityDefinitionValidationError: If the key is not an approved semantic identity.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate the semantic key without resolving any implementation.

        Raises:
            CapabilityDefinitionValidationError: If the key does not satisfy the capability grammar.
        """
        if not (
            type(self.value) is str
            and len(self.value) <= _MAXIMUM_CAPABILITY_KEY_LENGTH
            and _CAPABILITY_KEY_PATTERN.fullmatch(self.value) is not None
        ):
            raise CapabilityDefinitionValidationError("invalid capability key")


@dataclass(frozen=True, slots=True, init=False, order=True)
class CapabilityVersion:
    """Identify one exact immutable semantic contract version.

    Args:
        value: Exact stable ``MAJOR.MINOR.PATCH`` semantic version text.

    Raises:
        CapabilityDefinitionValidationError: If the version is not an exact supported version.
    """

    major: int
    minor: int
    patch: int

    def __init__(self, value: str) -> None:
        """Parse and store an exact portable semantic version.

        Args:
            value: Exact stable ``MAJOR.MINOR.PATCH`` version text.

        Raises:
            CapabilityDefinitionValidationError: If prerelease, build, or malformed text is
                supplied.
        """
        if type(value) is not str:
            raise CapabilityDefinitionValidationError("invalid capability version")
        match = _SEMANTIC_VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise CapabilityDefinitionValidationError("invalid capability version")
        object.__setattr__(self, "major", int(match.group(1)))
        object.__setattr__(self, "minor", int(match.group(2)))
        object.__setattr__(self, "patch", int(match.group(3)))

    def __str__(self) -> str:
        """Render the exact portable semantic version.

        Returns:
            The canonical ``MAJOR.MINOR.PATCH`` text.
        """
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class CapabilityContractRef:
    """Reference one exact portable capability contract version.

    Args:
        capability_key: Stable capability semantic identity.
        capability_version: Exact immutable semantic contract version.

    Raises:
        CapabilityDefinitionValidationError: If either identity component is invalid.
    """

    capability_key: CapabilityKey
    capability_version: CapabilityVersion

    def __post_init__(self) -> None:
        """Validate reference components in declaration order.

        Raises:
            CapabilityDefinitionValidationError: If a component is not an exact capability value.
        """
        if not isinstance(self.capability_key, CapabilityKey):
            raise CapabilityDefinitionValidationError("invalid capability contract reference key")
        if not isinstance(self.capability_version, CapabilityVersion):
            raise CapabilityDefinitionValidationError(
                "invalid capability contract reference version"
            )


class CapabilityType(StrEnum):
    """Classify the primary semantic category of a capability contract."""

    READ = "read"
    WRITE = "write"
    TRANSFORMATION = "transformation"
    EXECUTION = "execution"
    RENDERING = "rendering"
    EXPORT = "export"
    DISCOVERY = "discovery"
    PROVIDER = "provider"
    AI = "ai"
    ADMINISTRATIVE = "administrative"
    GOVERNANCE = "governance"
    COMPOSITE = "composite"


class CapabilityEffectCategory(StrEnum):
    """Classify a declared semantic side-effect category."""

    NONE = "none"
    RESOURCE_STATE = "resource_state"
    ARTIFACT = "artifact"
    EXTERNAL = "external"
    OPERATIONAL = "operational"


class CapabilityIdempotency(StrEnum):
    """Classify the contract-level idempotency obligation."""

    INHERENT = "inherent"
    KEY_REQUIRED = "key_required"
    KEY_OPTIONAL = "key_optional"
    NOT_IDEMPOTENT = "not_idempotent"


class CapabilityRetryPolicy(StrEnum):
    """Classify whether a future execution may retry a declared failure."""

    NEVER = "never"
    SAFE = "safe"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class CapabilityCancellationMode(StrEnum):
    """Classify the contract-level cancellation guarantee."""

    UNSUPPORTED = "unsupported"
    BEFORE_START = "before_start"
    COOPERATIVE = "cooperative"
    GUARANTEED_BOUNDARY = "guaranteed_boundary"


class CapabilityExecutionMode(StrEnum):
    """Classify an execution interaction mode declared by a contract."""

    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"
    LONG_RUNNING = "long_running"
    STREAMING = "streaming"


class CapabilityTimingClass(StrEnum):
    """Classify the expected latency profile without defining a concrete timeout."""

    INTERACTIVE = "interactive"
    BACKGROUND = "background"
    LONG_RUNNING = "long_running"


class CapabilityDeadlineBehavior(StrEnum):
    """Classify how a future executor must treat a caller-supplied deadline."""

    REQUIRED = "required"
    HONORED = "honored"
    UNSUPPORTED = "unsupported"


class CapabilityDeadlineClass(StrEnum):
    """Classify a portable maximum deadline without defining a concrete limit."""

    NONE = "none"
    SHORT = "short"
    STANDARD = "standard"
    EXTENDED = "extended"


class CapabilityLifecycleState(StrEnum):
    """Classify the lifecycle state of one capability contract version."""

    DRAFT = "draft"
    EXPERIMENTAL = "experimental"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"
    RETIRED = "retired"
    ARCHIVED = "archived"
    REMOVED = "removed"


class CapabilityCompatibilityDisposition(StrEnum):
    """Classify an explicit relationship to a predecessor contract version."""

    INITIAL = "initial"
    BACKWARD_COMPATIBLE = "backward_compatible"
    BREAKING = "breaking"


@dataclass(frozen=True, slots=True)
class CapabilitySchema:
    """Declare one bounded provider-neutral schema document.

    Args:
        profile_key: Stable schema profile identity.
        profile_version: Exact profile version text.
        schema_json: JSON object text describing only the portable contract shape.

    Raises:
        CapabilityDefinitionValidationError: If the profile or bounded schema is invalid.
    """

    profile_key: str
    profile_version: str
    schema_json: str

    def __post_init__(self) -> None:
        """Validate a bounded schema declaration without evaluating payload instances.

        Raises:
            CapabilityDefinitionValidationError: If the schema is malformed or exceeds its bounds.
        """
        if not _is_non_blank_string(self.profile_key) or not _is_non_blank_string(
            self.profile_version
        ):
            raise CapabilityDefinitionValidationError("invalid capability schema profile")
        if (
            type(self.schema_json) is not str
            or len(self.schema_json.encode("utf-8")) > _MAXIMUM_SCHEMA_BYTES
        ):
            raise CapabilityDefinitionValidationError("invalid capability schema")
        try:
            schema_value = json.loads(self.schema_json)
        except (TypeError, json.JSONDecodeError):
            raise CapabilityDefinitionValidationError("invalid capability schema") from None
        if type(schema_value) is not dict or not _is_bounded_json_value(schema_value):
            raise CapabilityDefinitionValidationError("invalid capability schema")
        canonical_schema = json.dumps(schema_value, separators=(",", ":"), sort_keys=True)
        object.__setattr__(self, "schema_json", canonical_schema)


@dataclass(frozen=True, slots=True)
class CapabilityErrorContract:
    """Declare one stable provider-neutral error outcome.

    Args:
        code: Stable lowercase portable error code.
        category: Non-blank provider-neutral error category.
        retry_policy: Declared retry requirement for a future executor.
        safe_detail: Whether safe bounded detail may be disclosed by a later adapter.

    Raises:
        CapabilityDefinitionValidationError: If the error declaration is invalid.
    """

    code: str
    category: str
    retry_policy: CapabilityRetryPolicy
    safe_detail: bool

    def __post_init__(self) -> None:
        """Validate the portable error declaration.

        Raises:
            CapabilityDefinitionValidationError: If a field is invalid.
        """
        if type(self.code) is not str or _ERROR_CODE_PATTERN.fullmatch(self.code) is None:
            raise CapabilityDefinitionValidationError("invalid capability error code")
        if not _is_non_blank_string(self.category):
            raise CapabilityDefinitionValidationError("invalid capability error category")
        if not isinstance(self.retry_policy, CapabilityRetryPolicy):
            raise CapabilityDefinitionValidationError("invalid capability error retry policy")
        if type(self.safe_detail) is not bool:
            raise CapabilityDefinitionValidationError("invalid capability error safe detail")


@dataclass(frozen=True, slots=True)
class CapabilityEffectContract:
    """Declare immutable semantic effect and operational obligation traits.

    Args:
        categories: Exact non-empty declared effect categories.
        idempotency: Contract-level idempotency requirement.
        retry_policy: Contract-level retry requirement.
        cancellation_mode: Contract-level cancellation guarantee.
        execution_modes: Exact supported interaction modes.
        atomicity_boundary: Non-blank semantic atomicity boundary label.

    Raises:
        CapabilityDefinitionValidationError: If the effect declaration is incoherent.
    """

    categories: tuple[CapabilityEffectCategory, ...]
    idempotency: CapabilityIdempotency
    retry_policy: CapabilityRetryPolicy
    cancellation_mode: CapabilityCancellationMode
    execution_modes: tuple[CapabilityExecutionMode, ...]
    atomicity_boundary: str

    def __post_init__(self) -> None:
        """Validate immutable effect traits without performing an effect.

        Raises:
            CapabilityDefinitionValidationError: If effect traits are malformed or contradictory.
        """
        if not _is_unique_tuple(self.categories, CapabilityEffectCategory):
            raise CapabilityDefinitionValidationError("invalid capability effect categories")
        if CapabilityEffectCategory.NONE in self.categories and len(self.categories) != 1:
            raise CapabilityDefinitionValidationError("invalid capability effect categories")
        if not isinstance(self.idempotency, CapabilityIdempotency):
            raise CapabilityDefinitionValidationError("invalid capability idempotency")
        if not isinstance(self.retry_policy, CapabilityRetryPolicy):
            raise CapabilityDefinitionValidationError("invalid capability retry policy")
        if not isinstance(self.cancellation_mode, CapabilityCancellationMode):
            raise CapabilityDefinitionValidationError("invalid capability cancellation mode")
        if not _is_unique_tuple(self.execution_modes, CapabilityExecutionMode):
            raise CapabilityDefinitionValidationError("invalid capability execution modes")
        if not _is_non_blank_string(self.atomicity_boundary):
            raise CapabilityDefinitionValidationError("invalid capability atomicity boundary")
        if (
            self.idempotency is CapabilityIdempotency.NOT_IDEMPOTENT
            and self.retry_policy is CapabilityRetryPolicy.SAFE
        ):
            raise CapabilityDefinitionValidationError("incoherent capability retry policy")


@dataclass(frozen=True, slots=True)
class CapabilityTimingContract:
    """Declare portable timing obligations without runtime timeout configuration.

    Args:
        expected_latency_class: Semantic latency profile expected by callers.
        deadline_behavior: Required handling of a caller-supplied deadline.
        maximum_deadline_class: Maximum portable deadline category allowed by the contract.

    Raises:
        CapabilityDefinitionValidationError: If a timing declaration is malformed or incoherent.
    """

    expected_latency_class: CapabilityTimingClass
    deadline_behavior: CapabilityDeadlineBehavior
    maximum_deadline_class: CapabilityDeadlineClass

    def __post_init__(self) -> None:
        """Validate semantic timing declarations without scheduling or enforcing work.

        Raises:
            CapabilityDefinitionValidationError: If a timing declaration is invalid.
        """
        if not isinstance(self.expected_latency_class, CapabilityTimingClass):
            raise CapabilityDefinitionValidationError("invalid capability timing class")
        if not isinstance(self.deadline_behavior, CapabilityDeadlineBehavior):
            raise CapabilityDefinitionValidationError("invalid capability deadline behavior")
        if not isinstance(self.maximum_deadline_class, CapabilityDeadlineClass):
            raise CapabilityDefinitionValidationError("invalid capability deadline class")
        if (
            self.deadline_behavior is CapabilityDeadlineBehavior.REQUIRED
            and self.maximum_deadline_class is CapabilityDeadlineClass.NONE
        ):
            raise CapabilityDefinitionValidationError("incoherent capability deadline declaration")


@dataclass(frozen=True, slots=True)
class CapabilityObservabilityContract:
    """Declare required portable correlation, metric, event, and audit categories.

    Args:
        required_correlation_fields: Exact correlation, idempotency, or provenance field labels.
        required_metric_categories: Exact metric categories a future implementation must emit.
        required_event_categories: Exact event or audit categories a future implementation must
            emit.

    Raises:
        CapabilityDefinitionValidationError: If observability declarations are malformed.
    """

    required_correlation_fields: tuple[str, ...]
    required_metric_categories: tuple[str, ...]
    required_event_categories: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate observability declarations without emitting metrics, events, or audit records.

        Raises:
            CapabilityDefinitionValidationError: If an observability declaration is malformed.
        """
        if not _is_non_empty_unique_string_tuple(self.required_correlation_fields):
            raise CapabilityDefinitionValidationError("invalid capability correlation fields")
        if not _is_non_empty_unique_string_tuple(self.required_metric_categories):
            raise CapabilityDefinitionValidationError("invalid capability metric categories")
        if not _is_non_empty_unique_string_tuple(self.required_event_categories):
            raise CapabilityDefinitionValidationError("invalid capability event categories")


@dataclass(frozen=True, slots=True)
class CapabilitySecurityContract:
    """Declare portable security and disclosure obligations without a policy decision.

    Args:
        input_classification: Non-blank input classification label.
        output_classification: Non-blank output classification label.
        required_purpose: Non-blank semantic purpose obligation.
        residency_requirement: Non-blank data residency requirement.
        prohibited_log_fields: Exact field names that a later implementation must not log.

    Raises:
        CapabilityDefinitionValidationError: If a security obligation is invalid.
    """

    input_classification: str
    output_classification: str
    required_purpose: str
    residency_requirement: str
    prohibited_log_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate portable obligations without evaluating an actor or policy.

        Raises:
            CapabilityDefinitionValidationError: If an obligation is malformed.
        """
        if not all(
            _is_non_blank_string(value)
            for value in (
                self.input_classification,
                self.output_classification,
                self.required_purpose,
                self.residency_requirement,
            )
        ):
            raise CapabilityDefinitionValidationError("invalid capability security contract")
        if (
            type(self.prohibited_log_fields) is not tuple
            or any(
                not _is_non_blank_string(field_name) for field_name in self.prohibited_log_fields
            )
            or len(set(self.prohibited_log_fields)) != len(self.prohibited_log_fields)
        ):
            raise CapabilityDefinitionValidationError("invalid capability prohibited log fields")


@dataclass(frozen=True, slots=True)
class CapabilityCompatibilityDeclaration:
    """Declare an explicit relationship to one predecessor capability contract.

    Args:
        disposition: Compatibility classification for this exact version.
        predecessor: Exact preceding contract reference, when this is not an initial version.
        replacement: Exact replacement reference known at declaration time, if any.
        reason: Non-blank bounded human-readable compatibility reason.

    Raises:
        CapabilityDefinitionValidationError: If the declaration is malformed.
    """

    disposition: CapabilityCompatibilityDisposition
    predecessor: CapabilityContractRef | None
    replacement: CapabilityContractRef | None
    reason: str

    def __post_init__(self) -> None:
        """Validate local compatibility facts before aggregate membership validation.

        Raises:
            CapabilityDefinitionValidationError: If the declaration is malformed.
        """
        if not isinstance(self.disposition, CapabilityCompatibilityDisposition):
            raise CapabilityDefinitionValidationError(
                "invalid capability compatibility disposition"
            )
        if self.predecessor is not None and not isinstance(self.predecessor, CapabilityContractRef):
            raise CapabilityDefinitionValidationError(
                "invalid capability compatibility predecessor"
            )
        if self.replacement is not None and not isinstance(self.replacement, CapabilityContractRef):
            raise CapabilityDefinitionValidationError(
                "invalid capability compatibility replacement"
            )
        if not _is_non_blank_string(self.reason):
            raise CapabilityDefinitionValidationError("invalid capability compatibility reason")
        if (
            self.disposition is CapabilityCompatibilityDisposition.INITIAL
            and self.predecessor is not None
        ):
            raise CapabilityDefinitionValidationError("invalid initial capability compatibility")
        if (
            self.disposition is not CapabilityCompatibilityDisposition.INITIAL
            and self.predecessor is None
        ):
            raise CapabilityDefinitionValidationError(
                "missing capability compatibility predecessor"
            )


@dataclass(frozen=True, slots=True)
class CapabilityLifecycleTransition:
    """Describe one pure immutable lifecycle transition result.

    Args:
        contract_ref: Exact contract version whose lifecycle changed.
        previous_state: Lifecycle state before the transition.
        next_state: Lifecycle state after the transition.

    Raises:
        CapabilityDefinitionValidationError: If transition evidence is invalid.
    """

    contract_ref: CapabilityContractRef
    previous_state: CapabilityLifecycleState
    next_state: CapabilityLifecycleState

    def __post_init__(self) -> None:
        """Validate transition evidence without authorizing or auditing it.

        Raises:
            CapabilityDefinitionValidationError: If evidence is malformed.
        """
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise CapabilityDefinitionValidationError("invalid capability lifecycle reference")
        if not isinstance(self.previous_state, CapabilityLifecycleState) or not isinstance(
            self.next_state, CapabilityLifecycleState
        ):
            raise CapabilityDefinitionValidationError("invalid capability lifecycle state")


@dataclass(frozen=True, slots=True)
class CapabilityContractVersion:
    """Bind immutable portable semantics to one exact capability contract reference.

    Args:
        contract_ref: Exact key and version for this contract.
        capability_type: Primary semantic classification.
        purpose: Non-blank provider-neutral intended outcome.
        preconditions: Exact declared conditions that must hold before future execution.
        postconditions: Exact declared guarantees after a successful outcome.
        semantic_invariants: Exact immutable semantic guarantees across all outcomes.
        input_schema: Exact bounded input schema declaration.
        output_schema: Exact bounded output schema declaration.
        errors: Stable portable error declarations.
        effect: Exact semantic effect declaration.
        timing: Exact portable timing obligations.
        observability: Exact portable correlation, metric, event, and audit obligations.
        security: Exact portable security obligations.
        compatibility: Exact predecessor/replacement declaration.
        lifecycle_state: Current immutable lifecycle state.
        disabled_from: State from which a disabled contract may return, when disabled.
        conformance_profile_ref: Non-blank exact reference to the portable conformance profile.

    Raises:
        CapabilityDefinitionValidationError: If contract content is invalid.
    """

    contract_ref: CapabilityContractRef
    capability_type: CapabilityType
    purpose: str
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    semantic_invariants: tuple[str, ...]
    input_schema: CapabilitySchema
    output_schema: CapabilitySchema
    errors: tuple[CapabilityErrorContract, ...]
    effect: CapabilityEffectContract
    timing: CapabilityTimingContract
    observability: CapabilityObservabilityContract
    security: CapabilitySecurityContract
    compatibility: CapabilityCompatibilityDeclaration
    lifecycle_state: CapabilityLifecycleState
    disabled_from: CapabilityLifecycleState | None = None
    conformance_profile_ref: str = ""

    def __post_init__(self) -> None:
        """Validate intrinsic immutable contract content.

        Raises:
            CapabilityDefinitionValidationError: If content is malformed or duplicate error codes
                exist.
        """
        if not isinstance(self.contract_ref, CapabilityContractRef):
            raise CapabilityDefinitionValidationError("invalid capability contract reference")
        if not isinstance(self.capability_type, CapabilityType):
            raise CapabilityDefinitionValidationError("invalid capability type")
        if not _is_non_blank_string(self.purpose):
            raise CapabilityDefinitionValidationError("invalid capability purpose")
        _validate_semantic_declarations(
            self.preconditions,
            self.postconditions,
            self.semantic_invariants,
        )
        if not isinstance(self.input_schema, CapabilitySchema) or not isinstance(
            self.output_schema, CapabilitySchema
        ):
            raise CapabilityDefinitionValidationError("invalid capability schema contract")
        if type(self.errors) is not tuple or not all(
            isinstance(error, CapabilityErrorContract) for error in self.errors
        ):
            raise CapabilityDefinitionValidationError("invalid capability errors")
        if len({error.code for error in self.errors}) != len(self.errors):
            raise CapabilityDefinitionValidationError("duplicate capability error code")
        if not isinstance(self.effect, CapabilityEffectContract):
            raise CapabilityDefinitionValidationError("invalid capability effect contract")
        if not isinstance(self.timing, CapabilityTimingContract):
            raise CapabilityDefinitionValidationError("invalid capability timing contract")
        if not isinstance(self.observability, CapabilityObservabilityContract):
            raise CapabilityDefinitionValidationError("invalid capability observability contract")
        if not isinstance(self.security, CapabilitySecurityContract):
            raise CapabilityDefinitionValidationError("invalid capability security contract")
        if not isinstance(self.compatibility, CapabilityCompatibilityDeclaration):
            raise CapabilityDefinitionValidationError(
                "invalid capability compatibility declaration"
            )
        _validate_lifecycle_declaration(self.lifecycle_state, self.disabled_from)
        if not _is_non_blank_string(self.conformance_profile_ref):
            raise CapabilityDefinitionValidationError(
                "invalid capability conformance profile reference"
            )

    def transition_to(
        self, next_state: CapabilityLifecycleState
    ) -> tuple[CapabilityContractVersion, CapabilityLifecycleTransition]:
        """Create an immutable successor with one allowed lifecycle transition.

        Args:
            next_state: Requested definition-version lifecycle state.

        Returns:
            The successor contract and its transition evidence.

        Raises:
            CapabilityDefinitionValidationError: If the target state is invalid or disallowed.
        """
        if not isinstance(next_state, CapabilityLifecycleState):
            raise CapabilityDefinitionValidationError("invalid capability lifecycle state")
        if next_state not in _ALLOWED_LIFECYCLE_TRANSITIONS.get(self.lifecycle_state, frozenset()):
            raise CapabilityDefinitionValidationError("invalid capability lifecycle transition")
        if (
            self.lifecycle_state is CapabilityLifecycleState.DISABLED
            and next_state is not self.disabled_from
        ):
            raise CapabilityDefinitionValidationError("invalid capability lifecycle transition")
        transition = CapabilityLifecycleTransition(
            self.contract_ref, self.lifecycle_state, next_state
        )
        return (
            replace(
                self,
                lifecycle_state=next_state,
                disabled_from=(
                    self.lifecycle_state
                    if next_state is CapabilityLifecycleState.DISABLED
                    else None
                ),
            ),
            transition,
        )


@dataclass(frozen=True, slots=True, init=False)
class CapabilityDefinitionAggregate:
    """Resolve immutable versions owned by one capability definition authority.

    Args:
        capability_key: Stable semantic identity shared by all versions.
        definition_authority: Non-blank semantic owner label.
        contract_versions: Exact contract versions in deterministic declaration order.

    Raises:
        CapabilityDefinitionValidationError: If aggregate membership or compatibility is invalid.
    """

    capability_key: CapabilityKey
    definition_authority: str
    contract_versions: tuple[CapabilityContractVersion, ...]

    def __init__(
        self,
        *,
        capability_key: CapabilityKey,
        definition_authority: str,
        contract_versions: Iterable[CapabilityContractVersion],
    ) -> None:
        """Snapshot and validate exact immutable contract versions.

        Args:
            capability_key: Stable semantic identity for all supplied versions.
            definition_authority: Opaque semantic owner identifier.
            contract_versions: Contract versions to snapshot in declaration order.

        Raises:
            CapabilityDefinitionValidationError: If ownership, versions, or compatibility are
                invalid.
        """
        if not isinstance(capability_key, CapabilityKey):
            raise CapabilityDefinitionValidationError("invalid capability definition key")
        if not _is_non_blank_string(definition_authority):
            raise CapabilityDefinitionValidationError("invalid capability definition authority")
        versions = _snapshot_contract_versions(contract_versions)
        if not versions:
            raise CapabilityDefinitionValidationError("empty capability definition")
        if any(version.contract_ref.capability_key != capability_key for version in versions):
            raise CapabilityDefinitionValidationError("capability definition key mismatch")
        refs = tuple(version.contract_ref for version in versions)
        if len(set(refs)) != len(refs):
            raise CapabilityDefinitionValidationError("duplicate capability contract version")
        _validate_aggregate_compatibility(refs, versions)
        object.__setattr__(self, "capability_key", capability_key)
        object.__setattr__(self, "definition_authority", definition_authority)
        object.__setattr__(self, "contract_versions", versions)

    def resolve_exact_version(
        self, capability_version: CapabilityVersion
    ) -> CapabilityContractVersion:
        """Resolve one explicit immutable contract version without fallback selection.

        Args:
            capability_version: Exact version to resolve.

        Returns:
            The immutable exact contract version.

        Raises:
            CapabilityDefinitionValidationError: If the version is invalid or unknown.
        """
        if not isinstance(capability_version, CapabilityVersion):
            raise CapabilityDefinitionValidationError("invalid capability version")
        for contract_version in self.contract_versions:
            if contract_version.contract_ref.capability_version == capability_version:
                return contract_version
        raise CapabilityDefinitionValidationError("unknown capability contract version")

    def transition_exact_version(
        self, contract_ref: CapabilityContractRef, next_state: CapabilityLifecycleState
    ) -> tuple[CapabilityDefinitionAggregate, CapabilityLifecycleTransition]:
        """Create an aggregate successor after one allowed exact-version transition.

        Args:
            contract_ref: Exact contract version to transition.
            next_state: Requested lifecycle state.

        Returns:
            The immutable aggregate successor and transition evidence.

        Raises:
            CapabilityDefinitionValidationError: If the reference is invalid, unknown, or
                mismatched.
        """
        if not isinstance(contract_ref, CapabilityContractRef):
            raise CapabilityDefinitionValidationError("invalid capability contract reference")
        if contract_ref.capability_key != self.capability_key:
            raise CapabilityDefinitionValidationError("capability definition key mismatch")
        current_contract = self.resolve_exact_version(contract_ref.capability_version)
        successor_contract, transition = current_contract.transition_to(next_state)
        successor_versions = tuple(
            successor_contract if version.contract_ref == contract_ref else version
            for version in self.contract_versions
        )
        return (
            CapabilityDefinitionAggregate(
                capability_key=self.capability_key,
                definition_authority=self.definition_authority,
                contract_versions=successor_versions,
            ),
            transition,
        )


_ALLOWED_LIFECYCLE_TRANSITIONS: dict[
    CapabilityLifecycleState, frozenset[CapabilityLifecycleState]
] = {
    CapabilityLifecycleState.DRAFT: frozenset({CapabilityLifecycleState.EXPERIMENTAL}),
    CapabilityLifecycleState.EXPERIMENTAL: frozenset(
        {CapabilityLifecycleState.PUBLISHED, CapabilityLifecycleState.DEPRECATED}
    ),
    CapabilityLifecycleState.PUBLISHED: frozenset(
        {CapabilityLifecycleState.DEPRECATED, CapabilityLifecycleState.DISABLED}
    ),
    CapabilityLifecycleState.DEPRECATED: frozenset(
        {CapabilityLifecycleState.RETIRED, CapabilityLifecycleState.DISABLED}
    ),
    CapabilityLifecycleState.DISABLED: frozenset(
        {CapabilityLifecycleState.PUBLISHED, CapabilityLifecycleState.DEPRECATED}
    ),
    CapabilityLifecycleState.RETIRED: frozenset({CapabilityLifecycleState.ARCHIVED}),
    CapabilityLifecycleState.ARCHIVED: frozenset({CapabilityLifecycleState.REMOVED}),
    CapabilityLifecycleState.REMOVED: frozenset(),
}


def _is_non_blank_string(candidate_value: object) -> bool:
    """Determine whether a value is a non-blank plain string.

    Args:
        candidate_value: Candidate value to validate.

    Returns:
        Whether the candidate is a non-blank plain string.
    """
    return type(candidate_value) is str and bool(candidate_value.strip())


def _validate_lifecycle_declaration(
    lifecycle_state: CapabilityLifecycleState,
    disabled_from: CapabilityLifecycleState | None,
) -> None:
    """Validate lifecycle state and a temporary-disablement return state.

    Args:
        lifecycle_state: Declared current contract lifecycle state.
        disabled_from: Prior state retained only while the contract is disabled.

    Raises:
        CapabilityDefinitionValidationError: If lifecycle state evidence is malformed.
    """
    if not isinstance(lifecycle_state, CapabilityLifecycleState):
        raise CapabilityDefinitionValidationError("invalid capability lifecycle state")
    if lifecycle_state is CapabilityLifecycleState.DISABLED:
        if disabled_from not in {
            CapabilityLifecycleState.PUBLISHED,
            CapabilityLifecycleState.DEPRECATED,
        }:
            raise CapabilityDefinitionValidationError("invalid disabled capability source state")
    elif disabled_from is not None:
        raise CapabilityDefinitionValidationError("invalid disabled capability source state")


def _is_unique_tuple(candidate_value: object, expected_type: type[StrEnum]) -> bool:
    """Determine whether an exact non-empty tuple has unique enum members.

    Args:
        candidate_value: Candidate tuple to validate.
        expected_type: Enum type required for every member.

    Returns:
        Whether the tuple is non-empty, exact, typed, and unique.
    """
    return (
        type(candidate_value) is tuple
        and bool(candidate_value)
        and all(isinstance(item, expected_type) for item in candidate_value)
        and len(set(candidate_value)) == len(candidate_value)
    )


def _is_unique_string_tuple(candidate_value: object) -> bool:
    """Determine whether an exact tuple contains unique non-blank strings.

    Args:
        candidate_value: Candidate tuple to validate.

    Returns:
        Whether the tuple contains only unique non-blank plain strings.
    """
    return (
        type(candidate_value) is tuple
        and all(_is_non_blank_string(item) for item in candidate_value)
        and len(set(candidate_value)) == len(candidate_value)
    )


def _is_non_empty_unique_string_tuple(candidate_value: object) -> bool:
    """Determine whether an exact tuple contains at least one unique non-blank string.

    Args:
        candidate_value: Candidate tuple to validate.

    Returns:
        Whether the tuple is non-empty and contains unique non-blank plain strings.
    """
    return _is_unique_string_tuple(candidate_value) and bool(candidate_value)


def _validate_semantic_declarations(
    preconditions: tuple[str, ...],
    postconditions: tuple[str, ...],
    semantic_invariants: tuple[str, ...],
) -> None:
    """Validate bounded immutable capability semantic declarations.

    Args:
        preconditions: Conditions required before a future execution.
        postconditions: Guarantees required after a successful outcome.
        semantic_invariants: Guarantees that hold for every outcome.

    Raises:
        CapabilityDefinitionValidationError: If semantic declarations are malformed.
    """
    if not _is_unique_string_tuple(preconditions):
        raise CapabilityDefinitionValidationError("invalid capability preconditions")
    if not _is_unique_string_tuple(postconditions):
        raise CapabilityDefinitionValidationError("invalid capability postconditions")
    if not _is_unique_string_tuple(semantic_invariants):
        raise CapabilityDefinitionValidationError("invalid capability semantic invariants")


def _is_bounded_json_value(candidate_value: object) -> bool:
    """Determine whether a JSON value stays inside schema declaration bounds.

    Args:
        candidate_value: Parsed JSON value to inspect.

    Returns:
        Whether the value is a bounded JSON object tree.
    """
    pending_values: list[tuple[object, int]] = [(candidate_value, 1)]
    node_count = 0
    while pending_values:
        value, depth = pending_values.pop()
        node_count += 1
        if node_count > _MAXIMUM_SCHEMA_NODES or depth > _MAXIMUM_SCHEMA_DEPTH:
            return False
        if type(value) is dict:
            if any(
                type(key) is not str or key.casefold() in _PROHIBITED_SCHEMA_KEYS for key in value
            ):
                return False
            pending_values.extend((nested_value, depth + 1) for nested_value in value.values())
        elif type(value) is list:
            pending_values.extend((nested_value, depth + 1) for nested_value in value)
        elif type(value) not in {str, int, float, bool, type(None)}:
            return False
    return True


def _snapshot_contract_versions(
    contract_versions: Iterable[CapabilityContractVersion],
) -> tuple[CapabilityContractVersion, ...]:
    """Snapshot an iterable of exact contract versions once.

    Args:
        contract_versions: Candidate iterable of immutable versions.

    Returns:
        The eager immutable version tuple.

    Raises:
        CapabilityDefinitionValidationError: If the iterable or an element is invalid.
    """
    if isinstance(contract_versions, str | bytes):
        raise CapabilityDefinitionValidationError("invalid capability contract versions")
    try:
        versions = tuple(contract_versions)
    except TypeError:
        raise CapabilityDefinitionValidationError("invalid capability contract versions") from None
    if not all(isinstance(version, CapabilityContractVersion) for version in versions):
        raise CapabilityDefinitionValidationError("invalid capability contract versions")
    return versions


def _validate_aggregate_compatibility(
    refs: tuple[CapabilityContractRef, ...], versions: tuple[CapabilityContractVersion, ...]
) -> None:
    """Validate explicit compatibility references and semantic-version policy.

    Args:
        refs: Exact aggregate contract references.
        versions: Exact aggregate contract declarations.

    Raises:
        CapabilityDefinitionValidationError: If compatibility is unknown, cyclic, or contradictory.
    """
    known_refs = set(refs)
    for version in versions:
        compatibility = version.compatibility
        predecessor = compatibility.predecessor
        replacement = compatibility.replacement
        if predecessor is not None:
            if predecessor not in known_refs or predecessor == version.contract_ref:
                raise CapabilityDefinitionValidationError(
                    "unknown capability compatibility predecessor"
                )
            if predecessor.capability_key != version.contract_ref.capability_key:
                raise CapabilityDefinitionValidationError("capability compatibility key mismatch")
            if (
                compatibility.disposition is CapabilityCompatibilityDisposition.BACKWARD_COMPATIBLE
                and (
                    predecessor.capability_version.major
                    != version.contract_ref.capability_version.major
                    or predecessor.capability_version >= version.contract_ref.capability_version
                )
            ):
                raise CapabilityDefinitionValidationError(
                    "invalid backward compatible capability version"
                )
            if compatibility.disposition is CapabilityCompatibilityDisposition.BREAKING and (
                predecessor.capability_version.major
                >= version.contract_ref.capability_version.major
            ):
                raise CapabilityDefinitionValidationError(
                    "breaking capability version requires new major"
                )
        if replacement is not None:
            if replacement not in known_refs or replacement == version.contract_ref:
                raise CapabilityDefinitionValidationError(
                    "unknown capability compatibility replacement"
                )
            if replacement.capability_key != version.contract_ref.capability_key:
                raise CapabilityDefinitionValidationError("capability compatibility key mismatch")
    _validate_replacement_acyclicity(versions)


def _validate_replacement_acyclicity(versions: tuple[CapabilityContractVersion, ...]) -> None:
    """Reject cycles in declared exact replacement references.

    Args:
        versions: Exact aggregate contract declarations.

    Raises:
        CapabilityDefinitionValidationError: If replacement references form a cycle.
    """
    replacements = {
        version.contract_ref: version.compatibility.replacement
        for version in versions
        if version.compatibility.replacement is not None
    }
    for starting_ref in replacements:
        visited_refs: set[CapabilityContractRef] = set()
        current_ref: CapabilityContractRef | None = starting_ref
        while current_ref is not None:
            if current_ref in visited_refs:
                raise CapabilityDefinitionValidationError(
                    "cyclic capability compatibility replacement"
                )
            visited_refs.add(current_ref)
            current_ref = replacements.get(current_ref)
