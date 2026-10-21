"""Validate persisted authority before asynchronous worker execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Never, cast
from uuid import UUID

from resource_registry.authorization.submission_authority import (
    SUBMISSION_AUTHORITY_METADATA_KEY,
    SUBMISSION_AUTHORITY_SCHEMA_VERSION,
    SubmissionAuthorityEnvelope,
)
from resource_registry.models import (
    DatasetIntelligenceJob,
    DatasetIntelligenceJobOperation,
    DatasetQueryJob,
    RegistryNamespace,
)
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.services.async_job_idempotency import build_async_job_request_fingerprint

_CONTEXT_PAIR_LENGTH = 2


class WorkerExecutionAuthorityError(ValueError):
    """Report one deterministic fail-closed worker authority violation."""

    def __init__(self, *, code: str, message: str) -> None:
        """Initialize a sanitized authority failure.

        Args:
            code: Stable diagnostic code persisted on the job.
            message: Sanitized internal diagnostic without protected resource details.
        """
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, kw_only=True)
class WorkerExecutionContext:
    """Carry immutable delegated authority for one claimed worker operation."""

    namespace_id: str
    organization_id: int
    workspace_id: int
    original_actor_id: int
    actor_type: str
    operation: str
    resource_scope: tuple[tuple[str, tuple[str, ...]], ...]
    policy_revisions: tuple[tuple[str, str | int], ...]
    job_id: str
    correlation_id: str
    authority_schema_version: int
    authority_source: str = "submission_authority"
    legacy_compatibility_state: str = "not_used"

    def __post_init__(self) -> None:
        """Reject mutable, forged, or noncanonical worker execution contexts."""
        if self.authority_source != "submission_authority":
            raise ValueError("Worker execution authority source is invalid.")
        if self.legacy_compatibility_state != "not_used":
            raise ValueError("Worker execution legacy state is invalid.")
        if self.actor_type != "user":
            raise ValueError("Worker execution actor type is invalid.")
        if self.authority_schema_version != SUBMISSION_AUTHORITY_SCHEMA_VERSION:
            raise ValueError("Worker execution authority schema is unsupported.")
        try:
            has_canonical_identity = (
                isinstance(self.namespace_id, str)
                and str(UUID(self.namespace_id)) == self.namespace_id
                and isinstance(self.job_id, str)
                and str(UUID(self.job_id)) == self.job_id
            )
        except (AttributeError, TypeError, ValueError):
            has_canonical_identity = False
        if not has_canonical_identity:
            raise ValueError("Worker execution identity is not canonical.")
        if any(
            type(identifier) is not int or identifier <= 0
            for identifier in (self.organization_id, self.workspace_id, self.original_actor_id)
        ):
            raise ValueError("Worker execution provenance is invalid.")
        if not isinstance(self.resource_scope, tuple) or any(
            not isinstance(entry, tuple)
            or len(entry) != _CONTEXT_PAIR_LENGTH
            or not isinstance(entry[1], tuple)
            for entry in self.resource_scope
        ):
            raise ValueError("Worker execution resource scope must be deeply immutable.")
        if not isinstance(self.policy_revisions, tuple) or any(
            not isinstance(entry, tuple) or len(entry) != _CONTEXT_PAIR_LENGTH
            for entry in self.policy_revisions
        ):
            raise ValueError("Worker execution policy revisions must be deeply immutable.")

    def resource_ids(self, resource_kind: str) -> frozenset[str]:
        """Return the immutable authorized identifiers for one resource kind.

        Args:
            resource_kind: Persisted resource-scope collection name.

        Returns:
            Authorized canonical UUID strings, or an empty set when omitted.
        """
        return frozenset(dict(self.resource_scope).get(resource_kind, ()))

    def to_read_authorization_context(self) -> ReadAuthorizationContext:
        """Build a bounded legacy read context without administrator authority."""
        return ReadAuthorizationContext(
            tenant_id=self.namespace_id,
            actor=f"{self.actor_type}:{self.original_actor_id}",
            roles=("reader",),
            scopes=(),
            allowed_dataset_ids=self.resource_ids("dataset_ids"),
            metadata={
                "source": "delegated_job_authority",
                "job_id": self.job_id,
                "organization_id": self.organization_id,
                "workspace_id": self.workspace_id,
                "correlation_id": self.correlation_id,
                "authority_schema_version": self.authority_schema_version,
            },
        )


class WorkerExecutionAuthorityService:
    """Validate job-bound submission authority and build worker execution contexts."""

    _QUERY_OPERATION: ClassVar[str] = "resource_registry_start_query_job"
    _INTELLIGENCE_OPERATION_PREFIX: ClassVar[str] = "resource_registry_start_intelligence_job:"
    _QUERY_RESOURCE_KINDS: ClassVar[frozenset[str]] = frozenset(
        {"dataset_ids", "dataset_version_ids"}
    )
    _INTELLIGENCE_RESOURCE_KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "dataset_ids",
            "dataset_version_ids",
            "export_plan_ids",
            "workbook_group_ids",
        }
    )

    def build_query_context(self, *, job: DatasetQueryJob) -> WorkerExecutionContext:
        """Validate one Query job and return its immutable execution authority.

        Args:
            job: Claimed persisted Query job.

        Returns:
            Bounded worker execution context.

        Raises:
            WorkerExecutionAuthorityError: If authority is absent, malformed, or inconsistent.
        """
        authority = self._load_authority(job=job)
        self._validate_common_job_identity(
            job=job,
            authority=authority,
            expected_operation=self._QUERY_OPERATION,
            allowed_resource_kinds=self._QUERY_RESOURCE_KINDS,
        )
        dataset_id = self._canonical_job_uuid(
            job.dataset_id,
            code="worker_authority_scope_mismatch",
        )
        self._require_scoped_identifier(
            authority=authority,
            resource_kind="dataset_ids",
            identifier=dataset_id,
        )
        version_id = self._optional_canonical_job_uuid(
            job.version_id,
            code="worker_authority_scope_mismatch",
        )
        if version_id is not None:
            self._require_scoped_identifier(
                authority=authority,
                resource_kind="dataset_version_ids",
                identifier=version_id,
            )
        expected_fingerprint = build_async_job_request_fingerprint(
            {
                "job_family": "dataset_query_job",
                "tenant_id": str(job.tenant_id),
                "dataset_id": dataset_id,
                "version_id": version_id,
                "sql_text": str(job.sql_text or "").strip(),
                "submission_authority": authority.idempotency_scope(),
            }
        )
        self._require_fingerprint(job=job, expected_fingerprint=expected_fingerprint)
        return self._build_context(job=job, authority=authority)

    def build_intelligence_context(
        self,
        *,
        job: DatasetIntelligenceJob,
    ) -> WorkerExecutionContext:
        """Validate one Intelligence job and return its immutable execution authority.

        Args:
            job: Claimed persisted Intelligence job.

        Returns:
            Bounded worker execution context.

        Raises:
            WorkerExecutionAuthorityError: If authority is absent, malformed, or inconsistent.
        """
        authority = self._load_authority(job=job)
        operation = str(job.operation or "").strip().lower()
        self._validate_common_job_identity(
            job=job,
            authority=authority,
            expected_operation=f"{self._INTELLIGENCE_OPERATION_PREFIX}{operation}",
            allowed_resource_kinds=self._INTELLIGENCE_RESOURCE_KINDS,
        )
        self._validate_intelligence_resources(job=job, authority=authority)
        expected_fingerprint = build_async_job_request_fingerprint(
            {
                "job_family": "dataset_intelligence_job",
                "tenant_id": str(job.tenant_id),
                "operation": operation,
                "dataset_id": str(job.dataset_id) if job.dataset_id else None,
                "version_id": str(job.version_id) if job.version_id else None,
                "export_plan_id": str(job.export_plan_id) if job.export_plan_id else None,
                "request_payload": job.request_payload_json,
                "job_spec": job.job_spec_json or {},
                "submission_authority": authority.idempotency_scope(),
            }
        )
        self._require_fingerprint(job=job, expected_fingerprint=expected_fingerprint)
        return self._build_context(job=job, authority=authority)

    def _validate_common_job_identity(
        self,
        *,
        job: DatasetQueryJob | DatasetIntelligenceJob,
        authority: SubmissionAuthorityEnvelope,
        expected_operation: str,
        allowed_resource_kinds: frozenset[str],
    ) -> None:
        if str(job.tenant_id) != authority.namespace_id:
            self._fail("worker_authority_namespace_mismatch")
        if authority.operation != expected_operation:
            self._fail("worker_authority_operation_mismatch")
        if not set(dict(authority.resource_scope)).issubset(allowed_resource_kinds):
            self._fail("worker_authority_scope_mismatch")
        creator_id = getattr(job, "created_by_id", None)
        if creator_id is not None and creator_id != authority.actor_id:
            self._fail("worker_authority_actor_mismatch")
        provenance = (
            RegistryNamespace.objects.filter(id=authority.namespace_id)
            .values_list("workspace_id", "workspace__organization_id")
            .first()
        )
        if provenance is None:
            self._fail("worker_authority_namespace_mismatch")
        workspace_id, organization_id = provenance
        if workspace_id != authority.workspace_id:
            self._fail("worker_authority_workspace_mismatch")
        if organization_id != authority.organization_id:
            self._fail("worker_authority_organization_mismatch")

    def _validate_intelligence_resources(  # noqa: PLR0912
        self,
        *,
        job: DatasetIntelligenceJob,
        authority: SubmissionAuthorityEnvelope,
    ) -> None:
        operation = str(job.operation or "").strip().lower()
        required_resources_by_operation: Mapping[
            str,
            tuple[tuple[UUID | None, str], ...],
        ] = {
            DatasetIntelligenceJobOperation.ANALYSIS: ((job.dataset_id, "dataset_ids"),),
            DatasetIntelligenceJobOperation.CHART: ((job.dataset_id, "dataset_ids"),),
            DatasetIntelligenceJobOperation.EXPORT_PLAN: ((job.dataset_id, "dataset_ids"),),
            DatasetIntelligenceJobOperation.ARTIFACT_GENERATION: (
                (job.dataset_id, "dataset_ids"),
                (job.export_plan_id, "export_plan_ids"),
            ),
        }
        required_resources = required_resources_by_operation.get(operation, ())
        if any(value is None or not str(value).strip() for value, _kind in required_resources):
            self._fail("worker_authority_scope_mismatch")
        for value, resource_kind in (
            (job.dataset_id, "dataset_ids"),
            (job.version_id, "dataset_version_ids"),
            (job.export_plan_id, "export_plan_ids"),
        ):
            identifier = self._optional_canonical_job_uuid(
                value,
                code="worker_authority_scope_mismatch",
            )
            if identifier is not None:
                self._require_scoped_identifier(
                    authority=authority,
                    resource_kind=resource_kind,
                    identifier=identifier,
                )
        for _value, resource_kind in required_resources:
            if not dict(authority.resource_scope).get(resource_kind):
                self._fail("worker_authority_scope_mismatch")

        if operation != DatasetIntelligenceJobOperation.TRANSFORMATION:
            return
        job_spec = job.job_spec_json if isinstance(job.job_spec_json, dict) else {}
        raw_inputs = job_spec.get("inputs")
        inputs = cast(dict[str, object], raw_inputs) if isinstance(raw_inputs, dict) else {}
        raw_scope = inputs.get("scope")
        scope = cast(dict[str, object], raw_scope) if isinstance(raw_scope, dict) else {}
        scope_type = str(scope.get("type") or "").strip().lower()
        scope_id = self._optional_canonical_job_uuid(
            scope.get("id"),
            code="worker_authority_scope_mismatch",
        )
        if scope_type == "dataset" and scope_id is not None:
            if (
                self._optional_canonical_job_uuid(
                    job.dataset_id,
                    code="worker_authority_scope_mismatch",
                )
                != scope_id
            ):
                self._fail("worker_authority_scope_mismatch")
            self._require_scoped_identifier(
                authority=authority,
                resource_kind="dataset_ids",
                identifier=scope_id,
            )
        elif scope_type == "workbook_group" and scope_id is not None:
            if any((job.dataset_id, job.version_id, job.export_plan_id)):
                self._fail("worker_authority_scope_mismatch")
            self._require_scoped_identifier(
                authority=authority,
                resource_kind="workbook_group_ids",
                identifier=scope_id,
            )
        elif scope_type == "query_result":
            if any((job.dataset_id, job.version_id, job.export_plan_id)) or any(
                values for _kind, values in authority.resource_scope
            ):
                self._fail("worker_authority_scope_mismatch")
        else:
            self._fail("worker_authority_scope_mismatch")

    def _load_authority(
        self,
        *,
        job: DatasetQueryJob | DatasetIntelligenceJob,
    ) -> SubmissionAuthorityEnvelope:
        metadata = job.metadata_json if isinstance(job.metadata_json, dict) else {}
        raw_authority = metadata.get(SUBMISSION_AUTHORITY_METADATA_KEY)
        if raw_authority is None:
            self._fail("worker_authority_missing")
        if (
            isinstance(raw_authority, Mapping)
            and raw_authority.get("schema_version") != SUBMISSION_AUTHORITY_SCHEMA_VERSION
        ):
            self._fail("worker_authority_unsupported_schema")
        try:
            return SubmissionAuthorityEnvelope.from_dict(raw_authority)
        except (TypeError, ValueError) as error:
            raise WorkerExecutionAuthorityError(
                code="worker_authority_malformed",
                message="Persisted worker execution authority is malformed.",
            ) from error

    @staticmethod
    def _require_scoped_identifier(
        *,
        authority: SubmissionAuthorityEnvelope,
        resource_kind: str,
        identifier: str,
    ) -> None:
        if identifier not in dict(authority.resource_scope).get(resource_kind, ()):
            WorkerExecutionAuthorityService._fail("worker_authority_scope_mismatch")

    @staticmethod
    def _require_fingerprint(
        *,
        job: DatasetQueryJob | DatasetIntelligenceJob,
        expected_fingerprint: str,
    ) -> None:
        if job.request_fingerprint != expected_fingerprint:
            WorkerExecutionAuthorityService._fail("worker_authority_fingerprint_mismatch")

    @staticmethod
    def _build_context(
        *,
        job: DatasetQueryJob | DatasetIntelligenceJob,
        authority: SubmissionAuthorityEnvelope,
    ) -> WorkerExecutionContext:
        return WorkerExecutionContext(
            namespace_id=authority.namespace_id,
            organization_id=authority.organization_id,
            workspace_id=authority.workspace_id,
            original_actor_id=authority.actor_id,
            actor_type=authority.actor_type,
            operation=authority.operation,
            resource_scope=authority.resource_scope,
            policy_revisions=authority.policy_revisions,
            job_id=WorkerExecutionAuthorityService._canonical_job_uuid(
                job.id,
                code="worker_authority_job_mismatch",
            ),
            correlation_id=authority.correlation_id,
            authority_schema_version=authority.schema_version,
        )

    @staticmethod
    def _optional_canonical_job_uuid(value: object, *, code: str) -> str | None:
        if value is None or not str(value).strip():
            return None
        return WorkerExecutionAuthorityService._canonical_job_uuid(value, code=code)

    @staticmethod
    def _canonical_job_uuid(value: object, *, code: str) -> str:
        try:
            return str(UUID(str(value).strip()))
        except (AttributeError, TypeError, ValueError) as error:
            raise WorkerExecutionAuthorityError(
                code=code,
                message="Persisted job authority identity is invalid.",
            ) from error

    @staticmethod
    def _fail(code: str) -> Never:
        raise WorkerExecutionAuthorityError(
            code=code,
            message="Persisted worker execution authority is not valid for this job.",
        )
