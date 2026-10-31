"""Expose shared dataset services through authenticated REST endpoints."""

from typing import cast
from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from Organization.models import Organization
from resource_registry.api.management.serializers import (
    CreateChangeSerializer,
    DatasetChangeResponseSerializer,
    DatasetMetadataSerializer,
    LifecycleSerializer,
    RestoreSerializer,
    ReviseChangeSerializer,
    RevisionSerializer,
    SourceControlSerializer,
    SourceDeactivationResultSerializer,
)
from resource_registry.management_domain.catalog import (
    DatasetCatalogService,
    change_payload,
    dataset_payload,
)
from resource_registry.management_domain.changes import DatasetChangeService
from resource_registry.management_domain.errors import DatasetManagementError
from resource_registry.management_domain.external_sources import (
    DatasetExternalSourceService,
    source_payload,
)
from resource_registry.management_domain.lifecycle import DatasetLifecycleService


class DatasetManagementView(APIView):
    """Apply business authority independently of staff admission."""

    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc: Exception) -> Response:
        """Translate domain failures without leaking storage or object details."""
        if isinstance(exc, DatasetManagementError):
            return Response({"code": exc.code, "detail": str(exc)}, status=exc.status)
        if isinstance(exc, ValidationError):
            return Response({"code": "invalid_request", "detail": exc.messages}, status=400)
        return super().handle_exception(exc)

    @staticmethod
    def actor(request: Request) -> User:
        """Return the authenticated persisted actor."""
        return cast(User, request.user)

    @staticmethod
    def organization(request: Request) -> Organization | None:
        """Read organization only from trusted middleware."""
        organization = getattr(request, "organization", None)
        return organization if isinstance(organization, Organization) else None


class DatasetDetailView(DatasetManagementView):
    """Read and edit authorized dataset metadata."""

    @extend_schema(responses={200: dict})
    def get(self, request: Request, dataset_id: UUID) -> Response:
        """Return metadata and the currently selected schema."""
        return Response(
            DatasetCatalogService(self.actor(request), self.organization(request)).get_detail(
                dataset_id
            )
        )

    @extend_schema(request=DatasetMetadataSerializer, responses={200: dict})
    def patch(self, request: Request, dataset_id: UUID) -> Response:
        """Apply a revision-checked metadata edit."""
        serializer = DatasetMetadataSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        revision = values.pop("expected_revision")
        dataset = DatasetLifecycleService(
            self.actor(request), self.organization(request)
        ).update_metadata(dataset_id, values, expected_revision=revision)
        return Response(dataset_payload(dataset))


class DatasetRelatedView(DatasetManagementView):
    """Read bounded versions, preview, activity and dependencies."""

    @extend_schema(responses={200: dict})
    def get(self, request: Request, dataset_id: UUID, section: str) -> Response:
        """Return the requested section after authorizing dataset access."""
        service = DatasetCatalogService(self.actor(request), self.organization(request))
        dataset = service.lifecycle.get_dataset(dataset_id)
        if section == "preview":
            return Response(service.get_preview(dataset_id))
        if section == "usage":
            return Response(service.get_usage(dataset_id))
        rows: list[dict[str, object]]
        if section == "versions":
            rows = [
                {
                    "id": str(version.id),
                    "version_number": version.version_number,
                    "is_current": version.id == dataset.current_version_id,
                    "data_available": dataset.purge_started_at is None,
                    "availability": (
                        "Deleted"
                        if dataset.purged_at
                        else "Deletion in progress" if dataset.purge_started_at else "Available"
                    ),
                    "created_at": version.created_at.isoformat(),
                }
                for version in dataset.versions.filter(status="READY").order_by("-version_number")[
                    :200
                ]
            ]
        else:
            rows = [
                {
                    "id": str(activity.id),
                    "action": activity.action,
                    "actor_id": activity.actor_id,
                    "reason": activity.reason,
                    "version_id": str(activity.version_id) if activity.version_id else None,
                    "created_at": activity.created_at.isoformat(),
                }
                for activity in dataset.management_activity.order_by("-created_at")[:200]
            ]
        return Response({"results": rows, "count": len(rows)})


class DatasetLifecycleView(DatasetManagementView):
    """Perform manager-only lifecycle actions."""

    @extend_schema(request=LifecycleSerializer, responses={200: dict})
    def post(self, request: Request, dataset_id: UUID, operation: str) -> Response:
        """Archive, recover or restore using the shared transaction boundary."""
        serializer = (RestoreSerializer if operation == "restore-version" else LifecycleSerializer)(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)
        service = DatasetLifecycleService(self.actor(request), self.organization(request))
        values = serializer.validated_data
        if operation == "restore-version":
            dataset = service.restore_version(
                dataset_id,
                version_id=values["version_id"],
                expected_revision=values["expected_revision"],
                reason=values["reason"],
            )
        else:
            method = service.archive if operation == "archive" else service.unarchive
            dataset = method(
                dataset_id, expected_revision=values["expected_revision"], reason=values["reason"]
            )
        return Response(dataset_payload(dataset))


class DatasetRestoreView(DatasetLifecycleView):
    """Describe the required historical version explicitly in OpenAPI."""

    @extend_schema(request=RestoreSerializer, responses={200: dict})
    def post(
        self, request: Request, dataset_id: UUID, operation: str = "restore-version"
    ) -> Response:
        """Restore a published version using the shared lifecycle service."""
        return super().post(request, dataset_id, operation)


class DatasetChangeCollectionView(DatasetManagementView):
    """Create asynchronous proposals from verified owned uploads."""

    @extend_schema(
        request=CreateChangeSerializer,
        responses={201: DatasetChangeResponseSerializer},
        parameters=[
            OpenApiParameter("Idempotency-Key", str, OpenApiParameter.HEADER, required=True)
        ],
    )
    def post(self, request: Request) -> Response:
        """Queue preparation without exposing a consumable version."""
        serializer = CreateChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change = DatasetChangeService(self.actor(request), self.organization(request)).create(
            idempotency_key=request.headers.get("Idempotency-Key", ""), **serializer.validated_data
        )
        return Response(change_payload(change), status=201)


class DatasetChangeDetailView(DatasetManagementView):
    """Read proposal progress or revise explicit targets."""

    @extend_schema(responses={200: DatasetChangeResponseSerializer})
    def get(self, request: Request, change_id: UUID) -> Response:
        """Return private progress and differences to the owner or manager."""
        return Response(
            change_payload(
                DatasetChangeService(self.actor(request), self.organization(request)).get_change(
                    change_id
                )
            )
        )

    @extend_schema(request=ReviseChangeSerializer, responses={200: DatasetChangeResponseSerializer})
    def patch(self, request: Request, change_id: UUID) -> Response:
        """Invalidate old evidence and recalculate the selected targets."""
        serializer = ReviseChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            change_payload(
                DatasetChangeService(self.actor(request), self.organization(request)).revise(
                    change_id, **serializer.validated_data
                )
            )
        )


class DatasetChangeActionView(DatasetManagementView):
    """Publish, cancel or retry one proposal."""

    @extend_schema(request=RevisionSerializer, responses={200: DatasetChangeResponseSerializer})
    def post(self, request: Request, change_id: UUID, operation: str) -> Response:
        """Apply a revision-checked proposal action."""
        serializer = RevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service = DatasetChangeService(self.actor(request), self.organization(request))
        method = {"publish": service.publish, "cancel": service.cancel, "retry": service.retry}[
            operation
        ]
        return Response(change_payload(method(change_id, **serializer.validated_data)))


class DatasetExternalSourceDetailView(DatasetManagementView):
    """Expose source control revisions and exact output identities to authorized operators."""

    @extend_schema(responses={200: SourceControlSerializer})
    def get(self, request: Request, source_id: UUID) -> Response:
        """Read source status without granting access to archived content.

        Args:
            request: Authenticated request with its trusted organization context.
            source_id: Requested source identity.

        Returns:
            Current source control metadata and exact output associations.

        Raises:
            DatasetManagementError: If source visibility or authority is absent.
        """
        service = DatasetExternalSourceService(self.actor(request), self.organization(request))
        return Response(source_payload(service.get_source(source_id)))


class DatasetExternalSourceDeactivateView(DatasetManagementView):
    """Retire an external source without physically deleting its versions."""

    @extend_schema(
        request=LifecycleSerializer,
        responses={200: SourceDeactivationResultSerializer},
        parameters=[
            OpenApiParameter("Idempotency-Key", str, OpenApiParameter.HEADER, required=True)
        ],
    )
    def post(self, request: Request, source_id: UUID) -> Response:
        """Apply a revision-checked, idempotent source disappearance.

        Args:
            request: Authenticated request carrying the removal body and idempotency key.
            source_id: Source whose publication assignments should be retired.

        Returns:
            The persisted removal result and per-dataset outcomes.

        Raises:
            DatasetManagementError: If admission, revision or lifecycle checks fail.
            rest_framework.exceptions.ValidationError: If request fields are invalid.

        Side Effects:
            Cancel pending proposals, detach source assignments and archive eligible outputs.
        """
        serializer = LifecycleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            DatasetExternalSourceService(
                self.actor(request), self.organization(request)
            ).deactivate(
                source_id,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
                **serializer.validated_data,
            )
        )
