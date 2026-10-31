# Change dataset management configuration

This guide explains every field on the Django Admin **Change dataset management configuration**
page, what can be edited, and how a saved change affects dataset maintenance and storage cleanup.

Open **Resource Registry → Dataset management configurations**, then select the organization's
configuration. The list URL is `/admin/resource_registry/datasetmanagementconfiguration/`.
You must be an active staff user with management authority over the organization resolved from
the request host. The configuration must already exist to appear in the Admin menu.

## Scope of the configuration

There is one configuration per organization. For example, **Digitas** owns one configuration
that applies to its organization namespace and all its workspace namespaces, including the
default workspace **Main**. This screen does not create a separate policy for Main.

Three fields are read-only; four can be edited. The page has no generic Add or Delete action.
Saving this form changes policy, not dataset content, user permissions or external-source ownership.

## Field reference

The defaults below are model defaults. The supported enablement flow changes `enabled` and
`legacy_updates_blocked` to `True`; existing cleanup and retention settings are preserved.

| Admin field | Internal field | Editable here? | Default / allowed value | Meaning |
| --- | --- | --- | --- | --- |
| **Organization** | `organization` | No | Existing organization | The owner of this policy. The current host determines which organization's configuration you can access. |
| **Enabled** | `enabled` | No | Model default `False`; `True` after enablement | Admits Dataset Management mutations and proposal preparation. Also required for this organization's cleanup execution. |
| **Legacy updates blocked** | `legacy_updates_blocked` | No | Model default `False`; set to `True` by enablement | Keeps old registration paths from silently replacing an existing dataset by slug/name. |
| **Cleanup mode** | `cleanup_mode` | Yes | **Simulation** (`SIMULATE`); also **Disabled** (`DISABLED`) or **Execute** (`EXECUTE`) | Controls whether cleanup can accept actionable deletion requests and physically execute them. |
| **Dataset recovery days** | `dataset_recovery_days` | Yes | `30`; integer, minimum `30` | Minimum recovery window after dataset archival before a confirmed permanent-deletion request can start. |
| **Published source retention days** | `intake_source_retention_days` | Yes | `30`; integer, minimum `30` | Retention for the original uploaded file of a published intake after its latest recorded publication/use. |
| **Discarded candidate retention days** | `discarded_candidate_retention_days` | Yes | `7`; integer, minimum `7` | Retention for unused preparation candidates after they are discarded or superseded. |

All three retention fields are required whole numbers of days. A blank value, fraction or value
below the stated minimum is rejected by the form. A larger number extends retention; it is not
a schedule guaranteeing deletion on a particular date.

## Read-only fields

### Organization

This is the policy owner, not a destination selector. To administer another organization, use
its authorized host and an account with management authority there. Staff access alone is
insufficient. A policy for Digitas does not authorize access to another organization's datasets.

### Enabled

`True` allows authorized users/integrations to use the maintenance workflow. Creation, edits,
publication and sensitive operations still require their ordinary destination permissions.
It does not automatically publish proposals or grant synchronization permission.

`False` pauses management mutations and preparation. Read access remains governed by the usual
Registry authorization rules; the setting does not itself archive or delete datasets.
Cleanup execution for requests belonging to this organization also requires `enabled=True`.

This field cannot be toggled on this page. To enable management, create an organization with
**Resource Registry enabled** checked or change that organization checkbox from off to on.
For an organization already enabled, or to resume/pause management explicitly, use the
operational command described in [enablement and migrations](DATASET_MANAGEMENT_ENABLEMENT.md).
Pending legacy registration/workbook jobs must be drained before activation succeeds.

Saving unrelated organization fields does not resume an intentional management pause.
The organization-level **Resource Registry enabled** checkbox is a separate, broader feature
control; unchecking it preserves this configuration rather than deleting it.

### Legacy updates blocked

`True` means old ingestion paths cannot implicitly replace an existing dataset by matching
its slug/name. Consumers must use an explicit dataset-change proposal with target ID and
revision. Legacy initial registrations remain supported.

Once set by the supported enablement flow, this barrier remains `True` when management is
paused. Disabling management is not a way to restore silent legacy replacement. External
content remains protected by its own source-ownership checks as well.

## Cleanup mode

| Admin option | Stored value | Effect |
| --- | --- | --- |
| **Disabled** | `DISABLED` | Blocks manager-submitted dataset deletion requests, reconfirmation and cleanup retries for this organization. The worker does not physically execute its requests. Authorized inventory/progress reads remain available. |
| **Simulation** | `SIMULATE` | Allows review and durable deletion requests, and makes eligible discovered candidates visible. The worker skips physical execution; stored files are not removed by these cleanup requests. |
| **Execute** | `EXECUTE` | Allows eligible cleanup requests to run. Retention, authority, inventory, ownership, active work and shared-reference checks still apply. |

Deletion-request creation/reconfirmation/retry also require enabled management and an enabled
global cleanup coordinator. Manager-authorized cancellation of an unstarted request remains
available when cleanup is paused; a request that has started cannot be cancelled.

**Simulation is not a terminal request status.** A request accepted in Simulation can remain
`SCHEDULED`. Switching to Execute can allow existing requests to run once they become eligible;
it does not require a second confirmation solely because the mode changed. Review the pending
requests before making that transition. Simulation does not prove that every execution-time
storage operation or blocker check will succeed.

Selecting Execute does not delete every archived dataset. Dataset deletion still requires an
explicit manager-reviewed request. Unused candidates and expired original intake sources can
be discovered automatically under their own retention rules.

Saving the form does not run cleanup synchronously. The integrated coordinator normally checks
for work every 15 minutes, subject to environment configuration and retries. Workers recheck the
mode between objects. Switching away from Execute stops further eligible execution at those
checks; it cannot undo deleted content or guarantee that an already in-flight storage call stops.
A dataset whose purge has started remains unavailable for recovery even if cleanup is paused.

## Retention fields

### Dataset recovery days

This window starts at the dataset's `archived_at` timestamp, not at upload or request creation.
For a new deletion request, the earliest execution is no sooner than:

```text
max(request time, archived_at + max(30, dataset_recovery_days) days)
```

With `30`, a dataset archived on day 0 cannot start permanent deletion before day 30.
With `60`, its minimum recovery window becomes 60 days. The setting does not archive active
datasets and does not schedule deletion simply because an archived dataset reaches that age.

A dataset can be recovered before purge starts if its artifacts remain available. Cancel its
pending deletion request first. After purge starts, restoration, recovery and cancellation are
unavailable. See [controlled cleanup](RESOURCE_CLEANUP.md).

### Published source retention days

This protects the **original uploaded intake file**. It does not expire the dataset's canonical
versions or change which version is current.

The cleanup discovery calculation considers the latest relevant intake creation, publication,
recorded source use and publication-completion timestamps. A later use can extend the deadline.
Before execution, the worker rechecks current use and retention and verifies independent
published copies. An intake with active inspection or a pending proposal remains protected.

For example, with `30`, a published source last used on day 0 cannot be eligible before day 30.
If preparation uses it again on day 20, retention is extended from that later use. If the intake
was published to multiple destinations, cleanup uses the longest applicable retention and
checks that all participating organization configurations permit execution.

Removing an eligible original leaves the intake record **Published**, with source-deletion
evidence. Available independent dataset versions remain usable. A later operation that needs
the removed original requires a new upload.

Unsubmitted/cancelled/expired intake staging has a separate lifecycle. Its usual 24-hour staging
TTL is configured outside this page; this field does not replace that TTL.

### Discarded candidate retention days

A candidate is a private file prepared while processing a proposed change or publication.
It is distinct from a retained, published dataset version.

Cancelled, failed, unchanged or superseded preparation generations can become cleanup
candidates after the configured period, with a hard minimum of seven days. The deadline is
based on the reference's creation and the proposal/publication's recorded completion or update,
not simply on the original upload date. Shared references can extend or block eligibility.

A current proposal in `QUEUED`, `PROCESSING`, `READY` or `NEEDS_MANAGER` retains its candidates.
An old READY proposal is not discarded merely because seven days have elapsed. This setting
does not authorize cleanup of canonical versions that active datasets still retain.

## What happens to existing requests after a policy change?

- Saving does not rewrite every stored `execute_after` value. Workers recheck applicable current
  retention before beginning irreversible cleanup.
- Increasing retention can delay a pending request beyond the date previously displayed; it may
  remain blocked by the recovery or source-retention period.
- Reducing retention does not guarantee that an existing request moves forward immediately:
  its stored deadline, retry timing and other protections still apply.
- Changing a policy does not restore content already removed or reset a started purge.
- Pausing cleanup preserves requests and progress. Resuming allows remaining eligible work to
  continue without repeating confirmed object deletions.

## Example: Digitas and Main

The following is an example policy, not a read of the current database:

| Field | Example value |
| --- | --- |
| Organization | Digitas |
| Enabled | Yes |
| Legacy updates blocked | Yes |
| Cleanup mode | Simulation |
| Dataset recovery days | 30 |
| Published source retention days | 30 |
| Discarded candidate retention days | 7 |

Authorized users can create and update datasets in **Main** and other admitted Digitas
workspaces. They use explicit proposals for replacements. Managers can review and request
deletion of archived datasets, but the cleanup worker removes no bytes while the policy stays
in Simulation. Selecting Execute later makes eligible existing requests executable; it does
not bypass the three retention windows or reference protections.

## Change the policy

1. Open the configuration on the Digitas host and confirm **Organization** is Digitas.
2. Check **Enabled** and **Legacy updates blocked**. If management needs activation, use the
   enablement flow instead of trying to edit these read-only fields.
3. Select the cleanup mode and enter the three retention periods.
4. Before selecting Execute, inspect existing deletion requests and cleanup candidates.
5. Click **Save**. The updated organization policy is used by subsequent service/worker checks;
   no deployment is needed for these database-backed values.
6. Follow request and per-object progress. Retention expiry alone does not imply completion.

## Settings outside this page

| Environment setting | Default | Relationship to this form |
| --- | --- | --- |
| `RESOURCE_REGISTRY_CLEANUP_COORDINATOR_ENABLED` | `true` | Global execution gate; disabling it also blocks deletion-request creation/reconfirmation/retry. |
| `RESOURCE_REGISTRY_CLEANUP_INTERVAL_SECONDS` | `900` | Automatic cleanup evaluation interval. |
| `RESOURCE_REGISTRY_CLEANUP_BATCH_SIZE` | `100` | Bounded work per cleanup cycle, capped at 100. |
| `RESOURCE_REGISTRY_PRIVATE_CLEANUP_MODE` | `SIMULATE` | Separate mode for private intakes/requests without a namespace. |

This organization's mode does not replace the independent private-cleanup setting. Storage
provider soft delete, versioning and backups also follow their own policies. Application cleanup
success is not a promise that the provider has physically reclaimed all retained bytes.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Page missing or empty | Correct organization host, existing configuration, active staff and organization management authority. |
| Enabled is read-only or No | Activate through Organization Admin or the operational command; do not add a configuration manually. |
| Invalid retention value | Required integer; minimum 30, 30 and 7 respectively. |
| `management_disabled` | Dataset Management is paused for the host organization. |
| `cleanup_disabled` | Organization mode is Disabled or the global coordinator is disabled. |
| Scheduled request does not execute | Management enabled, mode Execute, global gate enabled, deadline reached, worker running and no blockers. |
| Request is blocked or partial | Read the recorded reason: active work, pending proposals, retention, shared references, missing ownership evidence or unavailable published copies can retain objects. |
| Request shows Needs review | Dataset revision or inventory changed; review and reconfirm through the deletion workflow. |

## Implementation references

- [Configuration model](../models.py) and [Admin fields, validation and access](../cleanup/policy_admin.py).
- [Enablement service](../management_domain/configuration.py).
- [Deletion request service](../cleanup/service.py), [worker gates](../cleanup/worker.py) and
  [retention discovery/revalidation](../cleanup/sweeper.py).
- [Runtime settings](../../BotPlatform/resource_registry_settings.py) and
  [cleanup coordinator](../cleanup/runtime.py).
- [Enablement guide](DATASET_MANAGEMENT_ENABLEMENT.md) and [cleanup workflow](RESOURCE_CLEANUP.md).
