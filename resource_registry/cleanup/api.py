"""Expose reviewed cleanup through the existing session and token API trees."""

from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from resource_registry.api.management.serializers import StrictManagementSerializer
from resource_registry.api.management.views import DatasetManagementView
from resource_registry.cleanup.service import DatasetCleanupService, cleanup_payload


class DeletionReviewSerializer(StrictManagementSerializer):
    """Validate the exact dataset revision and inventory shown to the manager."""

    expected_revision = serializers.IntegerField(min_value=0)
    inventory_fingerprint = serializers.RegexField(r"^[0-9a-f]{64}$")


class DeletionRequestSerializer(DeletionReviewSerializer):
    """Validate explicit irreversible intent without accepting storage coordinates."""

    reason = serializers.CharField(max_length=500)
    idempotency_key = serializers.CharField(max_length=128)
    confirmed = serializers.BooleanField()

    def validate_confirmed(self, value: bool) -> bool:
        """Require explicit acknowledgement before creating a deletion request."""
        if not value:
            raise serializers.ValidationError(
                "Confirm permanent deletion after reviewing its impact."
            )
        return value


class CleanupPaginationSerializer(serializers.Serializer[dict[str, object]]):
    """Validate bounded cleanup pagination."""

    limit = serializers.IntegerField(min_value=1, max_value=100, default=100)
    offset = serializers.IntegerField(min_value=0, default=0)


class DatasetDeletionPreviewView(DatasetManagementView):
    """Read a manager-authorized deletion inventory without deleting content."""

    @extend_schema(parameters=[CleanupPaginationSerializer], responses={200: dict})
    def get(self, request: Request, dataset_id: UUID) -> Response:
        """Return file counts, sizes, dependencies and a review fingerprint."""
        pagination = CleanupPaginationSerializer(data=request.query_params)
        pagination.is_valid(raise_exception=True)
        return Response(
            DatasetCleanupService(self.actor(request), self.organization(request)).preview(
                dataset_id, **pagination.validated_data
            )
        )


class DatasetDeletionRequestView(DatasetManagementView):
    """Persist reviewed intent while leaving physical deletion to the worker."""

    @extend_schema(request=DeletionRequestSerializer, responses={202: dict})
    def post(self, request: Request, dataset_id: UUID) -> Response:
        """Schedule deletion of an archived dataset after the recovery deadline."""
        serializer = DeletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        values.pop("confirmed")
        result = DatasetCleanupService(
            self.actor(request), self.organization(request)
        ).request_deletion(dataset_id, **values)
        return Response(cleanup_payload(result), status=202)


class CleanupRequestCollectionView(DatasetManagementView):
    """List cleanup progress after tenant and manager authorization."""

    @extend_schema(responses={200: dict})
    def get(self, request: Request) -> Response:
        """Return a bounded page with filtered totals and navigation offsets."""
        service = DatasetCleanupService(self.actor(request), self.organization(request))
        rows = service.queryset().order_by("-created_at")
        if request.query_params.get("status"):
            rows = rows.filter(status=request.query_params["status"])
        if request.query_params.get("q"):
            rows = rows.filter(dataset__name__icontains=request.query_params["q"])
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), 100)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            return Response(
                {"code": "invalid_pagination", "detail": "Use numeric limit and offset."},
                status=400,
            )
        total = rows.count()
        results = [cleanup_payload(item) for item in rows[offset : offset + limit]]
        return Response(
            {
                "results": results,
                "count": len(results),
                "total": total,
                "next_offset": offset + limit if offset + limit < total else None,
                "previous_offset": max(0, offset - limit) if offset else None,
            }
        )


class CleanupRequestDetailView(DatasetManagementView):
    """Read per-object outcomes without exposing private storage coordinates."""

    @extend_schema(responses={200: dict})
    def get(self, request: Request, request_id: UUID) -> Response:
        """Return a scoped request and bounded item progress."""
        result = DatasetCleanupService(self.actor(request), self.organization(request)).get_request(
            request_id
        )
        payload = cleanup_payload(result)
        pagination = CleanupPaginationSerializer(data=request.query_params)
        pagination.is_valid(raise_exception=True)
        offset, limit = pagination.validated_data["offset"], pagination.validated_data["limit"]
        rows = result.items.order_by("id")
        total = rows.count()
        payload["items"] = list(
            rows.values("id", "status", "attempts", "block_reason")[offset : offset + limit]
        )
        payload.update(
            total=total,
            next_offset=offset + limit if offset + limit < total else None,
            previous_offset=max(0, offset - limit) if offset else None,
        )
        return Response(payload)


class CleanupRequestActionView(DatasetManagementView):
    """Apply cancellation, retry or reconfirmation through shared business services."""

    @extend_schema(request=None, responses={200: dict})
    def post(self, request: Request, request_id: UUID, operation: str) -> Response:
        """Revalidate live manager authority for each cleanup action."""
        service = DatasetCleanupService(self.actor(request), self.organization(request))
        if operation == "confirm":
            serializer = DeletionReviewSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            result = service.confirm(request_id, **serializer.validated_data)
        else:
            result = {"cancel": service.cancel, "retry": service.retry}[operation](request_id)
        return Response(cleanup_payload(result))


class CleanupRequestConfirmationView(CleanupRequestActionView):
    """Document reconfirmation separately from bodyless cancellation and retry."""

    @extend_schema(request=DeletionReviewSerializer, responses={200: dict})
    def post(self, request: Request, request_id: UUID, operation: str = "confirm") -> Response:
        """Confirm the newly reviewed revision and inventory."""
        return super().post(request, request_id, operation)
