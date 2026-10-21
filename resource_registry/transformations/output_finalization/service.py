from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from resource_registry.events.audit_service import AuditService
from resource_registry.events.event_types import EventType
from resource_registry.models import (
    ArtifactFormat,
    ArtifactRole,
    Dataset,
    DatasetArtifact,
    DatasetSourceType,
    DatasetStatus,
    DatasetVersion,
    DatasetVersionStatus,
    LineageType,
    TransformationRun,
    TransformationRunOutput,
    TransformationRunOutputKind,
    TransformationRunOutputPolicyKind,
    TransformationRunOutputStatus,
    TransformationRunStatus,
    TransformationRunStepAttempt,
    TransformationRunStepAttemptStatus,
)
from resource_registry.security.integrity import ArtifactIntegrityVerifier
from resource_registry.services.registry.artifact_lifecycle_service import ArtifactLifecycleService
from resource_registry.services.registry.dataset_registry_service import DatasetRegistryService
from resource_registry.services.registry.lineage_service import LineageService
from resource_registry.storage.base import StorageObjectMetadata, StorageProvider
from resource_registry.storage.factory import DataRegistryStorageFactory
from resource_registry.storage.read_adapters import (
    ArtifactReadAdapter,
    LocalArtifactReadAdapter,
    StorageProviderArtifactReadAdapter,
)
from resource_registry.transformations.execution.references import (
    deserialize_output_references,
    validate_canonical_reference,
)
from resource_registry.transformations.execution_plan import (
    TransformationExecutionPlan,
    deserialize_transformation_execution_plan,
)
from resource_registry.transformations.operational.logging import (
    TransformationOperationalLogContextBuilder,
    get_transformation_operational_logger,
)
from resource_registry.transformations.operational.metrics import TransformationOperationalMetricsPort
from resource_registry.transformations.output_finalization.contracts import (
    TransformationOutputFinalizationClaim,
)
from resource_registry.transformations.providers import TransformationProviderOutputReference
from resource_registry.transformations.runs.output_policies import (
    build_output_destination_key,
    calculate_output_policy_checksum,
)

logger = logging.getLogger(__name__)
operational_logger = get_transformation_operational_logger()
_SHA256_HEX_LENGTH = 64


class TransformationOutputFinalizationError(RuntimeError):
    """Raise when durable output finalization cannot safely continue."""

    def __init__(self, code: str, message: str, *, is_permanent: bool) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.is_permanent = is_permanent


@dataclass(frozen=True)
class TransformationOutputFinalizationResult:
    """Describe the durable result of one run finalization invocation."""

    run_id: UUID
    tenant_id: UUID
    status: str
    materialized_output_count: int
    registered_output_count: int
    failed_output_count: int
    is_retryable: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class _VerifiedMaterialization:
    canonical_uri: str
    checksum: str
    size_bytes: int
    storage_metadata: StorageObjectMetadata


class TransformationRunOutputFinalizationService:
    """Materialize and register successful run outputs without executing providers."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        storage_factory: DataRegistryStorageFactory,
        source_read_adapters: Sequence[ArtifactReadAdapter],
        integrity_verifier: ArtifactIntegrityVerifier,
        artifact_lifecycle_service: ArtifactLifecycleService,
        dataset_registry_service: DatasetRegistryService,
        lineage_service: LineageService,
        audit_service: AuditService,
        metrics: TransformationOperationalMetricsPort,
        log_context_builder: TransformationOperationalLogContextBuilder,
    ) -> None:
        """Initialize provider-neutral finalization dependencies."""
        self._storage_factory = storage_factory
        self._source_read_adapters = tuple(source_read_adapters)
        self._integrity_verifier = integrity_verifier
        self._artifact_lifecycle_service = artifact_lifecycle_service
        self._dataset_registry_service = dataset_registry_service
        self._lineage_service = lineage_service
        self._audit_service = audit_service
        self._metrics = metrics
        self._log_context_builder = log_context_builder

    def finalize_run(
        self,
        *,
        tenant_id: UUID | str,
        run_id: UUID | str,
        claim: TransformationOutputFinalizationClaim | None = None,
    ) -> TransformationOutputFinalizationResult:
        """Finalize every durable output for one tenant-scoped successful execution.

        Args:
            tenant_id: Exact tenant owning the run.
            run_id: Exact run in `OUTPUT_PENDING` or an already terminal state.

        Returns:
            Current durable run and output counts after the idempotent invocation.

        Raises:
            TransformationOutputFinalizationError: If identity or lifecycle is invalid.
        """
        parsed_tenant_id = _parse_uuid(tenant_id, field_name="tenant ID")
        parsed_run_id = _parse_uuid(run_id, field_name="run ID")
        run = self._get_run(tenant_id=parsed_tenant_id, run_id=parsed_run_id)
        self._require_matching_claim(run=run, claim=claim)
        if run.status in {
            TransformationRunStatus.SUCCEEDED,
            TransformationRunStatus.FAILED,
        }:
            return self._build_result(run=run)
        if run.status != TransformationRunStatus.OUTPUT_PENDING:
            raise TransformationOutputFinalizationError(
                "run_not_output_pending",
                "Transformation run is not eligible for output finalization.",
                is_permanent=True,
            )
        try:
            plan = self._deserialize_plan(run=run)
        except TransformationOutputFinalizationError as error:
            self._fail_run(
                tenant_id=parsed_tenant_id,
                run_id=parsed_run_id,
                error=error,
                claim=claim,
            )
            return self._build_result(
                run=self._get_run(tenant_id=parsed_tenant_id, run_id=parsed_run_id),
            )
        retryable_error: TransformationOutputFinalizationError | None = None
        for output_id in run.outputs.order_by("output_index").values_list("id", flat=True):
            try:
                self._assert_live_claim(claim=claim)
                self._finalize_output(
                    tenant_id=parsed_tenant_id,
                    run_id=parsed_run_id,
                    output_id=output_id,
                    plan=plan,
                    claim=claim,
                )
            except TransformationOutputFinalizationError as error:
                self._record_output_error(output_id=output_id, error=error, claim=claim)
                if error.is_permanent:
                    self._fail_run(
                        tenant_id=parsed_tenant_id,
                        run_id=parsed_run_id,
                        error=error,
                        claim=claim,
                    )
                    break
                retryable_error = error
                logger.warning(
                    "Transformation output finalization remains pending",
                    extra={"run_id": str(parsed_run_id), "output_id": str(output_id)},
                )
                break
        self.complete_run_if_ready(
            tenant_id=parsed_tenant_id,
            run_id=parsed_run_id,
            claim=claim,
        )
        return self._build_result(
            run=self._get_run(tenant_id=parsed_tenant_id, run_id=parsed_run_id),
            retryable_error=retryable_error,
        )

    def _finalize_output(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        output_id: UUID,
        plan: TransformationExecutionPlan,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        started = time.monotonic()
        output = TransformationRunOutput.objects.select_related("run").get(
            id=output_id,
            run_id=run_id,
            run__tenant_id=tenant_id,
        )
        self._validate_policy(output=output)
        if output.status == TransformationRunOutputStatus.REGISTERED:
            return
        if (
            output.status == TransformationRunOutputStatus.MATERIALIZED
            and output.output_kind == TransformationRunOutputKind.STANDALONE
        ):
            return
        if output.status == TransformationRunOutputStatus.FAILED:
            raise TransformationOutputFinalizationError(
                output.error_code or "output_failed",
                "Transformation output is permanently failed.",
                is_permanent=True,
            )
        if output.status == TransformationRunOutputStatus.CLEANUP_PENDING:
            self._cleanup_output(output=output, claim=claim)
            raise TransformationOutputFinalizationError(
                "output_cleanup_completed",
                "Transformation output was compensated after finalization failure.",
                is_permanent=True,
            )
        if output.status in {
            TransformationRunOutputStatus.PENDING,
            TransformationRunOutputStatus.MATERIALIZING,
        }:
            self._materialize_output(output=output, claim=claim)
            output.refresh_from_db()
        if output.output_kind == TransformationRunOutputKind.DATASET_BACKED:
            try:
                self._register_dataset_output(output_id=output.id, plan=plan, claim=claim)
            except TransformationOutputFinalizationError as error:
                if error.is_permanent:
                    self._compensate_output(output_id=output.id, claim=claim)
                raise
            except Exception as error:
                raise TransformationOutputFinalizationError(
                    "dataset_registration_failed",
                    "Dataset output metadata registration failed.",
                    is_permanent=False,
                ) from error
        self._record_output_metric(
            policy_kind=cast(str, output.output_policy_kind),
            outcome="succeeded",
            duration_ms=max(int((time.monotonic() - started) * 1000), 0),
            correlation_id=output.run.correlation_id,
        )

    def _materialize_output(
        self,
        *,
        output: TransformationRunOutput,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        provider = self._storage_factory.create_provider(output.destination_storage_provider)
        try:
            recovered = self._recover_materialization(output=output, provider=provider)
        except TransformationOutputFinalizationError as error:
            if error.is_permanent:
                self._compensate_output(output_id=output.id, claim=claim)
            raise
        except Exception as error:
            raise TransformationOutputFinalizationError(
                "output_storage_failed",
                "Managed output recovery inspection failed.",
                is_permanent=False,
            ) from error
        if recovered is not None:
            self._persist_materialization(output_id=output.id, verified=recovered, claim=claim)
            return
        step_attempt, reference = self._resolve_checkpoint_reference(output=output)
        adapter = self._resolve_source_adapter(reference=reference, provider=provider)
        was_prepared = False
        try:
            with adapter.materialize_to_temp(reference.materialized_reference) as source:
                checksum = self._integrity_verifier.compute_sha256(source.local_path)
                size_bytes = Path(source.local_path).stat().st_size
                self._validate_source_integrity(
                    reference=reference,
                    local_path=source.local_path,
                    checksum=checksum,
                    size_bytes=size_bytes,
                )
                self._prepare_materialization(
                    output_id=output.id,
                    step_attempt_id=step_attempt.id,
                    checksum=checksum,
                    size_bytes=size_bytes,
                    claim=claim,
                )
                was_prepared = True
                verified = self._store_and_verify(
                    provider=provider,
                    source_path=source.local_path,
                    destination_key=_require_string(output.destination_object_key),
                    expected_checksum=checksum,
                    expected_size=size_bytes,
                )
        except TransformationOutputFinalizationError as error:
            if error.is_permanent and was_prepared:
                self._compensate_output(output_id=output.id, claim=claim)
            raise
        except Exception as error:
            raise TransformationOutputFinalizationError(
                "output_storage_failed",
                "Managed output storage operation failed.",
                is_permanent=False,
            ) from error
        self._persist_materialization(output_id=output.id, verified=verified, claim=claim)

    def _recover_materialization(
        self,
        *,
        output: TransformationRunOutput,
        provider: StorageProvider,
    ) -> _VerifiedMaterialization | None:
        if (
            output.status != TransformationRunOutputStatus.MATERIALIZING
            or output.checksum_algorithm != "sha256"
            or output.checksum_value is None
            or output.size_bytes is None
            or output.destination_object_key is None
        ):
            return None
        canonical_uri = provider.get_uri(output.destination_object_key)
        if not provider.exists(canonical_uri):
            return None
        return self._verify_managed_object(
            provider=provider,
            canonical_uri=canonical_uri,
            expected_checksum=output.checksum_value,
            expected_size=output.size_bytes,
        )

    def _resolve_checkpoint_reference(
        self,
        *,
        output: TransformationRunOutput,
    ) -> tuple[TransformationRunStepAttempt, TransformationProviderOutputReference]:
        step_attempt = TransformationRunStepAttempt.objects.filter(
            run=output.run,
            run_attempt_number=output.run.latest_attempt_number,
            step_id=output.producer_step_id,
            status=TransformationRunStepAttemptStatus.SUCCEEDED,
        ).first()
        if step_attempt is None:
            raise TransformationOutputFinalizationError(
                "output_checkpoint_invalid",
                "Successful producing step checkpoint is unavailable.",
                is_permanent=True,
            )
        try:
            references = deserialize_output_references(step_attempt.checkpoint_outputs_json)
        except Exception as error:
            raise TransformationOutputFinalizationError(
                "output_checkpoint_invalid",
                "Successful producing step checkpoint is invalid.",
                is_permanent=True,
            ) from error
        matches = tuple(
            reference for reference in references if reference.logical_name == output.source_symbol
        )
        if len(matches) != 1 or matches[0].data_format != output.data_format:
            raise TransformationOutputFinalizationError(
                "output_checkpoint_invalid",
                "Output checkpoint does not match its persisted declaration.",
                is_permanent=True,
            )
        return step_attempt, matches[0]

    def _resolve_source_adapter(
        self,
        *,
        reference: TransformationProviderOutputReference,
        provider: StorageProvider,
    ) -> ArtifactReadAdapter:
        adapters = [*self._source_read_adapters]
        local_provider = self._storage_factory.create_provider("local")
        adapters.append(LocalArtifactReadAdapter(storage_provider=local_provider))
        provider_name = str(getattr(provider, "PROVIDER_NAME", "")).lower()
        schemes: tuple[str, ...] = ()
        if provider_name == "local":
            schemes = ("local://",)
        elif provider_name == "azure_blob":
            schemes = ("azure://", "https://")
        if schemes:
            adapters.append(
                StorageProviderArtifactReadAdapter(
                    storage_provider=provider,
                    schemes=schemes,
                ),
            )
        for adapter in adapters:
            if adapter.can_handle(reference.materialized_reference):
                return adapter
        raise TransformationOutputFinalizationError(
            "output_source_unavailable",
            "No configured read adapter supports the output checkpoint reference.",
            is_permanent=True,
        )

    def _validate_source_integrity(
        self,
        *,
        reference: TransformationProviderOutputReference,
        local_path: str,
        checksum: str,
        size_bytes: int,
    ) -> None:
        integrity = reference.integrity
        if integrity.checksum_algorithm is not None or integrity.checksum_value is not None:
            if (
                integrity.checksum_algorithm is None
                or integrity.checksum_value is None
                or integrity.checksum_algorithm.lower() != "sha256"
            ):
                raise TransformationOutputFinalizationError(
                    "output_integrity_failed",
                    "Output checkpoint integrity must use a complete SHA-256 pair.",
                    is_permanent=True,
                )
            try:
                self._integrity_verifier.verify_sha256(
                    local_path,
                    integrity.checksum_value,
                )
            except Exception as error:
                raise TransformationOutputFinalizationError(
                    "output_integrity_failed",
                    "Output checkpoint SHA-256 verification failed.",
                    is_permanent=True,
                ) from error
        if integrity.size_bytes is not None and integrity.size_bytes != size_bytes:
            raise TransformationOutputFinalizationError(
                "output_integrity_failed",
                "Output checkpoint size verification failed.",
                is_permanent=True,
            )
        if len(checksum) != _SHA256_HEX_LENGTH:
            raise TransformationOutputFinalizationError(
                "output_integrity_failed",
                "Output SHA-256 could not be established.",
                is_permanent=True,
            )

    def _prepare_materialization(
        self,
        *,
        output_id: UUID,
        step_attempt_id: UUID,
        checksum: str,
        size_bytes: int,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        with transaction.atomic():
            self._lock_matching_claim(claim=claim)
            output = TransformationRunOutput.objects.select_for_update().get(id=output_id)
            if output.status in {
                TransformationRunOutputStatus.MATERIALIZED,
                TransformationRunOutputStatus.REGISTERED,
            }:
                self._require_matching_integrity(
                    output=output,
                    checksum=checksum,
                    size_bytes=size_bytes,
                )
                return
            if output.status not in {
                TransformationRunOutputStatus.PENDING,
                TransformationRunOutputStatus.MATERIALIZING,
            }:
                raise TransformationOutputFinalizationError(
                    "output_state_invalid",
                    "Output cannot enter managed materialization from its current state.",
                    is_permanent=True,
                )
            if output.status == TransformationRunOutputStatus.MATERIALIZING:
                self._require_matching_integrity(
                    output=output,
                    checksum=checksum,
                    size_bytes=size_bytes,
                )
            output.status = TransformationRunOutputStatus.MATERIALIZING
            output.producing_step_attempt_id = step_attempt_id
            output.checksum_algorithm = "sha256"
            output.checksum_value = checksum
            output.size_bytes = size_bytes
            output.materialization_started_at = output.materialization_started_at or timezone.now()
            output.finalization_attempt_count += 1
            output.error_code = None
            output.error_message = None
            output.save()

    def _store_and_verify(
        self,
        *,
        provider: StorageProvider,
        source_path: str,
        destination_key: str,
        expected_checksum: str,
        expected_size: int,
    ) -> _VerifiedMaterialization:
        canonical_uri = provider.get_uri(destination_key)
        validate_canonical_reference(canonical_uri)
        if not provider.exists(canonical_uri):
            provider.put(source_path, destination_key)
        return self._verify_managed_object(
            provider=provider,
            canonical_uri=canonical_uri,
            expected_checksum=expected_checksum,
            expected_size=expected_size,
        )

    def _verify_managed_object(
        self,
        *,
        provider: StorageProvider,
        canonical_uri: str,
        expected_checksum: str,
        expected_size: int,
    ) -> _VerifiedMaterialization:
        handle = tempfile.NamedTemporaryFile(prefix="output-verify-", delete=False)
        verification_path = handle.name
        handle.close()
        try:
            storage_metadata = provider.download_to_file(canonical_uri, verification_path)
            try:
                verification = self._integrity_verifier.verify_sha256(
                    verification_path,
                    expected_checksum,
                )
            except Exception as error:
                raise TransformationOutputFinalizationError(
                    "output_storage_verification_failed",
                    "Managed output failed end-to-end SHA-256 verification.",
                    is_permanent=True,
                ) from error
            actual_size = Path(verification_path).stat().st_size
            if not verification.matched or actual_size != expected_size:
                raise TransformationOutputFinalizationError(
                    "output_storage_verification_failed",
                    "Managed output failed end-to-end integrity verification.",
                    is_permanent=True,
                )
            return _VerifiedMaterialization(
                canonical_uri=canonical_uri,
                checksum=verification.actual,
                size_bytes=actual_size,
                storage_metadata=storage_metadata,
            )
        finally:
            Path(verification_path).unlink(missing_ok=True)

    def _compensate_output(
        self,
        *,
        output_id: UUID,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        current = TransformationRunOutput.objects.select_related("run").get(id=output_id)
        provider = self._storage_factory.create_provider(
            current.destination_storage_provider,
        )
        canonical_uri = provider.get_uri(_require_string(current.destination_object_key))
        with transaction.atomic():
            self._lock_matching_claim(claim=claim)
            output = (
                TransformationRunOutput.objects.select_for_update()
                .select_related("run")
                .get(id=output_id)
            )
            if output.dataset_artifact_id is not None:
                return
            output.canonical_uri = canonical_uri
            output.status = TransformationRunOutputStatus.CLEANUP_PENDING
            output.save(update_fields=["canonical_uri", "status", "updated_at"])
        self._cleanup_output(output=output, claim=claim)
        with transaction.atomic():
            self._lock_matching_claim(claim=claim)
            locked = TransformationRunOutput.objects.select_for_update().get(id=output_id)
            if locked.status == TransformationRunOutputStatus.CLEANUP_PENDING:
                locked.status = TransformationRunOutputStatus.FAILED
                locked.completed_at = timezone.now()
                locked.save(update_fields=["status", "completed_at", "updated_at"])

    def _persist_materialization(
        self,
        *,
        output_id: UUID,
        verified: _VerifiedMaterialization,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        with transaction.atomic():
            self._lock_matching_claim(claim=claim)
            output = TransformationRunOutput.objects.select_for_update().get(id=output_id)
            self._require_matching_integrity(
                output=output,
                checksum=verified.checksum,
                size_bytes=verified.size_bytes,
            )
            if output.status in {
                TransformationRunOutputStatus.MATERIALIZED,
                TransformationRunOutputStatus.REGISTERED,
            }:
                if output.canonical_uri != verified.canonical_uri:
                    raise TransformationOutputFinalizationError(
                        "output_storage_verification_failed",
                        "Persisted output URI differs from verified managed storage.",
                        is_permanent=True,
                    )
                return
            output.canonical_uri = verified.canonical_uri
            output.status = TransformationRunOutputStatus.MATERIALIZED
            output.error_code = None
            output.error_message = None
            output.completed_at = (
                timezone.now()
                if output.output_kind == TransformationRunOutputKind.STANDALONE
                else None
            )
            output.save()

    def _register_dataset_output(
        self,
        *,
        output_id: UUID,
        plan: TransformationExecutionPlan,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        registered_output: TransformationRunOutput | None = None
        with transaction.atomic():
            self._lock_matching_claim(claim=claim)
            output = (
                TransformationRunOutput.objects.select_for_update()
                .select_related("run__definition", "run__definition_version")
                .get(id=output_id)
            )
            if output.status == TransformationRunOutputStatus.REGISTERED:
                return
            if output.status != TransformationRunOutputStatus.MATERIALIZED:
                raise TransformationOutputFinalizationError(
                    "output_state_invalid",
                    "Dataset output must be materialized before registration.",
                    is_permanent=True,
                )
            dataset = self._resolve_registration_dataset(output=output)
            dataset_version, version_was_created = self._resolve_dataset_version(
                output=output,
                dataset=dataset,
            )
            artifact_metadata = self._build_artifact_metadata(output=output)
            artifact = self._resolve_artifact(
                output=output,
                dataset_version=dataset_version,
                metadata=artifact_metadata,
                version_was_created=version_was_created,
            )
            self._register_lineage(output=output, dataset_version=dataset_version, plan=plan)
            if dataset.status != DatasetStatus.ACTIVE or dataset.archived_at is not None:
                dataset.status = DatasetStatus.ACTIVE
                dataset.archived_at = None
                dataset.save(update_fields=["status", "archived_at", "updated_at"])
            output.dataset = dataset
            output.dataset_version = dataset_version
            output.dataset_artifact = artifact
            output.status = TransformationRunOutputStatus.REGISTERED
            output.completed_at = timezone.now()
            output.error_code = None
            output.error_message = None
            output.save()
            registered_output = output
        if registered_output is not None:
            self._audit_output_registered(output=registered_output)

    def _resolve_registration_dataset(self, *, output: TransformationRunOutput) -> Dataset:
        configuration = _policy_configuration(output.output_policy_json)
        if output.output_policy_kind == TransformationRunOutputPolicyKind.NEW_DATASET:
            slug = _require_string(configuration.get("dataset_slug"))
            if Dataset.objects.filter(tenant_id=output.run.tenant_id, slug=slug).exists():
                raise TransformationOutputFinalizationError(
                    "dataset_registration_failed",
                    "New-dataset output target already exists.",
                    is_permanent=True,
                )
            return self._dataset_registry_service.create_dataset(
                tenant_id=str(output.run.tenant_id),
                slug=slug,
                name=_require_string(configuration.get("dataset_name")),
                source_type=DatasetSourceType.TRANSFORMATION,
                created_by=output.run.created_by,
                description=_optional_string(configuration.get("description")),
                owner_team=_optional_string(configuration.get("owner_team")),
                tags=_string_list(configuration.get("tags", [])),
            )
        if output.output_policy_kind == TransformationRunOutputPolicyKind.NEW_DATASET_VERSION:
            if output.target_dataset_id is None:
                raise TransformationOutputFinalizationError(
                    "dataset_target_invalid",
                    "Dataset-version output has no exact target dataset.",
                    is_permanent=True,
                )
            try:
                return Dataset.objects.select_for_update().get(
                    id=output.target_dataset_id,
                    tenant_id=output.run.tenant_id,
                )
            except Dataset.DoesNotExist as error:
                raise TransformationOutputFinalizationError(
                    "dataset_target_invalid",
                    "Dataset-version output target is unavailable.",
                    is_permanent=True,
                ) from error
        raise TransformationOutputFinalizationError(
            "output_policy_invalid",
            "Dataset-backed output policy is invalid.",
            is_permanent=True,
        )

    def _resolve_dataset_version(
        self,
        *,
        output: TransformationRunOutput,
        dataset: Dataset,
    ) -> tuple[DatasetVersion, bool]:
        checksum = _require_string(output.checksum_value)
        existing = DatasetVersion.objects.filter(dataset=dataset, content_hash=checksum).first()
        if existing is not None:
            if existing.status != DatasetVersionStatus.READY:
                raise TransformationOutputFinalizationError(
                    "dataset_registration_failed",
                    "Existing content-matched dataset version is not READY.",
                    is_permanent=True,
                )
            return existing, False
        latest = DatasetVersion.objects.filter(dataset=dataset).aggregate(
            maximum=Max("version_number"),
        )["maximum"]
        version_number = int(latest or 0) + 1
        return (
            self._dataset_registry_service.create_dataset_version(
                dataset=dataset,
                version_number=version_number,
                source_uri=_require_string(output.canonical_uri),
                content_hash=checksum,
                status=DatasetVersionStatus.READY,
                notes="Created from a durable transformation output.",
            ),
            True,
        )

    def _resolve_artifact(
        self,
        *,
        output: TransformationRunOutput,
        dataset_version: DatasetVersion,
        metadata: dict[str, object],
        version_was_created: bool,
    ) -> DatasetArtifact:
        artifact_format = _artifact_format(output.data_format)
        uri = _require_string(output.canonical_uri)
        checksum = _require_string(output.checksum_value)
        size_bytes = _require_int(output.size_bytes)
        existing = DatasetArtifact.objects.filter(
            dataset_version=dataset_version,
            artifact_role=ArtifactRole.PRIMARY,
            uri=uri,
        ).first()
        if not version_was_created and existing is None:
            raise TransformationOutputFinalizationError(
                "dataset_registration_failed",
                "Existing content-matched dataset version has no identical managed artifact.",
                is_permanent=True,
            )
        try:
            return self._artifact_lifecycle_service.register_uploaded_artifact_idempotently(
                dataset_version=dataset_version,
                artifact_role=ArtifactRole.PRIMARY,
                uri=uri,
                format=artifact_format,
                size_bytes=size_bytes,
                storage_provider=output.destination_storage_provider,
                checksum=checksum,
                metadata_json=metadata,
            )
        except ValueError as error:
            raise TransformationOutputFinalizationError(
                "artifact_registration_failed",
                "Existing artifact does not match output registration intent.",
                is_permanent=True,
            ) from error

    def _register_lineage(
        self,
        *,
        output: TransformationRunOutput,
        dataset_version: DatasetVersion,
        plan: TransformationExecutionPlan,
    ) -> None:
        input_ids = sorted({plan_input.dataset_version_id for plan_input in plan.inputs})
        inputs = {
            str(version.id): version
            for version in DatasetVersion.objects.select_related("dataset").filter(
                id__in=input_ids,
                dataset__tenant_id=output.run.tenant_id,
            )
        }
        if set(inputs) != set(input_ids):
            raise TransformationOutputFinalizationError(
                "lineage_registration_failed",
                "An exact tenant-scoped input dataset version is unavailable.",
                is_permanent=True,
            )
        metadata = {
            "definition_id": str(output.run.definition_id),
            "definition_version_id": str(output.run.definition_version_id),
            "specification_checksum": output.run.specification_checksum,
            "logical_output": output.logical_name,
            "output_index": output.output_index,
        }
        for input_id in input_ids:
            self._lineage_service.record_lineage_idempotently(
                downstream_dataset_version=dataset_version,
                upstream_dataset_version=inputs[input_id],
                lineage_type=LineageType.DERIVED_FROM,
                transformation_name=output.run.definition.definition_key,
                transformation_hash=output.run.execution_plan_checksum,
                job_id=str(output.run_id),
                metadata_json=metadata,
            )

    def complete_run_if_ready(
        self,
        *,
        tenant_id: UUID | str,
        run_id: UUID | str,
        claim: TransformationOutputFinalizationClaim | None = None,
    ) -> None:
        """Complete one run whose outputs are already policy-terminal."""
        parsed_tenant_id = _parse_uuid(tenant_id, field_name="tenant ID")
        parsed_run_id = _parse_uuid(run_id, field_name="run ID")
        completed_run: TransformationRun | None = None
        failed_run: TransformationRun | None = None
        with transaction.atomic():
            run = TransformationRun.objects.select_for_update().get(
                tenant_id=parsed_tenant_id,
                id=parsed_run_id,
            )
            self._require_matching_claim(run=run, claim=claim)
            if run.status != TransformationRunStatus.OUTPUT_PENDING:
                return
            outputs = tuple(run.outputs.select_for_update().order_by("output_index"))
            if any(output.status == TransformationRunOutputStatus.FAILED for output in outputs):
                self._set_failed_run(run=run, code="output_finalization_failed")
                failed_run = run
            elif outputs and all(_is_terminal_output(output) for output in outputs):
                run.status = TransformationRunStatus.SUCCEEDED
                run.completed_at = timezone.now()
                run.error_code = None
                run.error_message = None
                run.error_metadata_json = {}
                _clear_finalization_claim(run)
                run.save()
                completed_run = run
        if failed_run is not None:
            self._audit_run_transition(
                run=failed_run,
                event_type=EventType.RUN_FAILED,
                transition="OUTPUT_PENDING->FAILED",
            )
            self._record_lifecycle_metric(action="FAILED")
        if completed_run is not None:
            self._audit_run_transition(
                run=completed_run,
                event_type=EventType.RUN_SUCCEEDED,
                transition="OUTPUT_PENDING->SUCCEEDED",
            )
            self._record_lifecycle_metric(action="SUCCEEDED")
            operational_logger.info(
                "Transformation output finalization completed",
                extra=self._log_context_builder.build_for_run(
                    run=completed_run,
                    outcome="succeeded",
                ),
            )

    def _fail_run(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        error: TransformationOutputFinalizationError,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        failed_run: TransformationRun | None = None
        with transaction.atomic():
            run = TransformationRun.objects.select_for_update().get(
                tenant_id=tenant_id,
                id=run_id,
            )
            self._require_matching_claim(run=run, claim=claim)
            if run.status == TransformationRunStatus.OUTPUT_PENDING:
                self._set_failed_run(run=run, code=error.code)
                failed_run = run
        if failed_run is not None:
            self._audit_run_transition(
                run=failed_run,
                event_type=EventType.RUN_FAILED,
                transition="OUTPUT_PENDING->FAILED",
            )
            self._record_lifecycle_metric(action="FAILED")
            self._record_output_metric(
                policy_kind="run",
                outcome="failed",
                duration_ms=0,
                error_code=error.code,
                correlation_id=failed_run.correlation_id,
            )

    def _set_failed_run(self, *, run: TransformationRun, code: str) -> None:
        run.status = TransformationRunStatus.FAILED
        run.completed_at = timezone.now()
        run.error_code = code
        run.error_message = "Transformation output finalization failed."
        run.error_metadata_json = {}
        _clear_finalization_claim(run)
        run.save()

    def fail_claimed_run(
        self,
        *,
        claim: TransformationOutputFinalizationClaim,
        error_code: str,
    ) -> None:
        """Fail one output-pending run only through its matching finalization fence."""
        error = TransformationOutputFinalizationError(
            error_code,
            "Transformation output finalization failed.",
            is_permanent=True,
        )
        self._fail_run(
            tenant_id=_parse_uuid(claim.tenant_id, field_name="tenant ID"),
            run_id=_parse_uuid(claim.run_id, field_name="run ID"),
            error=error,
            claim=claim,
        )

    def _audit_output_registered(self, *, output: TransformationRunOutput) -> None:
        try:
            self._audit_service.record_transformation_event(
                event_type=EventType.OUTPUT_REGISTERED,
                tenant_id=str(output.run.tenant_id),
                run_id=str(output.run_id),
                correlation_id=output.run.correlation_id,
                transition="MATERIALIZED->REGISTERED",
                actor=output.run.authorized_actor,
                output_id=str(output.id),
                metadata={"policy_kind": output.output_policy_kind},
            )
        except Exception:
            return

    def _audit_run_transition(
        self,
        *,
        run: TransformationRun,
        event_type: EventType,
        transition: str,
    ) -> None:
        try:
            self._audit_service.record_transformation_event(
                event_type=event_type,
                tenant_id=str(run.tenant_id),
                run_id=str(run.id),
                correlation_id=run.correlation_id,
                transition=transition,
                actor=run.authorized_actor,
                attempt_number=int(run.latest_attempt_number) or None,
                metadata={"error_code": run.error_code} if run.error_code else {},
            )
        except Exception:
            return

    def _record_output_metric(
        self,
        *,
        policy_kind: str,
        outcome: str,
        duration_ms: int,
        error_code: str = "",
        correlation_id: str | None = None,
    ) -> None:
        try:
            self._metrics.record_output_finalization(
                policy_kind=policy_kind,
                outcome=outcome,
                duration_ms=duration_ms,
                error_code=error_code,
                correlation_id=correlation_id,
            )
        except Exception:
            return

    def _record_lifecycle_metric(self, *, action: str) -> None:
        try:
            self._metrics.record_lifecycle(action=action)
        except Exception:
            return

    def _record_output_error(
        self,
        *,
        output_id: UUID,
        error: TransformationOutputFinalizationError,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        with transaction.atomic():
            self._lock_matching_claim(claim=claim)
            output = TransformationRunOutput.objects.select_for_update().get(id=output_id)
            output.error_code = error.code
            output.error_message = error.safe_message
            if error.is_permanent:
                output.status = TransformationRunOutputStatus.FAILED
                output.completed_at = timezone.now()
            output.save()

    def _cleanup_output(
        self,
        *,
        output: TransformationRunOutput,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        self._assert_live_claim(claim=claim)
        provider = self._storage_factory.create_provider(output.destination_storage_provider)
        try:
            self._artifact_lifecycle_service.cleanup_orphan_upload(
                storage_provider=provider,
                uri=_require_string(output.canonical_uri),
                tenant_id=str(output.run.tenant_id),
                metadata={"run_id": str(output.run_id), "output_id": str(output.id)},
            )
        except Exception as error:
            raise TransformationOutputFinalizationError(
                "orphan_cleanup_failed",
                "Managed orphan cleanup failed.",
                is_permanent=False,
            ) from error

    def _validate_policy(self, *, output: TransformationRunOutput) -> None:
        if not isinstance(output.output_policy_json, Mapping):
            raise TransformationOutputFinalizationError(
                "output_policy_missing",
                "Output has no explicit immutable policy.",
                is_permanent=True,
            )
        snapshot = cast(Mapping[str, object], output.output_policy_json)
        expected_destination = build_output_destination_key(
            tenant_id=output.run.tenant_id,
            run_id=output.run_id,
            output_index=output.output_index,
            logical_name=output.logical_name,
            data_format=output.data_format,
        )
        target_dataset_id = (
            str(output.target_dataset_id) if output.target_dataset_id is not None else None
        )
        if (
            output.output_policy_checksum is None
            or calculate_output_policy_checksum(snapshot) != output.output_policy_checksum
            or snapshot.get("policy_kind") != output.output_policy_kind
            or snapshot.get("contract_version") != output.output_policy_contract_version
            or snapshot.get("logical_name") != output.logical_name
            or snapshot.get("destination_storage_provider") != output.destination_storage_provider
            or snapshot.get("target_dataset_id") != target_dataset_id
            or output.destination_object_key != expected_destination
        ):
            raise TransformationOutputFinalizationError(
                "output_policy_invalid",
                "Persisted output policy integrity validation failed.",
                is_permanent=True,
            )

    def _deserialize_plan(self, *, run: TransformationRun) -> TransformationExecutionPlan:
        try:
            snapshot = cast(Mapping[str, object], run.execution_plan_snapshot_json)
            plan = deserialize_transformation_execution_plan(snapshot)
        except Exception as error:
            raise TransformationOutputFinalizationError(
                "invalid_plan_snapshot",
                "Persisted execution plan is invalid.",
                is_permanent=True,
            ) from error
        if plan.plan_checksum != run.execution_plan_checksum:
            raise TransformationOutputFinalizationError(
                "invalid_plan_snapshot",
                "Persisted execution plan checksum does not match the run.",
                is_permanent=True,
            )
        return plan

    def _require_matching_integrity(
        self,
        *,
        output: TransformationRunOutput,
        checksum: str,
        size_bytes: int,
    ) -> None:
        if (
            output.checksum_algorithm != "sha256"
            or output.checksum_value != checksum
            or output.size_bytes != size_bytes
        ):
            raise TransformationOutputFinalizationError(
                "output_integrity_failed",
                "Concurrent output materialization integrity differs.",
                is_permanent=True,
            )

    def _get_run(self, *, tenant_id: UUID, run_id: UUID) -> TransformationRun:
        try:
            return TransformationRun.objects.get(tenant_id=tenant_id, id=run_id)
        except TransformationRun.DoesNotExist as error:
            raise TransformationOutputFinalizationError(
                "run_not_found",
                "Transformation run was not found.",
                is_permanent=True,
            ) from error

    def _build_artifact_metadata(self, *, output: TransformationRunOutput) -> dict[str, object]:
        return {
            "transformation_run_id": str(output.run_id),
            "definition_id": str(output.run.definition_id),
            "definition_version_id": str(output.run.definition_version_id),
            "execution_plan_checksum": output.run.execution_plan_checksum,
            "specification_checksum": output.run.specification_checksum,
            "logical_output": output.logical_name,
            "output_index": output.output_index,
            "data_format": output.data_format,
        }

    def _require_matching_claim(
        self,
        *,
        run: TransformationRun,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        if claim is None:
            if run.current_finalization_token is not None:
                raise TransformationOutputFinalizationError(
                    "stale_finalization_claim",
                    "Output finalization requires its active claim fence.",
                    is_permanent=False,
                )
            return
        if (
            str(run.tenant_id) != claim.tenant_id
            or str(run.id) != claim.run_id
            or run.status != TransformationRunStatus.OUTPUT_PENDING
            or str(run.current_finalization_token) != claim.token
            or run.finalization_owner != claim.worker_id
            or run.finalization_attempt_number != claim.attempt_number
        ):
            raise TransformationOutputFinalizationError(
                "stale_finalization_claim",
                "Output finalization claim is stale.",
                is_permanent=False,
            )

    def _assert_live_claim(
        self,
        *,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        if claim is None:
            return
        run = self._get_run(
            tenant_id=_parse_uuid(claim.tenant_id, field_name="tenant ID"),
            run_id=_parse_uuid(claim.run_id, field_name="run ID"),
        )
        self._require_matching_claim(run=run, claim=claim)

    def _lock_matching_claim(
        self,
        *,
        claim: TransformationOutputFinalizationClaim | None,
    ) -> None:
        if claim is None:
            return
        run = TransformationRun.objects.select_for_update().get(
            tenant_id=claim.tenant_id,
            id=claim.run_id,
        )
        self._require_matching_claim(run=run, claim=claim)

    def _build_result(
        self,
        *,
        run: TransformationRun,
        retryable_error: TransformationOutputFinalizationError | None = None,
    ) -> TransformationOutputFinalizationResult:
        outputs = run.outputs.all()
        return TransformationOutputFinalizationResult(
            run_id=run.id,
            tenant_id=run.tenant_id,
            status=run.status,
            materialized_output_count=outputs.filter(
                status=TransformationRunOutputStatus.MATERIALIZED,
            ).count(),
            registered_output_count=outputs.filter(
                status=TransformationRunOutputStatus.REGISTERED,
            ).count(),
            failed_output_count=outputs.filter(
                status__in=[
                    TransformationRunOutputStatus.FAILED,
                    TransformationRunOutputStatus.CLEANUP_PENDING,
                ],
            ).count(),
            is_retryable=retryable_error is not None,
            error_code=retryable_error.code if retryable_error is not None else run.error_code,
        )


def _is_terminal_output(output: TransformationRunOutput) -> bool:
    if output.output_kind == TransformationRunOutputKind.STANDALONE:
        return (
            output.status == TransformationRunOutputStatus.MATERIALIZED
            and output.checksum_algorithm == "sha256"
            and output.canonical_uri is not None
        )
    return (
        output.status == TransformationRunOutputStatus.REGISTERED
        and output.checksum_algorithm == "sha256"
        and output.dataset_id is not None
        and output.dataset_version_id is not None
        and output.dataset_artifact_id is not None
    )


def _clear_finalization_claim(run: TransformationRun) -> None:
    run.current_finalization_token = None
    run.finalization_owner = None
    run.finalization_claimed_at = None
    run.finalization_heartbeat_at = None
    run.finalization_lease_expires_at = None
    run.finalization_deadline_at = None
    run.finalization_tenant_slot = None


def _artifact_format(data_format: str) -> str:
    normalized = data_format.strip().upper()
    if normalized in {ArtifactFormat.CSV, ArtifactFormat.PARQUET, ArtifactFormat.JSON}:
        return normalized
    return ArtifactFormat.OTHER


def _policy_configuration(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    configuration = value.get("configuration")
    if not isinstance(configuration, Mapping):
        return {}
    return cast(Mapping[str, object], configuration)


def _parse_uuid(value: UUID | str, *, field_name: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise TransformationOutputFinalizationError(
            "invalid_identity",
            f"Explicit {field_name} must be a valid UUID.",
            is_permanent=True,
        ) from error


def _require_string(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TransformationOutputFinalizationError(
            "invalid_persisted_output",
            "Persisted output contains an invalid required string.",
            is_permanent=True,
        )
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _require_string(value)


def _require_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TransformationOutputFinalizationError(
            "invalid_persisted_output",
            "Persisted output contains an invalid size.",
            is_permanent=True,
        )
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise TransformationOutputFinalizationError(
            "output_policy_invalid",
            "Dataset tags must be a sequence.",
            is_permanent=True,
        )
    return [_require_string(item) for item in value]
