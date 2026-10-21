# Backend uploads for private Azure Storage

Staged intake uploads now use `browser → Unicorn AI → Azure Blob Storage`. The browser sends
bounded binary blocks to authenticated application endpoints. Only the backend contacts Azure;
no SAS or Azure upload URL is returned by the intake HTTP endpoints.

Supported files are CSV (`.csv`), XLSX (`.xlsx`), JSON (`.json`), JSONL/NDJSON (`.jsonl`, `.ndjson`),
and Parquet (`.parquet`), subject to environment allowlists. See the
[Intake Service API](../docs/RESOURCE_REGISTRY_INTAKES_API.md) for MIME types and response examples,
and [file preparation](../docs/RESOURCE_REGISTRY_FILE_PREPARATION.md) for format validation.

## API contract

1. `POST /api/v1/resource-registry/intakes/` with the existing JSON metadata and `Idempotency-Key`.
   The response contains `upload.method = backend_blocks_v1` and an application-relative
   `upload.url` ending in `/blocks/0/`.
2. Send each block with `PUT /api/v1/resource-registry/intakes/<intake-id>/blocks/<index>/`,
   `Content-Type: application/octet-stream`, and the same authentication. Session-authenticated
   requests also require CSRF. Block indexes start at zero. Every block is exactly 524288 bytes,
   except the final block, which must match the remaining declared size. Retry the same index
   after a transient failure; its deterministic Azure block ID is overwritten safely.
3. `POST /api/v1/resource-registry/intakes/<intake-id>/complete/` with `{}`. The server verifies
   every block length, commits them in file order, validates object metadata, and queues the
   existing inspection job. Completion retries return the accepted intake state.

The session API has the corresponding `/auth/api/v1/resource-registry/` prefix. Django Admin
uses its existing creation/status/completion routes and the new
`/admin/resource_registry/stagedresourceintake/upload/<intake-id>/blocks/<index>/` PUT route.
Admin checks the global intake-operator permission; API writes require intake ownership.
These private, unassigned intakes retain the existing destination authorization at publication.

## Resource bounds and lifecycle

- The application reads at most one block plus one byte for size validation, never the full file.
- One block per intake is transferred at a time. An intake row lock serializes transfers against
  completion and cancellation, including across replicas. Provider timeouts/retries remain bounded
  by the existing upload-provider settings.
- The fixed block protocol fits below a 1 MiB request limit and permits at most 50000 blocks.
  Existing file-size and per-user quota limits still apply. Infrastructure must permit 512 KiB
  binary requests and enough time for a block transfer plus configured Azure retries.
- Azure holds staged blocks; no replica-local upload state or new database migration is required.
- The browser saves acknowledged indexes for resumption. If completion reports missing blocks,
  it clears those acknowledgments and offers retransmission. Cancellation retains the existing
  exact-object cleanup and expiration workflow.
- Azure/Azurite supports this transfer protocol. The development-only `local` upload provider
  does not implement backend block transfer and returns `UPLOAD_PROVIDER_UNAVAILABLE`.

## Deployment requirements

Keep `allowBlobPublicAccess=false` and `publicNetworkAccess=Disabled`. This code does not change
either setting. Application replicas and inspection/publication workers must have working routes
and private DNS to the Blob private endpoint, plus valid server storage credentials. A backend
without that connectivity will still fail, now with a sanitized provider error instead of a
browser CORS error. No browser-to-Blob CORS rule is required for this intake transfer path.

Deploy the backend and updated Admin JavaScript together. The browser uses a new checkpoint key;
pending uploads from the old direct-SAS flow must restart and expire through existing cleanup.
External intake API clients must follow the returned `backend_blocks_v1` descriptor. The separate
`/uploads/` API and legacy internal service callers retain their existing contracts.

SharePoint Registry Sync still requests a direct authorization from the service in-process and
uploads its CSV manifest using a server-side SAS. Publication copies also retain an internal read
SAS. Neither path bypasses Storage network restrictions. This change addresses the two confirmed
GSO Storage controls; it does not establish full GSO compliance or an external-partner access policy.

## Failure messages

- `INTAKE_AUTHENTICATION_REQUIRED`: sign in again and retry; a permission denial does not prove
  that the session expired.
- `INTAKE_NETWORK_ERROR`: browser could not reach the application; check the connection and retry.
- `UPLOAD_PROVIDER_TRANSFER_FAILED`: retry; persistent failures require checking backend private
  connectivity, DNS, and storage credentials. Provider exceptions and credentials are not returned.
- `INTAKE_UPLOAD_INCOMPLETE`: retransmit missing blocks before completion.
- `UPLOAD_SIZE_MISMATCH` / `INTAKE_INVALID_BLOCK_INDEX`: correct the client's block boundaries.
- `INTAKE_EXPIRED` / `INTAKE_INVALID_STATE`: an expired or cancelled intake cannot accept new bytes.

Azure operations follow the SDK's [block upload contract](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python).
Private connectivity follows [Azure private endpoint guidance](https://learn.microsoft.com/en-us/azure/storage/common/storage-private-endpoints).
