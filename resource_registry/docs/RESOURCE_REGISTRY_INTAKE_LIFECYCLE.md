# Resource Registry Intake Service and lifecycle

A staged intake is a private, temporary source file. It becomes a Dataset only after inspection and
an explicit publication to a destination. This reference explains what each intake status means and
the next permitted operator action.

## Supported files and transfer paths

The service accepts CSV (`.csv`), Excel (`.xlsx`), JSON (`.json`), JSON Lines (`.jsonl` and
`.ndjson`), and Parquet (`.parquet`) by default. NDJSON is reported as `JSONL`. Admission checks
both the configured extension allowlist and compatible MIME types. See
[file preparation](RESOURCE_REGISTRY_FILE_PREPARATION.md) and the
[API format table](RESOURCE_REGISTRY_INTAKES_API.md#supported-formats).

| Caller | Current transfer path | SAS exposed to browser |
| --- | --- | --- |
| Django Admin | Browser → authenticated backend blocks → Azure | No |
| Intake HTTP API | Client → authenticated backend blocks → Azure | No |
| SharePoint Registry Sync | Backend creates intake → PUT of CSV manifest using internal SAS → completion | No; SAS remains server-side |

Admin/API creation passes `issue_direct_authorization=False`. Each block is validated against the
server-derived manifest before transfer. Completion uses `commit_backend_blocks=True` to verify
and commit the blocks. `backend_blocks_v1` fixes blocks at 512 KiB (except the final remainder),
with one transfer per intake at a time and a 50000-block ceiling. Existing size and quota checks
remain in force. The backend reads at most one block plus one byte for validation.

SharePoint's `Embeddings/sharepoint/services/intake_client.py` calls this same service in-process,
retaining the default `issue_direct_authorization=True`; its manifest upload still uses
`requests.put(authorization.upload_url, ...)` and the legacy completion path. It does not invoke
the Admin/API block endpoints. Publication's Azure copy operation also retains an internal read
SAS. The backend-upload change did not remove these internal authorization paths.

## GSO-related deployment boundary

For the confirmed Storage remediation, keep `allowBlobPublicAccess=false` and
`publicNetworkAccess=Disabled`. Application replicas, SharePoint sync execution, and workers
must have private endpoint connectivity and correct DNS. The browser needs access to Unicorn AI,
not to Azure Blob. Adding Blob CORS cannot repair missing private connectivity. A localhost backend
requires approved private connectivity or local Azurite; the development `local` upload provider
does not implement backend block transfer.

These are the two controls confirmed by GSO, not a declaration that every GSO requirement is met.
The existing format inspection is not evidence of malware scanning or an approved external-partner
intake boundary. See [configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md).

## Lifecycle

```text
PENDING_UPLOAD --complete--> COMPLETED --inspection--> ANALYZING
     |                              ^                     |
     |                              | retryable failure    | inspection succeeds
     +--cancel--> CANCELLED          +---------------------+
                                                        READY_FOR_PUBLICATION
                                                                 |
                                                              publish
                                                                 v
                                                            PUBLISHING
                                                                 |
                                                              success
                                                                 v
                                                             PUBLISHED

Active, unpublished states can expire -> EXPIRED
Inspection can terminally fail -> FAILED
```

## Status reference

| Status | Meaning | Operator action |
| --- | --- | --- |
| `PENDING_UPLOAD` | Intake exists; uploaded bytes have not yet been committed and verified. | Upload/resume the same unchanged file through authenticated blocks, or cancel. |
| `COMPLETED` | Storage metadata was verified and an inspection job was created or re-queued. | Wait for the inspection worker. Do not publish. |
| `ANALYZING` | An inspection worker has claimed the intake. | Wait for completion; use the support runbook if it remains here unexpectedly. |
| `READY_FOR_PUBLICATION` | Inspection completed successfully; the file is eligible for a destination. | Select **Publish to destinations** and choose the correct Workspace or Organization destination. |
| `PUBLISHING` | The verified source is being copied and submitted for registration. | Wait; cancellation is not available. |
| `PUBLISHED` | At least one destination publication and durable registration submission exist. | Monitor CSV/JSON/JSONL/Parquet registration or XLSX ingestion until the Dataset is available. |
| `FAILED` | Inspection reached a terminal failure. | Use the safe failure code, correct the issue, and create a new intake. Do not publish. |
| `CANCELLED` | The intake was cancelled and staging cleanup was requested. | Create a new intake if the file is still needed. |
| `EXPIRED` | The temporary retention deadline elapsed before completion of publication. | Create a new intake; staging cleanup is requested. |

## Completion and inspection

For backend uploads, completion first checks every expected block length and commits the blocks in
file order. It then verifies object identity, declared size, optional ETag, content type, and XLSX or
Parquet header signatures where applicable. Missing blocks return `INTAKE_UPLOAD_INCOMPLETE` while
leaving the intake pending. Repeating successful completion returns accepted state without adding
another inspection job.

Inspection validates JSON as a nonempty root array of objects, JSONL/NDJSON as one object per line,
and Parquet by schema and batch decoding. It calculates the file SHA-256 and checks any declared
checksum. Completion alone does not verify a declared SHA-256 or make a Dataset available.

Inspection runs asynchronously. A transient inspection failure returns the intake to `COMPLETED` for
retry. After the configured retry limit, the intake becomes `FAILED` with a safe
`INTAKE_INSPECTION_FAILED` code. Deterministic JSON, JSONL, and Parquet validation failures do not
consume retries: the intake becomes `FAILED` immediately and the intake and inspection job retain the safe validation
code, such as `JSON_ROOT_MUST_BE_NONEMPTY_ARRAY`, `JSONL_BLANK_LINE`, or `PARQUET_CONTENT_INVALID`.
Correct the source file and create a new intake for these terminal failures. A successful inspection records a safe summary and changes the intake
to `READY_FOR_PUBLICATION`.

## Publication

Publication is permitted only from `READY_FOR_PUBLICATION` (or is safely replayed when the intake is
already published to the same destination). It copies the verified source into the selected
destination and creates a durable registration submission. A failed publication attempt restores an
eligible intake to `READY_FOR_PUBLICATION`; it does not make unverified content publishable.

`PUBLISHED` means publication was accepted, not that the resulting Dataset is immediately ready.
The CSV/JSON/JSONL/Parquet registration or XLSX workbook-ingestion worker performs the remaining work.

## Expiration and cleanup

`PENDING_UPLOAD`, `COMPLETED`, `ANALYZING`, `READY_FOR_PUBLICATION`, and `PUBLISHING` are temporary
unpublished states. The expiry worker marks an intake `EXPIRED` after its configured intake-retention
deadline and requests cleanup of the staging object. `CANCELLED`, `EXPIRED`, and `PUBLISHED` intakes
with a pending cleanup requirement are retried by the expiry command.

The default intake retention is 24 hours, but `RESOURCE_REGISTRY_INTAKE_TTL_SECONDS` is environment
configuration and may differ. Backend block uploads are bounded by intake state/retention and
current caller authentication; there is no browser SAS renewal. The historical Admin `renew/` action
now validates the pending intake and returns backend instructions. Legacy internal SAS expiry is
shorter and remains independent of intake retention.

## Permissions

| Operation | Current authorization |
| --- | --- |
| Django Admin upload and **Publish to destinations** | Active staff superuser or staff with `resource_registry.manage_stagedresourceintake`. This is global intake authority across uploaders. Publication also checks destination readiness. The registration submission used by Admin publication currently requires a superuser. |
| Django Admin **Remove destination assignments** | Active staff superuser or holder of `resource_registry.manage_stagedresourceintake`; active staff in `unicornai-admin`; or active staff in `unicornai-scoped-admin` on the trusted Organization host. Scoped admins see only intakes with active, removable publications in that Organization and may select only its authorized destinations. |
| HTTP API private intake operations | Authenticated, active uploader of the intake. |
| HTTP API `publish/` | For a new publication, the uploader must also be a superuser, `unicornai-admin`, or `unicornai-scoped-admin`, hold `resource_registry.create_large_file_upload` and `resource_registry.submit_upload_registration`, and be authorized for the destination on the host-derived Organization. |

Removing an assignment does not delete the intake, Namespace, or storage objects. If a worker is
still processing, removal can remain pending until it stops; a Dataset with no remaining active
assignment is archived. See [publishing and removal](RESOURCE_REGISTRY_DATASET_PUBLICATION.md).
There is no dedicated external-company role or upload-only token scope in this change. External
access must be reviewed separately.

## Related documentation

- [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
- [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md)
- [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md)
- [Upload support and observability](RESOURCE_REGISTRY_UPLOAD_SUPPORT.md)
- [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md)
- [Private-storage configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md)
