from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from resource_registry.security.exceptions import (
    ArtifactGuardrailViolation,
    ArtifactTooLargeError,
    UnsafeArtifactUriError,
    UnsupportedArtifactFormatError,
    UnsupportedStorageProviderError,
)
from resource_registry.security.policies import ArtifactMaterializationPolicy


class ArtifactGuardrailValidator:
    """Fail-closed guardrail validation before artifact materialization."""

    def validate_uri(
        self,
        uri: str,
        *,
        policy: ArtifactMaterializationPolicy,
        policy_source: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        normalized_uri = uri.strip()
        if not normalized_uri:
            raise UnsafeArtifactUriError(
                "Artifact URI cannot be empty.",
                violated_rule="uri_not_empty",
                expected_value="non-empty URI",
                actual_value=uri,
                policy_source=policy_source,
                policy_revision=policy_revision,
            )
        if "sig=" in normalized_uri.lower():
            raise UnsafeArtifactUriError(
                "SAS-bearing URI is unsafe for artifact materialization.",
                violated_rule="forbid_sas_uri",
                expected_value="URI without sig= query parameter",
                actual_value=normalized_uri,
                policy_source=policy_source,
                policy_revision=policy_revision,
            )

        parsed = urlparse(normalized_uri)
        if parsed.scheme in {"http", "https", "azure", "s3", "gs"}:
            if not policy.allow_remote_uris:
                raise UnsafeArtifactUriError(
                    "Remote artifact URIs are disabled by policy.",
                    violated_rule="allow_remote_uris",
                    expected_value=True,
                    actual_value=policy.allow_remote_uris,
                    policy_source=policy_source,
                    policy_revision=policy_revision,
                )
            return

        if normalized_uri.startswith("local://"):
            return

        candidate = Path(normalized_uri)
        if candidate.is_absolute() or normalized_uri.startswith("."):
            if not policy.allow_local_files:
                raise UnsafeArtifactUriError(
                    "Raw local filesystem paths are disabled by policy.",
                    violated_rule="allow_local_files",
                    expected_value=True,
                    actual_value=policy.allow_local_files,
                    policy_source=policy_source,
                    policy_revision=policy_revision,
                )
            return

        if "://" in normalized_uri:
            if not policy.allow_remote_uris:
                raise UnsafeArtifactUriError(
                    "Remote artifact URIs are disabled by policy.",
                    violated_rule="allow_remote_uris",
                    expected_value=True,
                    actual_value=policy.allow_remote_uris,
                    policy_source=policy_source,
                    policy_revision=policy_revision,
                )
            return
        if not policy.allow_local_files:
            raise UnsafeArtifactUriError(
                "Raw local filesystem paths are disabled by policy.",
                violated_rule="allow_local_files",
                expected_value=True,
                actual_value=policy.allow_local_files,
                policy_source=policy_source,
                policy_revision=policy_revision,
            )

    def validate_format(
        self,
        artifact_format: str,
        *,
        policy: ArtifactMaterializationPolicy,
        policy_source: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        normalized = artifact_format.strip().lower()
        if not normalized or normalized not in policy.allowed_formats:
            raise UnsupportedArtifactFormatError(
                f"Artifact format '{artifact_format}' is not allowed.",
                violated_rule="allowed_formats",
                expected_value=sorted(policy.allowed_formats),
                actual_value=normalized,
                policy_source=policy_source,
                policy_revision=policy_revision,
            )

    def validate_size(
        self,
        *,
        size_bytes: int | None,
        uri: str,
        policy: ArtifactMaterializationPolicy,
        policy_source: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        resolved_size = size_bytes
        if resolved_size is None and _is_local_file_path(uri):
            local_path = Path(uri)
            if local_path.exists() and local_path.is_file():
                resolved_size = local_path.stat().st_size

        if resolved_size is None:
            return
        if resolved_size < 0:
            raise ArtifactGuardrailViolation(
                "Artifact size cannot be negative.",
                violated_rule="max_size_bytes",
                expected_value=f"0 < size <= {policy.max_size_bytes}",
                actual_value=resolved_size,
                policy_source=policy_source,
                policy_revision=policy_revision,
            )
        if resolved_size > policy.max_size_bytes:
            raise ArtifactTooLargeError(
                f"Artifact size {resolved_size} exceeds max {policy.max_size_bytes}.",
                violated_rule="max_size_bytes",
                expected_value=policy.max_size_bytes,
                actual_value=resolved_size,
                policy_source=policy_source,
                policy_revision=policy_revision,
            )

    def validate_provider(
        self,
        *,
        storage_provider: str | None,
        uri: str,
        policy: ArtifactMaterializationPolicy,
        policy_source: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        normalized = (storage_provider or "").strip().lower()
        if not normalized:
            normalized = _infer_provider_from_uri(uri)
        if normalized and normalized not in policy.allowed_storage_providers:
            raise UnsupportedStorageProviderError(
                f"Storage provider '{normalized}' is not allowed.",
                violated_rule="allowed_storage_providers",
                expected_value=sorted(policy.allowed_storage_providers),
                actual_value=normalized,
                policy_source=policy_source,
                policy_revision=policy_revision,
            )

    def validate_metadata(
        self,
        *,
        metadata: dict[str, object] | None,
        require_checksum: bool,
        provided_checksum: str | None,
        policy_source: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        _ = metadata
        if require_checksum and not provided_checksum:
            raise ArtifactGuardrailViolation(
                "Checksum is required by materialization policy.",
                violated_rule="require_checksum",
                expected_value=True,
                actual_value=False,
                policy_source=policy_source,
                policy_revision=policy_revision,
            )

    def validate_before_materialization(  # noqa: PLR0913
        self,
        *,
        uri: str,
        artifact_format: str,
        policy: ArtifactMaterializationPolicy,
        size_bytes: int | None = None,
        storage_provider: str | None = None,
        metadata: dict[str, object] | None = None,
        checksum: str | None = None,
        policy_source: str | None = None,
        policy_revision: int | None = None,
    ) -> None:
        self.validate_uri(
            uri,
            policy=policy,
            policy_source=policy_source,
            policy_revision=policy_revision,
        )
        self.validate_format(
            artifact_format,
            policy=policy,
            policy_source=policy_source,
            policy_revision=policy_revision,
        )
        self.validate_provider(
            storage_provider=storage_provider,
            uri=uri,
            policy=policy,
            policy_source=policy_source,
            policy_revision=policy_revision,
        )
        self.validate_metadata(
            metadata=metadata,
            require_checksum=policy.require_checksum,
            provided_checksum=checksum,
            policy_source=policy_source,
            policy_revision=policy_revision,
        )
        self.validate_size(
            size_bytes=size_bytes,
            uri=uri,
            policy=policy,
            policy_source=policy_source,
            policy_revision=policy_revision,
        )


def _is_local_file_path(uri: str) -> bool:
    if "://" in uri:
        return False
    candidate = Path(uri)
    return candidate.is_absolute() or uri.startswith(".")


def _infer_provider_from_uri(uri: str) -> str:
    lowered = uri.lower()
    if lowered.startswith("local://") or _is_local_file_path(uri):
        return "local"
    if lowered.startswith("azure://") or ".blob.core.windows.net/" in lowered:
        return "azure_blob"
    parsed = urlparse(uri)
    return parsed.scheme.lower()
