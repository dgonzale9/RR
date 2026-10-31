"""Freeze default dataset versions before fingerprinting asynchronous work."""

from dataclasses import replace

from resource_registry.authorization.submission_authority import SubmissionAuthorityEnvelope
from resource_registry.models import Dataset
from resource_registry.read.version_read_service import DatasetVersionReadService


def pin_job_dataset_version(
    *,
    tenant_id: str,
    dataset_id: str,
    version_id: str | None,
    submission_authority: SubmissionAuthorityEnvelope | None,
) -> tuple[str, SubmissionAuthorityEnvelope | None]:
    """Resolve the current version and bind it to existing dataset-scoped authority.

    Args:
        tenant_id: Previously authorized namespace.
        dataset_id: Previously authorized dataset.
        version_id: Explicit immutable reference, or None to select the current version.
        submission_authority: Trusted server-created authority for the dataset.

    Returns:
        Fixed version ID and an authority envelope carrying that same reference.

    Raises:
        ValueError: If the supplied authority does not cover this dataset or version.
        DatasetVersionNotFoundError: If no current version is available in this scope.
    """

    if Dataset.objects.filter(pk=dataset_id, purge_started_at__isnull=False).exists():
        raise ValueError("Dataset data is being deleted.")
    if version_id:
        return version_id, submission_authority
    selected = (
        DatasetVersionReadService()
        .get_latest_version(tenant_id=tenant_id, dataset_id=dataset_id)
        .id
    )
    if submission_authority is not None:
        scope = dict(submission_authority.resource_scope)
        if submission_authority.namespace_id != tenant_id or dataset_id not in scope.get(
            "dataset_ids", ()
        ):
            raise ValueError("Submission authority does not cover the selected dataset.")
        versions = scope.get("dataset_version_ids", ())
        if versions and selected not in versions:
            raise ValueError("Submission authority does not cover the selected version.")
        scope["dataset_version_ids"] = (selected,)
        submission_authority = replace(
            submission_authority, resource_scope=tuple(sorted(scope.items()))
        )
    return selected, submission_authority
