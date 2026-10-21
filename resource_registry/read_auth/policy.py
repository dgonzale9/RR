from __future__ import annotations

from uuid import UUID

from resource_registry.read.artifact_read_service import DatasetArtifactReadService
from resource_registry.read.dataset_context_read_service import DatasetContextReadService
from resource_registry.read.dataset_read_service import DatasetReadService
from resource_registry.read.dtos import (
    DatasetArtifactSummary,
    DatasetContextSummary,
    DatasetDetails,
    DatasetProfileSummary,
    DatasetSchemaSummary,
    DatasetSummary,
    DatasetVersionSummary,
    ExternalDatasetJobSummary,
    WorkbookContextSummary,
)
from resource_registry.read.job_read_service import ExternalDatasetJobReadService
from resource_registry.read.profile_read_service import DatasetProfileReadService
from resource_registry.read.schema_read_service import DatasetSchemaReadService
from resource_registry.read.version_read_service import DatasetVersionReadService
from resource_registry.read.workbook_context_read_service import WorkbookContextReadService
from resource_registry.read_auth.context import ReadAuthorizationContext
from resource_registry.read_auth.exceptions import DatasetAccessDeniedError
from resource_registry.read_auth.field_filtering import FieldFilterService
from resource_registry.read_auth.permissions import ReadAuthorizationService
from resource_registry.read_auth.visibility_explainability import FilteredWithExplanation


class AuthorizedDataRegistryReadFacade:
    """Authorization and field-filtering wrapper over data registry read services."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        authorization_service: ReadAuthorizationService | None = None,
        field_filter_service: FieldFilterService | None = None,
        dataset_read_service: DatasetReadService | None = None,
        dataset_context_read_service: DatasetContextReadService | None = None,
        version_read_service: DatasetVersionReadService | None = None,
        schema_read_service: DatasetSchemaReadService | None = None,
        profile_read_service: DatasetProfileReadService | None = None,
        artifact_read_service: DatasetArtifactReadService | None = None,
        job_read_service: ExternalDatasetJobReadService | None = None,
        workbook_context_read_service: WorkbookContextReadService | None = None,
    ) -> None:
        self._authorization_service = authorization_service or ReadAuthorizationService()
        self._field_filter_service = field_filter_service or FieldFilterService(
            self._authorization_service,
        )
        self._dataset_read_service = dataset_read_service or DatasetReadService()
        self._dataset_context_read_service = (
            dataset_context_read_service or DatasetContextReadService()
        )
        self._version_read_service = version_read_service or DatasetVersionReadService()
        self._schema_read_service = schema_read_service or DatasetSchemaReadService()
        self._profile_read_service = profile_read_service or DatasetProfileReadService()
        self._artifact_read_service = artifact_read_service or DatasetArtifactReadService()
        self._job_read_service = job_read_service or ExternalDatasetJobReadService()
        self._workbook_context_read_service = (
            workbook_context_read_service or WorkbookContextReadService()
        )

    def list_datasets(  # noqa: PLR0913
        self,
        context: ReadAuthorizationContext,
        *,
        search: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        workbook_group_id: str | None = None,
        sheet_name: str | None = None,
        relevance_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DatasetSummary]:
        """List datasets with authorization applied before pagination.

        Args:
            context: Authenticated caller and mandatory Workspace scope.
            search: Optional dataset search text.
            status: Optional dataset status filter.
            source_type: Optional source type filter.
            workbook_group_id: Optional workbook group filter.
            sheet_name: Optional sheet name filter.
            relevance_query: Optional ranking text.
            limit: Maximum number of authorized results, capped by the read service.
            offset: Number of authorized results to skip.

        Returns:
            Field-filtered summaries from the requested authorized page.

        Raises:
            MissingAuthorizationContextError: If the tenant context is missing.
            TenantAccessDeniedError: If the requested tenant conflicts with the context.
            PermissionDeniedError: If the caller cannot list datasets.
            DatasetAccessDeniedError: If the effective scope contains an invalid UUID.
        """
        self._authorization_service.assert_can_list_datasets(context)
        allowed_dataset_ids = self._authorization_service.get_effective_allowed_dataset_ids(context)
        for dataset_id in allowed_dataset_ids or ():
            try:
                UUID(dataset_id)
            except (ValueError, TypeError, AttributeError):
                raise DatasetAccessDeniedError(
                    "Dataset authorization scope contains an invalid identifier."
                ) from None
        if search:
            results = self._dataset_read_service.search_datasets(
                tenant_id=context.tenant_id,
                query=search,
                allowed_dataset_ids=allowed_dataset_ids,
                status=status,
                source_type=source_type,
                workbook_group_id=workbook_group_id,
                sheet_name=sheet_name,
                relevance_query=relevance_query,
                limit=limit,
                offset=offset,
            )
        else:
            results = self._dataset_read_service.list_datasets(
                tenant_id=context.tenant_id,
                allowed_dataset_ids=allowed_dataset_ids,
                status=status,
                source_type=source_type,
                workbook_group_id=workbook_group_id,
                sheet_name=sheet_name,
                relevance_query=relevance_query,
                limit=limit,
                offset=offset,
            )
        allowed_ids = self._authorization_service.filter_allowed_dataset_ids(
            context,
            [result.id for result in results],
        )
        allowed_set = set(allowed_ids)
        filtered = [result for result in results if result.id in allowed_set]
        return [
            self._field_filter_service.filter_dataset_summary(result, context)
            for result in filtered
        ]

    def get_dataset(self, context: ReadAuthorizationContext, dataset_id: str) -> DatasetDetails:
        self._authorization_service.assert_can_read_dataset(context, dataset_id)
        result = self._dataset_read_service.get_dataset(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
        )
        return self._field_filter_service.filter_dataset_details(result, context)

    def get_dataset_context(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
    ) -> DatasetContextSummary:
        self._authorization_service.assert_can_read_context(context, dataset_id)
        result = self._dataset_context_read_service.get_dataset_context(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
        )
        return self._field_filter_service.filter_dataset_context_summary(result, context)

    def get_workbook_context(
        self,
        context: ReadAuthorizationContext,
        workbook_group_id: str,
    ) -> WorkbookContextSummary:
        self._authorization_service.assert_can_list_datasets(context)
        return self._workbook_context_read_service.get_workbook_context(
            tenant_id=context.tenant_id,
            workbook_group_id=workbook_group_id,
            allowed_dataset_ids=self._authorization_service.get_effective_allowed_dataset_ids(
                context
            ),
        )

    def get_dataset_context_with_explanation(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        *,
        include_diagnostics: bool = False,
    ) -> FilteredWithExplanation:
        self._authorization_service.assert_can_read_context(context, dataset_id)
        result = self._dataset_context_read_service.get_dataset_context(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
        )
        return self._field_filter_service.filter_context_with_explanation(
            result,
            context,
            include_diagnostics=include_diagnostics,
        )

    def list_versions(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DatasetVersionSummary]:
        self._authorization_service.assert_can_read_dataset(context, dataset_id)
        results = self._version_read_service.list_versions(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
            limit=limit,
            offset=offset,
        )
        return [
            self._field_filter_service.filter_dataset_version_summary(result, context)
            for result in results
        ]

    def get_version(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        version_id: str,
    ) -> DatasetVersionSummary:
        """Return one exact authorized dataset version without implicit resolution."""
        self._authorization_service.assert_can_read_dataset(context, dataset_id)
        result = self._version_read_service.get_version(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
        )
        return self._field_filter_service.filter_dataset_version_summary(result, context)

    def get_schema(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        *,
        version_id: str | None = None,
    ) -> DatasetSchemaSummary:
        self._authorization_service.assert_can_read_schema(context, dataset_id)
        if version_id:
            result = self._schema_read_service.get_schema_for_version(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
                version_id=version_id,
            )
        else:
            result = self._schema_read_service.get_latest_schema(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
            )
        return self._field_filter_service.filter_dataset_schema_summary(
            result,
            context,
            dataset_id=dataset_id,
        )

    def get_schema_with_explanation(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        *,
        version_id: str | None = None,
        include_diagnostics: bool = False,
    ) -> FilteredWithExplanation:
        self._authorization_service.assert_can_read_schema(context, dataset_id)
        if version_id:
            result = self._schema_read_service.get_schema_for_version(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
                version_id=version_id,
            )
        else:
            result = self._schema_read_service.get_latest_schema(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
            )
        return self._field_filter_service.filter_schema_with_explanation(
            result,
            context,
            dataset_id=dataset_id,
            include_diagnostics=include_diagnostics,
        )

    def get_profile(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        *,
        version_id: str | None = None,
    ) -> DatasetProfileSummary:
        self._authorization_service.assert_can_read_profile(context, dataset_id)
        if version_id:
            result = self._profile_read_service.get_profile_for_version(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
                version_id=version_id,
            )
        else:
            result = self._profile_read_service.get_latest_profile(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
            )
        return self._field_filter_service.filter_dataset_profile_summary(
            result,
            context,
            dataset_id=dataset_id,
        )

    def get_profile_with_explanation(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        *,
        version_id: str | None = None,
        include_diagnostics: bool = False,
    ) -> FilteredWithExplanation:
        self._authorization_service.assert_can_read_profile(context, dataset_id)
        if version_id:
            result = self._profile_read_service.get_profile_for_version(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
                version_id=version_id,
            )
        else:
            result = self._profile_read_service.get_latest_profile(
                tenant_id=context.tenant_id,
                dataset_id=dataset_id,
            )
        return self._field_filter_service.filter_profile_with_explanation(
            result,
            context,
            dataset_id=dataset_id,
            include_diagnostics=include_diagnostics,
        )

    def list_artifacts(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        *,
        version_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DatasetArtifactSummary]:
        self._authorization_service.assert_can_read_artifact(context, dataset_id)
        results = self._artifact_read_service.list_artifacts(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
            version_id=version_id,
            limit=limit,
            offset=offset,
        )
        return [
            self._field_filter_service.filter_dataset_artifact_summary(result, context)
            for result in results
        ]

    def get_artifact(
        self,
        context: ReadAuthorizationContext,
        dataset_id: str,
        artifact_id: str,
    ) -> DatasetArtifactSummary:
        """Return one exact authorized dataset artifact without fallback selection."""
        self._authorization_service.assert_can_read_artifact(context, dataset_id)
        result = self._artifact_read_service.get_artifact(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
            artifact_id=artifact_id,
        )
        return self._field_filter_service.filter_dataset_artifact_summary(result, context)

    def list_jobs(  # noqa: PLR0913
        self,
        context: ReadAuthorizationContext,
        *,
        dataset_id: str | None = None,
        status: str | None = None,
        provider: str | None = None,
        job_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExternalDatasetJobSummary]:
        self._authorization_service.assert_can_read_job(context, dataset_id=dataset_id)
        results = self._job_read_service.list_jobs(
            tenant_id=context.tenant_id,
            dataset_id=dataset_id,
            status=status,
            provider=provider,
            job_type=job_type,
            limit=limit,
            offset=offset,
        )
        return [
            self._field_filter_service.filter_external_job_summary(result, context)
            for result in results
        ]
