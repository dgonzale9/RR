# Resource Registry storage architecture

Dataset Management adds private preparation candidates, explicit current-version selection,
external-source provenance and controlled cleanup. The [cleanup runbook](RESOURCE_CLEANUP.md)
is the operational contract for permanent deletion: inventory, shared-reference protection,
renewable read/write leases, retention, confirmation and resumable execution. Low-level
artifact lifecycle methods below are not a replacement for that reviewed workflow.

## Purpose

Resource Registry separates relational metadata from physical file content. Django models store
Dataset identity, versions, artifact references, checksums, lifecycle state, policy metadata, and
job evidence. Configured storage providers hold source files, canonical artifacts, and generated
export files.

Full source files and generated binary files are stored outside the database. Query results,
inspection summaries, and other bounded structured metadata can still be persisted as JSON.

Staged-intake uploads from Django Admin and the Intake Service API transfer file bytes through
the Unicorn AI backend. The separate legacy upload API and some internal callers retain direct
storage authorization. These paths have different transfer contracts.

## Storage layers

Paths below are relative to `resource_registry/`.

| Layer | Location | Responsibility |
| --- | --- | --- |
| Storage contract | [storage/base.py](../storage/base.py) | Provider-neutral operations and object metadata. |
| Storage factory | [storage/factory.py](../storage/factory.py) | Construct a configured provider for an already authorized operation. |
| Local provider | [storage/local.py](../storage/local.py) | Filesystem-backed development or local-reference storage. |
| Azure Blob provider | [storage/azure_blob.py](../storage/azure_blob.py) | Server-side upload, download, metadata, existence checks, and deletion. |
| Read adapters | [storage/read_adapters.py](../storage/read_adapters.py) | Materialize an authorized URI into a controlled temporary local file. |
| Upload providers and policy | [uploads/](../uploads/) | Format admission, backend block transfer, legacy direct authorization, and upload metadata. |
| Intake service | [intake/services.py](../intake/services.py) | Private staging, block authorization, completion, publication, cancellation, and expiry. |
| Intake inspection | [intake/inspection_service.py](../intake/inspection_service.py) | Asynchronous source validation and SHA-256 verification. |
| Dataset materialization | [services/csv_upload_materialization_service.py](../services/csv_upload_materialization_service.py) | Resolve trusted upload provenance for CSV, JSON, JSONL/NDJSON, and Parquet. |
| Workbook materialization | [services/workbook_upload_materialization_service.py](../services/workbook_upload_materialization_service.py) | Resolve trusted XLSX upload provenance and manage temporary workbooks. |
| Artifact lifecycle | [services/registry/artifact_lifecycle_service.py](../services/registry/artifact_lifecycle_service.py) | Artifact state changes, physical deletion, and orphan cleanup operations. |

The storage abstraction does not grant access or let callers select arbitrary storage coordinates.
Authorization, provider policy, and destination selection belong to the calling services.

## Provider capabilities

The following table describes the artifact `StorageProvider` contract, not the intake transfer
protocol.

| Operation | Local provider | Azure Blob provider |
| --- | --- | --- |
| Upload from a server file | Supported | Supported |
| Download to a server file | Supported | Supported |
| Delete | Supported; missing files are a no-op | Supported; missing blobs are a no-op |
| Existence check | Supported | Supported |
| Head/metadata | Supported, including computed SHA-256 | Supported through blob properties; provider checksum is not necessarily SHA-256 |
| Canonical URI | `local://...` | `azure://<container>/<path>` |
| Object listing | Not exposed by the contract | Not exposed by the contract |
| Signed URL generation | Not exposed by the contract | Handled separately by `uploads/` where needed |

Artifact storage uses `RESOURCE_REGISTRY_STORAGE_PROVIDER`. Its application default is
`azure_blob`. Local storage uses `RESOURCE_REGISTRY_LOCAL_STORAGE_DIR`, defaulting to
`BASE_DIR/resource_registry_storage`.

Intake transfer uses the separate `RESOURCE_REGISTRY_UPLOAD_PROVIDER` setting. Azure and Azurite
support backend block transfer. The development-only `local` upload provider also supports
backend blocks: it stages server-owned fragments, verifies their sizes and assembles the file.
Use `RESOURCE_REGISTRY_STORAGE_PROVIDER=local` and `RESOURCE_REGISTRY_UPLOAD_PROVIDER=local`
with a writable `RESOURCE_REGISTRY_LOCAL_STORAGE_DIR` for an isolated local-file walkthrough.

## Supported intake formats

These are the defaults; environment allowlists can restrict extensions and MIME types. The file
extension and declared MIME type must agree.

| Source | Extensions | Response format | Accepted declared MIME types |
| --- | --- | --- | --- |
| CSV | `.csv` | `CSV` | `text/csv`, `application/csv`, `text/plain`, `application/vnd.ms-excel` |
| Excel workbook | `.xlsx` | `XLSX` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| JSON array | `.json` | `JSON` | `application/json` |
| JSON Lines | `.jsonl`, `.ndjson` | `JSONL` | `application/x-ndjson`, `application/jsonl`, `application/json`, `text/plain` |
| Parquet | `.parquet` | `PARQUET` | `application/vnd.apache.parquet`, `application/octet-stream` |

JSON requires a nonempty root array of objects. JSONL/NDJSON requires one object per physical line;
blank lines, duplicate keys, and non-finite numbers are rejected. Parquet must be one complete,
unencrypted file; partition directories and archives are unsupported. Inspection decodes its
schema and batches rather than relying only on its header or footer.

The upload limit defaults to 10 GiB. Separate processing limits still apply: XLSX defaults to
100 MiB per file, 100000 rows and 1000 columns per sheet; JSONL defaults to 64 MiB per record.
These processing limits are independent of the HTTP transfer block size. See
[dataset formats](DATASET_FORMATS.md) for JSONL/Parquet preparation and rollout details.

## Staged-intake upload flow

```text
Browser or API client
    -> authenticated Unicorn AI block endpoint
    -> Azure Blob Storage through private connectivity
    -> completion and asynchronous inspection
    -> authorized publication and dataset/workbook registration
```

1. Create an intake with filename, original file MIME type, size, optional SHA-256 declaration,
   and `Idempotency-Key`. The service validates admission and creates a private intake owned by
   the uploader, initially without an Organization or Workspace destination.
2. The response returns `upload.method = backend_blocks_v1` and an application-relative block URL.
   It contains no Azure upload URL or SAS.
3. Send zero-based binary blocks to
   `PUT /api/v1/resource-registry/intakes/{intake_id}/blocks/{block_index}/`.
   Each block is exactly 524288 bytes, except the final block, which matches the remaining
   declared size. Use `Content-Type: application/octet-stream`, regardless of source format.
4. The backend checks ownership, pending state, expiry, block index, and length. It reads at most
   one block plus one byte for validation, then stages the block using server credentials.
   The API returns `204 No Content`; the Admin equivalent returns a JSON acknowledgment.
5. POST `{}` to `intakes/{intake_id}/complete/`. The backend verifies the expected block manifest,
   commits blocks in file order with the original file MIME type, and verifies object metadata.
   Completion checks size, optional ETag, content type, and relevant file signatures.
6. Inspection calculates SHA-256, checks a declared checksum, and validates source content.
   Completion alone does not verify a declared SHA-256 or make the Dataset available.
7. After `READY_FOR_PUBLICATION`, create a dataset-change proposal for an authorized destination
   and publish after preparation/review. This records publication and selects the current version
   atomically. Only the retained legacy intake publish path submits registration/workbook jobs.

The corresponding API routes also exist under `/auth/api/v1/resource-registry/`. Every block
requires authentication; session requests require CSRF. The legacy staged-intake Admin enforces
global operator permission; the new Dataset Management Admin uses destination creation/edit
authority. The private HTTP intake API enforces uploader ownership. Publication applies its
destination authorization checks.

The protocol permits at most 50000 blocks and advertises one concurrent transfer per intake.
Row locking serializes transfers against completion and cancellation. Retry the same block index
with the same unchanged file; deterministic block IDs support retransmission. Intake expiry and
cleanup still apply. There is no browser SAS renewal in this flow.

See [backend upload protocol](BACKEND_INTAKE_UPLOAD.md) for retry and failure details.

## Legacy and internal SAS paths

The backend-upload change did not remove all SAS use:

| Path | Current behavior |
| --- | --- |
| Admin and Intake Service HTTP API | Authenticated backend blocks; no client-facing SAS. |
| Separate `/uploads/` API | Retains its direct-upload authorization contract. |
| SharePoint Registry Sync | Calls the intake service in-process, obtains direct authorization, and uploads its CSV manifest with a backend `requests.put`. |
| Intake publication copy | Retains an internal read SAS for Azure copy operations. |

Legacy direct-upload creation validates metadata and authorization, returns short-lived access
to one server-selected object, and verifies provider metadata at completion. A direct-upload
client must itself have permitted network connectivity to Storage; a SAS does not bypass the
network boundary. This makes the legacy path unsuitable for an internet-only client accessing
Storage with public network access disabled.

Keep SAS credentials out of persistent artifact references, logs, and assistant payloads. A legacy
client uses only the provider-required headers when contacting its upload URL; it must not forward
the application's authentication header to Azure. Current intake clients authenticate their
requests to Unicorn AI instead.

## Private Azure configuration and GSO

The confirmed remediation requires both Storage controls below:

| Control | Required value or behavior |
| --- | --- |
| Anonymous Blob access (`allowBlobPublicAccess`) | `false` |
| Public Storage network access (`publicNetworkAccess`) | `Disabled` |
| Blob private endpoint | Reachable through approved routing and DNS from application and worker runtimes |
| Storage authentication | Valid server-side credentials; private networking does not replace authorization |

Azure configuration currently uses `AZ_STORAGE_ACCOUNT_NAME`, `AZ_STORAGE_ACCOUNT_KEY`,
`AZ_STORAGE_MEDIA_CONTAINER`, and the derived `AZURE_CONNECTION_STRING`. This upload change did
not add managed identity authentication. Credentials remain server-side.

Application replicas, inspection/registration workers, publication operations, and SharePoint sync
must have the required private connectivity. Localhost against private Azure needs the same approved
network access and DNS; local Azurite is an alternative for development. `AZ_CUSTOM_DOMAIN` is the
existing Azurite endpoint override, not a replacement for private DNS configuration.

Azure browser CORS is unnecessary for the same-origin backend intake upload. Application session
authentication and CSRF protections remain required. A missing private route, incorrect DNS, or
invalid credential can still cause backend failures; an Azure `403 AuthorizationFailure` can surface
as `503 UPLOAD_PROVIDER_TRANSFER_FAILED`. Diagnose the backend cause without reopening public access.

Deploy the backend and updated Admin JavaScript together. Restart checkpoints from the old browser
direct-upload flow. API clients must follow the `backend_blocks_v1` descriptor.

These changes address the two confirmed Storage controls. They do not establish full GSO compliance,
malware scanning, or an approved external-company access policy. External delivery requires an
approved application identity and authorization design; global Admin access is not an upload-only
partner permission.

## Dataset storage modes

Registration supports two source-storage approaches:

- **Reference-only:** retain an authorized source reference without copying the original into
  managed storage.
- **Managed upload:** copy the source through the selected provider to a service-selected
  destination, then persist provider metadata as a `DatasetArtifact`.

Dataset artifacts attach to a specific `DatasetVersion`. Managed destinations are derived from
trusted Dataset, tenant, version, and format information. Callers do not supply arbitrary storage
coordinates. Source references and canonical artifacts have separate roles; preserving a source
reference does not imply every query runs directly against that source.

## Worker materialization

Registration workers resolve this provenance chain before reading upload bytes:

```text
Job -> RegistrationSubmission -> completed LargeFileUploadSession -> provider/object identity
```

`CsvUploadMaterializationService` retains its historical name but supports CSV, JSON, JSONL/NDJSON,
and Parquet. `WorkbookUploadMaterializationService` handles XLSX. They use the storage factory and
read adapters to download the exact object to a server-created temporary file, validate persisted
metadata and source evidence, and preserve canonical provider references.

The temporary materialization path is not used as the persistent artifact identity. Context-managed
cleanup covers normal worker completion and exceptions; materialization failures also remove
temporary files. Workers may materialize a complete file on disk, even though intake HTTP transfer
uses bounded blocks. Provision worker disk capacity separately from upload request limits.

## Artifact and generated-file storage

Physical storage holds source Dataset artifacts, canonical Parquet artifacts, and generated CSV,
XLSX, PDF, or PPTX exports. JSON and JSONL are materialized as canonical Parquet before querying;
native Parquet preserves its existing types and bytes.

Query runs, analysis results, chart specifications, and export plans are persisted as bounded JSON
metadata. [ArtifactGenerationService](../services/artifact_generation_service.py) executes an authorized
export plan, creates a temporary file, uploads it through the storage factory, persists
`DatasetGeneratedArtifact` metadata, and removes the temporary file.

## Artifact lifecycle

Artifact metadata and physical bytes have separate lifecycle operations. A typical progression is:

```text
ACTIVE -> DEPRECATED -> DELETED_REFERENCE -> PHYSICAL_DELETED
```

This diagram is not a mandatory transition validator. The lifecycle service exposes explicit
deprecation, restoration, reference-deletion, and physical-deletion operations. Reference deletion
does not itself delete provider bytes. Physical deletion explicitly calls the provider. When a
managed write succeeds but persistence fails, callers can invoke `cleanup_orphan_upload` to remove
the orphan; this is a cleanup operation, not a provider-wide listing or automatic garbage collector.

Intake cancellation/expiry cleanup is separate from Dataset artifact lifecycle. Retention policy
and authorization remain responsibilities of the owning workflows.

## Storage safety and operational guidance

- Check tenant, Dataset, or intake ownership at the appropriate service boundary before reads or
  mutations. Constructing a provider does not authorize an operation.
- Apply provider allowlists, format admission, size limits, checksum checks, and metadata validation
  required by the workflow. Provider-reported checksums must not be assumed to be SHA-256.
- Keep URI parsing and path validation in the provider/read-adapter boundary. Persistent artifact
  references must not contain SAS tokens or other temporary credentials.
- Keep temporary files in controlled locations, remove them after use, and account for failed
  provider writes and failed database persistence separately.
- Expose sanitized application errors. Support evidence should contain safe IDs, timestamps, status
  codes, and approved request IDs; omit credentials, signed URLs, cookies, and file contents.
- Extend provider-neutral contracts only for operations needed by current workflows. Preserve
  existing artifact URIs, UUIDs, checksums, and lifecycle semantics.
- Test provider behavior and cleanup with fakes or simulated providers without live credentials.
  Separately verify private connectivity from every runtime during deployment.

Storage providers operate on bytes and object metadata. They do not own Dataset identity,
versioning, lineage, authorization, retention policy, or capability semantics.
