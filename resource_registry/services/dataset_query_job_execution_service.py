from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.db import DatabaseError

from Organization.models import Organization
from resource_registry.authorization.registry_authorization_context_service import (
    RegistryAuthorizationContextService,
)
from resource_registry.authorization.exceptions import RegistryAuthorizationContextError
from resource_registry.authorization.registry_roles import derive_registry_roles
from resource_registry.authorization.worker_execution_authority import (
    WorkerExecutionAuthorityService,
)
from resource_registry.bifs.capability_context import BifCapabilityContext
from resource_registry.bifs.capability_policy import DataRegistryBifCapabilityPolicy
from resource_registry.bifs.capability_resolution import DataRegistryBifCapabilityResolver
from resource_registry.bifs.exceptions import DataRegistryBifCapabilityError
from resource_registry.dataset_scope import get_visible_workspace_dataset_ids
from resource_registry.models import DatasetQueryJob
from resource_registry.read.exceptions import DataRegistryReadError
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import DataRegistryAuthorizationError
from resource_registry.read_auth.policy import AuthorizedDataRegistryReadFacade
from resource_registry.services.async_job_execution_timeout import (
    build_execution_timeout_message,
    has_execution_timeout_elapsed,
    normalize_execution_timeout_seconds,
)
from resource_registry.services.dataset_query_executor import DatasetQueryExecutor
from resource_registry.services.dataset_query_job_service import DatasetQueryJobService


class DatasetQueryJobExecutionService:
    """Executes queued dataset query jobs in a worker-compatible way."""

    def __init__(
        self,
        *,
        query_job_service: DatasetQueryJobService | None = None,
        query_executor: DatasetQueryExecutor | None = None,
        max_result_rows: int = 100,
        execution_timeout_seconds: float | int | None = None,
    ) -> None:
        self._query_job_service = query_job_service or DatasetQueryJobService()
        configured_timeout = (
            execution_timeout_seconds
            if execution_timeout_seconds is not None
            else getattr(settings, "RESOURCE_REGISTRY_ASYNC_QUERY_TIMEOUT_SECONDS", 10)
        )
        self._execution_timeout_seconds = normalize_execution_timeout_seconds(configured_timeout)
        self._query_executor = query_executor or DatasetQueryExecutor(
            execution_timeout_seconds=self._execution_timeout_seconds,
        )
        self._max_result_rows = max(int(max_result_rows), 1)

    def execute_job(  # noqa: PLR0911
        self,
        *,
        context: ReadAuthorizationContext,
        query_job_id: str,
    ) -> DatasetQueryJob:
        job = self._query_job_service.get_job(
            tenant_id=context.tenant_id,
            query_job_id=query_job_id,
        )
        live_context = self._resolve_live_authorization_context(job=job, context=context)
        if live_context is None:
            return self._query_job_service.mark_failed(
                job=job,
                error_code="query_authorization_revoked",
                error_message="Query job access is no longer authorized.",
                retryable=False,
            )
        context = live_context
        self._query_job_service.mark_running(job=job)
        started_at = time.perf_counter()
        try:
            result = self._query_executor.execute_query(
                context=context,
                dataset_id=str(job.dataset_id),
                version_id=(str(job.version_id) if job.version_id else None),
                sql=str(job.sql_text or ""),
            )
            elapsed_seconds = time.perf_counter() - started_at
            execution_ms = int(elapsed_seconds * 1000)
            if (
                self._query_job_service.is_cancellation_requested(
                    tenant_id=context.tenant_id,
                    query_job_id=query_job_id,
                )
                is True
            ):
                current_job = self._query_job_service.get_job(
                    tenant_id=context.tenant_id,
                    query_job_id=query_job_id,
                )
                return self._query_job_service.mark_cancelled(job=current_job)
            if self._is_timeout_result(result):
                return self._mark_execution_timeout(
                    job=job,
                    execution_ms=execution_ms,
                )
            if result.get("success") is False:
                return self._query_job_service.mark_failed(
                    job=job,
                    error_code=str(result.get("code") or "query_execution_error"),
                    error_message=str(result.get("error") or "Unable to execute query job."),
                    execution_ms=execution_ms,
                )
            if has_execution_timeout_elapsed(
                elapsed_seconds=elapsed_seconds,
                timeout_seconds=self._execution_timeout_seconds,
            ):
                return self._mark_execution_timeout(
                    job=job,
                    execution_ms=execution_ms,
                )
            controlled_result = self._apply_result_row_limit(result)
            return self._query_job_service.mark_completed(
                job=job,
                result=controlled_result,
                execution_ms=execution_ms,
            )
        except Exception:
            elapsed_seconds = time.perf_counter() - started_at
            execution_ms = int(elapsed_seconds * 1000)
            if (
                self._query_job_service.is_cancellation_requested(
                    tenant_id=context.tenant_id,
                    query_job_id=query_job_id,
                )
                is True
            ):
                current_job = self._query_job_service.get_job(
                    tenant_id=context.tenant_id,
                    query_job_id=query_job_id,
                )
                return self._query_job_service.mark_cancelled(job=current_job)
            if has_execution_timeout_elapsed(
                elapsed_seconds=elapsed_seconds,
                timeout_seconds=self._execution_timeout_seconds,
            ):
                return self._mark_execution_timeout(
                    job=job,
                    execution_ms=execution_ms,
                )
            return self._query_job_service.mark_failed(
                job=job,
                error_code="query_execution_error",
                error_message="Unable to execute query job.",
                execution_ms=execution_ms,
                retryable=True,
            )

    @staticmethod
    def _resolve_live_authorization_context(
        *,
        job: DatasetQueryJob,
        context: ReadAuthorizationContext,
    ) -> ReadAuthorizationContext | None:
        """Recheck the submitting user's current Registry authority before execution.

        Args:
            job: Queued dataset query job.
            context: Immutable submission authority from the worker.

        Returns:
            Current read context, or None when access has been revoked.
        """
        if context.metadata.get("source") != "delegated_job_authority":
            return context
        try:
            actor_id = int(str(context.actor).removeprefix("user:"))
            workspace_id = int(context.metadata["workspace_id"])
            organization_id = int(context.metadata["organization_id"])
            user = User.objects.get(pk=actor_id)
            organization = Organization.objects.get(pk=organization_id)
            authorized = RegistryAuthorizationContextService().build_context(
                user=user,
                organization=organization,
            )
            if not authorized.authorized_namespaces.contains_namespace(context.tenant_id):
                return None
            if (
                authorized.authorized_namespaces.resolve_workspace(context.tenant_id)
                != workspace_id
            ):
                return None

            dataset_id = str(job.dataset_id)
            visible_dataset_ids = get_visible_workspace_dataset_ids(
                namespace_id=context.tenant_id,
                workspace_id=workspace_id,
            )
            if dataset_id not in visible_dataset_ids:
                return None

            roles = derive_registry_roles(user)
            resolved = DataRegistryBifCapabilityResolver().resolve(
                tenant_id=context.tenant_id,
                actor=user.username,
                roles=roles,
                dataset_id=dataset_id,
            )
            DataRegistryBifCapabilityPolicy().assert_can_call(
                "resource_registry_start_query_job",
                BifCapabilityContext(
                    tenant_id=context.tenant_id,
                    actor=user.username,
                    roles=roles,
                    enabled_capabilities=resolved.enabled_capabilities,
                    disabled_capabilities=resolved.disabled_capabilities,
                ),
            )
            live_context = ReadAuthorizationContext(
                tenant_id=context.tenant_id,
                actor=user.username,
                roles=roles,
                allowed_dataset_ids=frozenset({dataset_id}),
                enforced_dataset_ids=visible_dataset_ids,
            )
            AuthorizedDataRegistryReadFacade().get_dataset(live_context, dataset_id)
            return live_context
        except DatabaseError:
            raise
        except (
            User.DoesNotExist,
            Organization.DoesNotExist,
            RegistryAuthorizationContextError,
            DataRegistryBifCapabilityError,
            DataRegistryAuthorizationError,
            DataRegistryReadError,
            KeyError,
            TypeError,
            ValueError,
        ):
            return None

    def _apply_result_row_limit(self, result: dict[str, Any]) -> dict[str, Any]:
        try:
            rows = result.get("rows")
            if not isinstance(rows, list):
                return dict(result)

            truncated_rows = rows[: self._max_result_rows]
            is_truncated = bool(result.get("truncated", False)) or len(rows) > len(truncated_rows)
            raw_row_count = result.get("row_count")
            if isinstance(raw_row_count, bool):
                row_count = int(raw_row_count)
            elif raw_row_count is None:
                row_count = len(rows)
            else:
                row_count = int(raw_row_count)

            controlled = dict(result)
            controlled["rows"] = truncated_rows
            controlled["row_count"] = min(max(row_count, 0), len(truncated_rows))
            controlled["truncated"] = is_truncated
            return controlled
        except Exception:
            return dict(result)

    def _mark_execution_timeout(
        self,
        *,
        job: DatasetQueryJob,
        execution_ms: int,
    ) -> DatasetQueryJob:
        return self._query_job_service.mark_failed(
            job=job,
            error_code="execution_timeout",
            error_message=build_execution_timeout_message(
                job_family="Dataset query job",
                timeout_seconds=self._execution_timeout_seconds,
            ),
            execution_ms=execution_ms,
            retryable=True,
        )

    def _is_timeout_result(self, result: dict[str, Any]) -> bool:
        return result.get("success") is False and str(result.get("code") or "").strip().lower() in {
            "query_timeout",
            "execution_timeout",
        }


def execute_dataset_query_job(  # noqa: PLR0913
    *,
    tenant_id: str,
    query_job_id: str,
    actor: str | None = None,
    roles: tuple[str, ...] = (),
    scopes: tuple[str, ...] = (),
    allowed_dataset_ids: frozenset[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one query job using only its persisted delegated authority."""
    _ = (actor, roles, scopes, allowed_dataset_ids, metadata)
    job_service = DatasetQueryJobService()
    persisted_job = job_service.get_job(tenant_id=tenant_id, query_job_id=query_job_id)
    execution_authority = WorkerExecutionAuthorityService().build_query_context(job=persisted_job)
    job = DatasetQueryJobExecutionService(query_job_service=job_service).execute_job(
        context=execution_authority.to_read_authorization_context(),
        query_job_id=query_job_id,
    )
    return {
        "query_job_id": str(job.id),
        "job_status": str(job.status),
    }
