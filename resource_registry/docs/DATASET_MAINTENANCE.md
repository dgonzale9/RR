# Dataset maintenance: backend and Django Admin

This is the operational guide for proposals. For manual request bodies, see the
[maintenance API](DATASET_MANAGEMENT_API.md); for synchronized content, see the
[external endpoints](EXTERNAL_SOURCE_ENDPOINTS_ES.md). The
[enablement guide](DATASET_MANAGEMENT_ENABLEMENT.md) covers migrations and configuration.

Dataset maintenance does not change `apps/uniai-site`, proxies, SSO or staff assignments.
The APIs require authentication and business permissions; Django Admin additionally requires
an active user with existing staff access. The maintenance interface uses English labels.

## Daily operations

Open **Resource registry → Datasets** in Django Admin on your organization's host.
The catalogue supports search, pagination and filters for destination, scope, responsible
user, tags, source and status. Dataset details show identity, current version, schema,
bounded preview, history, operation actors and known registered dependencies. These
references are not a complete inventory of external consumers.

To filter dates, open **Date range** in the right panel. Choose **Last successful update**
(the current version's creation date) or **Created date** (dataset creation), fill in
**From** and/or **To**, and click **Apply dates**. Both dates are fully inclusive in the
Admin's displayed time zone. After restoration, **Last successful update** shows the
selected version's date, not the restoration date. Datasets without a current version
are excluded when filtering **Last successful update**. **Clear dates** removes only
the date range, preserving search, sorting and other filters. Applying or clearing a
range returns to page one. An invalid range shows an error and no results until corrected.

Managers administer datasets within their authorized scopes. Other users need the
appropriate Guardian creation grant on the destination or editing grant on the dataset.
This implementation does not include screens or APIs to assign or revoke these grants.
A dataset grant does not provide workspace membership or staff access. The responsible
user is independent of the creator and permissions. New datasets created through this
flow grant editing to their creator; migrations do not grant permissions retroactively.

1. Select **Create from file** or **Update data** on the dataset page.
2. Upload the file. The backend verifies blocks, size and content through existing
   intakes, without requiring the global intake-operator permission.
3. Prepare the proposal. Processing runs automatically in the background, outside the
   HTTP request and publication transaction. No additional terminal is required.
4. Review previous/new row counts, added/removed columns and type changes.
5. Publish. An authorized editor can publish compatible changes to their datasets.
   Removing columns or changing any type requires a manager.

Manual updates support only `FILE_UPLOAD` datasets without an external source. Linked
datasets also remain `FILE_UPLOAD`, but their content is updated through proposals with
`source_reference`. Admin shows external ownership and hides manual replacement; shared
services enforce the same protection. A filename never selects the target dataset.
An unpublished proposal produces no consumable versions or publication events. Failure
preserves the current version. A revision conflict requires recalculation and review.

Publishing a proposal marks its source intake **Published** in the same transaction,
with a publication date and no expiry. Preparation, failure and **No changes** do not
mark the intake published. Replaying a confirmed publication preserves its date and
creates no additional versions or events. Published source files remain protected by
pending uses and retention periods; completed proposals do not retain them indefinitely.
See [controlled cleanup](RESOURCE_CLEANUP.md). Cancelling an unpublished upload requests
cleanup subject to storage protections.

Each published intake has a `StagedResourcePublication` per active destination. Proposals
link to that publication; `StagedResourcePublicationDataset` records exact versions,
including historical versions. Intake details show **Published by**, **Maintenance updates**,
**Published versions** and **Dataset maintenance: Published**. Maintenance publications
do not create legacy registration jobs.

Multiple proposals from the same intake to the same destination reuse its publication;
other destinations have independent records. Replaying a proposal does not reactivate
a withdrawn publication. The legacy endpoint returns `INTAKE_ALREADY_PUBLISHED` (409)
when asked to register a maintenance publication again at that destination.

**My updates**, **Pending publication** and **Failed updates** display durable notices.
Progress is available in Admin; no emails are sent. Failed proposals can be recalculated
or retried after correcting the cause. Interrupted processing remains recoverable after
the processing deadline.

### Automatic processing

Completing an upload through the maintenance Admin wakes intake inspection. Creating,
recalculating or retrying a proposal wakes preparation after transaction commit. Both
use the application's existing low-priority executor. No additional process is required,
and preparation does not publish automatically.

Tasks persist in PostgreSQL. Authorized progress reads wake pending work missed after a
server restart or executor saturation. Each wakeup processes only the requested proposal
or intake; database locks prevent duplicate claims. Repeated wakeups are coalesced, and
in-memory tasks are bounded by the existing per-process `MAX_GLOBAL_WORKERS` budget.

After a server interruption during preparation, a progress read after the one-hour deadline
marks the proposal failed and allows **Retry**. Inspection uses its independent
`RESOURCE_REGISTRY_INTAKE_INSPECTION_LEASE_SECONDS`. A queued proposal without subsequent
reads may wait until its review is reopened. The processing command remains available
to drain the queue without connected users.

## Excel

`DatasetWorkbook` identifies a stable workbook at a destination. `WorkbookIngestionGroup`
continues to record evidence of a particular ingestion. The initial review shows sheets
and suggested mappings. New sheets require explicit selection. Map a renamed sheet to
its existing dataset. Missing sheets retain their datasets unless a manager requests
archival. If a selected sheet is invalid, none are published; exclude it and recalculate.
Selected sheets and archives commit in one transaction. Publications of the same file
to different destinations are independent.

Content identical to the current version ends as **No changes**. A historical match identifies
the version a manager can restore from the dataset page. Restoration changes the current
pointer without creating another version or modifying previous versions. For external
proposals, approval selects the retained historical version through the source contract.

## Archive and read behavior

Managers can archive, view archived datasets and recover them. A later ingestion does not
undo a manual archive. Recovery requires physically available artifacts and does not
reactivate withdrawn publications; those require their publication workflow.
Archiving does not schedule deletion. [Controlled cleanup](RESOURCE_CLEANUP.md) provides
a separate permanent-deletion request for archived datasets, with inventory review,
confirmation and minimum retention. There is no immediate DELETE endpoint or selective
version purge in this workflow.

Readers without an explicit version resolve `Dataset.current_version`; asynchronous
queries and export plans pin the selected version. Jobs retain authorization checks,
including after archival. Historical records without a current pointer can resolve only
READY versions with active artifacts. The catalogue shows **Needs review** if no current
version is available.

## REST contract

Routes are included under `auth/api/v1/resource-registry/` (session) and
`api/v1/resource-registry/` (token) in `BotPlatform/urls.py`. OpenAPI includes the operations.
Request bodies reject unknown fields.

| Relative route | Method and operation |
| --- | --- |
| `datasets/` | GET catalogue; `results`, page `count`, `total`, `limit`, `offset`, `next_offset`, `previous_offset` |
| `datasets/{id}/` | GET details; PATCH metadata with `expected_revision` |
| `datasets/{id}/versions/`, `preview/`, `activity/`, `usage/` | GET history, preview, activity and visible dependencies |
| `dataset-changes/` | POST proposal with `Idempotency-Key` header |
| `dataset-changes/{id}/` | GET progress/differences; PATCH selection and `expected_revision` |
| `dataset-changes/{id}/publish/`, `cancel/`, `retry/` | POST with `expected_revision` |
| `datasets/{id}/restore-version/` | POST `version_id`, `expected_revision`, `reason` |
| `datasets/{id}/archive/`, `unarchive/` | POST `expected_revision`, `reason` |

Create proposals with `namespace_id`, exactly one of `intake_id`/`upload_id`, and
`specification`. The verified file must belong to the actor. Storage paths and a
client-supplied organization are not accepted as authority.

```json
{
  "namespace_id": "<namespace UUID>",
  "intake_id": "<verified intake UUID>",
  "specification": {
    "dataset_id": "<dataset UUID>",
    "expected_revision": 4
  }
}
```

For creation, `specification` includes `name`. Workbooks use `workbook_id`,
`workbook_revision` and `sheets`: each item has `sheet_name`, `selected`, and, for an
existing sheet, `dataset_id`/`expected_revision`. A selected new sheet omits those last
two fields. `archive` lists IDs/revisions of missing sheets from that workbook.
Changing targets invalidates earlier results and queues preparation again.

## Deployment and pilot

Migrations 0004–0008 are additive: they preserve IDs, versions, files and provenance.
They initialize the latest READY version with active artifacts, assign responsible users
from creators with valid membership, and reconstruct only unambiguous workbook mappings.
Ambiguous cases require explicit selection during the first update.

Migration 0008 adds the nullable proposal-to-publication link. Historical proposals can
be reconciled on authorized publication replay only when artifacts identify exact versions
and activity identifies the original actor. The current version is not used to guess a
historical version. Missing evidence, including unchanged historical sheets without adequate
provenance, returns `publication_evidence_missing` and requires review. Withdrawn destinations
are not reconstructed automatically and files are not processed again.

1. Stop legacy write admission during transition. Drain registration/workbook jobs and
   old workers. Migration 0014 and the enablement command reject active persisted jobs,
   but cannot detect unregistered processes running old code.
2. Apply additive migrations before starting code that reads the new fields.
   `0014_dataset_external_sources` enables management for every organization with
   `resource_registry_enabled=True`. It creates missing configurations and sets
   `enabled=True` and `legacy_updates_blocked=True`, including on disabled existing
   configurations. It needs no environment-specific IDs. Existing cleanup/retention
   policies are preserved; new configurations use `SIMULATE` and 30/30/7-day defaults.
   It assigns no user permissions. Eligible scopes with `submitted`, `queued` or
   `running` jobs abort the migration atomically; drain them and retry.
3. Start only backend/workers compatible with `current_version`; retain existing intake
   inspection workers. Keep old writers stopped throughout transition, because enablement
   now happens during `migrate`.
4. The new Admin automatically wakes inspection and preparation through the existing
   executor; `run_dataset_changes` is not mandatory for that flow.
5. Verify the workflow with a staff editor and a manager. Configuration is visible in
   Admin to staff who manage the host organization.

```bash
uv run --env-file .env python manage.py migrate
```

Enablement is consolidated into migration 0014. An environment that already applied its
earlier contents will not run it again. Use the following command once per organization
in that case, for activation outside Admin, or to resume a later management pause.
Do not reverse or mark 0014 pending to force enablement. Replace `123` with the actual ID.

```bash
uv run --env-file .env python manage.py configure_dataset_management --organization-id 123 --mode enable
```

Creating an organization with **Resource Registry** checked, or changing its checkbox
from off to on in Django Admin, enables Dataset Management in the same transaction.
Missing configuration is created; existing cleanup/retention policy is preserved. Pending
legacy jobs produce a form error and prevent partial enablement. Admin and the command
share the activation validations.

Editing unrelated fields on an already enabled organization does not resume a management
pause. Unchecking Resource Registry preserves configuration and legacy-write protection.
Admin visibility still requires staff and management authority over the host organization;
activation grants no new permissions. If Resource Registry was already checked but the
configuration is missing, run the command once; saving the unchanged checkbox does not create it.

The optional `run_dataset_changes --once --batch-size 5` command shares queues and locks
with automatic processing. Prepared candidates remain private and are subject to
[candidate retention](RESOURCE_CLEANUP.md).

Publication verifies prepared-object size and identity. New remote candidates retain the
ETag returned when saving Parquet; local storage is checked with SHA-256. Older remote
proposals without an ETag require temporary download and SHA-256 verification outside
the publication transaction, which may take longer for large files. Azure MD5 is not
compared with the canonical SHA-256.

Once a scope is enabled, legacy registration against an existing slug fails with a conflict
and requires an explicit update. Legacy initial creation remains supported. Consumers must
migrate replacements to proposals with target ID and revision; do not return to clients
that depend on silent replacement by name.

For an operational rollback:

```bash
uv run --env-file .env python manage.py configure_dataset_management --organization-id 123 --mode disable
```

This blocks management mutations and preparation while preserving reads and the legacy
replacement barrier. Keep code that understands `current_version`, or completed restorations
will lose their meaning. Do not reverse migrations or delete their data during rollback.
Also retain maintenance-publication readers and the legacy duplicate-registration guard:
older code could mistake a publication without `registration_submission` for pending registration.

## Verification

`test_dataset_management*`, `test_dataset_changes` and `test_dataset_maintenance_flows`
use isolated PostgreSQL and local storage. They cover authorization, Admin/API, versions,
proposals, Excel, restoration, archival, migrations, retry, idempotency, concurrency and
catalogue/history queries.

`test_dataset_management_enablement_migration` applies 0014 from historical state 0013,
checking organization selection, policy preservation, atomic rejection of active legacy
jobs, retry, reversal without removing safeguards, and grouped organization queries.
`tests/organization/test_dataset_management_enablement_admin` submits real Admin forms
and covers creation, activation, pauses, permissions, pending jobs and failure rollback.
`test_dataset_management_admin` covers blocks → inspection → proposal → publication and
produces HTML for visual inspection. No external provider is contacted.

Compare older-suite failures against the same environment on a clean baseline. Failed or
skipped tests are not successes. Historical verification reports record their own results
and environment limitations separately.
