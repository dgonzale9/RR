# Resource Registry worker operations

Intake inspection, dataset-change preparation, legacy registration and physical cleanup are
separate jobs. Use the durable object status to identify the relevant worker. Run Django commands
with the environment file for the intended environment; do not expose credentials in logs.

## Dataset Management

```text
Upload -> intake inspection -> READY_FOR_PUBLICATION
       -> dataset-change preparation -> READY / NEEDS_MANAGER / NO_CHANGES / FAILED
       -> explicit publication -> current dataset version
```

**Datasets → Create from file / Update data** automatically wakes intake inspection after
upload completion. Creating, revising or retrying a proposal wakes preparation after transaction
commit. Both use the existing low-priority executor, with no separate mandatory process for this
Admin flow. Authorized progress reads can wake durable work missed after restart or saturation.
Preparation never publishes automatically: the UI or an authorized integration calls `publish/`.
`NO_CHANGES` is a successful preparation result and requires no publication call.

The generic intake HTTP path still needs its inspection worker. Keep the existing inspection
schedule for API clients and other producers. A queued proposal with no later progress reads can
wait until a reader or the optional proposal worker wakes it.

If a process dies during proposal preparation, a progress read after the one-hour processing
deadline can mark it failed and allow Retry. Intake inspection has its independent
`RESOURCE_REGISTRY_INTAKE_INSPECTION_LEASE_SECONDS`. Review safe error codes rather than
manually editing job rows. New source bytes require a new intake/proposal.

## Commands

| Command | Purpose and operation |
| --- | --- |
| `uv run --env-file .env python manage.py run_staged_resource_inspection_jobs --batch-size 10` | Inspect completed intakes; retain environment schedule, typically every minute. |
| `uv run --env-file .env python manage.py expire_staged_resource_intakes --batch-size 100` | Evaluate unsubmitted staging expiry and protected cleanup; typically hourly. |
| `uv run --env-file .env python manage.py run_dataset_changes --once --batch-size 5` | Optional bounded preparation batch; same queue/locks as automatic wakeups. |
| `uv run --env-file .env python manage.py run_dataset_registration_jobs --once` | Legacy tabular registration jobs from the retained intake publication flow. |
| `uv run --env-file .env python manage.py run_workbook_ingestion_jobs --once` | Legacy XLSX ingestion jobs, not proposal preparation. |
| `uv run --env-file .env python manage.py run_resource_cleanup --batch-size 100` | One policy-authorized cleanup batch; does not override retention or mode. |

Without `--once`, the proposal command polls; it supports `--poll-interval-seconds` and
`--batch-size`. Legacy registration commands retain their deployment scheduler. Stop old writer
versions and drain jobs before [enabling management](DATASET_MANAGEMENT_ENABLEMENT.md).

## Cleanup coordinator

Compatible WSGI/ASGI entrypoints initialize the integrated coordinator. By default it evaluates
every 900 seconds and considers at most 100 objects per cycle. It uses persistent claims and
renewable reservations to protect active reads/writes. Requests can remain scheduled, blocked
or partial while retention/references prevent deletion.

`RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED`, `RESOURCE_REGISTRY_CLEANUP_INTERVAL_SECONDS`,
`RESOURCE_REGISTRY_CLEANUP_BATCH_SIZE` and `RESOURCE_REGISTRY_PRIVATE_CLEANUP_MODE` configure
the runtime. Organization cleanup policy starts in `SIMULATE`; actual deletion requires an
enabled organization management configuration in `EXECUTE`. Private intakes without a namespace
use the separate global mode. See [cleanup](RESOURCE_CLEANUP.md) before executing a batch.

## Diagnose by state

| State | Follow-up |
| --- | --- |
| Intake `PENDING_UPLOAD` | Finish/resume the same bytes or cancel. No inspection job can process an incomplete upload. |
| Intake `COMPLETED` / `ANALYZING` | Check inspection worker; the maintenance Admin also has automatic wakeups. |
| Intake `READY_FOR_PUBLICATION` | Create/review a proposal; a ready intake does not publish by itself. |
| Proposal `QUEUED` / `PROCESSING` | Read progress; check executor or run optional preparation batch. |
| Proposal `NEEDS_MANAGER` | Waiting for review, not a stuck worker. |
| Proposal `NO_CHANGES` | Successful terminal result; no publish required. |
| Proposal `FAILED` | Inspect `error_code`/`error_message`; retry only while the source is available. |
| Intake `PUBLISHED` with a maintenance proposal | Read proposal result/current version; no legacy registration job is expected. |
| Legacy intake publication with registration pending | Check the corresponding registration/workbook job. |
| Cleanup `SCHEDULED` / `BLOCKED` / `PARTIAL` | Check deadline, mode, inventory, references and permissions; do not bypass blockers. |

Legacy jobs use `submitted`, `queued`, `running`, `completed`, `failed`, `cancelled`; workbook
ingestion may use `completed_with_skips`. Dataset-change proposals instead publish selected
sheets atomically, with a selected invalid sheet blocking the whole set.

Record only authorized IDs, timestamps and safe codes for diagnosis. Use lifecycle services for
recovery and reviewed cleanup requests for deletion; do not delete blobs by prefix or alter
version pointers directly. Replaying a historical result never recreates removed content.

See [maintenance QA](DATASET_MANAGEMENT_QA.md), [upload support](RESOURCE_REGISTRY_UPLOAD_SUPPORT.md)
and [storage configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md).
