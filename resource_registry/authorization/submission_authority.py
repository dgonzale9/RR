"""Build immutable versioned authority for asynchronous submissions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias
from uuid import UUID

from django.utils import timezone

AuthorityScalar: TypeAlias = str | int
AuthorityResourceScope: TypeAlias = tuple[tuple[str, tuple[str, ...]], ...]
AuthorityPolicyRevisions: TypeAlias = tuple[tuple[str, AuthorityScalar], ...]

SUBMISSION_AUTHORITY_SCHEMA_VERSION = 1
SUBMISSION_AUTHORITY_METADATA_KEY = "submission_authority"
MAX_SUBMISSION_CORRELATION_ID_LENGTH = 128
_AUTHORITY_PAIR_LENGTH = 2


@dataclass(frozen=True, kw_only=True)
class SubmissionAuthorityEnvelope:
    """Represent immutable request-time authority without transport state."""

    namespace_id: str
    organization_id: int
    workspace_id: int
    actor_id: int
    operation: str
    resource_scope: AuthorityResourceScope
    policy_revisions: AuthorityPolicyRevisions
    correlation_id: str
    submitted_at: datetime
    actor_type: str = "user"
    schema_version: int = SUBMISSION_AUTHORITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Reject forged or non-canonical authority envelopes."""
        if self.schema_version != SUBMISSION_AUTHORITY_SCHEMA_VERSION:
            raise ValueError("Submission authority schema_version is unsupported.")
        if self.actor_type != "user":
            raise ValueError("Submission authority actor_type is unsupported.")
        if (
            not isinstance(self.namespace_id, str)
            or str(UUID(self.namespace_id)) != self.namespace_id
        ):
            raise ValueError("Submission authority namespace_id must be canonical.")
        if any(
            type(identifier) is not int or identifier <= 0
            for identifier in (self.organization_id, self.workspace_id, self.actor_id)
        ):
            raise ValueError("Submission authority identifiers must be positive integers.")
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("Submission authority operation is required.")
        if (
            not isinstance(self.correlation_id, str)
            or not self.correlation_id.strip()
            or len(self.correlation_id) > MAX_SUBMISSION_CORRELATION_ID_LENGTH
        ):
            raise ValueError("Submission authority correlation_id is invalid.")
        self._validate_resource_scope()
        self._validate_policy_revisions()
        if not isinstance(self.submitted_at, datetime) or timezone.is_naive(self.submitted_at):
            raise ValueError("Submission authority submitted_at must be timezone-aware.")

    def _validate_resource_scope(self) -> None:
        """Require deeply immutable, canonical, deterministically ordered resource scope."""
        if not isinstance(self.resource_scope, tuple):
            raise ValueError("Submission authority resource_scope must be immutable.")
        normalized_scope: list[tuple[str, tuple[str, ...]]] = []
        for entry in self.resource_scope:
            if not isinstance(entry, tuple) or len(entry) != _AUTHORITY_PAIR_LENGTH:
                raise ValueError("Submission authority resource_scope is invalid.")
            key, values = entry
            if not isinstance(key, str) or not key.strip() or not isinstance(values, tuple):
                raise ValueError("Submission authority resource_scope is invalid.")
            try:
                canonical_values = tuple(str(UUID(value)) for value in values)
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError("Submission authority resource_scope is invalid.") from error
            if canonical_values != values or tuple(sorted(values)) != values:
                raise ValueError("Submission authority resource_scope is not canonical.")
            normalized_scope.append((key, values))
        if tuple(sorted(normalized_scope)) != self.resource_scope:
            raise ValueError("Submission authority resource_scope is not deterministic.")

    def _validate_policy_revisions(self) -> None:
        """Require immutable and deterministically ordered available policy revisions."""
        if not isinstance(self.policy_revisions, tuple):
            raise ValueError("Submission authority policy_revisions must be immutable.")
        for entry in self.policy_revisions:
            if not isinstance(entry, tuple) or len(entry) != _AUTHORITY_PAIR_LENGTH:
                raise ValueError("Submission authority policy_revisions are invalid.")
            key, value = entry
            if (
                not isinstance(key, str)
                or not key.strip()
                or isinstance(value, bool)
                or not isinstance(value, str | int)
                or not str(value).strip()
            ):
                raise ValueError("Submission authority policy_revisions are invalid.")
        revision_keys = tuple(key for key, _value in self.policy_revisions)
        if revision_keys != tuple(sorted(set(revision_keys))):
            raise ValueError("Submission authority policy_revisions are not deterministic.")

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible persisted representation."""
        return {
            "schema_version": self.schema_version,
            "namespace_id": self.namespace_id,
            "organization_id": self.organization_id,
            "workspace_ids": [self.workspace_id],
            "actor": {"type": self.actor_type, "id": self.actor_id},
            "operation": self.operation,
            "resource_scope": {key: list(values) for key, values in self.resource_scope},
            "policy_revisions": dict(self.policy_revisions),
            "correlation_id": self.correlation_id,
            "submitted_at": self.submitted_at.isoformat(),
        }

    def idempotency_scope(self) -> dict[str, object]:
        """Return stable authority identity excluding time and correlation metadata."""
        return {
            "schema_version": self.schema_version,
            "namespace_id": self.namespace_id,
            "organization_id": self.organization_id,
            "workspace_ids": [self.workspace_id],
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "operation": self.operation,
            "resource_scope": {key: list(values) for key, values in self.resource_scope},
            "policy_revisions": dict(self.policy_revisions),
        }

    @classmethod
    def from_dict(cls, value: object) -> SubmissionAuthorityEnvelope:
        """Deserialize one exact persisted authority representation.

        Args:
            value: Persisted JSON authority object.

        Returns:
            Deeply immutable validated authority envelope.

        Raises:
            ValueError: If the representation is missing, malformed, or contains extra fields.
        """
        if not isinstance(value, Mapping):
            raise ValueError("Submission authority must be an object.")
        expected_keys = {
            "schema_version",
            "namespace_id",
            "organization_id",
            "workspace_ids",
            "actor",
            "operation",
            "resource_scope",
            "policy_revisions",
            "correlation_id",
            "submitted_at",
        }
        if set(value) != expected_keys:
            raise ValueError("Submission authority fields are invalid.")
        workspace_ids = value["workspace_ids"]
        actor = value["actor"]
        resource_scope = value["resource_scope"]
        policy_revisions = value["policy_revisions"]
        if (
            type(value["schema_version"]) is not int
            or type(value["organization_id"]) is not int
            or not isinstance(value["namespace_id"], str)
            or not isinstance(value["operation"], str)
            or not isinstance(value["correlation_id"], str)
            or not isinstance(value["submitted_at"], str)
        ):
            raise ValueError("Submission authority scalar fields are invalid.")
        if (
            not isinstance(workspace_ids, list)
            or len(workspace_ids) != 1
            or type(workspace_ids[0]) is not int
        ):
            raise ValueError("Submission authority Workspace provenance is invalid.")
        if not isinstance(actor, Mapping) or set(actor) != {"type", "id"}:
            raise ValueError("Submission authority actor is invalid.")
        if not isinstance(actor["type"], str) or type(actor["id"]) is not int:
            raise ValueError("Submission authority actor is invalid.")
        if not isinstance(resource_scope, Mapping) or not all(
            isinstance(key, str)
            and isinstance(values, list)
            and all(isinstance(item, str) for item in values)
            for key, values in resource_scope.items()
        ):
            raise ValueError("Submission authority resource_scope is invalid.")
        if not isinstance(policy_revisions, Mapping):
            raise ValueError("Submission authority policy_revisions are invalid.")
        try:
            submitted_at = datetime.fromisoformat(str(value["submitted_at"]))
            envelope = SubmissionAuthorityEnvelopeBuilder().build(
                namespace_id=str(value["namespace_id"]),
                organization_id=value["organization_id"],
                workspace_id=workspace_ids[0],
                actor_id=actor["id"],
                operation=value["operation"],
                resource_scope={str(key): tuple(values) for key, values in resource_scope.items()},
                correlation_id=value["correlation_id"],
                policy_revisions={str(key): revision for key, revision in policy_revisions.items()},
                submitted_at=submitted_at,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Submission authority representation is invalid.") from error
        if envelope.schema_version != value["schema_version"] or envelope.to_dict() != dict(value):
            raise ValueError("Submission authority representation is not canonical.")
        return envelope


class SubmissionAuthorityEnvelopeBuilder:
    """Validate and build one deterministic asynchronous submission authority envelope."""

    def build(  # noqa: PLR0913
        self,
        *,
        namespace_id: str,
        organization_id: int,
        workspace_id: int,
        actor_id: int,
        operation: str,
        resource_scope: Mapping[str, Sequence[str]],
        correlation_id: str,
        policy_revisions: Mapping[str, AuthorityScalar] | None = None,
        submitted_at: datetime | None = None,
    ) -> SubmissionAuthorityEnvelope:
        """Build immutable authority from canonical identifiers and authorized resources.

        Args:
            namespace_id: Canonical Registry Namespace UUID.
            organization_id: Trusted persisted Organization primary key.
            workspace_id: Workspace primary key that owns the namespace.
            actor_id: Authenticated persisted user primary key.
            operation: Exact submitted operation identifier.
            resource_scope: Authorized resource UUIDs grouped by resource kind.
            correlation_id: Sanitized request correlation identifier.
            policy_revisions: Available authorization or governance policy revisions.
            submitted_at: Optional deterministic clock value for tests.

        Returns:
            Frozen versioned authority envelope.

        Raises:
            ValueError: If any identity, resource, or metadata value is invalid.
        """
        canonical_namespace_id = self._canonical_uuid(namespace_id, "namespace_id")
        if organization_id <= 0 or workspace_id <= 0 or actor_id <= 0:
            raise ValueError("Submission authority identifiers must be positive integers.")
        operation_value = str(operation or "").strip()
        correlation_value = str(correlation_id or "").strip()
        if not operation_value:
            raise ValueError("Submission authority operation is required.")
        if not correlation_value or len(correlation_value) > MAX_SUBMISSION_CORRELATION_ID_LENGTH:
            raise ValueError("Submission authority correlation_id is invalid.")

        normalized_scope = tuple(
            (
                str(key),
                tuple(
                    sorted(
                        self._canonical_uuid(value, str(key))
                        for value in values
                        if str(value).strip()
                    )
                ),
            )
            for key, values in sorted(resource_scope.items())
        )
        normalized_revisions = tuple(
            (str(key), value)
            for key, value in sorted((policy_revisions or {}).items())
            if not isinstance(value, bool) and isinstance(value, str | int) and str(value).strip()
        )
        active_submitted_at = submitted_at or timezone.now()
        if timezone.is_naive(active_submitted_at):
            raise ValueError("Submission authority submitted_at must be timezone-aware.")
        return SubmissionAuthorityEnvelope(
            namespace_id=canonical_namespace_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            operation=operation_value,
            resource_scope=normalized_scope,
            policy_revisions=normalized_revisions,
            correlation_id=correlation_value,
            submitted_at=active_submitted_at,
        )

    @staticmethod
    def _canonical_uuid(value: object, field_name: str) -> str:
        try:
            return str(UUID(str(value).strip()))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"Submission authority {field_name} must contain UUIDs.") from error
