from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from resource_registry.models import (
    Dataset,
    DatasetArtifact,
    DatasetProfile,
    DatasetSchema,
    DatasetVersion,
)


@dataclass(frozen=True)
class IdempotencyResolution:
    dataset: Dataset | None
    existing_version: DatasetVersion | None
    existing_schema: DatasetSchema | None
    existing_profile: DatasetProfile | None
    existing_artifact: DatasetArtifact | None
    content_hash: str


class LocalFileHashService:
    """Computes file digests by streaming local files in chunks."""

    def compute_sha256(self, path: str | Path, chunk_size: int = 64 * 1024) -> str:
        file_path = Path(path)
        digest = hashlib.sha256()
        with file_path.open("rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()


class DatasetRegistrationIdempotencyService:
    """Resolves idempotent registrations from tenant/slug/content hash."""

    def __init__(self, hash_service: LocalFileHashService | None = None) -> None:
        self._hash_service = hash_service or LocalFileHashService()

    def compute_content_hash(self, path: str | Path) -> str:
        return self._hash_service.compute_sha256(path)

    def resolve(
        self,
        *,
        tenant_id: str,
        slug: str,
        path: str | Path,
        precomputed_content_hash: str | None = None,
        only_latest_version: bool = False,
    ) -> IdempotencyResolution:
        content_hash = str(precomputed_content_hash or "").strip() or self.compute_content_hash(
            path
        )
        dataset = Dataset.objects.filter(tenant_id=tenant_id, slug=slug).first()
        if dataset is None:
            return IdempotencyResolution(
                dataset=None,
                existing_version=None,
                existing_schema=None,
                existing_profile=None,
                existing_artifact=None,
                content_hash=content_hash,
            )

        versions = dataset.versions.order_by("-version_number")
        latest_version = versions.first()
        if only_latest_version:
            existing_version = (
                latest_version
                if latest_version is not None and latest_version.content_hash == content_hash
                else None
            )
        else:
            existing_version = versions.filter(content_hash=content_hash).first()
        if existing_version is None:
            return IdempotencyResolution(
                dataset=dataset,
                existing_version=None,
                existing_schema=None,
                existing_profile=None,
                existing_artifact=None,
                content_hash=content_hash,
            )

        existing_schema = existing_version.schemas.order_by("-created_at").first()
        existing_profile = existing_version.profiles.order_by("-computed_at").first()
        existing_artifact = existing_version.artifacts.order_by("-created_at").first()
        return IdempotencyResolution(
            dataset=dataset,
            existing_version=existing_version,
            existing_schema=existing_schema,
            existing_profile=existing_profile,
            existing_artifact=existing_artifact,
            content_hash=content_hash,
        )

    def get_next_version_number(self, *, dataset: Dataset) -> int:
        latest_version = dataset.versions.order_by("-version_number").first()
        if latest_version is None:
            return 1
        return int(latest_version.version_number) + 1
