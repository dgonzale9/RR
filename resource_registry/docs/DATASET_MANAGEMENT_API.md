# Maintenance API: create, replace, restore and archive

This guide covers datasets without an external source. For file synchronization, use the
[external endpoints](EXTERNAL_SOURCE_ENDPOINTS_ES.md), which add `source_reference` and
source revision. OpenAPI is generated from serializers/views at `/api/schema/`.
Examples show partial responses; IDs and revisions are illustrative.

Base: `/api/v1/resource-registry/`, or `/auth/api/v1/resource-registry/` with a session.
Token authentication: `Authorization: Token <token>`. Session mutations require CSRF.
Use the organization's authorized host and `Content-Type: application/json`, except for
binary transfers. Bodies reject unknown fields. See [enablement and permissions](DATASET_MANAGEMENT_ENABLEMENT.md).

## Available routes

| Method and relative route | Purpose |
| --- | --- |
| `GET namespaces/` | Admitted destinations; use `results[].id` as `namespace_id`. |
| `GET datasets/` | Authorized, paginated catalogue. |
| `GET`, `PATCH datasets/{id}/` | Details or metadata editing. |
| `GET datasets/{id}/versions/`, `preview/`, `activity/`, `usage/` | History, bounded current preview, activity and known dependencies. |
| `POST dataset-changes/` | Prepare creation or replacement; requires `Idempotency-Key`. |
| `GET`, `PATCH dataset-changes/{id}/` | Read progress or replace the specification and recalculate. |
| `POST dataset-changes/{id}/publish/`, `cancel/`, `retry/` | Action using the proposal revision. |
| `POST datasets/{id}/restore-version/` | Manager selects a historical version. |
| `POST datasets/{id}/archive/`, `unarchive/` | Manager archives or recovers a dataset. |
| `GET datasets/{id}/deletion-preview/` | Review deletion impact without deleting. |
| `POST datasets/{id}/deletion-requests/` | Separate [cleanup request](RESOURCE_CLEANUP.md#rest). |

`GET namespaces/` returns `{results, count}` and up to 200 admitted destinations. A numeric
workspace ID is not a namespace UUID. `GET datasets/` returns `results`, page `count`,
`total`, `limit`, `offset`, `next_offset` and `previous_offset`. `versions/` and `activity/`
return `results`/`count` with a maximum of 200 records.

## 1. Upload the file

Follow the [Intake API](RESOURCE_REGISTRY_INTAKES_API.md): create an intake with an idempotency
key, transfer binary blocks, complete it and poll until `READY_FOR_PUBLICATION`. `size_bytes`
is the exact transmitted byte count, including line endings and UTF-8 encoding.

Alternatively, use a verified `uploads/` session in `COMPLETED`. Supply exactly one of
`intake_id` and `upload_id`; the actor must own the file. Do not additionally call
`intakes/{id}/publish/` or `uploads/{id}/register/` for this operation.

## 2. Create a new dataset

`POST dataset-changes/`, header `Idempotency-Key: ventas-manual-create-001`:

```json
{
  "namespace_id": "<namespace_uuid>",
  "intake_id": "<intake_v1_uuid>",
  "specification": {"name": "Sales"}
}
```

Response `201`, including an accepted replay of this proposal:

```json
{
  "id": "<change_uuid>",
  "status": "QUEUED",
  "revision": 0,
  "source_id": null,
  "source_revision": null,
  "source_reference": null,
  "result": {}
}
```

Poll `GET dataset-changes/{change_uuid}/` until preparation finishes:

| Status | Next action |
| --- | --- |
| `QUEUED`, `PROCESSING` | Continue polling with bounded delays/timeouts. |
| `READY` | Review differences and publish. |
| `NEEDS_MANAGER` | Await manager review; restore manual historical matches from the dataset. |
| `NO_CHANGES` | Success without a new version; do not call `publish/`. |
| `PUBLISHED` | Save the result; read current state before another operation. |
| `FAILED` | Read `error_code`/`error_message`; correct the cause and retry/recalculate while the source is available. |
| `CANCELLED` | Terminal; a different file requires a new proposal. |

`POST dataset-changes/{change_uuid}/publish/`, using the revision returned by GET:

```json
{"expected_revision": 0}
```

Response `200`, relevant fields:

```json
{
  "id": "<change_uuid>",
  "status": "PUBLISHED",
  "revision": 1,
  "result": {"dataset_ids": ["<dataset_uuid>"], "workbook_id": null}
}
```

Read `GET datasets/{dataset_uuid}/` for its `revision` and `current_version_id`.
Proposal and dataset revisions are independent; never calculate them on the client.

## 3. Replace the entire content

Upload and verify a new intake. Read the dataset and send `POST dataset-changes/` with a
new key, for example `ventas-manual-update-002`:

```json
{
  "namespace_id": "<namespace_uuid>",
  "intake_id": "<intake_v2_uuid>",
  "specification": {"dataset_id": "<dataset_uuid>", "expected_revision": 1}
}
```

Poll and publish as for creation. The dataset ID is preserved. More/fewer rows represent
the complete new snapshot, not an append. Removed columns and changed types require a
manager. Failed preparation preserves the current version.

To recalculate targets or revisions for the same file, `PATCH dataset-changes/{change_id}/`:

```json
{
  "expected_revision": 0,
  "specification": {"dataset_id": "<dataset_uuid>", "expected_revision": 2}
}
```

Response `200` contains the queued proposal and updated revision. Read it before publishing.
There is no `recalculate/` route: use PATCH. `cancel/` and `retry/` receive the proposal's
`expected_revision` only and return the proposal. A different file requires a new proposal.

## 4. Metadata, history and archival

`PATCH datasets/{dataset_id}/`:

```json
{"expected_revision": 2, "name": "Monthly sales", "tags": ["sales"]}
```

Also accepts `description`, `owner_team` and `responsible_user_id`. It does not replace content.
Response `200` contains the dataset and resulting revision.

A manager obtains the historical ID from `GET datasets/{id}/versions/` and calls
`POST datasets/{id}/restore-version/`:

```json
{"version_id": "<version_v1_uuid>", "expected_revision": 3, "reason": "Restore reviewed data"}
```

Response `200` contains the dataset, new revision and historical `current_version_id`.
It reuses that version without copying it. If a manual proposal remains pending because
of a historical match, cancel it with its revision after resolving restoration. For external
datasets this endpoint returns `source_managed_dataset`; approve the external proposal instead.

`POST datasets/{id}/archive/`:

```json
{"expected_revision": 4, "reason": "Dataset no longer in use"}
```

Response `200` contains status `ARCHIVED`, a new revision and preserved content.
To recover it, read the dataset again and send to `unarchive/`:

```json
{"expected_revision": 5, "reason": "Resume dataset use"}
```

Recovery requires available content and no started purge. Cancel any pending deletion
request first. Recovery does not reactivate withdrawn publication assignments.
Archiving does not schedule deletion; use [cleanup](RESOURCE_CLEANUP.md#rest).

## Errors and retries

Domain errors contain `code` and `detail`; DRF validation can return field errors.
Preparation errors appear on the proposal even when its GET returns `200`.

```json
{"code": "management_disabled", "detail": "Dataset management is not enabled."}
```

| Code | Action |
| --- | --- |
| `management_disabled` (403) | Check [enablement](DATASET_MANAGEMENT_ENABLEMENT.md). |
| `create_denied`, `edit_denied`, `manager_required` (403) | Obtain the required destination authority. |
| `not_found` (404) | ID does not exist or is inaccessible to the actor. |
| `revision_conflict` (409) | Read current state, review differences and recalculate if appropriate. |
| `idempotency_conflict` (409) | Recover the original body associated with that key. |
| `source_managed_dataset` (409) | Use the external contract to modify content. |
| `dataset_purging` (410) | Recovery/restoration is unavailable. |

Persist body and key before POST. After response loss, resend exactly both. Do not change
the revision in an accepted request while retaining its key. Replays do not repeat effects;
a historical result does not replace a current-state read.
