"""Strict JSON serializers for the large-file upload control plane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from rest_framework import serializers


class StrictUploadSerializer(serializers.Serializer[object]):
    """Reject every field not declared by the bounded endpoint contract."""

    def to_internal_value(self, data: object) -> dict[str, object]:
        """Validate that clients did not submit authority or storage fields."""
        if not isinstance(data, Mapping):
            raise serializers.ValidationError("Expected a JSON object.")
        unknown_fields = sorted(str(key) for key in set(data) - set(self.fields))
        if unknown_fields:
            raise serializers.ValidationError("Unknown upload request fields are not allowed.")
        return cast(dict[str, object], super().to_internal_value(data))


class CreateUploadSessionSerializer(StrictUploadSerializer):
    """Validate create-session metadata without accepting bytes or storage coordinates."""

    workspace_id = serializers.IntegerField(min_value=1, required=False)
    filename = serializers.CharField(max_length=255, trim_whitespace=True)
    content_type = serializers.CharField(max_length=255, trim_whitespace=True)
    size_bytes = serializers.IntegerField(min_value=1)
    checksum_algorithm = serializers.ChoiceField(
        choices=("sha256",), required=False, allow_null=True
    )
    checksum = serializers.RegexField(regex=r"^[0-9a-fA-F]{64}$", required=False, allow_null=True)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Require checksum algorithm and value together."""
        if (attrs.get("checksum_algorithm") is None) != (attrs.get("checksum") is None):
            raise serializers.ValidationError("Checksum algorithm and checksum must be paired.")
        return attrs


class CompleteUploadSessionSerializer(StrictUploadSerializer):
    """Validate optional client evidence without accepting a path or URL."""

    etag = serializers.CharField(max_length=255, required=False, allow_null=True)


class RegistrationSubmissionSerializer(StrictUploadSerializer):
    """Reject all registration metadata because the upload owns canonical provenance."""


class UploadSessionResponseSerializer(serializers.Serializer[object]):
    """Document sanitized persisted upload state."""

    upload_id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    filename = serializers.CharField(read_only=True)
    file_format = serializers.CharField(read_only=True)
    declared_content_type = serializers.CharField(read_only=True)
    detected_content_type = serializers.CharField(read_only=True, allow_null=True)
    declared_size_bytes = serializers.IntegerField(read_only=True)
    actual_size_bytes = serializers.IntegerField(read_only=True, allow_null=True)
    checksum_verification_status = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    cancelled_at = serializers.DateTimeField(read_only=True, allow_null=True)
    failure_code = serializers.CharField(read_only=True, allow_null=True)
    ready_for_registration = serializers.BooleanField(read_only=True)
