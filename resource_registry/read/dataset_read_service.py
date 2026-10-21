from __future__ import annotations

import re
from datetime import timedelta

from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone

from resource_registry.dataset_visibility import build_dataset_visibility_filter
from resource_registry.models import (
    ArtifactLifecycleStatus,
    ArtifactRole,
    Dataset,
    DatasetArtifact,
    DatasetProfile,
    DatasetStatus,
    DatasetVersion,
)
from resource_registry.read.dtos import DatasetDetails, DatasetSummary, to_iso
from resource_registry.read.exceptions import DatasetNotFoundError


class DatasetReadService:
    """Tenant-scoped read service for dataset metadata."""

    MAX_LIMIT = 100
    MIN_IES_STEM_LENGTH = 4
    MIN_ES_STEM_LENGTH = 4
    MIN_S_STEM_LENGTH = 3
    TOKEN_MATCH_SCORE = 8
    EXPANDED_TOKEN_MATCH_SCORE = 16

    def list_datasets(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        allowed_dataset_ids: frozenset[str] | None = None,
        status: str | None = None,
        source_type: str | None = None,
        workbook_group_id: str | None = None,
        sheet_name: str | None = None,
        relevance_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DatasetSummary]:
        """List tenant-visible datasets within the supplied authorized scope.

        Args:
            tenant_id: Namespace whose visibility rules constrain the query.
            allowed_dataset_ids: Server-resolved IDs to filter before pagination;
                None adds no ID restriction, while an empty set permits no results.
            status: Optional dataset status filter.
            source_type: Optional source type filter.
            workbook_group_id: Optional workbook group filter.
            sheet_name: Optional sheet name filter.
            relevance_query: Optional ranking text.
            limit: Maximum results, capped at MAX_LIMIT.
            offset: Number of matching scoped results to skip.

        Returns:
            Ordered dataset summaries from the requested page.
        """
        queryset = Dataset.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id),
            archived_at__isnull=True,
            status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        )
        if allowed_dataset_ids is not None:
            queryset = queryset.filter(id__in=allowed_dataset_ids)
        queryset = self._apply_common_filters(
            queryset,
            status=status,
            source_type=source_type,
            workbook_group_id=workbook_group_id,
            sheet_name=sheet_name,
        )
        annotated = self._annotate_latest_sheet_name(queryset)
        ordered = self._order_with_relevance(annotated, relevance_query=relevance_query)
        bounded = self._paginate_queryset(ordered, limit=limit, offset=offset)
        return [self._to_summary(dataset) for dataset in bounded]

    def get_dataset(self, *, tenant_id: str, dataset_id: str) -> DatasetDetails:
        dataset = Dataset.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id),
            id=dataset_id,
            archived_at__isnull=True,
            status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        ).first()
        if dataset is None:
            raise DatasetNotFoundError("Dataset not found for tenant.")
        return self._to_details(dataset)

    def search_datasets(  # noqa: PLR0913
        self,
        *,
        tenant_id: str,
        query: str,
        allowed_dataset_ids: frozenset[str] | None = None,
        status: str | None = None,
        source_type: str | None = None,
        workbook_group_id: str | None = None,
        sheet_name: str | None = None,
        relevance_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DatasetSummary]:
        """Search tenant-visible datasets within the supplied authorized scope.

        Args:
            tenant_id: Namespace whose visibility rules constrain the query.
            query: Dataset search text.
            allowed_dataset_ids: Server-resolved IDs to filter before pagination;
                None adds no ID restriction, while an empty set permits no results.
            status: Optional dataset status filter.
            source_type: Optional source type filter.
            workbook_group_id: Optional workbook group filter.
            sheet_name: Optional sheet name filter.
            relevance_query: Optional ranking override for the search text.
            limit: Maximum results, capped at MAX_LIMIT.
            offset: Number of matching scoped results to skip.

        Returns:
            Ranked dataset summaries from the requested page.
        """
        queryset = Dataset.objects.filter(
            build_dataset_visibility_filter(tenant_id=tenant_id),
            archived_at__isnull=True,
            status__in=[DatasetStatus.DRAFT, DatasetStatus.ACTIVE],
        ).filter(
            self._dataset_search_filter(query),
        )
        if allowed_dataset_ids is not None:
            queryset = queryset.filter(id__in=allowed_dataset_ids)
        queryset = self._apply_common_filters(
            queryset,
            status=status,
            source_type=source_type,
            workbook_group_id=workbook_group_id,
            sheet_name=sheet_name,
        )
        annotated = self._annotate_latest_sheet_name(queryset)
        ranking_query = str(relevance_query or query or "").strip() or None
        ordered = self._order_with_relevance(annotated, relevance_query=ranking_query)
        bounded = self._paginate_queryset(ordered, limit=limit, offset=offset)
        return [self._to_summary(dataset) for dataset in bounded]

    def _dataset_search_filter(self, query: str) -> Q:
        normalized_query = str(query or "").strip()
        search_filter = (
            Q(slug__icontains=normalized_query)
            | Q(name__icontains=normalized_query)
            | Q(tags__icontains=normalized_query)
        )
        token_filter = Q()
        token_fallback_filter = Q()
        for token_group in self._search_token_groups(normalized_query):
            token_group_filter = Q()
            for token in token_group:
                token_group_filter |= (
                    Q(slug__icontains=token)
                    | Q(name__icontains=token)
                    | Q(tags__icontains=token)
                )
            token_filter &= token_group_filter
            token_fallback_filter |= token_group_filter
        if token_filter:
            search_filter |= token_filter
        if token_fallback_filter:
            search_filter |= token_fallback_filter
        return search_filter

    def _search_tokens(self, query: str) -> tuple[str, ...]:
        return tuple(
            token
            for token in dict.fromkeys(re.split(r"[^a-z0-9]+", query.lower()))
            if len(token) > 1
        )

    def _search_token_groups(self, query: str) -> tuple[tuple[str, ...], ...]:
        return tuple(
            tuple(self._expand_search_token(token))
            for token in self._search_tokens(query)
        )

    def _expand_search_token(self, token: str) -> tuple[str, ...]:
        expanded = [token]
        if len(token) > self.MIN_IES_STEM_LENGTH and token.endswith("ies"):
            expanded.append(f"{token[:-3]}y")
        if len(token) > self.MIN_ES_STEM_LENGTH and token.endswith(("ches", "shes")):
            expanded.append(token[:-2])
        if len(token) > self.MIN_ES_STEM_LENGTH and token.endswith("xes"):
            expanded.append(token[:-2])
        if len(token) > self.MIN_S_STEM_LENGTH and token.endswith("s"):
            expanded.append(token[:-1])
        return tuple(dict.fromkeys(expanded))

    def _apply_common_filters(
        self,
        queryset,
        *,
        status: str | None,
        source_type: str | None,
        workbook_group_id: str | None,
        sheet_name: str | None,
    ):
        if status:
            queryset = queryset.filter(status=status)
        if source_type:
            queryset = queryset.filter(source_type=source_type)
        if workbook_group_id:
            queryset = queryset.filter(workbook_group_id=workbook_group_id)
        if sheet_name:
            queryset = queryset.filter(
                versions__artifacts__sheet_name__iexact=sheet_name,
                versions__artifacts__lifecycle_status="ACTIVE",
            ).distinct()
        return queryset

    def _order_with_relevance(self, queryset, *, relevance_query: str | None):
        normalized_query = str(relevance_query or "").strip()
        if not normalized_query:
            return queryset.order_by("slug", "id")
        if not hasattr(queryset, "query"):
            return queryset.order_by("slug", "id")

        latest_version_id = Subquery(
            DatasetVersion.objects.filter(dataset_id=OuterRef("pk"))
            .order_by("-version_number", "-created_at")
            .values("id")[:1],
        )
        latest_row_count = Subquery(
            DatasetProfile.objects.filter(dataset_version_id=latest_version_id)
            .annotate(row_count_text=KeyTextTransform("row_count", "metrics_json"))
            .order_by("-computed_at")
            .values("row_count_text")[:1],
        )
        has_sheet_match = Exists(
            Dataset.objects.filter(id=OuterRef("pk")).filter(
                versions__artifacts__sheet_name__icontains=normalized_query,
                versions__artifacts__lifecycle_status="ACTIVE",
            ),
        )
        token_match_score = Value(0, output_field=IntegerField())
        for token_group in self._search_token_groups(normalized_query):
            token_group_match = Q()
            for token in token_group:
                token_group_match |= (
                    Q(slug__icontains=token)
                    | Q(name__icontains=token)
                    | Q(tags__icontains=token)
                )
            token_score = (
                self.EXPANDED_TOKEN_MATCH_SCORE
                if len(token_group) > 1
                else self.TOKEN_MATCH_SCORE
            )
            token_match_score = token_match_score + Case(
                When(token_group_match, then=Value(token_score)),
                default=Value(0),
                output_field=IntegerField(),
            )
        now = timezone.now()
        recent_7d = now - timedelta(days=7)
        recent_30d = now - timedelta(days=30)

        annotated = queryset.annotate(
            _name_match_score=Case(
                When(name__icontains=normalized_query, then=Value(40)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            _slug_match_score=Case(
                When(slug__icontains=normalized_query, then=Value(35)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            _tag_match_score=Case(
                When(tags__icontains=normalized_query, then=Value(20)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            _sheet_match_score=Case(
                When(has_sheet_match, then=Value(25)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            _token_match_score=token_match_score,
            _recency_score=Case(
                When(updated_at__gte=recent_7d, then=Value(10)),
                When(updated_at__gte=recent_30d, then=Value(5)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            _row_count_value=Coalesce(
                Cast(latest_row_count, output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            ),
        ).annotate(
            _row_count_score=Case(
                When(_row_count_value__gte=1_000_000, then=Value(8)),
                When(_row_count_value__gte=100_000, then=Value(5)),
                When(_row_count_value__gte=10_000, then=Value(2)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        ).annotate(
            _relevance_score=(
                Coalesce(F("_name_match_score"), Value(0))
                + Coalesce(F("_slug_match_score"), Value(0))
                + Coalesce(F("_tag_match_score"), Value(0))
                + Coalesce(F("_sheet_match_score"), Value(0))
                + Coalesce(F("_token_match_score"), Value(0))
                + Coalesce(F("_recency_score"), Value(0))
                + Coalesce(F("_row_count_score"), Value(0))
            ),
        )
        return annotated.order_by("-_relevance_score", "-updated_at", "slug", "id")

    def _paginate_queryset(self, queryset, *, limit: int, offset: int) -> list[Dataset]:
        safe_limit = min(max(limit, 1), self.MAX_LIMIT)
        safe_offset = max(offset, 0)
        return list(queryset[safe_offset : safe_offset + safe_limit])

    def _annotate_latest_sheet_name(self, queryset):
        latest_sheet_name = Subquery(
            DatasetArtifact.objects.filter(
                dataset_version__dataset_id=OuterRef("pk"),
                artifact_role=ArtifactRole.PRIMARY,
                lifecycle_status=ArtifactLifecycleStatus.ACTIVE,
            )
            .exclude(sheet_name__isnull=True)
            .exclude(sheet_name="")
            .order_by(
                "-dataset_version__version_number",
                "-dataset_version__created_at",
                "-created_at",
            )
            .values("sheet_name")[:1],
        )
        return queryset.annotate(_latest_sheet_name=latest_sheet_name)

    def _to_summary(self, dataset: Dataset) -> DatasetSummary:
        return DatasetSummary(
            id=str(dataset.id),
            tenant_id=str(dataset.tenant_id),
            slug=dataset.slug,
            name=dataset.name,
            source_type=dataset.source_type,
            status=dataset.status,
            created_at=str(to_iso(dataset.created_at)),
            updated_at=str(to_iso(dataset.updated_at)),
            sheet_name=str(getattr(dataset, "_latest_sheet_name", "") or "") or None,
        )

    def _to_details(self, dataset: Dataset) -> DatasetDetails:
        return DatasetDetails(
            id=str(dataset.id),
            tenant_id=str(dataset.tenant_id),
            slug=dataset.slug,
            name=dataset.name,
            source_type=dataset.source_type,
            status=dataset.status,
            description=dataset.description,
            owner_team=dataset.owner_team,
            tags=list(dataset.tags or []),
            created_at=str(to_iso(dataset.created_at)),
            updated_at=str(to_iso(dataset.updated_at)),
            archived_at=to_iso(dataset.archived_at),
        )
