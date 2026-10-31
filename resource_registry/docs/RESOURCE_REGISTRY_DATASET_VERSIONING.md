# Resource Registry dataset versioning

A Dataset is a stable identity. DatasetVersions are immutable materializations, and
`Dataset.current_version` selects the version used by readers when no version is specified.
A restoration can select an older version; the greatest version number is not necessarily current.
`revision` is a concurrency counter on a resource, not a DatasetVersion number.

## Create and replace

1. Upload and verify a supported file using an intake or upload session.
2. Create a proposal with `POST dataset-changes/` and a persisted `Idempotency-Key`.
3. For creation, specify the destination namespace and a name. For replacement, specify the
   existing `dataset_id` and its `expected_revision`.
4. Poll the proposal, review its differences and publish using the proposal's own revision.
   Compatible changes can be published by an authorized editor; sensitive changes need a manager.
5. Read the returned dataset/version IDs and current revision. A failure leaves the current
   content unchanged.

Admin provides the same workflow through **Datasets → Create from file / Update data**.
The new file replaces the complete dataset; rows are not implicitly appended.
Filename, display name and array order are not target identifiers. Renaming metadata through
`PATCH datasets/{id}/` preserves identity and content version, while advancing dataset revision.
See [request/response examples](DATASET_MANAGEMENT_API.md).

## Content outcomes

| Prepared content | Result |
| --- | --- |
| New dataset | Publication creates the dataset and first version. |
| Changed content compatible with the current schema | `READY`; publication selects a new immutable version. |
| Identical current content | `NO_CHANGES`; no duplicate version and no publication call needed. |
| Removed columns or changed column types | `NEEDS_MANAGER`; manager reviews and publishes. |
| Content matching a retained historical version | Requires manager review. Manual flows restore the existing version from the dataset; external proposals select it on approval. No duplicate historical version. |
| Parsing/storage failure or stale revision | No replacement of the current version; inspect the error and recover or recalculate. |

Do not edit READY versions directly. Manual restoration uses `restore-version/` with `version_id`,
dataset `expected_revision` and a reason. Retained artifacts must still exist. A new changed file
after restoration creates the next version number without renumbering history.

## Externally managed content

An external source is unique by namespace, provider and stable external ID. Its opaque version
is supplied by the connector; RR does not order provider versions. File renames preserve this
identity. Distinct external IDs with equal filenames remain distinct sources.

Use the same source identity, a new verified file and current source/dataset revisions to replace
content. A verified no-op records the checked external version. Historical content goes through
an external proposal and manager publication, which selects the retained version. Manual
replacement and manual `restore-version/` are blocked, even for managers; metadata editing and
manual archive retain their existing permissions. Source-backed datasets remain `FILE_UPLOAD`.
See [the external contract](EXTERNAL_SOURCE_SYNC.md).

## Excel mappings

`DatasetWorkbook` is the stable workbook identity. Specify its revision and a mapping from each
selected sheet name to an existing dataset ID/revision or to a new dataset. A renamed sheet
retains its dataset when the caller supplies that mapping. New sheets need explicit selection.
Omitted sheets are preserved unless explicitly listed for archive; removal requires a manager.

Selected updates, historical restorations and approved archives commit together. An invalid
selected sheet prevents publication of the entire proposal. The legacy ingestion result
`completed_with_skips` is not a dataset-change proposal result. External proposal `result.items`
provides exact sheet/dataset/version/revision/outcome mappings.

## Archive, disappearance and deletion

- Manual archive hides content without deleting files or history. A later import does not
  automatically undo a manual archive.
- Source deactivation withdraws that source's assignments and archives unprotected datasets
  with `SOURCE_REMOVED`. Reappearance can recover those outputs only after successful new
  publication or verified identical content.
- Independent assignment removal is not reversed by replaying an old publication.
- Permanent deletion is a separate manager-reviewed [cleanup request](RESOURCE_CLEANUP.md),
  with at least 30 days of recovery after archive. Purge start prevents restoration/recovery.

## Legacy compatibility

The older registration service derives slugs from filenames. Once the organization has enabled
Dataset Management, `legacy_updates_blocked` prevents implicit replacement of an existing slug.
Legacy initial registrations remain available, but uploading the same filename is no longer the
procedure for creating its next version. Disabling management preserves that protection.
Existing datasets are not automatically assigned external sources by name.

## Reads and verification

`GET datasets/{id}/` returns `current_version_id`; `versions/` marks `is_current`. The catalogue,
previews and readers resolve the selected current version. Asynchronous queries and export plans
pin their chosen version and retain authorization checks. Historical records without a current
pointer use the guarded compatibility resolver; ambiguous/unavailable content needs review.

Use [maintenance QA](DATASET_MANAGEMENT_QA.md) and the [Postman sequence](POSTMAN_WALKTHROUGH.md)
to verify that replacement preserves dataset ID and restoration changes the current pointer
without duplicating versions.
