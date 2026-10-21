"""Verify upload dataset naming without opening a database connection."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from resource_registry.models import Dataset, DatasetRegistrationJob
from resource_registry.services.csv_upload_materialization_service import MaterializedCsvUpload
from resource_registry.services.dataset_registration_job_execution_service import (
    DatasetRegistrationJobExecutionService,
)


@pytest.mark.parametrize("extension", ["json", "jsonl", "ndjson", "csv", "xlsx", "parquet"])
def test_uploaded_dataset_identity_includes_file_extension(extension: str) -> None:
    """Keep same-stem uploads separate across supported file formats."""
    source = cast(
        MaterializedCsvUpload,
        SimpleNamespace(
            original_filename=f"testing_sales_10000.{extension}",
            tenant_id=uuid4(),
            local_path=f"/tmp/source.{extension}",
            storage_provider="local",
            content_type="application/octet-stream",
        ),
    )
    submission = SimpleNamespace(
        organization_id=1,
        workspace_id=2,
        visibility_scope="WORKSPACE",
    )
    job = cast(
        DatasetRegistrationJob,
        SimpleNamespace(registration_submission=submission, created_by=None),
    )
    with patch.object(Dataset.objects, "filter") as filter_datasets:
        filter_datasets.return_value.first.return_value = None
        command = DatasetRegistrationJobExecutionService._build_registration_command(
            job=job,
            source=source,
        )

    assert command.slug == f"testing_sales_10000-{extension}"
    assert command.name == f"testing_sales_10000.{extension}"


@pytest.mark.parametrize(
    ("has_other_format", "expected_slug"),
    [
        (False, "testing_sales_10000"),
        (True, "testing_sales_10000-jsonl"),
    ],
)
def test_legacy_dataset_reuse_requires_matching_extension(
    has_other_format: bool,
    expected_slug: str,
) -> None:
    """Reuse homogeneous legacy datasets and separate those with other source formats."""
    source = cast(
        MaterializedCsvUpload,
        SimpleNamespace(
            original_filename="testing_sales_10000.jsonl",
            tenant_id=uuid4(),
            local_path="/tmp/source.jsonl",
            storage_provider="local",
            content_type="application/x-ndjson",
        ),
    )
    submission = SimpleNamespace(
        organization_id=1,
        workspace_id=2,
        visibility_scope="WORKSPACE",
    )
    job = cast(
        DatasetRegistrationJob,
        SimpleNamespace(registration_submission=submission, created_by=None),
    )
    legacy_dataset = SimpleNamespace(versions=MagicMock())
    legacy_dataset.versions.exists.return_value = True
    legacy_dataset.versions.exclude.return_value.exists.return_value = has_other_format
    with patch.object(Dataset.objects, "filter") as filter_datasets:
        filter_datasets.return_value.first.return_value = legacy_dataset
        command = DatasetRegistrationJobExecutionService._build_registration_command(
            job=job,
            source=source,
        )

    assert command.slug == expected_slug
    legacy_dataset.versions.exclude.assert_called_once_with(source_uri__iendswith=".jsonl")
