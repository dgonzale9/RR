# Resource Registry private-storage upload configuration

This guide covers the current backend upload flow for Admin and the staged-intake API. Its filename
is retained for existing links. Configure each environment independently; do not put credentials or
real SAS URLs in this document.

## GSO-confirmed Storage controls

| Control | Required value for this remediation | Owner |
| --- | --- | --- |
| Allow Blob anonymous access (`allowBlobPublicAccess`) | `false` | Storage/platform operations |
| Public network access (`publicNetworkAccess`) | `Disabled` | Storage/platform operations |
| Blob private endpoint, routing, and DNS | Reachable from application replicas and workers | Network/platform operations |
| Authenticated application access and CSRF | Retain current authorization and session protections | Application/platform operations |

The application change does not configure Azure networking automatically. It moves intake transfer
from the browser to the backend. It addresses the two confirmed controls and is not a blanket GSO
certification or approval for external-company access.

Use the usual account Blob hostname; DNS must resolve it through the approved private endpoint
from each application/worker network. Do not replace the connection hostname with a `privatelink`
URL. Local development against real private Azure needs approved network access and private DNS;
otherwise use Azurite. See [Azure private endpoints](https://learn.microsoft.com/en-us/azure/storage/common/storage-private-endpoints)
and [disabling anonymous Blob access](https://learn.microsoft.com/en-us/azure/storage/blobs/anonymous-read-access-prevent).

## Server storage configuration

| Variable | Purpose | Secret |
| --- | --- | --- |
| `AZ_STORAGE_ACCOUNT_NAME` | Storage account identity | No |
| `AZ_STORAGE_MEDIA_CONTAINER` | Container holding staged objects | No |
| `AZ_STORAGE_ACCOUNT_KEY` | Current implementation's backend storage credential | Yes |
| `AZ_CUSTOM_DOMAIN` | Optional local Azurite endpoint override, not a private-DNS setting | No |

The derived `AZURE_CONNECTION_STRING` and account key stay server-side. The intake HTTP endpoints
return application block URLs instead of storage credentials. The current implementation uses the
configured account credential; this change did not implement managed identity authentication.

Azure Blob browser CORS is not required by the current intake flow. Django CSRF origin settings
still govern browser requests to the application. A separate cross-origin client would require
an approved application CORS/access policy; no wildcard Blob CORS rule solves a network denial.

## Protocol and policy limits

| Setting or protocol field | Default/value | Applies to |
| --- | --- | --- |
| `RESOURCE_REGISTRY_UPLOAD_PROVIDER` | `azure_blob` | Intake storage adapter. Azure and Azurite support backend blocks; `local` does not. |
| `RESOURCE_REGISTRY_UPLOAD_MAX_FILE_SIZE_BYTES` | 10 GiB | File admission; stricter downstream format limits still apply. |
| `RESOURCE_REGISTRY_UPLOAD_MAX_PENDING_PER_USER` | 5 | Per-user active staging quota. |
| `RESOURCE_REGISTRY_INTAKE_TTL_SECONDS` | 86400; minimum 300 | Unpublished intake retention. |
| `backend_blocks_v1` block size | 524288 bytes (512 KiB) | Fixed protocol size, with an exact shorter final block. |
| `backend_blocks_v1` parallel transfers | 1 per intake | Browser descriptor; intake locking serializes server mutations. |
| `backend_blocks_v1` maximum blocks | 50000 | Protocol ceiling, independent of the configured file-size limit. |
| `RESOURCE_REGISTRY_UPLOAD_PROVIDER_CONNECT_TIMEOUT_SECONDS` | 10 | Azure upload-provider connection timeout. |
| `RESOURCE_REGISTRY_UPLOAD_PROVIDER_READ_TIMEOUT_SECONDS` | 30 | Azure upload-provider read timeout. |
| `RESOURCE_REGISTRY_UPLOAD_PROVIDER_RETRY_TOTAL` | 3; clamped to 0–5 | Azure upload-provider retry budget. |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_EXTENSIONS` | `xlsx,csv,json,jsonl,ndjson,parquet` | Extension admission; NDJSON normalizes to JSONL. |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_XLSX` | XLSX MIME type | XLSX metadata validation. |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_CSV` | `text/csv,application/csv,text/plain,application/vnd.ms-excel` | CSV metadata validation. |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_JSON` | `application/json` | JSON metadata validation. |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_JSONL` | `application/x-ndjson,application/jsonl,application/json,text/plain` | JSONL/NDJSON metadata validation. |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_PARQUET` | `application/vnd.apache.parquet,application/octet-stream` | Parquet metadata validation. |
| `RESOURCE_REGISTRY_JSON_MAX_RECORD_BYTES` | 64 MiB | JSONL record processing limit; not the HTTP block limit. |

Binary block requests use `Content-Type: application/octet-stream`. The original file's declared
MIME type is validated at creation and applied by the backend when committing the blob.
Ingress must accept 512 KiB binary PUT bodies and allow enough time for the configured provider
retry budget. The application reads at most one block plus one byte for validation, not a whole file.
The fixed block protocol is versioned; legacy upload tuning variables do not resize these blocks.

## Legacy/internal SAS paths

These variables still apply to legacy direct-upload authorizations, including internal SharePoint
manifest submission and the separate `/uploads/` API:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RESOURCE_REGISTRY_UPLOAD_AUTHORIZATION_TTL_SECONDS` | 3600; clamped to 60–3600 | Direct SAS authorization lifetime. |
| `RESOURCE_REGISTRY_UPLOAD_RECOMMENDED_CHUNK_SIZE_BYTES` | 16 MiB | Legacy descriptor recommendation, not backend intake block size. |
| `RESOURCE_REGISTRY_UPLOAD_MAX_PARALLEL_CHUNKS` | 4 | Legacy descriptor recommendation, not backend intake concurrency. |

SharePoint Registry Sync still creates an intake internally with direct authorization enabled and
uploads the CSV manifest from the backend using its SAS. Publication copies retain a server-side
read SAS. No statement here implies all SAS use was removed. SAS authorizes an operation; it does
not bypass Storage network restrictions. See the [service reference](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md).

## Processing and rollout

XLSX processing defaults remain 100 MiB per file, 100000 rows, and 1000 columns per sheet
(`RESOURCE_REGISTRY_EXCEL_MAX_FILE_BYTES`, `RESOURCE_REGISTRY_EXCEL_MAX_ROWS`,
`RESOURCE_REGISTRY_EXCEL_MAX_COLUMNS`). CSV, JSON, JSONL/NDJSON, and Parquet use the dataset
registration worker; XLSX uses workbook ingestion. Schedule inspection and expiry as documented
in [worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md).

Deploy backend and Admin JavaScript together. Restart old direct-upload browser checkpoints.
API integrations must understand `backend_blocks_v1`. Environments with explicit extension
allowlists must enable JSONL/NDJSON and Parquet deliberately; see
[format rollout](DATASET_FORMATS.md) for the existing format migration and policies.

## Per-environment verification

1. Confirm both GSO Storage controls remain disabled and the intended account/container is selected.
2. Verify private DNS and connectivity from every application/worker network, not just a browser.
3. Create a harmless intake. Its descriptor must reference the application and contain no SAS.
4. Transfer binary blocks and complete. Browser traffic should not go directly to Blob Storage.
5. Check `READY_FOR_PUBLICATION`, publish to an authorized test destination, and verify worker output.
6. Check an unauthorized caller, a wrong-sized block, a retry, and cancellation.

A `503 UPLOAD_PROVIDER_TRANSFER_FAILED` can represent an underlying Azure `403 AuthorizationFailure`.
Check safe backend diagnostics and private connectivity; do not re-enable public access as a fix.

## Related documentation

- [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
- [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md)
- [Preparing supported files](RESOURCE_REGISTRY_FILE_PREPARATION.md)
- [Upload support](RESOURCE_REGISTRY_UPLOAD_SUPPORT.md)
