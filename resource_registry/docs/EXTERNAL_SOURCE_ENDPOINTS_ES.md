# Resource Registry: external dataset synchronization endpoints

Check [enablement and permissions](DATASET_MANAGEMENT_ENABLEMENT.md) before starting.
Select a destination with `GET /api/v1/resource-registry/namespaces/`; use the workspace's
returned `id` as `namespace_id`, not its numeric workspace ID. The
[Postman guide](POSTMAN_WALKTHROUGH.md) exercises this workflow with local JSON files.

This request/response guide is for the SharePoint Sync developer. Its historical filename
is retained so existing links continue to work; the maintained text is in English.

**Base URL:** `https://<host>/api/v1/resource-registry/`.
Session alternative: `https://<host>/auth/api/v1/resource-registry/`, with normal CSRF.
All routes below are relative to that base. Authenticate every request and use
`Content-Type: application/json`, except for binary block transfer. The integration
needs `synchronize_dataset_sources` on the destination and applicable creation/edit grants.

Replace `<...>` placeholders with actual IDs. **Responses show only relevant fields.**
Illustrative revisions assume no concurrent changes; always use revisions returned by RR.

| Case | Sequence |
| --- | --- |
| Create | Upload → create proposal → poll → publish. |
| Update/replace | Read source → upload new file → create targeted proposal → poll → publish. |
| Remove from catalogue after source disappearance | Read source → deactivate source. |

## Common step: upload and verify the file

Required for creation and replacement. Example `ventas.csv`, exactly 15 bytes:

```csv
id,amount
1,10
```

### POST `intakes/`

Header: `Idempotency-Key: ventas-upload-v1`.

Request:

```json
{
  "filename": "ventas.csv",
  "content_type": "text/csv",
  "size_bytes": 15
}
```

Response **201 Created**; an identical replay returns **200 OK**:

```json
{
  "intake_id": "<intake_v1>",
  "status": "PENDING_UPLOAD",
  "idempotent_replay": false,
  "upload": {
    "method": "backend_blocks_v1",
    "url": "/api/v1/resource-registry/intakes/<intake_v1>/blocks/0/",
    "recommended_chunk_size_bytes": 524288,
    "max_parallel_chunks": 1,
    "required_headers": {"Content-Type": "application/octet-stream"}
  }
}
```

### PUT `intakes/{intake_id}/blocks/{block_index}/`

Header: `Content-Type: application/octet-stream`.
Body: **raw file bytes**, without JSON or multipart encoding.
Send the 15 bytes of the example CSV to `blocks/0/`.

Response: **204 No Content**, with no body.

For larger files, split according to `recommended_chunk_size_bytes`; send consecutive
blocks starting at `0`, respecting `max_parallel_chunks`. The final block may be smaller.

### POST `intakes/{intake_id}/complete/`

Request:

```json
{}
```

Response **200 OK**, example after successful verification:

```json
{
  "intake_id": "<intake_v1>",
  "status": "READY_FOR_PUBLICATION",
  "filename": "ventas.csv",
  "actual_size_bytes": 15,
  "failure_code": null
}
```

### GET `intakes/{intake_id}/`

No body. Response **200 OK** contains intake state as above. While verification or
inspection is pending, poll until `READY_FOR_PUBLICATION`. Only then use `intake_id`
in the proposal. Publish the dataset through `dataset-changes/{id}/publish/`.

The same upload flow supports CSV, XLSX, JSON, JSONL/NDJSON and Parquet. Supply the actual
filename, MIME type and byte size.

## 1. Create a dataset that does not exist yet

### POST `dataset-changes/`

Header: `Idempotency-Key: ventas-create-v1`.
Use the verified intake and `expected_source_revision: 0` for a new source.

Request:

```json
{
  "namespace_id": "<namespace_id>",
  "intake_id": "<intake_v1>",
  "source_reference": {
    "provider": "sharepoint",
    "external_id": "drive-123:item-456",
    "version": "v1"
  },
  "expected_source_revision": 0,
  "specification": {"name": "Sales"}
}
```

`external_id` identifies the file independently of its name. `version` carries the opaque
version supplied by the provider.

Response **201 Created**:

```json
{
  "id": "<change_v1>",
  "status": "QUEUED",
  "revision": 0,
  "source_id": "<source_id>",
  "source_revision": 1,
  "result": {}
}
```

### GET `dataset-changes/{change_id}/`

No body. Poll until preparation finishes.

Response **200 OK**, ready-to-publish example:

```json
{
  "id": "<change_v1>",
  "status": "READY",
  "revision": 0,
  "source_id": "<source_id>",
  "source_revision": 1,
  "error_code": "",
  "error_message": "",
  "result": {}
}
```

### POST `dataset-changes/{change_id}/publish/`

Request: use the **proposal** `revision` from the preceding GET.

```json
{
  "expected_revision": 0
}
```

Response **200 OK**:

```json
{
  "id": "<change_v1>",
  "status": "PUBLISHED",
  "revision": 1,
  "source_id": "<source_id>",
  "source_revision": 2,
  "result": {
    "source_id": "<source_id>",
    "source_revision": 2,
    "source_reference": {
      "provider": "sharepoint",
      "external_id": "drive-123:item-456",
      "version": "v1"
    },
    "dataset_ids": ["<dataset_id>"],
    "workbook_id": null,
    "workbook_revision": null,
    "items": [
      {
        "dataset_id": "<dataset_id>",
        "dataset_version_id": "<dataset_version_v1>",
        "dataset_revision": 1,
        "sheet_name": null,
        "outcome": "created"
      }
    ]
  }
}
```

Save `source_id`, `dataset_id` and `result.items` mappings for later operations.

**For XLSX:** use this `specification` to select initial sheets. RR returns a workbook
and one dataset per sheet in `result.items`.

```json
{
  "name": "Sales",
  "sheets": [
    {"sheet_name": "Sales", "selected": true},
    {"sheet_name": "Costs", "selected": true}
  ]
}
```

## 2. Update or replace an existing dataset

This applies to datasets created through this contract and linked to the same source.
Existing manual datasets cannot be adopted by adding `source_reference`.
The new file replaces all content while preserving `dataset_id`.

### GET `dataset-sources/{source_id}/`

No body. Obtain current revisions before preparing the update.

Response **200 OK**, after creation in the preceding example:

```json
{
  "id": "<source_id>",
  "namespace_id": "<namespace_id>",
  "provider": "sharepoint",
  "external_id": "drive-123:item-456",
  "revision": 2,
  "status": "ACTIVE",
  "last_applied_version": "v1",
  "workbook_id": null,
  "workbook_revision": null,
  "items": [
    {
      "dataset_id": "<dataset_id>",
      "dataset_revision": 1,
      "dataset_version_id": "<dataset_version_v1>",
      "sheet_name": null,
      "name": "Sales",
      "status": "ACTIVE",
      "archive_reason": "",
      "data_available": true
    }
  ]
}
```

Upload the new file through the common step, with a new key such as `ventas-upload-v2`.
The result is another verified intake, `<intake_v2>`.

### POST `dataset-changes/`

Header: `Idempotency-Key: ventas-update-v2`.

Request:

```json
{
  "namespace_id": "<namespace_id>",
  "intake_id": "<intake_v2>",
  "source_reference": {
    "provider": "sharepoint",
    "external_id": "drive-123:item-456",
    "version": "v2"
  },
  "expected_source_revision": 2,
  "specification": {
    "dataset_id": "<dataset_id>",
    "expected_revision": 1
  }
}
```

Response **201 Created**:

```json
{
  "id": "<change_v2>",
  "status": "QUEUED",
  "revision": 0,
  "source_id": "<source_id>",
  "source_revision": 3,
  "result": {}
}
```

Poll `GET dataset-changes/<change_v2>/`. When `READY`, call
`POST dataset-changes/<change_v2>/publish/` with the proposal revision:

```json
{
  "expected_revision": 0
}
```

Response **200 OK**:

```json
{
  "id": "<change_v2>",
  "status": "PUBLISHED",
  "revision": 1,
  "source_id": "<source_id>",
  "source_revision": 4,
  "result": {
    "source_id": "<source_id>",
    "source_revision": 4,
    "source_reference": {
      "provider": "sharepoint",
      "external_id": "drive-123:item-456",
      "version": "v2"
    },
    "dataset_ids": ["<dataset_id>"],
    "items": [
      {
        "dataset_id": "<dataset_id>",
        "dataset_version_id": "<dataset_version_v2>",
        "dataset_revision": 2,
        "sheet_name": null,
        "outcome": "updated"
      }
    ]
  }
}
```

**Proposal states the consumer must handle:**

| Status | Action |
| --- | --- |
| `QUEUED`, `PROCESSING` | Continue polling with delays and a timeout. |
| `READY` | Publish using the proposal revision. |
| `NO_CHANGES` | Success: identical content and checked external version recorded. Do not call `publish/`; `result.items[].outcome` is `unchanged`. |
| `NEEDS_MANAGER` | Await approval. A manager uses the same `publish/`; the integration cannot publish this change. |
| `PUBLISHED` | Save the result. Approved historical content returns `restored` and reuses its existing version. |
| `FAILED` | Read `error_code` and `error_message`; current content is preserved. |
| `CANCELLED` | Terminal; another file/version requires a new proposal. |

**For XLSX:** use workbook identity and revisions from the source GET. Example
`specification` renaming one sheet while preserving its dataset, adding another and
withdrawing a third:

```json
{
  "workbook_id": "<workbook_id>",
  "workbook_revision": 1,
  "sheets": [
    {
      "sheet_name": "Revenue",
      "selected": true,
      "dataset_id": "<dataset_ventas>",
      "expected_revision": 1
    },
    {"sheet_name": "Regions", "selected": true}
  ],
  "archive": [
    {"dataset_id": "<dataset_costos>", "expected_revision": 1}
  ]
}
```

Sheet withdrawal requires a manager. Selected sheets publish together; one invalid selected
sheet blocks the whole publication. Read `result.items` mappings and `workbook_revision`
from the response; never infer IDs from names or array positions.

### PATCH `datasets/{dataset_id}/` — name-only change

For a name-only change, send the current dataset revision:

```json
{
  "name": "Monthly sales",
  "expected_revision": 2
}
```

Response **200 OK**, without a new content version:

```json
{
  "id": "<dataset_id>",
  "name": "Monthly sales",
  "revision": 3,
  "current_version_id": "<dataset_version_v2>",
  "source_id": "<source_id>",
  "content_managed_externally": true
}
```

### POST `dataset-changes/{change_id}/cancel/` — supersede a pending proposal

Only one pending proposal is allowed per source. If another file/version arrives before
resolution, cancel the previous proposal. Request: current **proposal** revision.

```json
{
  "expected_revision": 0
}
```

Response **200 OK**, relevant fields:

```json
{
  "id": "<change_pendiente>",
  "status": "CANCELLED",
  "revision": 1,
  "source_id": "<source_id>"
}
```

Read the source again and create the replacement proposal with its current revision.

## 3. Remove a dataset after its source file disappears

In this contract, removal means **deactivating the source and archiving datasets that
lose their last active assignment**. Files and history are preserved. For Excel, all
sheets of the source are included. Subsequent physical deletion uses the separate
manager-reviewed [cleanup contract](RESOURCE_CLEANUP.md#rest); `deactivate/` never deletes Parquet.

### GET `dataset-sources/{source_id}/`

No body. Response **200 OK** has the same shape as case 2. Use its `revision`; in the
replacement example above it is `4`.

### POST `dataset-sources/{source_id}/deactivate/`

Header: `Idempotency-Key: ventas-remove-v3`.
Send only after the synchronizer confirms that the file has disappeared from every folder
publishing it to this destination.

Request:

```json
{
  "expected_revision": 4,
  "reason": "File removed from synchronized folders"
}
```

Response **200 OK**:

```json
{
  "source_id": "<source_id>",
  "source_revision": 5,
  "status": "INACTIVE",
  "items": [
    {
      "dataset_id": "<dataset_id>",
      "dataset_revision": 3,
      "outcome": "archived"
    }
  ]
}
```

`dataset_revision: 3` corresponds to the sequence without the optional name PATCH.
Other outcomes are `retained` when another assignment protects the dataset, or
`already_archived` if it was already archived. Deactivation cancels pending proposals.

If the file returns, repeat case 2 with the same external identity and a new intake.
RR automatically recovers only datasets archived for `SOURCE_REMOVED`, after successful
publication or verified identical content.

## Retries and endpoint errors

Persist each `Idempotency-Key` with its body. After response loss, resend exactly both.
A new operation requires a new key. Historical replays do not publish again or reactivate
a source deactivated afterwards.

| Request field | Required revision |
| --- | --- |
| `expected_source_revision` when creating a proposal | Source GET `revision`; `0` for a new source. |
| `specification.expected_revision` | Source item `dataset_revision`. |
| `expected_revision` in `publish/` or `cancel/` | Proposal GET `revision`. |
| `expected_revision` in `deactivate/` | Source GET `revision`. |

Dataset/source error example, **409 Conflict**:

```json
{
  "code": "source_change_pending",
  "detail": "Resolve the existing source proposal first."
}
```

| HTTP / code | Action |
| --- | --- |
| `403 management_disabled` | Enable organization Dataset Management; see [enablement](DATASET_MANAGEMENT_ENABLEMENT.md). |
| `409 source_managed_dataset` | Use a source proposal; manual replacement/restoration was rejected. |
| `409 revision_conflict` | Read current revisions and review the operation. |
| `409 source_change_pending` | Resolve or cancel the pending proposal. |
| `409 idempotency_conflict` | Recover the original body for that key. |
| `409 source_identity_conflict` | Correct destination/target; do not adopt manual datasets or another source's datasets. |
| `403 source_sync_denied`, `create_denied`, `edit_denied` | Correct destination permissions. |
| `403 manager_required` | Await manager approval. |
| `410 dataset_purging` | Automatic recovery is unavailable. |

Intake errors use `code` and `message`; dataset/source domain errors use `code` and
`detail`. Preparation failures appear in proposal `error_code` and `error_message`.
