# Controlled Resource Registry cleanup

Cleanup is implemented in the backend and Django Admin. It is disabled for physical
execution by default: every organization starts in **Simulation**. No frontend,
proxy, SSO or collaborator-management changes are required.

## Manager workflow

1. Open **Resource Registry → Datasets** and archive the dataset if necessary.
   Archiving alone never schedules deletion.
2. Open **Storage usage → Review deletion**. Review versions, publications,
   derivatives, exclusive/shared storage, blockers and the earliest execution date.
3. Enter a reason, check the confirmation and choose **Request permanent deletion**.
   A request can start no earlier than 30 days after archiving. Longer organization
   retention periods take precedence.
4. Follow **Cleanup candidates**, **Scheduled deletion**, **Partial cleanup** and
   **Cleanup failed**. Request detail pages show individual retained objects and
   English explanations without identifying another destination.
5. **Cancel deletion** is available before execution starts. Cancel a pending
   request before recovering the dataset. Once execution starts, recovery,
   restoration and cancellation are unavailable.

A changed dataset revision, version set, publication set or file inventory requires
**Needs review** and a new confirmation. The current manager can adopt a request
blocked because its original requester's permission was revoked. A failed request
can be retried without repeating already confirmed object deletions.

Organization managers with existing staff access can configure cleanup mode and
retention periods in **Dataset management configurations**. Staff is an additional
Admin requirement; dataset REST operations use the existing scope/business ACLs.
Global intake permissions are not required to request dataset deletion.

For each configuration field, defaults, minimum values and the effect of changing modes, see
[Change dataset management configuration](CHANGE_DATASET_MANAGEMENT_CONFIGURATION.md).

## Retention and availability

- Active datasets retain all canonical versions and managed source copies.
- Dataset deletion is explicit and preserves identity, version numbers, actors,
  timestamps, hashes, sizes and operation outcomes. Data-bearing schemas, profiles,
  results, SQL, analyses, chart specifications and export payloads are redacted.
- A published intake remains **Published** after source cleanup, with **Source file
  deleted**. Its original is retained for at least 30 days after its last publication
  or use. Independent published copies must be verified before removing it.
- Pending inspections, ready proposals and active work retain their sources. Ready
  proposals do not expire just because they are old. Abandoned unsubmitted intakes
  keep their configured staging deadline (normally 24 hours).
- Cancelled, failed, unchanged and superseded preparation generations can be cleaned
  after at least seven days. A failed proposal can be recalculated only while its
  source remains available; otherwise upload the source again.
- Dataset requests include original-intake provenance. Original retention or another
  required reference can leave a request in **Partial cleanup** even after its
  exclusive Parquet files have been removed.
- Removing one sheet detaches only that dataset's publication associations. A
  publication becomes **Detached** only when no active associations remain.
- Historical replay returns its recorded result. It does not recreate removed
  files, datasets or publication associations.

Historical files are inventoried without deleting them. Unknown ownership remains
blocked, even in Execute mode. Local discovery stays within the configured storage
root; Azure discovery lists the server-owned `resource-registry/`,
`resource_registry/` and `datasets/` prefixes. An inventory entry is not proof of ownership.

## Automatic execution and rollout

The WSGI and ASGI entrypoints initialize the coordinator. It uses the existing
low-priority executor, polls every 15 minutes and attempts at most 100 objects per
cycle. Persistent request/object claims prevent workers from duplicating deletion.
Object reads and writes and synchronous dataset queries have renewable reservations.
Interrupted work resumes from durable state after its reservation expires.

Migrations are additive and never purge data. Deploy compatible backend/worker code
and apply migrations before enabling execution. Finish or terminate old workers
that do not participate in the new reservations before changing an organization's
mode to **Execute**. Check that the inventory and sharing evidence are correct in
Simulation first.

Settings (environment-backed):

| Setting | Default | Purpose |
| --- | --- | --- |
| `RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED` | `true` | Start the integrated coordinator from web processes. |
| `RESOURCE_REGISTRY_CLEANUP_INTERVAL_SECONDS` | `900` | Periodic evaluation interval. |
| `RESOURCE_REGISTRY_CLEANUP_BATCH_SIZE` | `100` | Maximum objects considered for execution per cycle, capped at 100. |
| `RESOURCE_REGISTRY_PRIVATE_CLEANUP_MODE` | `SIMULATE` | Global mode for private intakes without an organization. |

Storage failures receive exponential backoff and at most five automatic attempts.
Reference blockers are reevaluated without spending that budget. A missing object
is idempotent success. Connectivity and permission failures are not absence.
Storage paths never come from deletion request bodies; deletion uses exact server
inventory entries and verifies their identity. No folder is removed by prefix.

To stop execution, set organization mode to **Disabled** or disable the coordinator.
Workers recheck the mode between objects. Keep this version of the read guards when
rolling back mutations: older code cannot interpret deletion tombstones safely.
Already removed content cannot be recovered by rollback.

A successful cleanup means the application can no longer retrieve the deleted
objects. Provider versioning, soft delete and backups follow their own policies;
the displayed estimate is not a confirmation of physically reclaimed provider space.

## REST

Both existing `/auth/api/v1/resource-registry/` and
`/api/v1/resource-registry/` prefixes expose:

- `GET datasets/{id}/deletion-preview/`
- `POST datasets/{id}/deletion-requests/`
- `GET cleanup-requests/` and `GET cleanup-requests/{id}/`
- `POST cleanup-requests/{id}/cancel/`, `retry/` and `confirm/`

Creation requires `expected_revision`, `inventory_fingerprint`, `reason`,
`idempotency_key` and `confirmed: true`. Reviews/fingerprints are generated by the
server. Use a new idempotency key for a new intent; identical requests reuse the
original result. Listings apply authorization before filtering, counting and
pagination. Django Admin calls the same services directly without internal HTTP.

### Review and schedule an archived dataset

Use the organization's trusted host and an authenticated destination manager. Dataset
Management must be enabled; physical execution also requires cleanup mode `EXECUTE`.
`SIMULATE` permits review and simulation without removing provider bytes. `DISABLED`
blocks cleanup mutations/execution. See [enablement](DATASET_MANAGEMENT_ENABLEMENT.md).

Archive through `POST datasets/{id}/archive/`, or deactivate an external source through
`POST dataset-sources/{id}/deactivate/` after confirmed disappearance. Neither operation
schedules deletion. Source deactivation can retain a dataset protected by another assignment;
read its actual state before proceeding.

`GET datasets/{id}/deletion-preview/?limit=100&offset=0` returns `200`. Partial example:

```json
{
  "dataset_id": "<dataset_uuid>",
  "revision": 6,
  "inventory_fingerprint": "<server_returned_64_hex_characters>",
  "execute_after": "2026-11-29T12:00:00+00:00",
  "mode": "SIMULATE",
  "blocker": "",
  "versions": [1, 2],
  "object_count": 2,
  "next_offset": null,
  "previous_offset": null,
  "recoverable": true
}
```

The real response also includes object ownership/shared-reference flags, estimated bytes,
publication counts and derivatives. Review every page of `objects`; use `next_offset` until
null. Copy the server's fingerprint and revision rather than calculating them. The date is
illustrative: the server calculates it from archive time and policy, with a 30-day minimum.

`POST datasets/{id}/deletion-requests/`:

```json
{
  "expected_revision": 6,
  "inventory_fingerprint": "<server_returned_64_hex_characters>",
  "reason": "Dataset retired and retention reviewed",
  "idempotency_key": "ventas-permanent-delete-001",
  "confirmed": true
}
```

Unlike proposal creation/source deactivation, this endpoint receives `idempotency_key` in
the JSON body, not an `Idempotency-Key` header. Keep the exact reviewed body for retries.
It responds `202`, including on an accepted replay. Partial response:

```json
{
  "id": "<cleanup_request_uuid>",
  "dataset_id": "<dataset_uuid>",
  "kind": "DATASET",
  "status": "SCHEDULED",
  "execute_after": "2026-11-29T12:00:00+00:00",
  "started_at": null,
  "completed_at": null,
  "error_code": "",
  "recoverable": true,
  "inventory_fingerprint": "<server_returned_64_hex_characters>"
}
```

Acceptance is durable intent, not evidence that files were deleted. Poll
`GET cleanup-requests/{id}/` for request status and bounded per-object `items` with
`id`, `status`, `attempts`, `block_reason`. Follow `next_offset` for more items.

| Status | Follow-up |
| --- | --- |
| `SCHEDULED` | Await retention deadline, policy and worker. |
| `NEEDS_REVIEW` | Review fresh inventory and reconfirm. |
| `BLOCKED` | Resolve the reported reference, activity or permission blocker. |
| `RUNNING` | Execution started; cancellation and recovery unavailable. |
| `PARTIAL` | Some objects remain retained; inspect their reasons. |
| `FAILED` | Read safe error; correct the cause and retry eligible work. |
| `COMPLETED` | Review recorded outcomes; evidence/history remain. |
| `CANCELLED` | Request stopped before execution; recovery still checks content availability. |

`POST cleanup-requests/{id}/cancel/` and `retry/` have no required body and return `200`.
Cancellation is only allowed before execution starts. `confirm/` also returns `200` and
requires the fresh dataset revision and fingerprint:

```json
{
  "expected_revision": 7,
  "inventory_fingerprint": "<fresh_server_fingerprint>"
}
```

Common domain failures use `code`/`detail`: `archive_required`, `deletion_pending`,
`revision_conflict`, `inventory_changed`, `cleanup_disabled` and `idempotency_conflict`
are conflicts; `dataset_purging` is `410`. Authentication/manager checks still apply on
every action. Invalid serializer fields use DRF validation responses.

Source disappearance is never a substitute for this explicit deletion review. A purged
source output cannot recover automatically when its external file reappears.
