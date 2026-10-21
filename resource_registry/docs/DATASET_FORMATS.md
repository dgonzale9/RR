# JSON Lines and Parquet dataset uploads

Resource Registry accepts JSONL/NDJSON and Parquet through the existing upload API and Django
Admin staged intake flow, alongside CSV, JSON, and XLSX. These formats share the intake endpoints
and metadata fields; they do not add separate routes or export formats. Both use the existing
dataset registration worker. For the current transfer protocol, see
[backend uploads](BACKEND_INTAKE_UPLOAD.md) and the
[Intake Service API](../docs/RESOURCE_REGISTRY_INTAKES_API.md).

## File preparation and API values

| Source | Extensions | Format in upload/intake responses | Accepted MIME types by default |
| --- | --- | --- | --- |
| JSON Lines | `.jsonl`, `.ndjson` | `JSONL` | `application/x-ndjson`, `application/jsonl`, `application/json`, `text/plain` |
| Parquet | `.parquet` | `PARQUET` | `application/vnd.apache.parquet`, `application/octet-stream` |

NDJSON is an alias of JSONL. Source metadata uses `jsonl` or `parquet`, and preserves the original
filename. The Admin chooses `application/x-ndjson` or `application/vnd.apache.parquet` for these
extensions, regardless of browser MIME guessing. API clients must declare a MIME type compatible
with their filename extension at creation. Backend intake block requests use
`Content-Type: application/octet-stream`; the backend applies the original declared file MIME type
when committing the blob. Legacy direct-upload callers use their authorization's required headers.

### JSON Lines

Use UTF-8, exactly one JSON object per physical line, at least one record and at least one column.
An initial UTF-8 BOM, LF or CRLF line endings, and a final record without a newline are accepted.
Blank lines (including an extra blank line at the end), scalar/array records, duplicate keys,
non-finite numbers, and invalid UTF-8 are rejected. `.json` still requires a nonempty root array;
it does not implicitly accept NDJSON.

```jsonl
{"identifier":"one","payload":{"customer":{"id":"a"}}}
{"identifier":"two","late_column":[1,2]}
```

The union of keys in first-seen order becomes the columns; absent keys become `NULL`. Scalar
values and nested objects/arrays use exactly the same representation as JSON datasets. There
is no flattening or additional numeric/date/boolean type inference. All records are scanned to
find late columns and invalid content. Reading is incremental with a per-record byte limit.

JSONL becomes canonical Parquet before querying. Existing authorized SQL tools support:

```sql
SELECT json_extract_string(payload, '$.customer.id') AS customer_id
FROM dataset
LIMIT 10
```

### Parquet

Upload one complete, unencrypted file. Partition directories and externally compressed archives
are unsupported. The canonical artifact preserves the existing native types and bytes without
conversion. Files with columns and zero rows remain valid, matching the existing dataset engine.

Completion verifies the header; asynchronous inspection reads the schema and decodes every batch.
A valid footer alone does not establish a valid file. Corrupt, truncated, encrypted, and columnless
files fail before publication. The registration worker validates the materialized file again.
Existing tenant, integrity, size, and query limits still apply.

## Configuration and rollout

| Environment setting | Default |
| --- | --- |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_EXTENSIONS` | `xlsx,csv,json,jsonl,ndjson,parquet` |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_JSONL` | `application/x-ndjson,application/jsonl,application/json,text/plain` |
| `RESOURCE_REGISTRY_UPLOAD_ALLOWED_CONTENT_TYPES_PARQUET` | `application/vnd.apache.parquet,application/octet-stream` |
| `RESOURCE_REGISTRY_JSON_MAX_RECORD_BYTES` | `67108864` (64 MiB) |

The JSONL record limit counts UTF-8 bytes excluding the initial BOM and line ending. Existing total
upload and artifact-materialization limits remain separate and unchanged.

1. Deploy compatible application and inspection/registration workers. Apply migration
   `0003_datasetartifact_jsonl_format` using `uv run --env-file .env python manage.py migrate`.
   This only extends artifact choices; it does not rewrite existing rows.
2. Environments with an explicit extension allowlist must add `jsonl,ndjson,parquet` to enable them.
   Clients must accept the new `JSONL` and `PARQUET` response values.
3. Existing explicit tenant `allowed_formats` policies remain unchanged. Add `jsonl` or `parquet`
   only where intended. System materialization defaults include both formats.
4. Verify upload → inspection → publication → registration → authorized query. Use existing durable
   job states, sanitized error codes, and execution times; no new monitoring service is required.

To stop new admissions, remove the extensions from the upload allowlist and keep compatible workers
available for accepted jobs. Existing datasets and their canonical Parquet queries remain usable.

## Errors and troubleshooting

`JSONL_*` and `PARQUET_*` inspection codes are terminal content errors, recorded on both the intake
and inspection job. Examples: `JSONL_RECORD_TOO_LARGE`, `JSONL_DUPLICATE_KEY`,
`JSONL_NON_FINITE_NUMBER`, `JSONL_BLANK_LINE`, and `PARQUET_CONTENT_INVALID`.
Registration workers expose corresponding lowercase codes. Correct the source and submit a new
upload. Content errors do not include record data. Storage/network failures retain existing retry
and cleanup behavior. Legacy CSV/JSON codes remain compatible.

Existing upload controls still verify extension/MIME compatibility, size, checksum, ETag, ownership,
and namespace permissions. Staging completion does not make a dataset available: inspection and
publication must finish, followed by successful dataset registration.
