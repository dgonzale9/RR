"""Thin authenticated REST adapters for direct-to-storage upload control."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast
from uuid import UUID

from django.contrib.auth.models import User
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from Organization.models import Organization
from resource_registry.api.scope import (
    REGISTRY_SCOPE_PARAMETER,
    RegistryApiContextPermission,
    get_registry_scope_http_status,
)
from resource_registry.api.uploads.dependencies import get_large_file_upload_service
from resource_registry.api.uploads.serializers import (
    CompleteUploadSessionSerializer,
    CreateUploadSessionSerializer,
    RegistrationSubmissionSerializer,
    UploadSessionResponseSerializer,
)
from resource_registry.namespace_selection import RegistryScopeSelectionError
from resource_registry.registration_submissions.services import RegistrationSubmissionService
from resource_registry.scope_adapters import (
    get_request_scope_policy,
    select_request_registry_namespace,
    validate_registry_destination,
)
from resource_registry.uploads.exceptions import UploadApplicationError
from resource_registry.uploads.services import LargeFileUploadAuthorizationService, UploadAdmission


class LargeFileUploadApiView(APIView):
    """Provide shared authenticated and sanitized upload error handling."""

    permission_classes: list[type[BasePermission]] = [
        IsAuthenticated,
        RegistryApiContextPermission,
    ]
    parser_classes = [JSONParser]
    authorization_service = LargeFileUploadAuthorizationService()

    def handle_exception(self, exc: Exception) -> Response:
        """Map Registry selection errors without exposing destination identifiers.

        Args:
            exc: Failure raised by the adapter or permission boundary.

        Returns:
            A sanitized Registry error or the standard DRF response.
        """
        if isinstance(exc, RegistryScopeSelectionError):
            return Response(
                {"code": exc.code, "message": str(exc)}, status=get_registry_scope_http_status(exc)
            )
        return super().handle_exception(exc)

    def _admit_existing_upload(
        self, request: Request, upload_id: UUID, *, require_mutation: bool
    ) -> UploadAdmission:
        admission = self.authorization_service.admit_existing_upload(
            user=self._user(request),
            organization=self._organization(request),
            upload_id=upload_id,
            require_mutation=require_mutation,
        )
        validate_registry_destination(
            request=request,
            namespace_id=str(admission.namespace_id),
            workspace_id=admission.workspace_id,
            surface="upload_existing",
        )
        return admission

    @staticmethod
    def _error(error: UploadApplicationError) -> Response:
        return Response(
            {"code": error.code, "message": str(error)},
            status=error.http_status,
        )

    @staticmethod
    def _organization(request: Request) -> Organization | None:
        value = getattr(request, "organization", None)
        return value if isinstance(value, Organization) else None

    @staticmethod
    def _user(request: Request) -> User:
        return cast(User, request.user)


class UploadSessionCollectionView(LargeFileUploadApiView):
    """Create one canonically authorized direct-upload session."""

    @extend_schema(
        parameters=[REGISTRY_SCOPE_PARAMETER],
        request=CreateUploadSessionSerializer,
        responses={200: dict, 201: dict},
    )
    def post(self, request: Request) -> Response:
        serializer = CreateUploadSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "UPLOAD_INVALID_REQUEST", "message": "Upload request rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        idempotency_key = request.headers.get("Idempotency-Key", "")
        try:
            values = serializer.validated_data
            workspace_id = values.get("workspace_id")
            selected_workspace_id = get_request_scope_policy(request).resolve_selection(
                select_current=lambda: select_request_registry_namespace(
                    request=request,
                    workspace_id=workspace_id,
                ).workspace_id,
                select_legacy=lambda: workspace_id,
                requires_current=workspace_id is None,
                surface="upload_create",
            )
            if selected_workspace_id is None:
                raise RegistryScopeSelectionError(
                    "registry_scope_conflict",
                    "Direct upload creation requires a Workspace destination.",
                )
            admission = self.authorization_service.admit(
                user=self._user(request),
                organization=self._organization(request),
                workspace_id=int(selected_workspace_id),
                require_mutation=True,
            )
            created = get_large_file_upload_service().create_session(
                admission=admission,
                user=self._user(request),
                filename=str(values["filename"]),
                content_type=str(values["content_type"]),
                size_bytes=int(values["size_bytes"]),
                checksum_algorithm=(
                    str(values["checksum_algorithm"])
                    if values.get("checksum_algorithm") is not None
                    else None
                ),
                checksum=(str(values["checksum"]) if values.get("checksum") else None),
                idempotency_key=idempotency_key,
            )
        except UploadApplicationError as error:
            return self._error(error)
        payload = asdict(created.session)
        payload.update(
            {
                "storage_provider": created.storage_provider,
                "idempotent_replay": created.idempotent_replay,
                "limits": {"max_file_size_bytes": created.max_file_size_bytes},
            }
        )
        if created.authorization is not None:
            payload["upload"] = {
                "method": created.authorization.upload_method,
                "url": created.authorization.upload_url,
                "expires_at": created.authorization.expires_at,
                "recommended_chunk_size_bytes": (
                    created.authorization.recommended_chunk_size_bytes
                ),
                "max_parallel_chunks": created.authorization.max_parallel_chunks,
                "required_headers": dict(created.authorization.required_headers),
            }
        response_status = (
            status.HTTP_200_OK if created.idempotent_replay else status.HTTP_201_CREATED
        )
        return Response(payload, status=response_status)


class UploadSessionDetailView(LargeFileUploadApiView):
    """Return one sanitized currently authorized upload session."""

    @extend_schema(
        parameters=[REGISTRY_SCOPE_PARAMETER], responses={200: UploadSessionResponseSerializer}
    )
    def get(self, request: Request, upload_id: UUID) -> Response:
        try:
            admission = self._admit_existing_upload(request, upload_id, require_mutation=False)
            snapshot = get_large_file_upload_service().get_session(
                upload_id=upload_id,
                admission=admission,
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(asdict(snapshot))


class UploadSessionCompleteView(LargeFileUploadApiView):
    """Verify provider metadata and complete one upload session."""

    @extend_schema(
        parameters=[REGISTRY_SCOPE_PARAMETER],
        request=CompleteUploadSessionSerializer,
        responses={200: dict},
    )
    def post(self, request: Request, upload_id: UUID) -> Response:
        serializer = CompleteUploadSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"code": "UPLOAD_INVALID_REQUEST"}, status=400)
        try:
            admission = self._admit_existing_upload(request, upload_id, require_mutation=True)
            snapshot = get_large_file_upload_service().complete_session(
                upload_id=upload_id,
                admission=admission,
                expected_etag=(
                    str(serializer.validated_data["etag"])
                    if serializer.validated_data.get("etag")
                    else None
                ),
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(asdict(snapshot))


class UploadSessionCancelView(LargeFileUploadApiView):
    """Idempotently cancel and clean one authorized upload."""

    @extend_schema(parameters=[REGISTRY_SCOPE_PARAMETER], request=None, responses={200: dict})
    def post(self, request: Request, upload_id: UUID) -> Response:
        try:
            admission = self._admit_existing_upload(request, upload_id, require_mutation=True)
            snapshot = get_large_file_upload_service().cancel_session(
                upload_id=upload_id,
                admission=admission,
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(asdict(snapshot))


class UploadSessionRegistrationView(LargeFileUploadApiView):
    """Submit one completed upload for later registration worker execution."""

    submission_service = RegistrationSubmissionService()

    @extend_schema(
        parameters=[REGISTRY_SCOPE_PARAMETER],
        request=RegistrationSubmissionSerializer,
        responses={202: dict},
    )
    def post(self, request: Request, upload_id: UUID) -> Response:
        """Persist one authorized idempotent registration request and job."""
        serializer = RegistrationSubmissionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"code": "UPLOAD_INVALID_REQUEST", "message": "Upload request rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            self._admit_existing_upload(request, upload_id, require_mutation=True)
            result = self.submission_service.submit(
                user=self._user(request),
                organization=self._organization(request),
                upload_id=upload_id,
            )
        except UploadApplicationError as error:
            return self._error(error)
        return Response(
            {
                "registration_id": result.registration_id,
                "status": result.status,
                "job_type": result.job_type.value,
            },
            status=status.HTTP_202_ACCEPTED,
        )
