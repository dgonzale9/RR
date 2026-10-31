# External source contract: verification record

The acceptance baseline is the approved Resource Registry external-file synchronization
plan: generic destination-scoped source identity, live scoped authority, immutable
provenance, compatible/sensitive publication, exact workbook mappings, logical removal
and verified recovery, idempotency/concurrency, and compatibility with manual consumers.
Graph, folder configuration, connector scheduling, production changes and UI changes are
outside this delivery.

## Environment and scope

Validation used a full isolated copy of the existing dirty backend, PostgreSQL 16 in a
task-specific local container, a separate test database and temporary local storage. The
copy included the pre-existing maintenance/cleanup changes and migrations through 0013.
Migration 0014 was applied when creating the isolated test database. The production or
developer application database was not migrated. No real provider was called.

All project commands used `uv run --no-sync --env-file .env`. A temporary settings module
inherited `BotPlatform.test_settings`, selected the isolated PostgreSQL database, disabled
replica routing and selected local Registry storage/upload providers. That machine-specific
settings module and credentials are intentionally not repository files. To reproduce, point
an equivalent test settings module at a disposable PostgreSQL instance.

## Executed checks

| Check | Result |
| --- | --- |
| Combined acceptance and neighboring regression suite | 232 passed; two pre-existing failures reproduced on the unchanged baseline. |
| New external-source modules (included above) | 37 passed, including real concurrent PostgreSQL transactions. |
| Ruff on changed Python files | Passed. |
| Black check on changed Python files | Passed. |
| Scoped strict mypy through `python -m mypy --follow-imports=silent` | Passed: 23 source files. |
| Django system checks | No issues. |
| `makemigrations resource_registry --check --dry-run` | No changes detected. |
| Migration application | Additive migration 0014 applied successfully to the isolated test database. |
| OpenAPI generation with `spectacular --validate` | Exit zero; both route trees and new request/response schemas present. Existing global diagnostics unchanged from baseline: 130 warnings (119 unique), 343 errors (36 unique). |
| Local client `--help` | Passed; no provider/network call. |

The new modules are `test_external_source_contract.py`,
`test_external_source_formats_api.py`, and `test_external_source_concurrency.py`.
Their integration tests invoke real intake-backed preparation, canonicalization,
publication, lifecycle and authenticated REST views using local files. They cover CSV,
XLSX, JSON, JSONL/NDJSON and Parquet, source identity and reservation, full replacement,
historical/no-op content, workbook mappings, manager approval, permissions, legacy-writer
protection, missing retained storage, purge fences, recovery and historical replay.
The source-control query-count test compares one output with eight worksheet outputs.

The broader run also included:

```text
test_dataset_management.py             test_dataset_management_api.py
test_dataset_management_admin.py       test_dataset_management_integrity.py
test_dataset_changes.py                test_dataset_maintenance_flows.py
test_dataset_publication_links.py      test_dataset_intake_publication_state.py
test_dataset_background_processing.py  test_current_version_discovery.py
test_application_dataset_registration.py
test_destination_assignment_removal.py test_cleanup_admission.py
test_dataset_candidate_integrity.py    test_external_dataset_provider_framework.py
test_read_services.py                  test_workbook_retention_service.py
```

Example commands, using a locally configured isolated settings module:

```bash
uv run --env-file .env pytest --ds=rr_test_settings --reuse-db \
  resource_registry/tests/test_external_source_contract.py \
  resource_registry/tests/test_external_source_formats_api.py \
  resource_registry/tests/test_external_source_concurrency.py
uv run --env-file .env python manage.py check --settings=rr_test_settings
uv run --env-file .env python manage.py makemigrations resource_registry \
  --check --dry-run --settings=rr_test_settings
uv run --env-file .env python manage.py spectacular --validate \
  --file /tmp/resource-registry-openapi.yaml --settings=rr_test_settings
```

## Pre-existing failures and limitations

Both failures are in `test_external_dataset_provider_framework.py`:

- `test_simulated_provider_submit_status_advance_result_and_cancel`
- `test_external_provider_service_submit_sync_fetch_cancel`

The pre-existing storage object lease path opens a database transaction, but these tests
do not enable database access. Pytest raises `Database access not allowed`. The same two
failures occur before this delivery's changes; neither the tests nor cleanup implementation
was weakened or changed to obtain a green run.

OpenAPI's existing diagnostics originate outside the new management endpoints. Diagnostics
were compared against the original dirty baseline under identical settings and match after
normalizing checkout paths/line numbers. The complete repository-wide suite and cloud
storage providers were not exercised; checks target this contract and its shared boundaries.

## Operational handoff

See [the consumer guide](EXTERNAL_SOURCE_SYNC.md) and
[`examples/external_sources/client.py`](examples/external_sources/client.py).
Review source/dataset/proposal revision handling, sheet mapping and source-removal semantics
before granting integration permissions. Deploy compatible writers and migration 0014 first;
enable the connector and scoped grants afterward. Operational rollback stops synchronization
mutations while retaining the source-aware backend and schema. This record does not authorize
a deployment, permission assignment, commit or push.
