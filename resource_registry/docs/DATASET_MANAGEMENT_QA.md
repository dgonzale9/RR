# QA for maintenance, external sources and cleanup

This matrix describes verification requirements; it does not claim that backend tests were
rerun when updating documentation. Use isolated PostgreSQL and local storage/test adapters
for suites. HTTP block transfer supports Azure/Azurite and the `local` development adapter.

Prepare two destinations, an integration with synchronization and creation/edit grants,
a manual editor and a manager. Use synthetic files from the
[local client](../examples/external_sources/) or [Postman](POSTMAN_WALKTHROUGH.md), without
relying on real SharePoint access.

| Scenario | Expected evidence |
| --- | --- |
| Valid CSV, XLSX, JSON, JSONL/NDJSON and Parquet | Successful publication with identifiable source/result mappings. |
| More/fewer file rows | Same dataset ID, complete snapshot and new version. |
| Rename / equal filenames | Renaming preserves identity; different external IDs do not merge. |
| Identical content | `NO_CHANGES`, checked external version recorded, no duplicate. |
| External historical content | `NEEDS_MANAGER`; approval selects retained version without duplication. |
| Manual historical content | Manager restoration selects existing version; readers/preview use it as current. |
| Removed columns or changed types | Editor/integration cannot publish; manager can review and approve. |
| New, renamed, omitted, archived or invalid Excel sheets | Explicit mapping; omission is not withdrawal; one invalid selected sheet blocks the whole set. |
| Source disappears with a pending proposal | Deactivation cancels/fences obsolete publication and withdraws only its assignments. |
| Source returns | Only `SOURCE_REMOVED` outputs recover after publication/verified no-op; manual archives and purge states remain protected. |
| Independent assignments | Remain protected; replay does not reactivate withdrawn records. |
| Lost response/concurrent operations | Same body/key recovers result without repeated effects; stale revisions fail. |
| Manual or legacy producer write against external content | Rejected without modifying content/provenance. |
| Revoked permission or foreign destination | Denied without effects or information disclosure. |
| Invalid file/storage failure | Current version preserved; inspectable error and recovery according to availability. |
| Archive / deletion request | Archiving does not delete; request requires inventory, confirmation and retention. |
| Partial, shared or retried cleanup | Protected objects retained; confirmed effects are not repeated. |
| Purge started | Restoration, recovery and cancellation unavailable. |
| Organization activation in Admin | Atomic automatic configuration; policies preserved; pending jobs block activation. |
| Pause followed by organization metadata edit | Unrelated saves do not resume management; legacy replacement stays blocked. |
| Migration 0014 from 0013 | Only enabled organizations affected; jobs block atomically; no name-based association. |

Reference tests:

- [Management API](../tests/test_dataset_management_api.py),
  [integrity](../tests/test_dataset_management_integrity.py) and
  [Admin](../tests/test_dataset_management_admin.py).
- [External contract](../tests/test_external_source_contract.py),
  [formats/API](../tests/test_external_source_formats_api.py) and
  [concurrency](../tests/test_external_source_concurrency.py).
- [Cleanup](../tests/test_resource_cleanup.py),
  [interfaces](../tests/test_cleanup_interfaces.py),
  [concurrency](../tests/test_cleanup_concurrency.py) and
  [recovery](../tests/test_cleanup_recovery.py).
- [Migration enablement](../tests/test_dataset_management_enablement_migration.py) and
  [Organization Admin enablement](../../tests/organization/test_dataset_management_enablement_admin.py).

Run suites with `uv run --env-file .env` and settings that explicitly select the isolated
test database. Do not reuse the development database containing real data. For code changes,
apply Ruff, Black, mypy, Django checks, migration checks and OpenAPI validation as appropriate.
Regressions must cover manual workflows, current-version reads, publication withdrawal and
grouped catalogue/result queries without per-item queries.

The [maintenance](../DATASET_MAINTENANCE_VERIFICATION.md),
[external source](../EXTERNAL_SOURCE_SYNC_VERIFICATION.md) and
[cleanup](../RESOURCE_CLEANUP_VERIFICATION.md) reports record historical verification and
limitations for their deliveries. Distinguish pre-existing failures from introduced failures
for every run.
