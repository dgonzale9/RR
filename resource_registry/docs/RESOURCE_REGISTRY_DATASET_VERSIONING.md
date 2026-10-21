# Resource Registry dataset versioning

## Purpose

Resource Registry treats a Dataset as a logical, durable data product and a DatasetVersion as
one immutable materialized revision of that product. Operators do not select `v1`, `v2`, or any
other version number in the upload UI. The registration pipeline assigns it.

## How a new version is identified

For an uploaded CSV, JSON, JSONL/NDJSON, or Parquet file, the registration worker slugifies the
original filename stem and appends its extension. For example, `sales-report.csv` normally resolves
to `sales-report-csv`; `sales-report.ndjson` resolves to `sales-report-ndjson`. An existing legacy
Dataset with the stem-only slug (such as `sales-report`) is reused when its versions have the same
source extension. This preserves versioning for datasets registered before the suffix rule.
Registration then resolves the Dataset by **tenant ID and slug**:

| Existing Dataset with that tenant and slug | File content hash | Outcome |
| --- | --- | --- |
| No | Any | Create Dataset and version `v1`. |
| Yes | Matches an existing version | Reuse the existing version; no new version is created. |
| Yes | Does not match | Create the next integer version (`v2`, then `v3`, and so on). |

The content hash is SHA-256 over the source bytes. A filename or extension change normally creates
a different slug and therefore a different Dataset, even when its data is related. Conversely,
adding a label such as `sales-report-v2.csv` normally creates `sales-report-v2-csv`; it does not
create `v2` of `sales-report-csv`.

Dataset identity uses the Registry Namespace (`tenant_id`) and slug together. Each Workspace has
its own Namespace, so the same filename in different Workspaces resolves to separate Datasets.
Within one Workspace or Organization Namespace, reuse the same filename only for revisions of
the same logical Dataset.

## Operator procedure: publish `v2`

1. Prepare the revised CSV, JSON, JSONL/NDJSON, Parquet, or XLSX with changed content and retain
   the same filename, including its extension, as the Dataset being revised.
2. In Django Admin, open **Resource Registry → Staged resource intakes → Upload resource** and
   complete the upload.
3. Wait until the intake status is **Ready for publication**.
4. Select **Publish to destinations** and select the intended existing Organization or Workspace
   destination.
5. Wait for the dataset registration or workbook ingestion worker to complete. Confirm the generated
   DatasetVersion is `v2` and is `READY`.

Publish to the same destination when revising an existing dataset. This preserves the expected
access boundary for users and Personas. A successful intake publication only queues registration;
the new version is available after the relevant worker completes.

## Tabular files and workbook behaviour

- CSV, JSON, JSONL/NDJSON, and Parquet each produce one Dataset whose slug is derived from the
  filename stem and extension, subject to the legacy slug compatibility rule above.
- JSON input must use a non-empty root array of objects; JSONL/NDJSON requires one object per line.
  See the file-preparation guide for the complete contracts.
- An XLSX workbook can produce one Dataset per accepted sheet. Each derived sheet slug is resolved
  independently, so a revised workbook may produce `v2` for each matching sheet Dataset.
- An identical upload is an intentional no-op at the DatasetVersion level. It may still create or
  reuse publication processing records, but it does not duplicate immutable data versions.

## Constraints and troubleshooting

- Do not create `DatasetVersion` rows directly in Django Admin, a shell, or SQL. Registration also
  creates the schema, profile, artifacts, provenance, and (when enabled) publication event needed
  for a valid version.
- `READY` DatasetVersions are immutable. Correct data by publishing a new revision rather than
  editing a ready version.
- If `v2` did not appear, check that the full filename (including extension) maps to the existing
  slug, the tenant is the same, the content hash differs, and the worker completed successfully.
- If a different Dataset was created, correct the filename/slug strategy and publish the revised
  source again. Do not rename a Dataset just to force versioning without assessing consumers.

## Developer notes

The direct-upload JavaScript only creates a staged intake, uploads blocks to protected storage,
and completes verification. It does not receive a Dataset ID or version number. The worker builds
the registration command from the source filename, including the extension for new tabular Datasets;
`DatasetRegistrationIdempotencyService` resolves
the tenant/slug/hash combination and `get_next_version_number()` returns the latest version number
plus one. The DatasetVersion database constraints prevent duplicate `(dataset, version_number)` and
`(dataset, content_hash)` pairs.

Related guides: [Admin direct upload](RESOURCE_REGISTRY_ADMIN_DIRECT_UPLOAD.md),
[Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md), and
[Worker operations](RESOURCE_REGISTRY_WORKER_OPERATIONS.md).
