from __future__ import annotations

from dataclasses import dataclass

from django.test import SimpleTestCase

from resource_registry.services.dataset_query_routing_policy import DatasetQueryRoutingPolicy


@dataclass(frozen=True)
class _VersionStub:
    id: str


@dataclass(frozen=True)
class _ArtifactStub:
    id: str
    format: str
    lifecycle_status: str
    artifact_role: str
    size_bytes: int | None
    metadata_json: dict[str, object]


class _VersionReadServiceStub:
    def __init__(self, version_id: str = "version-1") -> None:
        self._version_id = version_id

    def get_latest_version(self, *, tenant_id: str, dataset_id: str) -> _VersionStub:
        _ = tenant_id, dataset_id
        return _VersionStub(id=self._version_id)


class _ArtifactReadServiceStub:
    def __init__(self, artifacts: list[_ArtifactStub]) -> None:
        self._artifacts = artifacts

    def list_artifacts(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        version_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[_ArtifactStub]:
        _ = tenant_id, dataset_id, version_id, limit, offset
        return self._artifacts


class DatasetQueryRoutingPolicyTests(SimpleTestCase):
    def test_returns_sync_allowed_for_small_artifact(self) -> None:
        policy = DatasetQueryRoutingPolicy(
            version_read_service=_VersionReadServiceStub(),
            artifact_read_service=_ArtifactReadServiceStub(
                [
                    _ArtifactStub(
                        id="a1",
                        format="PARQUET",
                        lifecycle_status="ACTIVE",
                        artifact_role="SNAPSHOT",
                        size_bytes=1024,
                        metadata_json={"is_canonical": True},
                    ),
                ],
            ),
            max_sync_artifact_bytes=10_000,
        )

        decision = policy.evaluate(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            version_id=None,
            sql="SELECT country FROM dataset LIMIT 10",
        )

        self.assertEqual(decision.mode, "sync_allowed")
        self.assertEqual(decision.reason, "configured_policy")

    def test_returns_async_required_for_large_artifact(self) -> None:
        policy = DatasetQueryRoutingPolicy(
            version_read_service=_VersionReadServiceStub(),
            artifact_read_service=_ArtifactReadServiceStub(
                [
                    _ArtifactStub(
                        id="a1",
                        format="PARQUET",
                        lifecycle_status="ACTIVE",
                        artifact_role="SNAPSHOT",
                        size_bytes=50_000,
                        metadata_json={"is_canonical": True},
                    ),
                ],
            ),
            max_sync_artifact_bytes=10_000,
        )

        decision = policy.evaluate(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            version_id=None,
            sql="SELECT country FROM dataset LIMIT 10",
        )

        self.assertEqual(decision.mode, "async_required")
        self.assertEqual(decision.reason, "artifact_size")

    def test_handles_unknown_artifact_size_deterministically(self) -> None:
        policy = DatasetQueryRoutingPolicy(
            version_read_service=_VersionReadServiceStub(),
            artifact_read_service=_ArtifactReadServiceStub(
                [
                    _ArtifactStub(
                        id="a1",
                        format="PARQUET",
                        lifecycle_status="ACTIVE",
                        artifact_role="SNAPSHOT",
                        size_bytes=None,
                        metadata_json={"is_canonical": True},
                    ),
                ],
            ),
            max_sync_artifact_bytes=10_000,
        )

        decision = policy.evaluate(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            version_id=None,
            sql="SELECT country FROM dataset LIMIT 10",
        )

        self.assertEqual(decision.mode, "async_recommended")
        self.assertEqual(decision.reason, "unknown")
