"""Authenticated REST endpoints for private staged resource intakes."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast
from uuid import UUID

from django.contrib.auth.models import User
from django.urls import ResolverMatch, reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from Organization.models import Organization
from resource_registry.api.intakes.dependencies import get_staged_resource_intake_service
from resource_registry.api.intakes.serializers import (
    CompleteIntakeSerializer,
    CreateIntakeSerializer,
    PublishIntakeSerializer,
)
from resource_registry.api.scope import (
    REGISTRY_SCOPE_PARAMETER,
    RegistryApiContextPermission,
    get_registry_scope_http_status,
)
from resource_registry.namespace_selection import RegistryScopeSelectionError
from resource_registry.scope_adapters import (
    get_request_scope_policy,
    select_request_registry_namespace,
)
from resource_registry.uploads.backend_blocks import build_backend_upload_descriptor
from resource_registry.uploads.exceptions import UploadApplicationError


class StagedResourceIntakeApiView(APIView):
    """Provide common private-intake authentication and error mapping."""

    permission_classes: list[type[BasePermission]] = [
        IsAuthenticated,
        RegistryApiContextPermission,
    ]
    parser_classes = [JSONParser]
    registry_private_intake: bool = True

    def handle_exception(self, exc: Exception) -> Response:
        """Map a scope failure without changing private staging authorization.

        Args:
            exc: Failure raised at the REST boundary.

        Returns:
            Sanitized scope failure or the standard DRF response.
        """
        if isinstance(exc, RegistryScopeSelectionError):
            return Response(
                {"code": exc.code, "message": str(exc)}, status=get_registry_scope_http_status(exc)
            )
        return super().handle_exception(exc)

    @staticmethod
    def _user(request: Request) -> User:
        """Return the authenticated persisted user."""
        return cast(User, request.user)

    @staticmethod
    def _organization(request: Request) -> Organization | None:
        """Return only the trusted host-resolved Organization."""
        organization = getattr(request, "organization", None)
        return organization if isinstance(organization, Organization) else None

    @staticmethod
    def _error(error: UploadApplicationError) -> Response:
        """Map a stable application failure to the public response shape."""
        return Response({"code": error.code, "message": str(error)}, status=error.http_status)


class StagedResourceIntakeCollectionView(StagedResourceIntakeApiView):
    """Create one private unassigned staging intake."""

    @extend_schema(request=CreateIntakeSerializer, responses={200: dict, 201: dict})
    def post(self, request: Request) -> Response:
        """Create an intake and return authenticated server-upload instructions."""
        serializer = CreateIntakeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"code": "INTAKE_INVALID_REQUEST"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            values = serializer.validated_data
            result = get_staged_resource_intake_service().create_intake(
                user=self._user(request),
                filename=str(values["filename"]),
                content_type=str(values["content_type"]),
                size_bytes=int(values["size_bytes"]),
                checksum_algorithm=(
                    str(values["checksum_algorithm"]) if values.get("checksum_algorithm") else None
                ),
                checksum=(str(values["checksum"]) if values.get("checksum") else None),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
                issue_direct_authorization=False,
            )
        except UploadApplicationError as error:
            return self._error(error)
        payload = asdict(result.intake)
        payload.update(
            {
                "storage_provider": result.storage_provider,
                "idempotent_replay": result.idempotent_replay,
            }
        )
        if result.intake.status == "PENDING_UPLOAD":
            namespace = cast(ResolverMatch, request.resolver_match).namespace
            payload["upload"] = build_backend_upload_descriptor(
                reverse(f"{namespace}:block", args=(result.intake.intake_id, 0))
            )
        return Response(
            payload,
            status=status.HTTP_200_OK if result.idempotent_replay else status.HTTP_201_CREATED,
        )


class StagedResourceIntakeBlockView(StagedResourceIntakeApiView):
    """Receive one bounded binary block for its authenticated intake owner."""

    @extend_schema(request={"application/octet-stream": OpenApiTypes.BINARY}, responses={204: None})
    def put(self, request: Request, intake_id: UUID, block_index: int) -> Response:
        """Authorize and forward a bounded request body to private storage.

        Args:
            request: Authenticated binary upload request.
            intake_id: Server-issued intake identifier.
            block_index: Zero-based block position.

        Returns:
            An empty success response or a sanitized application error.
        """
        if request.content_type != "application/octet-stream":
            return Response({"code": "INTAKE_INVALID_CONTENT_TYPE"}, status=415)
        try:
            get_staged_resource_intake_service().upload_backend_block(
                user=self._user(request),
                intake_id=intake_id,
                block_index=block_index,
                read_chunk=request.read,
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(status=204)


class StagedResourceIntakeDetailView(StagedResourceIntakeApiView):
    """Read one private staged intake."""

    @extend_schema(responses={200: dict})
    def get(self, request: Request, intake_id: UUID) -> Response:
        """Return the uploader's sanitized intake state."""
        try:
            result = get_staged_resource_intake_service().get_intake(
                user=self._user(request), intake_id=intake_id
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(asdict(result))


class StagedResourceIntakeCompleteView(StagedResourceIntakeApiView):
    """Verify and inspect one private staged intake."""

    @extend_schema(request=CompleteIntakeSerializer, responses={200: dict})
    def post(self, request: Request, intake_id: UUID) -> Response:
        """Complete upload verification and synchronously persist safe inspection metadata."""
        serializer = CompleteIntakeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"code": "INTAKE_INVALID_REQUEST"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = get_staged_resource_intake_service().complete_intake(
                user=self._user(request),
                intake_id=intake_id,
                expected_etag=(
                    str(serializer.validated_data["etag"])
                    if serializer.validated_data.get("etag")
                    else None
                ),
                commit_backend_blocks=True,
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(asdict(result))


class StagedResourceIntakeCancelView(StagedResourceIntakeApiView):
    """Cancel one private staged intake."""

    @extend_schema(request=None, responses={200: dict})
    def post(self, request: Request, intake_id: UUID) -> Response:
        """Cancel the intake and request exact staging cleanup."""
        try:
            result = get_staged_resource_intake_service().cancel_intake(
                user=self._user(request), intake_id=intake_id
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(asdict(result))


class StagedResourceIntakePublishView(StagedResourceIntakeApiView):
    """Publish one verified intake into an authorized Registry destination."""

    registry_private_intake: bool = False

    @extend_schema(
        parameters=[REGISTRY_SCOPE_PARAMETER],
        request=PublishIntakeSerializer,
        responses={200: dict, 202: dict},
    )
    def post(self, request: Request, intake_id: UUID) -> Response:
        """Copy the intake into destination provenance and queue standard registration."""
        serializer = PublishIntakeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"code": "INTAKE_INVALID_REQUEST"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            values = serializer.validated_data
            workspace_id = values.get("workspace_id")
            selected_workspace_id = get_request_scope_policy(request).resolve_selection(
                select_current=lambda: select_request_registry_namespace(
                    request=request,
                    workspace_id=workspace_id,
                    visibility_scope=str(values["visibility_scope"]),
                ).workspace_id,
                select_legacy=lambda: workspace_id,
                requires_current=values["visibility_scope"] == "WORKSPACE" and workspace_id is None,
                surface="intake_publish",
            )
            result = get_staged_resource_intake_service().publish_intake(
                user=self._user(request),
                organization=self._organization(request),
                intake_id=intake_id,
                visibility_scope=str(values["visibility_scope"]),
                workspace_id=selected_workspace_id,
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(
            asdict(result),
            status=status.HTTP_200_OK if result.idempotent_replay else status.HTTP_202_ACCEPTED,
        )
