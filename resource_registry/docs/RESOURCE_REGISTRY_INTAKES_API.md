# Resource Registry Intake Service API

The staged-intake API receives CSV, XLSX, JSON, JSONL/NDJSON, and Parquet through the Unicorn AI
backend. Creation returns an authenticated application upload descriptor, not an Azure SAS URL.
The backend transfers the file to private Azure storage, verifies completion, and queues inspection.
Publication to an authorized Organization or Workspace is a separate operation.

```text
Client -> authenticated Unicorn AI API -> Azure Blob private endpoint
```

## Authentication and scope

The routes are mounted under both `/api/v1/resource-registry/` and
`/auth/api/v1/resource-registry/`. Current DRF configuration supports TokenAuthentication and
SessionAuthentication for these views; the prefix alone does not restrict the authentication type.
Token clients use `Authorization: Token <token>`. Session clients send their session cookie and a
valid CSRF token for unsafe methods, including binary PUT. Use HTTPS outside local development.

An intake is private to its uploader and initially has no Organization/Workspace destination.
Creation requires an authenticated active user; reads and mutations resolve the uploader's intake.
Publication additionally validates the trusted host-derived Organization and destination permissions.
For a new publication, the uploader must be a superuser or belong to `unicornai-admin` or
`unicornai-scoped-admin`, and must hold both `resource_registry.create_large_file_upload` and
`resource_registry.submit_upload_registration` (superusers satisfy Django permission checks).
Workspace publication must pass the selected Workspace's mutation admission check; Organization
publication must pass Organization namespace admission. Group membership alone is insufficient.
For Workspace publication, `workspace_id` may be omitted: the service derives the destination
from the active Workspace resolved from `X-Workspace` or the Organization default. A supplied
`workspace_id` is checked against that active Workspace when current scope selection is enforced.
The private create, block, read, complete, and cancel operations remain uploader-scoped.
The Django Admin upload/publish flow has a separate, global operator boundary. Its **Remove
destination assignments** action also admits scoped administrators for authorized destinations;
there is no intake removal route in this HTTP API. Do not grant global intake management merely
to let an external company deliver files. Authentication here is not a dedicated partner or
upload-only credential scope. External partner exposure requires its own approved access design.

## Supported formats

These are the application defaults. An environment can restrict extensions and accepted MIME types.
The `content_type` below belongs to the original file metadata supplied at creation.

| Source | Extensions | `file_format` | Accepted declared MIME types by default |
| --- | --- | --- | --- |
| CSV | `.csv` | `CSV` | `text/csv`, `application/csv`, `text/plain`, `application/vnd.ms-excel` |
| Excel workbook | `.xlsx` | `XLSX` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| JSON array | `.json` | `JSON` | `application/json` |
| JSON Lines | `.jsonl`, `.ndjson` | `JSONL` | `application/x-ndjson`, `application/jsonl`, `application/json`, `text/plain` |
| Parquet | `.parquet` | `PARQUET` | `application/vnd.apache.parquet`, `application/octet-stream` |

NDJSON normalizes to JSONL. JSON requires a nonempty root array of objects; JSONL requires one object
per physical line. Parquet must be one complete, unencrypted file. Renaming an unsupported file does
not convert it. See [file preparation](RESOURCE_REGISTRY_FILE_PREPARATION.md) for validation details.

## Routes and request bodies

The following paths are relative to either API prefix:

| Method and route | Purpose | Request body |
| --- | --- | --- |
| `POST intakes/` | Create intake and backend upload descriptor | JSON: `filename`, `content_type`, `size_bytes`; optional paired `checksum_algorithm: "sha256"` and 64-character hex `checksum` |
| `PUT intakes/{intake_id}/blocks/{block_index}/` | Transfer one bounded block through the backend | Raw bytes with `Content-Type: application/octet-stream` |
| `GET intakes/{intake_id}/` | Read the uploader's sanitized state | None |
| `POST intakes/{intake_id}/complete/` | Commit validated blocks, verify object, queue inspection | JSON `{}`; optional `etag` retained for compatibility |
| `POST intakes/{intake_id}/cancel/` | Cancel and request exact-object cleanup | No fields required |
| `POST intakes/{intake_id}/publish/` | Publish inspected intake to an authorized destination | JSON: `visibility_scope: "WORKSPACE"` with optional `workspace_id`, or `visibility_scope: "ORGANIZATION"` without `workspace_id` |

Creation requires `Idempotency-Key`, 1–128 characters matching `[A-Za-z0-9._:-]+`. Reuse it only for
the same file metadata. JSON serializers reject undeclared fields. Clients never select the storage
account, object key, or Azure block ID. Use `application/json` for JSON bodies; binary PUT
uses neither JSON nor multipart encoding.

The publication endpoint accepts `X-Registry-Scope: workspace|organization|explicit` for callers
using the current scope contract. `workspace` uses the active Workspace; `organization` selects
the host Organization namespace; `explicit` requires an authorized destination selector such as
`X-Tenant-ID`. Without that header, `RESOURCE_REGISTRY_SCOPE_POLICY` defaults to `observe` and
keeps the legacy destination selection while logging scope discrepancies. In `strict`, the current
scope contract is enforced without an opt-in header. An omitted Workspace ID is resolved through
the current contract in every policy mode. Scope selection does not replace publication permission
checks. Private staging operations do not select a publication destination.

## Create, transfer, and complete

Example creation body for an eight-byte CSV (`code\nA1\n`):

```json
{"filename":"codes.csv","content_type":"text/csv","size_bytes":8}
```

Creation returns `201`, or `200` with `idempotent_replay: true` for matching metadata and key.
While pending, the sanitized response includes this descriptor (other snapshot fields omitted):

```json
{
  "intake_id": "11111111-1111-4111-8111-111111111111",
  "status": "PENDING_UPLOAD",
  "upload": {
    "method": "backend_blocks_v1",
    "url": "/api/v1/resource-registry/intakes/11111111-1111-4111-8111-111111111111/blocks/0/",
    "recommended_chunk_size_bytes": 524288,
    "max_parallel_chunks": 1,
    "required_headers": {"Content-Type": "application/octet-stream"}
  }
}
```

1. Check `upload.method`. Resolve its relative URL against the application origin, retaining the
   returned prefix. Replace only the final `0/` with each zero-based block index.
2. Send 524288-byte (512 KiB) blocks, except the final block, whose exact length is the remaining
   declared size. For the eight-byte example, send just one eight-byte block at index zero.
3. Authenticate every PUT. Success is `204 No Content` for the API; do not attempt to parse a JSON
   success body. The Admin equivalent returns a JSON acknowledgment.
4. After all blocks succeed, POST `{}` to `complete/`. Do not issue an Azure block-list request:
   the backend determines block IDs, order, content type, and destination, then commits them.
5. Poll the intake state. `COMPLETED` queues inspection; wait for `READY_FOR_PUBLICATION` before
   publishing. `PUBLISHED` still requires the registration/ingestion worker to finish.

The binary HTTP request always uses `application/octet-stream`, even for a JSON or XLSX file.
The backend uses the original validated `content_type` when committing the blob.

## Retry, resume, cancellation, and limits

- Retry transient transfer failures with bounded backoff at the same block index. Deterministic
  block IDs make retransmission safe while the intake remains pending.
- Persist acknowledged indexes and the intake ID locally. Resume only the same unchanged file.
  The API does not expose a list of received blocks; retransmit uncertain indexes when necessary.
- `INTAKE_UPLOAD_INCOMPLETE` means completion could not find all expected block lengths. Re-upload
  missing/uncertain blocks and retry completion. Completion replays do not create duplicate jobs.
  Accepted replay states are `COMPLETED`, `ANALYZING`, `READY_FOR_PUBLICATION`, and `PUBLISHED`;
  `PUBLISHING` returns a state conflict.
- A missing `upload` descriptor on a successful create replay can mean the upload was already
  accepted. Inspect `status`; do not unconditionally create another intake.
- There is no browser SAS to expire or renew. The Admin's historical `renew/` action now validates
  pending state and returns backend instructions. API clients resume using their authenticated
  block URLs while the intake remains pending and unexpired.
- Cancel with the cancellation route; merely aborting a client request does not cancel server state.
  Intake expiration and cleanup remain active.
- Default maximum file size is 10 GiB, subject to environment policy, format processing limits,
  and existing user quotas. The fixed protocol allows at most 50000 blocks. It does not use the
  legacy 16 MiB / four-parallel-block recommendations. The application reads at most one block
  plus one byte for validation; ingress must accept at least 512 KiB binary bodies.

## Outcomes and safe diagnosis

Application failures use a sanitized `code`, often with a `message`. DRF authentication/CSRF and
upstream ingress failures may have different bodies; clients must also handle non-JSON errors.

| HTTP status / code | Meaning and action |
| --- | --- |
| `201`, `200`, `204`, `202` | Creation; state/completion/cancellation/replay; block acknowledgment; new publication accepted, respectively. |
| `400` | Fix metadata, unknown fields, checksum pair, unsupported extension/MIME, invalid block index, or block size. |
| `401` / `403` | Check identity, permissions, and CSRF where applicable. This alone does not prove session expiration. |
| `404` | Intake unavailable to caller; do not infer whether another user's intake exists. |
| `409` / `INTAKE_UPLOAD_INCOMPLETE` | Re-upload missing/uncertain blocks before completion. |
| Other `409` | Check state, expiry, idempotency conflict, or optional ETag mismatch. Terminal intakes need a new upload. |
| `413` | File exceeds application limits, or request exceeds an upstream body limit. |
| `415` | Binary block request must use `application/octet-stream`. |
| `503` / `UPLOAD_PROVIDER_UNAVAILABLE` | Provider configuration/support unavailable. Check deployment configuration. |
| `503` / `UPLOAD_PROVIDER_TRANSFER_FAILED` | Backend storage operation failed. Retry transient errors; persistent failures require checking private connectivity and credentials. |

A backend Azure `403 AuthorizationFailure` can surface as API `503`; the browser status does not
identify the precise storage cause. Never fix this by reopening public Storage access. Collect only
safe intake IDs, timestamps, codes, and approved diagnostic request IDs; omit credentials and files.

## GSO-related network change and compatibility

Keep `allowBlobPublicAccess=false` and `publicNetworkAccess=Disabled`, as confirmed for this
remediation. The browser contacts Unicorn AI; application replicas and workers need routes and DNS
to the Blob private endpoint. Azure Blob browser CORS is not part of this intake transfer path.
Localhost works only if its backend has that private connectivity, or uses local Azurite.
These changes address the two confirmed Storage controls; they do not certify all GSO requirements.

Deploy the backend and Admin JavaScript together. Old direct-upload clients must adopt
`backend_blocks_v1`; old browser upload checkpoints must restart. The separate `/uploads/` API
retains its own contract.

SharePoint Registry Sync calls the service in-process and still requests a SAS, then sends its
manifest with a backend `requests.put`. It does not use these HTTP block endpoints. Publication
copy operations also retain an internal read SAS. These are server-side credentials, not browser
upload authorizations, and neither bypasses private network restrictions.

## Related documentation

- [Intake Service and lifecycle](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md)
- [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
- [Private-storage upload configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md)
- [Preparing supported files](RESOURCE_REGISTRY_FILE_PREPARATION.md)
- [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md)
- [Backend implementation reference](BACKEND_INTAKE_UPLOAD.md)
