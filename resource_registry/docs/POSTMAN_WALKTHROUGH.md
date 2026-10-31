# Test the dataset lifecycle with Postman

The existing collection has 44 requests covering intake, creation, replacement, identical
content, approved historical content, archive/recovery, source deactivation and deletion requests.
It uses a simulated external source and local JSON files; it does not call SharePoint.

- [Collection v2.1](../../test/resource-registry-postman/resource-registry.postman_collection.json)
- [Example environment](../../test/resource-registry-postman/local.postman_environment.json)
- [JSON fixtures](../../test/resource-registry-postman/fixtures/)
- [Package README](../../test/resource-registry-postman/README.md)
- [Importable ZIP](../../test/resource-registry-postman.zip)

## Prepare the environment

1. Import the collection and environment. Set `base_url` to the environment's server, for
   example `http://localhost:8000`. The host must resolve the authorized organization.
2. Set `auth_token` and `manager_token` as local Postman values; do not export them. Paste
   only the DRF token without `Token`; the script constructs the header.
3. Obtain the destination from `GET /api/v1/resource-registry/namespaces/`.
   `/api/v1/namespaces/` is not this catalogue route. Copy the authorized workspace's
   `results[].id` into `namespace_id`, not its numeric workspace ID.
4. The environment retains a historical `json-test` destination: replace its namespace
   before the first POST. Do not assume it exists after database cleanup or migration.
5. Verify [Dataset Management and permissions](DATASET_MANAGEMENT_ENABLEMENT.md). The
   integration token needs synchronization on the namespace and creation/edit authority.
   The manager token approves sensitive changes and lifecycle actions. The collection
   does not assign permissions.
6. Keep `confirm_deletion=false` during the normal walkthrough and read the current cleanup
   mode before requesting deletion. Do not infer it from the example environment.

HTTP intake transfer supports Azure/Azurite or the `local` development provider, which now
implements blocks. For isolation, configure uploads and storage as `local` with a writable
local directory. With Azure, the backend needs connectivity and credentials. See
[upload configuration](RESOURCE_REGISTRY_DIRECT_UPLOAD_CONFIGURATION.md).

## Sequence

Use **Send** in order. Repeat verification/preparation GETs until the expected state.
Existing collection labels may be in Spanish; folder numbers identify the steps below.

| Folder | Expected result |
| --- | --- |
| `00` | Verify the authorized namespace. |
| `01` | Two-row JSON v1 → verified intake → proposal → publish and save IDs. |
| `02` | Three-row JSON v2 → same dataset and new version. |
| `03` | Same JSON v2 with another external version → `NO_CHANGES`, no duplicate version. |
| `04` | JSON v1 with another external version → `NEEDS_MANAGER` → manager publication; current version returns to v1, with only two versions retained. |
| `05` | Manager manual archive and recovery; files/versions preserved. |
| `06` | Source deactivation → archive if no other active assignments remain. |
| `07` | Review inventory → explicitly request deletion → poll or cancel before execution. |

The Runner bounds polling to 120 requests; configure a 1000 ms delay. It stops after the
deletion-inventory review. With Send, `setNextRequest` does not execute the next request:
advance manually. Resolve unexpected `NEEDS_MANAGER` or `FAILED` states before proceeding.

## `size_bytes` and step 5

Scripts calculate `v1_file_size_bytes` from the exact UTF-8 bytes of `fixture_v1`, including
line endings. Use it as a number in the body, without quotes or asterisks:

```text
"size_bytes": {{v1_file_size_bytes}}
```

Do not count rows or estimate the size. The collection sends the same fixtures as
`application/octet-stream`; no file selection in Postman is required.

In folder `01`, request 5 creates the proposal. Its raw body is `{{v1_proposal_body}}`.
The script builds it from `namespace_id`, the verified intake, `source_reference`,
`expected_source_revision: 0` and a name. Send it to
`POST /api/v1/resource-registry/dataset-changes/`. Response: `201 QUEUED`; read the
proposal in step 6, then publish in step 7 once `READY`. Creating the proposal does not
yet make a consumable dataset available.

Resolve `403 management_disabled` by enabling the organization, not by changing the body.
Bodies are frozen for retries: if `namespace_id` changes after body creation, do not edit
an accepted operation while retaining its key. For an independent run, import a clean
copy of the collection and configure the destination before the first request.

## Deletion and validation

After reviewing inventory, set `confirm_deletion=true` only for the intended request.
POST returns `202`; `SCHEDULED` does not mean deleted. The server enforces at least 30 days
since archival and protects shared references. `SIMULATE` removes no bytes; `EXECUTE`
may execute the request when retention allows. The walkthrough does not change dates or
policy and does not trigger the worker. See the [cleanup contract](RESOURCE_CLEANUP.md#rest).

To verify scripts and fixtures without backend requests, run from the repository root:

```bash
node --test test/resource-registry-postman/validate_collection.test.cjs
```

Offline tests check scripts, sizes, IDs, revisions, polling, replay and confirmation. They
do not replace an authorized HTTP run in the target environment. The collection retains
its scripts as an executable artifact; it is not regenerated from OpenAPI, which does
not describe those assertions.
