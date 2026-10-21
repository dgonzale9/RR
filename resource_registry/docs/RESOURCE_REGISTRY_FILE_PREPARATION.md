# Preparing files for Resource Registry

Prepare tabular source files before creating a staged intake in Django Admin or the Intake Service
API. Both transfer bytes through the authenticated backend to storage. This guide does not describe
external-provider or transformation sources.

## Supported files

These file types are accepted by default; environment allowlists can restrict admission:

| File type | Required extension | Accepted declared MIME types |
| --- | --- | --- |
| Comma-separated values | `.csv` | `text/csv`, `application/csv`, `text/plain`, `application/vnd.ms-excel` |
| Excel workbook | `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| JSON dataset | `.json` | `application/json` |
| JSON Lines dataset | `.jsonl`, `.ndjson` | `application/x-ndjson`, `application/jsonl`, `application/json`, `text/plain` |
| Parquet dataset | `.parquet` | `application/vnd.apache.parquet`, `application/octet-stream` |

The extension and declared MIME type must agree. Legacy `.xls`, password-protected workbooks,
archives, PDFs, and other formats are not supported by this flow.

## General preparation

- Upload a complete, final copy of the file. Do not replace its contents while the browser is
  uploading or resuming it.
- Use a meaningful filename; the filename is retained as source provenance.
- Put project- or team-specific data in a dedicated Workspace when publishing. Use an
  Organization destination only for data intended for organization-wide sharing.
- Do not include credentials, connection strings, access tokens, or data that is not authorized
  for the intended Workspace or Organization.
- Keep a local original. A staged intake is temporary and can expire before it is published.

## CSV preparation

CSV ingestion treats the first row as column headers. Include one header row followed by tabular
data rows. Use a consistent delimiter, quoting convention, and value type within each column.

Recommended practices:

- Use UTF-8 encoding.
- Give each column a non-empty, unique, descriptive header.
- Avoid decorative title rows, notes, totals, blank separator rows, and merged concepts above the
  header row.
- Keep dates and numeric formats consistent down a column.
- Open the file in a plain-text editor when diagnosing unexpected column splits or character
  encoding issues.

The service rejects an empty CSV, binary data, and content that appears to be an XLSX archive
renamed as `.csv`. The configured intake upload limit defaults to 10 GiB, but deployment policy
can set a different value.

## XLSX preparation

Each usable worksheet is processed as tabular data. The workbook worker can produce
`completed_with_skips` when one or more sheets cannot be ingested; review the resulting datasets
and skipped-sheet information after publication.

Recommended practices:

- Use an unencrypted `.xlsx` workbook; password-protected workbooks cannot be parsed.
- Put one logical table on each sheet.
- Keep the table rectangular: one header area followed by rows of data, without interleaved notes,
  subtotal rows, or unrelated tables.
- Use clear column names and avoid duplicate or blank headers. The service can normalize headers,
  but clean source names produce more predictable schemas.
- Avoid formula dependencies on external workbooks. Values available in the workbook are used for
  ingestion.
- Remove empty sheets when they are not intentional.

The current processing defaults permit up to 100 MiB per file, and 100,000 rows and 1,000 columns
per sheet.
Those are deployment settings (`RESOURCE_REGISTRY_EXCEL_MAX_FILE_BYTES`,
`RESOURCE_REGISTRY_EXCEL_MAX_ROWS`, and `RESOURCE_REGISTRY_EXCEL_MAX_COLUMNS`), so confirm the
effective limits with platform operations before preparing a large workbook. The browser upload
limit and the XLSX processing limit are separate: an uploaded workbook can still fail processing
when it exceeds the XLSX processing limits.

## JSON preparation

JSON uploads must be UTF-8 (a UTF-8 BOM is accepted) and contain one non-empty root array. Each
array item must be an object. The union of object keys becomes the dataset columns; an absent key
becomes `NULL`. NDJSON content in a `.json` file, a single root object, empty arrays, duplicate
object keys, and `NaN` or infinite numbers are rejected. Use `.jsonl` or `.ndjson` for JSON Lines.

Nested objects and arrays remain JSON values rather than being flattened. Query them after
publication with the authorized Resource Registry SQL interface and DuckDB JSON functions, for
example:

```sql
SELECT json_extract_string(payload, '$.customer.id') AS customer_id FROM dataset LIMIT 10
```

`json_extract`, `json_extract_string`, `->`, and `->>` are supported for logical JSON columns.
JSON files are materialized as canonical Parquet before querying; direct `read_json*` access is
not available. JSON response cells and CSV/XLSX exports use valid, single-serialized JSON text,
so an exported JSON-valued cell can be reimported without relying on Python dictionary notation.

For CSV/XLSX, use valid JSON object or array text consistently throughout a column when it is
intended for JSON querying. Mixed text or invalid JSON keeps that column as ordinary text.
Empty cells and JSON `null` are accepted. A column becomes logical JSON only when every non-empty
value is compatible JSON and at least one value is an object or array. Scalars alone remain their
ordinary inferred type. JSON numbers retain their source numeric lexemes during JSON
serialization; do not use non-standard numeric values such as `NaN`, `Infinity`, or `-Infinity`.

Example JSON file:

```json
[
  {
    "configuration_id": "cfg-001",
    "enabled": true,
    "limits": {"max_rows": 100000, "timeout_seconds": 30},
    "labels": ["production", "synthetic"]
  },
  {
    "configuration_id": "cfg-002",
    "enabled": false,
    "limits": {"max_rows": 50000, "timeout_seconds": 60}
  }
]
```

The file is streamed in bounded batches; it is not read as one in-memory document. Upload and
worker size limits still apply. Format errors are permanent for that intake: create a corrected
intake after fixing the source file.

## JSONL / NDJSON preparation

Use UTF-8 and exactly one JSON object per physical line, with at least one record and one column.
An initial UTF-8 BOM, LF or CRLF endings, and a final record without a newline are accepted.
Blank lines, array/scalar records, duplicate object keys, non-finite numbers, and invalid UTF-8
are rejected. Both extensions produce the `JSONL` format in intake responses.

```jsonl
{"identifier":"one","payload":{"customer":{"id":"a"}}}
{"identifier":"two","labels":["example"]}
```

The union of keys becomes the columns; missing keys become `NULL`. Nested objects and arrays
retain the same JSON representation as JSON datasets. Inspection scans all records, including
late columns and invalid content. The per-record limit defaults to 64 MiB
(`RESOURCE_REGISTRY_JSON_MAX_RECORD_BYTES`), excluding the initial BOM and line ending; this is
separate from the 512 KiB HTTP transfer block size. JSONL is materialized as canonical Parquet
before querying.

## Parquet preparation

Upload one complete, unencrypted `.parquet` file. Partition directories and externally compressed
archives are not supported. Files with columns and zero rows are valid. The canonical artifact
preserves native types and bytes without conversion.

Completion checks the header; inspection reads the schema and decodes every batch. A valid footer
alone is insufficient. Corrupt, truncated, encrypted, and columnless files fail before publication.
The registration worker validates the materialized file again. Existing size and tenant policies
still apply.

## Before uploading

1. Confirm the extension is `.csv`, `.xlsx`, `.json`, `.jsonl`, `.ndjson`, or `.parquet`, is enabled
   in the environment, and the file opens correctly locally.
2. Confirm the file size is within the effective environment limit.
3. For CSV, verify the first row contains headers. For XLSX, verify each intended sheet contains a
   single table with headers.
4. Start **Resource Registry → Staged resource intakes → Upload resource** in Django Admin.
5. After inspection reaches **Ready for publication**, choose the intended Workspace destination.

## When validation or processing fails

Use the safe `failure_code` shown in Django Admin and retain the original file for correction.
Correct the file and create a new intake; do not publish an intake that is not
**Ready for publication**. For worker status and safe operational recovery, see
[Resource Registry worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md).

## Related documentation

- [Admin backend upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md)
- [Intake Service API](RESOURCE_REGISTRY_INTAKES_API.md)
- [JSONL and Parquet implementation and rollout](DATASET_FORMATS.md)
- [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md)
- [Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md)
