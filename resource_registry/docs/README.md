# Resource Registry documentation

These guides describe the current backend. The
[complete index](RESOURCE_REGISTRY_DOCUMENTATION_INDEX.md) retains reading paths for access,
BIFs, analysis and configuration.

| Task | Guide |
| --- | --- |
| Create, replace, restore versions and archive through REST | [Maintenance API](DATASET_MANAGEMENT_API.md) |
| Synchronize external files: creation, updates and disappearance | [Endpoint requests/responses](EXTERNAL_SOURCE_ENDPOINTS_ES.md) |
| Understand identity, revisions, Excel, retries and connector responsibilities | [External source contract](EXTERNAL_SOURCE_SYNC.md) |
| Work through Django Admin | [Dataset maintenance](DATASET_MAINTENANCE.md) |
| Understand and edit every Dataset management configuration field | [Configuration fields and behavior](CHANGE_DATASET_MANAGEMENT_CONFIGURATION.md) |
| Enable dev/other environments and resolve `management_disabled` | [Enablement and migrations](DATASET_MANAGEMENT_ENABLEMENT.md) |
| Understand current/historical versions, archival and external ownership | [Versioning](RESOURCE_REGISTRY_DATASET_VERSIONING.md) |
| Request physical deletion with inventory review and retention | [Controlled cleanup](RESOURCE_CLEANUP.md) |
| Test step by step with local JSON files | [Postman](POSTMAN_WALKTHROUGH.md) |
| Operate queues, inspection, preparation and cleanup | [Workers](RESOURCE_REGISTRY_WORKER_OPERATIONS.md) |
| Verify behavior and regressions | [QA matrix](DATASET_MANAGEMENT_QA.md) |

Catalogue/maintenance routes start with `/api/v1/resource-registry/` or
`/auth/api/v1/resource-registry/`. Obtain namespaces from `GET namespaces/` on the
organization's host; do not automatically reuse one environment's IDs in another.

Serializers/views and their tests define behavior; `/api/schema/` generates OpenAPI.
The Postman collection retains examples, variables and assertions for an executable walkthrough.
The module-root `*_VERIFICATION.md` reports record historical evidence, not new backend test
runs for each documentation update. Earlier maintenance, synchronization and cleanup entry
points now link to these guides.
