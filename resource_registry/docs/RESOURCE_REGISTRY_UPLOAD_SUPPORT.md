# Resource Registry upload support and observability

Use this runbook to triage a backend-upload intake or its publication processing without exposing
customer data or Azure credentials. It covers the signals currently available in Django Admin,
durable job records, and worker output; it does not establish a new monitoring platform.

## Safe support evidence

Collect only the following before escalating:

| Item | Source | Safe to include in a ticket |
| --- | --- | --- |
| Environment and observed time | Operator report | Yes |
| Intake ID, status, and `failure_code` | Staged intake Admin detail | Yes |
| Inspection job status, retry count, and `error_code` | Approved read-only operational access to durable jobs | Yes |
| Publication destination and registration/job status | Intake publication information | Yes |
| Registration or ingestion job ID, status, `error_code`, and `execution_ms` | Approved read-only operational access to durable jobs | Yes |
| Application request path, method, HTTP status, and safe error code | Browser developer tools | Yes; omit headers containing credentials |
| Azure request ID, when available | Approved backend diagnostics | Yes; the browser no longer contacts Azure for intake upload |

Never include a SAS URL or token, Azure storage account key, connection string, blob URL/object key,
raw file contents, preview rows, or user session cookies in a ticket or log search.

## Where to inspect

1. Open **Resource Registry → Staged resource intakes** in Django Admin and find the intake by ID
   or filename. The list supports filtering by status, cleanup requirement, and file format.
2. On the read-only intake detail, review status, declared and actual size, content type, checksum
   status, inspection summary, `failure_code`, expiry, and destination publications.
3. Use the approved read-only operational access for durable inspection, CSV/JSON/JSONL/Parquet registration, or XLSX
   workbook-ingestion records. Inspect only their status, retry count, timestamps, and safe errors.
4. Review output from the matching command in the same environment. CSV worker logs include the
   registration job ID, tenant ID, final job status, error code, and execution duration. Worker
   command output includes the number of claimed and processed jobs.

## Triage by stage

| Symptom | Evidence to inspect | First safe action |
| --- | --- | --- |
| Browser upload fails or stalls | Application request status, safe code, intake status and expiry | Retry the same block of a pending intake with the unchanged file. Check authentication/CSRF for 401/403. |
| Block PUT returns 503 | Safe provider code and approved backend diagnostics | Check backend private routing, DNS, and storage credentials; an Azure 403 can surface as application 503. |
| Completion reports `INTAKE_UPLOAD_INCOMPLETE` | Expected block count and acknowledged indexes | Retransmit missing blocks, then complete again. |
| Request returns 413 | Application/ingress request limit and actual block size | Send exactly 512 KiB blocks, except the final remainder; check ingress body limits. |
| Browser still sends requests directly to Azure | Deployed Admin JavaScript and upload descriptor | Deploy matching backend and JavaScript, refresh, and restart old direct-upload checkpoints. |
| Intake stays `PENDING_UPLOAD` | Intake expiry and browser completion state | Complete, retry, or cancel the upload. Inspection cannot start yet. |
| Intake stays `COMPLETED` or `ANALYZING` | Inspection job status, retry count, error code, scheduler output | Confirm the inspection worker ran in that environment. Do not publish manually. |
| Intake is `FAILED` or `EXPIRED` | `failure_code`, expiry, and inspection-job error code | Correct the source or condition, then create a new intake when necessary. |
| Intake is ready but Dataset is unavailable | Destination publication and registration/ingestion job status | Confirm the appropriate CSV/JSON/JSONL/Parquet or XLSX worker is running. |
| Registration or ingestion fails | Job `error_code`, `execution_ms`, sanitized worker output | Correct the source/configuration indicated by the safe code; follow the worker recovery boundary. |
| XLSX completes with skips | Workbook job result and skipped-sheet information | Confirm the remaining datasets are sufficient or upload a corrected workbook. |

## Expected worker signals

The following existing signals are suitable for scheduler health checks and log-based monitoring:

- Inspection command output: completed staged inspection count.
- Expiry command output: expired staged intake count.
- CSV/JSON/JSONL/Parquet worker output: claimed and processed job counts; structured processing logs include job ID,
  tenant ID, job status, error code, and execution duration.
- XLSX worker output: claimed and processed job counts.
- Durable records: intake, inspection, CSV/JSON/JSONL/Parquet registration, and workbook ingestion statuses, retry
  counts, timestamps, and safe error codes.

There is no separate staged-intake metrics emitter in the current implementation. Use the durable
records and scheduler/worker output as the source of truth until a dedicated observability contract
is introduced.

## Suggested alerts

Configure these in the environment's existing monitoring system if they are not already covered:

- No successful inspection-worker execution within the expected schedule window.
- Growing age or count of `COMPLETED`, `ANALYZING`, `submitted`, `queued`, or `running` work.
- Any terminal `FAILED` intake or registration/ingestion job above the team's agreed threshold.
- Repeated cleanup failures or a growing count of `cleanup_required` intakes.
- Repeated backend storage transfer failures or unavailable-provider errors.

Alert thresholds and recipients are deployment-owned. They must be configured independently for
development, sandbox, and production; this document does not prescribe a shared monitoring endpoint.

Keep anonymous Blob access and public Storage network access disabled. Blob browser CORS is not
required for backend intake uploads. For localhost against private Azure, verify approved private
connectivity and DNS from the backend, or use Azurite. The development `local` upload provider
now supports backend block transfer; configure a writable local storage root for isolated tests. Internal SharePoint SAS uploads still require private connectivity;
see [configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md).

## Escalation package

When the first safe action does not resolve the issue, include:

```text
Environment:
Observed at (with timezone):
Intake ID:
Intake status:
Failure code:
Inspection job status / retry count / error code:
Publication destination:
Registration or workbook job ID / status / error code:
Worker command and last observed output summary:
Application request path / method / HTTP status / safe error code:
Azure request ID from approved backend diagnostics, if available:
```

## Related documentation

- [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
- [Private-storage upload configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md)
- [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md)
- [Preparing supported files](RESOURCE_REGISTRY_FILE_PREPARATION.md)
- [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md)
