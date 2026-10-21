from __future__ import annotations

from django.test import SimpleTestCase

from resource_registry.services.async_job_idempotency import (
    build_async_job_idempotency_key,
    build_async_job_request_fingerprint,
)


class AsyncJobIdempotencyTests(SimpleTestCase):
    def test_same_payload_produces_same_fingerprint(self) -> None:
        payload = {
            "tenant_id": "tenant-1",
            "job_family": "dataset_query_job",
            "sql_text": "SELECT 1",
        }

        self.assertEqual(
            build_async_job_request_fingerprint(payload),
            build_async_job_request_fingerprint(payload),
        )

    def test_field_ordering_is_ignored(self) -> None:
        first = {"tenant_id": "tenant-1", "payload": {"a": 1, "b": 2}}
        second = {"payload": {"b": 2, "a": 1}, "tenant_id": "tenant-1"}

        self.assertEqual(
            build_async_job_request_fingerprint(first),
            build_async_job_request_fingerprint(second),
        )

    def test_excluded_runtime_fields_are_ignored(self) -> None:
        first = {"tenant_id": "tenant-1", "payload": {"slug": "sales"}}
        second = {
            "tenant_id": "tenant-1",
            "payload": {
                "slug": "sales",
                "status": "running",
                "retry_count": 2,
                "started_at": "2026-01-01T00:00:00Z",
                "metadata": {"source": "request"},
            },
        }

        self.assertEqual(
            build_async_job_request_fingerprint(first),
            build_async_job_request_fingerprint(second),
        )

    def test_tenant_isolation_changes_fingerprint(self) -> None:
        first = {"tenant_id": "tenant-1", "payload": {"slug": "sales"}}
        second = {"tenant_id": "tenant-2", "payload": {"slug": "sales"}}

        self.assertNotEqual(
            build_async_job_request_fingerprint(first),
            build_async_job_request_fingerprint(second),
        )

    def test_hash_stability_uses_sha256_hex(self) -> None:
        fingerprint = build_async_job_request_fingerprint(
            {"tenant_id": "tenant-1", "payload": {"slug": "sales"}},
        )

        self.assertEqual(len(fingerprint), 64)
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")

    def test_idempotency_key_scopes_fingerprint_by_family(self) -> None:
        fingerprint = "a" * 64

        self.assertEqual(
            build_async_job_idempotency_key(
                job_family="dataset_query_job",
                request_fingerprint=fingerprint,
            ),
            f"dataset_query_job:{fingerprint}",
        )
