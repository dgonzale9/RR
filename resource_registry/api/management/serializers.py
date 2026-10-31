"""Validate maintenance requests without accepting storage coordinates."""

from collections.abc import Mapping
from typing import cast

from rest_framework import serializers


class StrictManagementSerializer(serializers.Serializer[object]):
    """Reject undeclared fields with a valid DRF field-error mapping."""

    def to_internal_value(self, data: object) -> dict[str, object]:
        """Validate the object shape before field conversion."""
        if not isinstance(data, Mapping) or set(data) - set(self.fields):
            raise serializers.ValidationError(
                {"non_field_errors": ["Unknown or invalid request fields."]}
            )
        return cast(dict[str, object], super().to_internal_value(data))


class RevisionSerializer(StrictManagementSerializer):
    """Require the resource revision reviewed by the caller."""

    expected_revision = serializers.IntegerField(min_value=0)


class DatasetMetadataSerializer(RevisionSerializer):
    """Accept only editable metadata."""

    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    owner_team = serializers.CharField(
        max_length=255, required=False, allow_blank=True, allow_null=True
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100), max_length=100, required=False
    )
    responsible_user_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)


class LifecycleSerializer(RevisionSerializer):
    """Require a reason for manager lifecycle transitions."""

    reason = serializers.CharField(max_length=500)


class RestoreSerializer(LifecycleSerializer):
    """Select an immutable historical version to restore."""

    version_id = serializers.UUIDField()


class SourceReferenceSerializer(StrictManagementSerializer):
    """Carry opaque source identity without accepting storage URLs or authority."""

    provider = serializers.RegexField(regex=r"^[a-z][a-z0-9_-]*$", max_length=64)
    external_id = serializers.CharField(max_length=512)
    version = serializers.CharField(max_length=512)


class CreateChangeSerializer(StrictManagementSerializer):
    """Select a verified source and explicit destination targets."""

    namespace_id = serializers.UUIDField()
    intake_id = serializers.UUIDField(required=False)
    upload_id = serializers.UUIDField(required=False)
    specification = serializers.DictField()
    source_reference = SourceReferenceSerializer(required=False)
    expected_source_revision = serializers.IntegerField(min_value=0, required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Require source identity and its concurrency revision together.

        Args:
            attrs: Deserialized proposal fields.

        Returns:
            The validated fields without changing their provenance.

        Raises:
            serializers.ValidationError: If only one external-source field is provided.
        """
        if ("source_reference" in attrs) != ("expected_source_revision" in attrs):
            raise serializers.ValidationError(
                "Source reference and revision must be provided together."
            )
        return attrs


class ReviseChangeSerializer(RevisionSerializer):
    """Replace and recalculate the reviewed update specification."""

    specification = serializers.DictField()


class SourceOutputSerializer(serializers.Serializer[object]):
    """Describe exact dataset identities returned to an integration."""

    dataset_id = serializers.UUIDField()
    dataset_revision = serializers.IntegerField()
    dataset_version_id = serializers.UUIDField(allow_null=True, required=False)
    sheet_name = serializers.CharField(allow_null=True, required=False)
    outcome = serializers.ChoiceField(
        choices=(
            "created",
            "updated",
            "unchanged",
            "restored",
            "archived",
            "retained",
            "already_archived",
        ),
        required=False,
    )
    name = serializers.CharField(required=False)
    status = serializers.CharField(required=False)
    archive_reason = serializers.CharField(required=False, allow_blank=True)
    data_available = serializers.BooleanField(required=False)


class SourceControlSerializer(serializers.Serializer[object]):
    """Document source control metadata without exposing file contents."""

    id = serializers.UUIDField()
    namespace_id = serializers.UUIDField()
    provider = serializers.CharField()
    external_id = serializers.CharField()
    revision = serializers.IntegerField()
    status = serializers.ChoiceField(choices=("RESERVED", "ACTIVE", "INACTIVE"))
    last_applied_version = serializers.CharField(allow_blank=True)
    workbook_id = serializers.UUIDField(allow_null=True)
    workbook_revision = serializers.IntegerField(allow_null=True)
    items = SourceOutputSerializer(many=True)


class SourceDeactivationResultSerializer(serializers.Serializer[object]):
    """Document the immutable result of an idempotent source disappearance."""

    source_id = serializers.UUIDField()
    source_revision = serializers.IntegerField()
    status = serializers.CharField()
    items = SourceOutputSerializer(many=True)


class ChangeResultSerializer(serializers.Serializer[object]):
    """Describe published identities while retaining existing result fields."""

    dataset_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    workbook_id = serializers.UUIDField(allow_null=True, required=False)
    workbook_revision = serializers.IntegerField(allow_null=True, required=False)
    source_id = serializers.UUIDField(required=False)
    source_revision = serializers.IntegerField(required=False)
    source_reference = SourceReferenceSerializer(required=False)
    items = SourceOutputSerializer(many=True, required=False)
    versions = serializers.ListField(child=serializers.DictField(), required=False)


class DatasetChangeResponseSerializer(serializers.Serializer[object]):
    """Document durable proposal state, provenance, differences, and outcomes."""

    id = serializers.UUIDField()
    namespace_id = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=(
            "QUEUED",
            "PROCESSING",
            "READY",
            "NEEDS_MANAGER",
            "PUBLISHED",
            "NO_CHANGES",
            "FAILED",
            "CANCELLED",
        )
    )
    revision = serializers.IntegerField()
    submitted_by_id = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    specification = serializers.DictField()
    source_id = serializers.UUIDField(allow_null=True)
    source_revision = serializers.IntegerField(allow_null=True)
    source_reference = SourceReferenceSerializer(allow_null=True)
    error_code = serializers.CharField(allow_blank=True)
    error_message = serializers.CharField(allow_blank=True)
    result = ChangeResultSerializer()
    available_sheets = serializers.ListField(child=serializers.CharField())
    suggested_sheets = serializers.ListField(child=serializers.DictField())
    items = serializers.ListField(child=serializers.DictField())
