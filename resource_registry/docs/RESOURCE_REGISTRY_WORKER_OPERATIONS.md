# Resource Registry worker operations

This runbook describes the workers used by the staged-intake upload and publication flow. It is
for platform operations and administrators investigating a delayed or failed upload. Environment
schedulers already own recurring execution in deployed environments. Run commands manually only
for local development or an approved operational intervention.

## Upload-to-dataset flow

```text
Browser upload
  -> staged intake inspection
  -> Ready for publication
  -> destination publication
  -> CSV/JSON/JSONL/Parquet registration or XLSX workbook ingestion
  -> Dataset available
```

The inspection worker does not publish a Dataset. Publication is an explicit Admin action after
the intake reaches `READY_FOR_PUBLICATION`.

## Commands

Run all Django commands with the environment file for the target environment. Do not copy SAS URLs,
storage keys, connection strings, or other credentials into command output, tickets, or logs.

| Command | Processes | Normal deployment cadence | Local manual invocation |
| --- | --- | --- | --- |
| `run_staged_resource_inspection_jobs` | Completed staged intakes awaiting validation | Every minute | `uv run --env-file .env python manage.py run_staged_resource_inspection_jobs --batch-size 10` |
| `expire_staged_resource_intakes` | Expired, unassigned staged intakes and their staging cleanup | At least hourly | `uv run --env-file .env python manage.py expire_staged_resource_intakes --batch-size 100` |
| `run_dataset_registration_jobs` | Published CSV, JSON, JSONL/NDJSON, and Parquet registration jobs | Worker process managed by the environment scheduler | `uv run --env-file .env python manage.py run_dataset_registration_jobs --once` |
| `run_workbook_ingestion_jobs` | Published XLSX workbook ingestion jobs | Worker process managed by the environment scheduler | `uv run --env-file .env python manage.py run_workbook_ingestion_jobs --once` |

The batch sizes shown are command defaults, not mandatory deployment values. Tune them through the
environment scheduler and Resource Registry settings according to available capacity. The two
long-running registration commands also support `--poll-interval-seconds`, `--batch-size`,
`--max-jobs`, and `--tenant-id`; use `--once` for a bounded local check.

## Expected states

| Area | Progress states | Terminal or operator-action states |
| --- | --- | --- |
| Staged intake | `PENDING_UPLOAD`, `COMPLETED`, `ANALYZING`, `READY_FOR_PUBLICATION`, `PUBLISHING` | `PUBLISHED`, `FAILED`, `CANCELLED`, `EXPIRED` |
| CSV, JSON, JSONL/NDJSON, or Parquet registration job | `submitted`, `queued`, `running` | `completed`, `failed`, `cancelled` |
| XLSX workbook ingestion job | `submitted`, `queued`, `running` | `completed`, `completed_with_skips`, `failed`, `cancelled` |

`COMPLETED` on an intake means browser upload completion was accepted; it does not mean the file
is validated or published. `completed_with_skips` means the workbook generated usable output but
one or more sheets were not ingested. Review the published result before treating it as complete.

## Diagnose a delayed item

1. Find the staged intake in Django Admin and record only its intake ID, status, `failure_code`,
   target environment, and timestamp.
2. If it is `PENDING_UPLOAD`, the browser upload was not completed. The operator can retry or
   cancel it from **Upload resource**; the inspection worker has no work to perform.
3. If it remains `COMPLETED` or `ANALYZING`, confirm that the inspection command is running in the
   same environment and review its sanitized worker output. Do not publish it manually.
4. If it is `READY_FOR_PUBLICATION`, select the intended Workspace destination in Admin. A
   dedicated Workspace is recommended for project- or team-specific data.
5. If a published CSV, JSON, JSONL/NDJSON, Parquet, or XLSX file remains pending, inspect its corresponding registration or workbook
   job and confirm that the matching worker is running.
6. If an item is `FAILED`, use its safe failure code and correct the source file or configuration.
   Create a new intake for a corrected file; do not attempt to publish an unverified intake.

## Safe recovery boundaries

- Re-running the inspection and expiry commands is safe; they claim bounded eligible work.
- Registration and workbook workers use durable jobs. Restarting their worker process is the
  normal recovery action after an infrastructure interruption.
- Do not mutate intake, job, publication, namespace, or storage records directly in the database.
- Do not delete staging blobs manually unless cleanup has been investigated through the intake
  lifecycle. The expiry and cancellation flows request exact cleanup.
- Escalate persistent failures with the sanitized identifiers above and the relevant worker name.

## Related documentation

- [Admin direct upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
- [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md)
- [Upload support and observability](RESOURCE_REGISTRY_UPLOAD_SUPPORT.md)
- [Resource Registry getting started](RESOURCE_REGISTRY_GETTING_STARTED.md)
