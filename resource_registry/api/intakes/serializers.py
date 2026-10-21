"""Strict serializers for private staged workbook intake endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from rest_framework import serializers


class StrictIntakeSerializer(serializers.Serializer[object]):
    """Reject fields outside the explicit intake request contract."""

    def to_internal_value(self, data: object) -> dict[str, object]:
        """Validate a JSON object with no undeclared fields."""
        if not isinstance(data, Mapping):
            raise serializers.ValidationError("Expected a JSON object.")
        unknown_fields = sorted(str(key) for key in set(data) - set(self.fields))
        if unknown_fields:
            raise serializers.ValidationError("Unknown intake request fields are not allowed.")
        return cast(dict[str, object], super().to_internal_value(data))


class CreateIntakeSerializer(StrictIntakeSerializer):
    """Validate metadata for a direct private intake upload."""

    filename = serializers.CharField(max_length=255, trim_whitespace=True)
    content_type = serializers.CharField(max_length=255, trim_whitespace=True)
    size_bytes = serializers.IntegerField(min_value=1)
    checksum_algorithm = serializers.ChoiceField(
        choices=("sha256",), required=False, allow_null=True
    )
    checksum = serializers.RegexField(regex=r"^[0-9a-fA-F]{64}$", required=False, allow_null=True)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Require paired checksum algorithm and digest."""
        if (attrs.get("checksum_algorithm") is None) != (attrs.get("checksum") is None):
            raise serializers.ValidationError("Checksum algorithm and checksum must be paired.")
        return attrs


class CompleteIntakeSerializer(StrictIntakeSerializer):
    """Validate optional client evidence for completion."""

    etag = serializers.CharField(max_length=255, required=False, allow_null=True)


class PublishIntakeSerializer(StrictIntakeSerializer):
    """Validate the destination selector without accepting organization identity."""

    visibility_scope = serializers.ChoiceField(choices=("WORKSPACE", "ORGANIZATION"))
    workspace_id = serializers.IntegerField(min_value=1, required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Enforce the visibility-specific Workspace selector contract."""
        scope = str(attrs["visibility_scope"])
        workspace_id = attrs.get("workspace_id")
        if scope == "ORGANIZATION" and workspace_id is not None:
            raise serializers.ValidationError({"workspace_id": "This field is not allowed."})
        return attrs
