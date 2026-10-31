# External file synchronization contract

For complete request/response examples in English, use the
[endpoint walkthrough](EXTERNAL_SOURCE_ENDPOINTS_ES.md). For runnable scenarios, use
[the Postman guide](POSTMAN_WALKTHROUGH.md).

This is the Resource Registry contract consumed by an external connector. The connector
owns provider authentication, folder membership/overlap, stable file IDs, downloads,
change ordering, polling, and retry scheduling. Registry never calls SharePoint or Graph.
A reference is provenance supplied by an authorized integration, not proof of provider access.

## Destinations and permissions

All routes below are relative to `/api/v1/resource-registry/` or
`/auth/api/v1/resource-registry/`. Use the existing token or session authentication and
trusted organization host. Session callers must also meet CSRF requirements.

The integration needs admitted Organization/Workspace access, the namespace object grant
`resource_registry.synchronize_dataset_sources`, and ordinary creation/editing grants.
The synchronization grant is not automatically granted to staff or managers. Managers
can review/publish sensitive proposals using their existing destination authority.
Intake/upload ownership and existing upload admission remain required. No real permissions
are assigned by this delivery.

New imports use `FILE_UPLOAD` and store a separate external source association. Existing
manually managed datasets cannot be adopted by passing a reference. No migration matches
names or slugs. References contain no credentials, signed download URLs, or storage paths.

## Identity and revisions

`source_reference` contains exactly `provider`, `external_id`, and `version`.
Provider is a lowercase identifier (up to 64 characters); external ID and opaque version
are nonempty strings up to 512 characters. The connector builds an unambiguous stable ID,
for example from drive ID and item ID. File name and folder path are never identity.

Identity is unique within `(namespace_id, provider, external_id)`. Names may be duplicated.
One source identifies one ordinary dataset or one workbook and its sheet datasets.
A new physical item with the same name is a new external ID and a new source.

Do not confuse these revisions:

| Value | Meaning |
| --- | --- |
| `source_reference.version` | Opaque provider version; Registry never sorts it. |
| `expected_source_revision` | Registry source concurrency revision supplied to proposal creation; zero for a new source. |
| `specification.expected_revision` | Dataset revision, or each selected/archived sheet's revision. |
| `specification.workbook_revision` | Workbook mapping revision. |
| Action `expected_revision` | Proposal revision for publish/cancel/retry/PATCH; source revision for deactivate. |

Acceptance reserves/increments the source revision. Publication or a verified no-op
increments it again. Cancellation and deactivation also advance the source revision.
Always read the current source before a new operation. Recalculation retains the original
source reference and only changes proposal selection/revision. A different external version
or file requires a new proposal. Cancel an obsolete pending proposal before submitting its
replacement; do not allow a reviewer to approve known-obsolete content.

## Upload and create

Upload through existing `intakes/` (create, PUT numbered binary blocks, complete, GET until
`READY_FOR_PUBLICATION`) or `uploads/` until `COMPLETED`. Use exactly one `intake_id` or
`upload_id`. The verified file must belong to the integration actor; an upload session must
also belong to the requested destination. For this contract use `dataset-changes/` to publish;
do not additionally call the legacy intake publish or upload register endpoints.

```http
POST dataset-changes/
Idempotency-Key: <persisted operation key>
Content-Type: application/json
```

```json
{
  "namespace_id": "<namespace UUID>",
  "intake_id": "<verified intake UUID>",
  "source_reference": {
    "provider": "sharepoint",
    "external_id": "<stable drive/item identity>",
    "version": "<opaque provider version>"
  },
  "expected_source_revision": 0,
  "specification": {"name": "Sales"}
}
```

The response is a proposal, not a published dataset. Persist its `id`, `source_id`, source
revision and original request body. Poll `GET dataset-changes/{id}/`:

| Status | Connector action |
| --- | --- |
| `QUEUED`, `PROCESSING` | Continue bounded polling; no published change yet. |
| `READY` | POST `publish/` with the proposal revision. |
| `NEEDS_MANAGER` | Await manager approval; do not retry publication as the integration. |
| `PUBLISHED` | Record returned dataset/version/worksheet associations and applied source version. |
| `NO_CHANGES` | Successful verified no-op; record its result/source version without calling publish. |
| `FAILED` | Inspect error; retry or recalculate the same file after correcting the cause. |
| `CANCELLED` | Terminal; submit a separate proposal for a new source version. |

The original dataset/version remains available while processing fails or review is pending.
Removing columns, changing types, removing sheets, and reusing historical content require
manager approval. Historical approval selects the retained version instead of creating a
duplicate. Physical artifacts must still be available. Metadata edits require ordinary
revision checks and do not grant content control.

## Updating data, names, and Excel

For a single dataset, keep the same source identity and send:

```json
{"dataset_id":"<existing dataset UUID>","expected_revision":4}
```

as `specification`, with the new verified file, external version and current source revision.
The new file is a full replacement snapshot, not an append. A name-only change uses
`PATCH datasets/{id}/` with `name` and the dataset revision; there is no new content version.

For an existing workbook:

```json
{
  "workbook_id": "<workbook UUID>",
  "workbook_revision": 2,
  "sheets": [
    {"sheet_name":"Revenue","selected":true,"dataset_id":"<former Sales UUID>","expected_revision":3},
    {"sheet_name":"Regions","selected":true}
  ],
  "archive": [{"dataset_id":"<absent Costs UUID>","expected_revision":2}]
}
```

A first workbook has `name` and `sheets`, without workbook/dataset IDs. An initial proposal
without selected sheets exposes `available_sheets`/`suggested_sheets`; explicitly select
sheets through PATCH and wait for preparation. Never guess an ambiguous renamed sheet's
identity. Invalid selected sheets block the entire publication; explicitly exclude them or
replace the source file. Selected updates, historical restoration, mappings and approved
sheet removals commit in one transaction. Omitted sheets are not automatically removed.

`result` retains `dataset_ids`, `workbook_id` and existing `versions`, and includes:
`source_id`, `source_revision`, `source_reference`, `workbook_revision`, and `items` with
`dataset_id`, `dataset_version_id`, `dataset_revision`, `sheet_name`, and `outcome`
(`created`, `updated`, `unchanged`, `restored`, `archived`). Record this correspondence rather
than inferring IDs by name or array position. GET `dataset-sources/{source_id}/` returns
current control metadata, including archived outputs, without exposing their data.

## Removal and return

Only after the connector confirms that the item has left every configured folder for this
destination, call:

```http
POST dataset-sources/{source_id}/deactivate/
Idempotency-Key: <persisted removal key>
```

```json
{"expected_revision":5,"reason":"Source removed from all configured folders"}
```

Registry cancels pending proposals, detaches only this source's publication assignments,
and archives unprotected outputs as `SOURCE_REMOVED`. A workbook is handled atomically.
Other active assignments preserve visibility. Manual archives retain their reason. The
response lists `archived`, `retained` or `already_archived` outcomes. Files/versions are not
physically deleted. A Graph error, incomplete enumeration, or revoked provider permission
is not evidence of removal; the connector is responsible for that distinction.

On return, submit a new verified file proposal against the same source and existing target
IDs. Only `SOURCE_REMOVED` outputs can automatically recover, after successful publication
or a verified no-op. Failed/approval-pending files stay archived. Manual archives, independently
withdrawn publications and purged datasets are not automatically recovered. New publication
assignments are recorded; historical detached assignments stay detached.

## Conflicts, retries and ordering

Persist the original body and idempotency key before sending. After response loss, replay
exactly that request. Changing body or key can create a new operation or conflict. Source
identity also prevents duplicate ownership under different keys. One pending proposal per
source is enforced in the database. Older completed replays return their immutable result
and never reactivate a subsequently removed source. Their returned applied state is historical;
read source control metadata for the current state.

| Code | Meaning / response |
| --- | --- |
| `invalid_source_reference`, `invalid_request` | Correct request fields; HTTP 400. |
| `source_sync_denied`, `edit_denied`, `create_denied` | Restore appropriate scoped authority; HTTP 403. |
| `not_found` | Missing/inaccessible resource; HTTP 404. |
| `source_managed_dataset` | A manual content writer targeted source-owned content; HTTP 409. |
| `source_identity_conflict` | Wrong target, attempted adoption, or different workbook; HTTP 409. |
| `source_change_pending` | Resolve/cancel the pending proposal before submitting another; HTTP 409. |
| `revision_conflict` | Read current control state and review/recalculate; never blindly overwrite. |
| `idempotency_conflict` | The key was reused with different content; recover the original operation. |
| `manager_required` | Sensitive proposal requires manager publication; HTTP 403. |
| `target_not_editable`, `version_unavailable` | Manual archive, missing retained artifacts, or invalid lifecycle; review required. |
| `dataset_purging` | Physical deletion has begun; automatic recovery is unavailable; HTTP 410. |
| `candidate_changed`, `candidate_unavailable`, `publication_failed` | Current version preserved; follow proposal recovery rules. |

Registry validates revisions and serializes source writes; it cannot tell which of two opaque
provider versions is newer. The connector owns ordering, stable membership and durable cursors.

## Local-file connector example

[local-file client](../examples/external_sources/client.py) has finite HTTP and polling timeouts and does not call
SharePoint. Set `RR_AUTHORIZATION` through your normal local secret mechanism. Do not put tokens
in commands, fixtures, source control or logs. Use an authorized development host:

```bash
uv run --env-file .env python resource_registry/examples/external_sources/client.py \
  --base-url https://<authorized-host>/api/v1/resource-registry \
  --key sales-first-import submit \
  --file resource_registry/examples/external_sources/sales-v1.csv \
  --specification resource_registry/examples/external_sources/create.json \
  --namespace <namespace-uuid> --external-id <stable-file-id> --version v1
```

Use `sales-v2.csv` with an update specification and current source revision for replacement;
`sales-sensitive.csv` demonstrates a pending manager review. JSON and JSONL fixtures are also
included. Automated tests generate XLSX and Parquet locally. The client deliberately stops on
`NEEDS_MANAGER`; it never escalates permission. Reuse the original key/body after response loss.

## Deployment and rollback

Drain legacy registration/workbook jobs and stop old writers before applying additive
migration `0014_dataset_external_sources`. This migration also enables dataset management
for every existing organization with `resource_registry_enabled=True`, creates missing
configurations, and keeps legacy implicit updates blocked. Existing cleanup and retention
policies are preserved; new configurations use `SIMULATE`. Active legacy jobs in eligible
organizations abort the migration atomically; drain them and retry. It does not grant user
permissions or auto-enroll existing datasets. Start compatible backend/workers after migrating,
then grant scoped permissions and enable the connector through the existing operational process.

This enablement is consolidated into 0014. Environments that already applied its earlier
contents will not rerun it. Use `configure_dataset_management --organization-id <id> --mode enable`
there. New organizations created with Resource Registry checked in Django Admin, and existing
organizations whose checkbox changes from disabled to enabled, now enable dataset management
automatically in the same transaction. Pending legacy jobs produce a form error. Existing
policies and user permissions are preserved; unrelated Admin edits do not undo an operational
management pause. Activations outside the Admin still use the command. Do not reverse or fake
migrations to rerun the data operation. See [dataset maintenance](DATASET_MAINTENANCE.md) for deployment and Admin access.

To pause only this integration, stop the connector/revoke its synchronization grant. The
existing management configuration can stop all management mutations while preserving reads.
Keep the schema and compatible source-aware writers on rollback: older code does not enforce
external ownership. Graph access, folder configuration, connector scheduling, UI changes and production
deployment remain outside this backend contract. No existing dataset is adopted by name.
