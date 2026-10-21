"""Django Admin operations for private staged resource intake."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse

from BotServer.util.user_group_management import UserGroupManagement
from Organization.models import Organization
from resource_registry.api.intakes.dependencies import get_staged_resource_intake_service
from resource_registry.intake.permissions import (
    MANAGE_STAGED_RESOURCE_INTAKES_PERMISSION,
    can_manage_staged_resource_intakes,
    can_remove_global_destination_assignments,
    can_remove_scoped_destination_assignments,
    get_removable_staged_resource_publications,
)
from resource_registry.intake.services import IntakeSnapshot
from resource_registry.models import (
    RegistryNamespace,
    StagedResourceIntake,
    StagedResourceIntakeStatus,
    StagedResourcePublication,
    StagedResourcePublicationStatus,
)
from resource_registry.services.destination_assignment_removal_service import (
    DestinationAssignmentRemovalService,
)
from resource_registry.uploads.backend_blocks import build_backend_upload_descriptor
from resource_registry.uploads.exceptions import UploadApplicationError


def _get_request_organization(request: HttpRequest) -> Organization | None:
    """Get the trusted Organization attached to the current Admin request.

    Args:
        request: Incoming Django Admin request.

    Returns:
        Host-resolved Organization, when one is available.
    """
    organization = getattr(request, "organization", None)
    return organization if isinstance(organization, Organization) else None


class StagedResourcePublicationInline(admin.TabularInline):
    """Display immutable destination publications and their processing state."""

    model = StagedResourcePublication
    extra = 0
    can_delete = False
    fields = (
        "registry_namespace",
        "organization",
        "workspace",
        "visibility_scope",
        "assignment_status",
        "publication_attempt",
        "registration_submission",
        "processing_status",
        "detachment_requested_at",
        "detached_at",
        "detached_by",
        "detachment_reason",
        "completed_at",
        "cleanup_required",
    )
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        """Prevent manual publication creation from inline controls."""
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[StagedResourcePublication]:
        """Fetch both supported processing jobs with each destination publication."""
        publications = (
            super()
            .get_queryset(request)
            .select_related(
                "registration_submission__dataset_registration_job",
                "registration_submission__workbook_ingestion_job",
            )
        )
        if UserGroupManagement.is_user_scoped_admin(  # type: ignore[no-untyped-call]
            request.user
        ) and not can_remove_global_destination_assignments(request.user):
            return publications.filter(
                pk__in=get_removable_staged_resource_publications(
                    request.user, _get_request_organization(request)
                )
            )
        return publications

    @admin.display(description="Dataset processing")
    def processing_status(self, publication: StagedResourcePublication | None) -> str:
        """Describe the downstream processing state for one destination publication.

        Args:
            publication: Destination publication being rendered in Django Admin.

        Returns:
            A user-facing state that distinguishes publication from Dataset processing.
        """
        return self._processing_status_for_publication(publication)

    @staticmethod
    def _processing_status_for_publication(publication: StagedResourcePublication | None) -> str:
        """Describe the downstream processing state for one destination publication.

        Args:
            publication: Destination publication whose processing status is requested.

        Returns:
            A user-facing state that distinguishes publication from Dataset processing.
        """
        if publication is None:
            return "—"
        if publication.registration_submission_id is None:
            return "Registration pending"
        submission = publication.registration_submission
        dataset_registration_job = getattr(submission, "dataset_registration_job", None)
        if dataset_registration_job is not None:
            return (
                "CSV registration: "
                f"{StagedResourcePublicationInline._processing_status_label(dataset_registration_job.status)}"
            )
        workbook_ingestion_job = getattr(submission, "workbook_ingestion_job", None)
        if workbook_ingestion_job is not None:
            return (
                "Workbook ingestion: "
                f"{StagedResourcePublicationInline._processing_status_label(workbook_ingestion_job.status)}"
            )
        return "Registration submitted; awaiting processing"

    @staticmethod
    def _processing_status_label(job_status: str) -> str:
        """Translate a durable worker state into unambiguous operator language.

        Args:
            job_status: Persisted status of a CSV registration or workbook ingestion job.

        Returns:
            User-facing Dataset processing status.
        """
        labels = {
            "submitted": "Processing pending",
            "queued": "Queued for processing",
            "running": "Processing",
            "completed": "Dataset available",
            "completed_with_skips": "Dataset available (some sheets skipped)",
            "failed": "Processing failed",
            "cancelled": "Processing cancelled",
        }
        return labels.get(job_status.lower(), "Processing status unavailable")


class PublishDestinationsForm(forms.Form):
    """Validate explicit immutable destination namespace selections."""

    namespace_ids = forms.MultipleChoiceField(
        choices=(),
        required=True,
        label="Destinations",
        help_text="Select one or more enabled Organization or Workspace destinations.",
    )

    def __init__(
        self,
        *args: object,
        choices: Sequence[tuple[str, Sequence[tuple[str, str]]]],
        **kwargs: object,
    ) -> None:
        """Initialize grouped destination choices.

        Args:
            args: Positional form arguments.
            choices: Grouped namespace choices.
            kwargs: Keyword form arguments.
        """
        super().__init__(*args, **kwargs)
        self.fields["namespace_ids"].choices = choices


class CreateStagedResourceIntakeForm(forms.Form):
    """Validate browser-supplied metadata before an Admin intake is authorized."""

    filename = forms.CharField(max_length=255, strip=True)
    content_type = forms.CharField(max_length=255, strip=True)
    size_bytes = forms.IntegerField(min_value=1)
    idempotency_key = forms.RegexField(regex=r"^[A-Za-z0-9._:-]{1,128}$", max_length=128)


class RemoveDestinationsForm(forms.Form):
    """Validate selected active publication assignments and an audit reason."""

    publication_ids = forms.MultipleChoiceField(
        choices=(),
        required=True,
        label="Destination assignments",
    )
    reason = forms.CharField(
        max_length=500,
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Record why this destination assignment is being removed.",
    )
    confirmation = forms.BooleanField(
        required=True,
        label="I confirm this logically removes the selected destination assignments.",
        help_text=(
            "Datasets may be archived when no active assignment remains; Azure objects stay intact."
        ),
    )

    def __init__(
        self,
        *args: object,
        choices: Sequence[tuple[str, str]],
        **kwargs: object,
    ) -> None:
        """Initialize choices for active destination publications.

        Args:
            args: Positional form arguments.
            choices: Publication choices eligible for removal.
            kwargs: Keyword form arguments.
        """
        super().__init__(*args, **kwargs)
        self.fields["publication_ids"].choices = choices


@admin.register(StagedResourceIntake)
class StagedResourceIntakeAdmin(admin.ModelAdmin):
    """Operate on private intakes while keeping durable provenance immutable."""

    management_permission_name = MANAGE_STAGED_RESOURCE_INTAKES_PERMISSION
    change_form_template = "admin/resource_registry/stagedresourceintake/change_form.html"
    change_list_template = "admin/resource_registry/stagedresourceintake/change_list.html"
    inlines = (StagedResourcePublicationInline,)
    list_display = (
        "normalized_filename",
        "submitted_by",
        "status",
        "expires_at",
        "cleanup_required",
        "created_at",
    )
    list_filter = ("status", "cleanup_required", "resolved_file_format")
    search_fields = ("id", "normalized_filename", "submitted_by__username")
    readonly_fields = (
        "id",
        "submitted_by",
        "original_filename",
        "normalized_filename",
        "resolved_file_format",
        "declared_content_type",
        "detected_content_type",
        "declared_size_bytes",
        "actual_size_bytes",
        "storage_provider",
        "storage_object_key",
        "storage_etag",
        "storage_version_id",
        "checksum_algorithm",
        "declared_checksum",
        "verified_checksum",
        "checksum_verification_status",
        "inspection_json",
        "status",
        "failure_code",
        "cleanup_required",
        "expires_at",
        "created_at",
        "updated_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Prevent intake creation from Django Admin."""
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: StagedResourceIntake | None = None
    ) -> bool:
        """Prevent destructive intake deletion from Django Admin."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: StagedResourceIntake | None = None
    ) -> bool:
        """Allow intake operations only for authorized operational staff.

        Args:
            request: Incoming Django Admin request.
            obj: Intake being displayed or operated on.

        Returns:
            True when the active staff user is a superuser or holds the intake-management permission.
        """
        return self._can_manage_staged_resource_intakes(request)

    def has_view_permission(
        self, request: HttpRequest, obj: StagedResourceIntake | None = None
    ) -> bool:
        """Allow permitted operational staff to view staged intakes in Django Admin.

        Args:
            request: Incoming Django Admin request.
            obj: Intake being displayed, if applicable.

        Returns:
            True when Django's standard view permission or intake-management authority is present.
        """
        if self._can_manage_staged_resource_intakes(request):
            return True
        if self._can_remove_destinations(request, intake=obj):
            return True
        return super().has_view_permission(request, obj)

    def get_model_perms(self, request: HttpRequest) -> dict[str, bool]:
        """Expose the intake model in Django Admin for authorized operational staff.

        Args:
            request: Incoming Django Admin request.

        Returns:
            Django Admin model permissions, including the global intake-management permission.
        """
        permissions = super().get_model_perms(request)
        if self._can_manage_staged_resource_intakes(request):
            permissions["view"] = True
            permissions["change"] = True
        elif self._can_remove_destinations(request):
            permissions["view"] = True
        return permissions

    def get_queryset(self, request: HttpRequest) -> QuerySet[StagedResourceIntake]:
        """Limit scoped administrators to intakes with removable current-host publications.

        Args:
            request: Incoming Django Admin request.

        Returns:
            Intakes visible under the current user's destination-removal authority.
        """
        intakes = super().get_queryset(request)
        if UserGroupManagement.is_user_scoped_admin(  # type: ignore[no-untyped-call]
            request.user
        ) and not can_remove_global_destination_assignments(request.user):
            return intakes.filter(
                publications__in=get_removable_staged_resource_publications(
                    request.user, _get_request_organization(request)
                ).filter(assignment_status=StagedResourcePublicationStatus.ACTIVE)
            ).distinct()
        return intakes

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, object],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: StagedResourceIntake | None = None,
    ) -> HttpResponse:
        """Show the destination-removal action only when the current user can use it.

        Args:
            request: Incoming Django Admin request.
            context: Template context for the intake change form.
            add: Whether this is an add form.
            change: Whether this is a change form.
            form_url: Form submission URL.
            obj: Intake being displayed, when available.

        Returns:
            Rendered change form with scoped action visibility.
        """
        context["can_remove_destinations"] = self._can_remove_destinations(request, intake=obj)
        return super().render_change_form(request, context, add, change, form_url, obj)

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, object] | None = None
    ) -> HttpResponse:
        """Render the intake list with the upload action for authorized operators."""
        context = dict(extra_context or {})
        context["can_upload_resource"] = self._can_manage_staged_resource_intakes(request)
        return super().changelist_view(request, extra_context=context)

    def get_urls(self) -> list[object]:
        """Add intake upload, publication, and destination-removal pages."""
        return [
            path(
                "upload/",
                self.admin_site.admin_view(self.upload_resource_view),
                name="resource_registry_stagedresourceintake_upload",
            ),
            path(
                "upload/create/",
                self.admin_site.admin_view(self.create_upload_intake_view),
                name="resource_registry_stagedresourceintake_upload_create",
            ),
            path(
                "upload/<uuid:intake_id>/",
                self.admin_site.admin_view(self.upload_intake_status_view),
                name="resource_registry_stagedresourceintake_upload_status",
            ),
            path(
                "upload/<uuid:intake_id>/blocks/<int:block_index>/",
                self.admin_site.admin_view(self.upload_intake_block_view),
                name="resource_registry_stagedresourceintake_upload_block",
            ),
            path(
                "upload/<uuid:intake_id>/renew/",
                self.admin_site.admin_view(self.renew_upload_authorization_view),
                name="resource_registry_stagedresourceintake_upload_renew",
            ),
            path(
                "upload/<uuid:intake_id>/complete/",
                self.admin_site.admin_view(self.complete_upload_intake_view),
                name="resource_registry_stagedresourceintake_upload_complete",
            ),
            path(
                "upload/<uuid:intake_id>/cancel/",
                self.admin_site.admin_view(self.cancel_upload_intake_view),
                name="resource_registry_stagedresourceintake_upload_cancel",
            ),
            path(
                "<path:object_id>/publish/",
                self.admin_site.admin_view(self.publish_destinations_view),
                name="resource_registry_stagedresourceintake_publish",
            ),
            path(
                "<path:object_id>/remove-destinations/",
                self.admin_site.admin_view(self.remove_destinations_view),
                name="resource_registry_stagedresourceintake_remove_destinations",
            ),
            *super().get_urls(),
        ]

    def upload_resource_view(self, request: HttpRequest) -> HttpResponse:
        """Render the resumable server-upload interface."""
        self._require_intake_operator(request)
        placeholder = UUID("00000000-0000-0000-0000-000000000000")
        return TemplateResponse(
            request,
            "admin/resource_registry/stagedresourceintake/upload.html",
            {
                **self.admin_site.each_context(request),
                "create_url": reverse("admin:resource_registry_stagedresourceintake_upload_create"),
                "status_url_pattern": reverse(
                    "admin:resource_registry_stagedresourceintake_upload_status",
                    args=(placeholder,),
                ),
                "renew_url_pattern": reverse(
                    "admin:resource_registry_stagedresourceintake_upload_renew",
                    args=(placeholder,),
                ),
                "complete_url_pattern": reverse(
                    "admin:resource_registry_stagedresourceintake_upload_complete",
                    args=(placeholder,),
                ),
                "cancel_url_pattern": reverse(
                    "admin:resource_registry_stagedresourceintake_upload_cancel",
                    args=(placeholder,),
                ),
            },
        )

    def create_upload_intake_view(self, request: HttpRequest) -> JsonResponse:
        """Create one Admin-authorized intake and return a short-lived upload descriptor."""
        self._require_post_intake_operator(request)
        form = CreateStagedResourceIntakeForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"code": "INTAKE_INVALID_REQUEST"}, status=400)
        try:
            result = get_staged_resource_intake_service().create_intake(
                user=request.user,
                filename=str(form.cleaned_data["filename"]),
                content_type=str(form.cleaned_data["content_type"]),
                size_bytes=int(form.cleaned_data["size_bytes"]),
                checksum_algorithm=None,
                checksum=None,
                idempotency_key=str(form.cleaned_data["idempotency_key"]),
                issue_direct_authorization=False,
            )
        except UploadApplicationError as error:
            return self._upload_error_response(error)
        return JsonResponse(
            self._upload_authorization_payload(result.intake),
            status=200 if result.idempotent_replay else 201,
        )

    def upload_intake_status_view(self, request: HttpRequest, intake_id: UUID) -> JsonResponse:
        """Return an operator-visible, sanitized intake status."""
        self._require_intake_operator(request)
        try:
            intake = self._get_operator_intake(intake_id=intake_id)
            snapshot = get_staged_resource_intake_service().get_intake(
                user=intake.submitted_by, intake_id=intake_id
            )
        except UploadApplicationError as error:
            return self._upload_error_response(error)
        return JsonResponse(self._intake_snapshot_payload(snapshot))

    def upload_intake_block_view(
        self, request: HttpRequest, intake_id: UUID, block_index: int
    ) -> JsonResponse:
        """Forward one bounded block after verifying global intake-operator permission.

        Args:
            request: CSRF-protected binary PUT from an authenticated operator.
            intake_id: Existing intake identifier.
            block_index: Zero-based block position.

        Returns:
            Acknowledgment or a sanitized application error.

        Raises:
            PermissionDenied: If the caller is not an intake operator.
        """
        self._require_intake_operator(request)
        if request.method != "PUT":
            return JsonResponse({"code": "INTAKE_METHOD_NOT_ALLOWED"}, status=405)
        if request.content_type != "application/octet-stream":
            return JsonResponse({"code": "INTAKE_INVALID_CONTENT_TYPE"}, status=415)
        try:
            intake = self._get_operator_intake(intake_id=intake_id)
            get_staged_resource_intake_service().upload_backend_block(
                user=intake.submitted_by,
                intake_id=intake_id,
                block_index=block_index,
                read_chunk=request.read,
            )
        except UploadApplicationError as error:
            return self._upload_error_response(error)
        return JsonResponse({"block_index": block_index})

    def renew_upload_authorization_view(
        self, request: HttpRequest, intake_id: UUID
    ) -> JsonResponse:
        """Resume a pending server upload without issuing storage credentials."""
        self._require_post_intake_operator(request)
        try:
            intake = self._get_operator_intake(intake_id=intake_id)
            result = get_staged_resource_intake_service().get_backend_upload(
                user=intake.submitted_by,
                intake_id=intake_id,
            )
        except UploadApplicationError as error:
            return self._upload_error_response(error)
        return JsonResponse(self._upload_authorization_payload(result))

    def complete_upload_intake_view(self, request: HttpRequest, intake_id: UUID) -> JsonResponse:
        """Commit server-uploaded blocks, verify the object, and queue inspection."""
        self._require_post_intake_operator(request)
        try:
            intake = self._get_operator_intake(intake_id=intake_id)
            result = get_staged_resource_intake_service().complete_intake(
                user=intake.submitted_by,
                intake_id=intake_id,
                expected_etag=request.POST.get("etag") or None,
                commit_backend_blocks=True,
            )
        except UploadApplicationError as error:
            return self._upload_error_response(error)
        return JsonResponse(self._intake_snapshot_payload(result))

    def cancel_upload_intake_view(self, request: HttpRequest, intake_id: UUID) -> JsonResponse:
        """Cancel an Admin-visible pending upload and request staging cleanup."""
        self._require_post_intake_operator(request)
        try:
            intake = self._get_operator_intake(intake_id=intake_id)
            result = get_staged_resource_intake_service().cancel_intake(
                user=intake.submitted_by, intake_id=intake_id
            )
        except UploadApplicationError as error:
            return self._upload_error_response(error)
        return JsonResponse(self._intake_snapshot_payload(result))

    def _get_operator_intake(self, *, intake_id: UUID) -> StagedResourceIntake:
        """Fetch one intake after the global operator permission has been checked."""
        intake = (
            StagedResourceIntake.objects.filter(pk=intake_id).select_related("submitted_by").first()
        )
        if intake is None:
            raise UploadApplicationError("INTAKE_NOT_FOUND", 404)
        return intake

    @staticmethod
    def _intake_snapshot_payload(intake: IntakeSnapshot) -> dict[str, object]:
        """Serialize a sanitized service snapshot without storage credentials."""
        return {
            "intake_id": str(intake.intake_id),
            "status": intake.status,
            "filename": intake.filename,
            "file_format": intake.file_format,
            "declared_size_bytes": intake.declared_size_bytes,
            "actual_size_bytes": intake.actual_size_bytes,
            "checksum_verification_status": intake.checksum_verification_status,
            "inspection": intake.inspection,
            "expires_at": intake.expires_at,
            "failure_code": intake.failure_code,
        }

    def _upload_authorization_payload(self, intake: IntakeSnapshot) -> dict[str, object]:
        """Add authenticated application upload instructions without a SAS."""
        payload = self._intake_snapshot_payload(intake)
        if intake.status == StagedResourceIntakeStatus.PENDING_UPLOAD:
            payload["upload"] = build_backend_upload_descriptor(
                reverse(
                    "admin:resource_registry_stagedresourceintake_upload_block",
                    args=(intake.intake_id, 0),
                )
            )
        return payload

    @staticmethod
    def _upload_error_response(error: UploadApplicationError) -> JsonResponse:
        """Return one stable, provider-safe error response to Admin JavaScript."""
        return JsonResponse({"code": error.code, "message": str(error)}, status=error.http_status)

    def _require_intake_operator(self, request: HttpRequest) -> None:
        """Reject Admin upload access unless the user has global intake authority."""
        if not self._can_manage_staged_resource_intakes(request):
            raise PermissionDenied

    def _require_post_intake_operator(self, request: HttpRequest) -> None:
        """Require a CSRF-protected POST from an authorized intake operator."""
        self._require_intake_operator(request)
        if request.method != "POST":
            raise PermissionDenied

    def publish_destinations_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Publish one intake to independently selected destination namespaces."""
        if not self._can_manage_staged_resource_intakes(request):
            raise PermissionDenied
        intake = self.get_object(request, object_id)
        if intake is None:
            raise PermissionDenied
        if not self._is_intake_eligible_for_destination_publication(intake):
            return HttpResponse("INTAKE_NOT_READY_FOR_PUBLICATION", status=409)
        choices = self._destination_choices(intake)
        if request.method == "POST":
            form = PublishDestinationsForm(request.POST, choices=choices)
            if form.is_valid():
                service = get_staged_resource_intake_service()
                for namespace_id in form.cleaned_data["namespace_ids"]:
                    try:
                        result = service.publish_intake_to_namespace(
                            user=request.user,
                            intake_id=intake.id,
                            namespace_id=UUID(namespace_id),
                        )
                    except UploadApplicationError as error:
                        self.message_user(
                            request,
                            f"{self._namespace_label(UUID(namespace_id))}: {error.code}",
                            level=messages.ERROR,
                        )
                    else:
                        outcome = (
                            "registration was already submitted; Dataset processing status is shown below"
                            if result.idempotent_replay
                            else "file was published to the destination; Dataset processing is pending"
                        )
                        self.message_user(
                            request,
                            f"{self._namespace_label(UUID(namespace_id))}: {outcome}.",
                            level=messages.SUCCESS,
                        )
                return HttpResponseRedirect(
                    reverse(
                        "admin:resource_registry_stagedresourceintake_change", args=(intake.pk,)
                    )
                )
        else:
            form = PublishDestinationsForm(choices=choices)
        return TemplateResponse(
            request,
            "admin/resource_registry/stagedresourceintake/publish.html",
            {
                **self.admin_site.each_context(request),
                "title": "Publish staged intake",
                "intake": intake,
                "form": form,
            },
        )

    def remove_destinations_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Remove selected active destination assignments without deleting durable data."""
        if not self._can_remove_destinations(request):
            raise PermissionDenied
        intake = self.get_object(request, object_id)
        if intake is None:
            raise PermissionDenied
        allowed_publications = get_removable_staged_resource_publications(
            request.user, _get_request_organization(request)
        )
        choices = self._removal_choices(intake=intake, allowed_publications=allowed_publications)
        if request.method == "POST":
            form = RemoveDestinationsForm(request.POST, choices=choices)
            if form.is_valid():
                removal_service = DestinationAssignmentRemovalService()
                for publication_id in form.cleaned_data["publication_ids"]:
                    try:
                        result = removal_service.request_detachment(
                            user=request.user,
                            publication_id=UUID(publication_id),
                            reason=form.cleaned_data["reason"],
                            organization=_get_request_organization(request),
                        )
                    except UploadApplicationError as error:
                        self.message_user(
                            request,
                            f"{self._publication_label(UUID(publication_id))}: {error.code}",
                            level=messages.ERROR,
                        )
                    else:
                        outcome = "pending worker cancellation" if result.is_pending else "detached"
                        self.message_user(
                            request,
                            f"{self._publication_label(UUID(publication_id))}: {outcome}.",
                            level=messages.SUCCESS,
                        )
                if not can_remove_global_destination_assignments(request.user):
                    return HttpResponseRedirect(
                        reverse("admin:resource_registry_stagedresourceintake_changelist")
                    )
                return HttpResponseRedirect(
                    reverse(
                        "admin:resource_registry_stagedresourceintake_change", args=(intake.pk,)
                    )
                )
        else:
            form = RemoveDestinationsForm(choices=choices)
        return TemplateResponse(
            request,
            "admin/resource_registry/stagedresourceintake/remove_destinations.html",
            {
                **self.admin_site.each_context(request),
                "title": "Remove destination assignments",
                "intake": intake,
                "form": form,
            },
        )

    def _can_manage_staged_resource_intakes(self, request: HttpRequest) -> bool:
        """Determine whether a request user can manage all staged intake destinations.

        Args:
            request: Incoming Django Admin request.

        Returns:
            True for active staff superusers and active staff users with the global permission.
        """
        return can_manage_staged_resource_intakes(request.user)

    def _can_remove_destinations(
        self,
        request: HttpRequest,
        *,
        intake: StagedResourceIntake | None = None,
    ) -> bool:
        """Check global or current-host scoped authority for destination removal.

        Args:
            request: Incoming Django Admin request.
            intake: Optional intake whose publication must be removable.

        Returns:
            True when removal is allowed for the request and optional intake.
        """
        if can_remove_global_destination_assignments(request.user):
            return True
        organization = _get_request_organization(request)
        if not can_remove_scoped_destination_assignments(request.user, organization):
            return False
        if intake is None:
            return True
        return (
            get_removable_staged_resource_publications(request.user, organization)
            .filter(
                intake=intake,
                assignment_status=StagedResourcePublicationStatus.ACTIVE,
            )
            .exists()
        )

    @staticmethod
    def _is_intake_eligible_for_destination_publication(intake: StagedResourceIntake) -> bool:
        """Return whether an intake can receive an initial or additional destination.

        Args:
            intake: Intake whose lifecycle state determines publication eligibility.

        Returns:
            True only when the intake is ready to publish or already published.
        """
        return intake.status in {
            StagedResourceIntakeStatus.READY_FOR_PUBLICATION,
            StagedResourceIntakeStatus.PUBLISHED,
        }

    @staticmethod
    def _destination_choices(
        intake: StagedResourceIntake,
    ) -> list[tuple[str, list[tuple[str, str]]]]:
        """Build eligible destinations grouped by Organization ownership."""
        assigned_namespace_ids = StagedResourcePublication.objects.filter(
            intake=intake,
            assignment_status__in=[
                StagedResourcePublicationStatus.ACTIVE,
                StagedResourcePublicationStatus.DETACH_REQUESTED,
            ],
        ).values("registry_namespace_id")
        namespaces = (
            RegistryNamespace.objects.select_related("organization", "workspace__organization")
            .filter(
                Q(organization__is_active=True, organization__resource_registry_enabled=True)
                | Q(
                    workspace__is_active=True,
                    workspace__resource_registry_enabled=True,
                    workspace__organization__is_active=True,
                    workspace__organization__resource_registry_enabled=True,
                )
            )
            .exclude(pk__in=assigned_namespace_ids)
            .distinct()
            .order_by("organization__name", "workspace__organization__name", "workspace__name")
        )
        grouped: dict[str, list[tuple[str, str]]] = {}
        for namespace in namespaces:
            if namespace.organization_id is not None:
                label = f"{namespace.organization.name} — Organization"
                group = f"{namespace.organization.name}"
            else:
                workspace = namespace.workspace
                if workspace is None:
                    continue
                label = f"{workspace.organization.name} — Workspace: {workspace.name}"
                group = workspace.organization.name
            grouped.setdefault(group, []).append((str(namespace.pk), label))
        return list(grouped.items())

    @staticmethod
    def _removal_choices(
        intake: StagedResourceIntake,
        allowed_publications: QuerySet[StagedResourcePublication] | None = None,
    ) -> list[tuple[str, str]]:
        """Return active destinations that can be logically detached.

        Args:
            intake: Intake whose destination assignments are listed.
            allowed_publications: Optional authorization-filtered publication set.

        Returns:
            Publication identifiers and labels for eligible assignments.
        """
        publications = (
            intake.publications.select_related(
                "organization",
                "workspace",
                "registration_submission__dataset_registration_job",
                "registration_submission__workbook_ingestion_job",
            )
            .annotate(
                active_dataset_count=Count(
                    "dataset_assignments__dataset_id",
                    filter=Q(dataset_assignments__detached_at__isnull=True),
                    distinct=True,
                ),
            )
            .filter(assignment_status=StagedResourcePublicationStatus.ACTIVE)
        )
        if allowed_publications is not None:
            publications = publications.filter(pk__in=allowed_publications)
        publications = publications.order_by(
            "organization__name", "workspace__name", "publication_attempt"
        )
        choices: list[tuple[str, str]] = []
        for publication in publications:
            destination = (
                f"{publication.organization.name} — Organization"
                if publication.workspace_id is None
                else f"{publication.organization.name} — Workspace: {publication.workspace.name}"
            )
            job_status = StagedResourcePublicationInline._processing_status_for_publication(
                publication
            )
            label = (
                f"{destination} (attempt {publication.publication_attempt}; {job_status}; "
                f"{publication.active_dataset_count} Dataset(s))"
            )
            choices.append(
                (
                    str(publication.id),
                    label,
                )
            )
        return choices

    @staticmethod
    def _publication_label(publication_id: UUID) -> str:
        """Render a stable destination label for removal result messages."""
        publication = StagedResourcePublication.objects.select_related(
            "organization", "workspace"
        ).get(pk=publication_id)
        if publication.workspace_id is None:
            return f"{publication.organization.name} — Organization"
        return f"{publication.organization.name} — Workspace: {publication.workspace.name}"

    @staticmethod
    def _namespace_label(namespace_id: UUID) -> str:
        """Render a stable destination label for Admin result messages."""
        namespace = RegistryNamespace.objects.select_related(
            "organization", "workspace__organization"
        ).get(pk=namespace_id)
        if namespace.organization_id is not None:
            return f"{namespace.organization.name} — Organization"
        return f"{namespace.workspace.organization.name} — Workspace: {namespace.workspace.name}"
