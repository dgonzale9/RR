"""Translate persistent cleanup reason codes into non-sensitive English explanations."""

REASONS = {
    "authority_revoked": "The requesting manager no longer has access. An authorized manager must review this request.",
    "inventory_changed": "The deletion inventory changed. Review it and confirm again.",
    "dataset_not_archived": "Archive this dataset before requesting permanent deletion.",
    "recovery_period": "The minimum recovery period has not elapsed.",
    "work_in_progress": "An operation is still using this dataset.",
    "object_in_use": "An active read or upload is using this file.",
    "shared_dataset": "Another retained resource still needs this file.",
    "retained_source": "The original source is still within its retention period or needed by another operation.",
    "pending_update": "A pending dataset update still needs this file.",
    "source_in_use": "An inspection or update still needs this source.",
    "source_retention_period": "The source retention period restarts from its most recent use.",
    "published_copy_unavailable": "An independent published copy could not be verified. The original is retained.",
    "ownership_unverified": "Historical ownership is uncertain. This object will be retained until its provenance is established.",
    "identity_unverified": "The file has insufficient identity evidence and requires review.",
    "object_changed": "The stored file differs from the reviewed generation. It was retained.",
    "storage_location_changed": "The configured storage location changed. The reviewed file was retained.",
    "shared_scope_disabled": "A retaining destination has not enabled cleanup.",
    "storage_delete_failed": "Storage deletion failed. Automatic retries are bounded; a manager can retry after failure.",
    "cleanup_in_progress": "Another cleanup worker has reserved this shared file.",
    "write_in_progress": "A preparation worker has not finished writing this generation.",
    "dependent_dataset": "A registered dependent dataset still requires this version.",
    "registered_transformation": "A published transformation still refers to this dataset or version.",
    "transformation_in_progress": "An active transformation still needs this data.",
    "ingestion_in_progress": "An ingestion is still using this data.",
    "publication_in_progress": "A publication still needs this file.",
}


def describe_cleanup_reason(code: str) -> str:
    """Return an English explanation without revealing foreign destinations or storage paths."""
    return REASONS.get(code, code.replace("_", " ").capitalize())
