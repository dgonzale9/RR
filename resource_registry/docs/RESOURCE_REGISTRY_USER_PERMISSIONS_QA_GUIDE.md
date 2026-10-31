# QA Guide — Resource Registry User Permissions

## Purpose

Use this guide to confirm that Resource Registry exposes only the data and actions
authorized for a user. Run the checks in a non-production environment with synthetic
datasets only.

This guide complements [Resource Registry User Permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md).

## Test setup

Create the following within one active, Resource Registry-enabled Organization:

- Two active, Resource Registry-enabled Workspaces: **Workspace A** (`PRIVATE`) and
  **Workspace B** (`OPEN`).
- One published, workspace-scoped test dataset in each Workspace. Record both dataset IDs.
- One published, organization-global test dataset. Record its dataset ID.
- A Persona with the Registry BIFs needed for the test. Include listing, dataset details,
  schema, profile, versions, the canonical `resource_registry_query_dataset` assignment, and
  query-job assignments when testing ordinary-user queries. These are published to the model as
  `resource_registry_inspect` and `resource_registry_query` actions.

Create these test accounts. All accounts should be Organization members unless a case says
otherwise.
When testing `strict` scope, make the scenario's Workspace active in the interface before
requesting Registry data. `X-Workspace` or the Organization default supplies that context.

| Account | Workspace assignment | Platform role | Expected Registry role |
| --- | --- | --- | --- |
| `qa-no-workspace` | None | Standard user | `restricted_reader` |
| `qa-workspace-a` | Workspace A user | Standard user | `restricted_reader` |
| `qa-workspace-a-peer` | Workspace A user | Standard user | `restricted_reader` |
| `qa-open-workspace` | None; Organization member | Standard user | `restricted_reader` |
| `qa-admin` | None | `unicornai-admin` or superuser | `admin` |

For a standard user, do not assign `unicornai-power-users`, `unicornai-publishers`,
`unicornai-bif-managers`, or `unicornai-admin`. The first three produce the `reader`
Registry role; `unicornai-admin` produces `admin`.

## Evidence to capture

For every scenario, record:

- Test account and Persona.
- Active Workspace in the interface and Workspace named in chat, if any.
- Dataset name and ID used.
- User-visible result: success, unavailable, or denied.
- Screenshot or safe response excerpt. Do not capture production data or credentials.

## Test scenarios

### 1. User is not assigned to the Workspace

Sign in as `qa-no-workspace` and use a Persona assigned only to Workspace A.

1. Ask the Persona to list datasets.
2. Ask for the Workspace A dataset by its known ID.
3. Ask for a bounded query of that dataset.

Expected result: Registry access is denied or unavailable. The Workspace A dataset must not
be listed, read, or queried. Adding the user to Workspace B must not change this result.

### 2. Organization membership admits an eligible OPEN Workspace

Sign in as `qa-open-workspace`, which is an Organization member but has no Workspace membership.
Configure the Persona with only Workspace B.

1. Ask the Persona to list datasets without naming a Workspace.
2. Request details for the Workspace B dataset.
3. Try the Workspace A dataset using its known ID.
4. Request the organization-global dataset by known ID.

Expected result: Workspace B is selected and its dataset is available according to the user's
Registry role and Persona BIFs. The `PRIVATE` Workspace A dataset is unavailable. The test account
can also use the same-Organization global dataset when its policy permits it. The test account
must not receive Workspace management or Registry mutation capabilities merely because Workspace B
is `OPEN`.

### 3. One Persona Workspace

Sign in as `qa-workspace-a`. Configure the Persona with only Workspace A.

1. Ask to list datasets without naming a Workspace.
2. Request details for the Workspace A dataset.
3. Try the Workspace B and organization-global datasets using their known IDs.

Expected result: with the default `observe` policy, Workspace A is used automatically. In
`strict`, the active Workspace must be A. Its Dataset and the same-Organization
`ORGANIZATION` Dataset are available when their policies admit them; Workspace B's
`WORKSPACE` Dataset is absent and cannot be read or queried by known ID.

### 4. Multiple Persona Workspaces require an explicit choice

Assign `qa-workspace-a` to both Workspaces. Configure the Persona with both Workspaces.
Run the scenario matching `RESOURCE_REGISTRY_SCOPE_POLICY`.

With the default `observe` policy:

1. Open a fresh conversation and ask to list datasets without naming a Workspace.
2. If the browser shows a workspace choice or retains an earlier choice, repeat without naming
   a Workspace.
3. Explicitly say “Use Workspace A” and then ask to list datasets.
4. Start another fresh conversation, explicitly say “Use Workspace B,” and list datasets.

Expected result: steps 1 and 2 return no Registry data. Steps 3 and 4 return the named
Workspace's `WORKSPACE` Dataset plus the authorized same-Organization Dataset. A browser
choice, cookie, session value, or stale namespace must not silently select a Workspace
for this Persona in `observe`.

With `strict` policy:

1. Select Workspace A in the interface and list datasets without naming a Workspace in chat.
2. Ask to use Workspace B while A remains active.
3. Switch the interface to Workspace B and list datasets in a fresh conversation.

Expected result: step 1 returns A plus authorized same-Organization data; step 2 executes
no Registry tool and requests an interface switch; step 3 returns B plus authorized
same-Organization data.

### 5. Dataset boundaries after selection

With the multi-Workspace Persona set to Workspace A through the selection path for the policy:

1. List datasets.
2. Request the Workspace A dataset by name and by known ID.
3. Request the Workspace B dataset by known ID.
4. Request the organization-global dataset by known ID.
5. Query the organization-global Dataset, then attempt a query using the blocked Workspace B ID.

Expected result: Workspace A and same-Organization `ORGANIZATION` Datasets are accessible.
Workspace B's `WORKSPACE` Dataset is not listed, read, or queryable.

### 6. Ordinary reader queries and explicit denial

Use a Persona assigned only to Workspace A.

1. Sign in as `qa-workspace-a`; verify listing, details, schema, profile, and versions for the
   Workspace A dataset.
2. Ask for a bounded read-only SQL query and an exploration using assigned BIFs.
3. Explicitly disable the query capability in an isolated tenant/Dataset policy and repeat.
4. Restore the policy and attempt a separate analysis/chart/export BIF, if assigned.

Expected result: the ordinary `restricted_reader` can query and explore by default, the
explicit denial returns `capability_error`, and higher-privilege analysis/chart/export
remains unavailable to this role. Restore the original policy after the case.

An explicit tenant or Dataset capability denial takes precedence over the default allow.

### 7. Missing Persona BIF

Use `qa-workspace-a` with a Persona assigned to Workspace A but without
the canonical `resource_registry_query_dataset` action.

1. Confirm that the user can perform the assigned Registry read actions.
2. Ask the Persona to query data.

Expected result: `query_dataset` is absent from `resource_registry_query`, so querying is
unavailable. The user's role cannot make an unassigned canonical Persona BIF callable.

### 8. Administrator does not bypass a scoped Persona

Sign in as `qa-admin`. Configure a Persona with Workspace A only.

1. List datasets without naming a Workspace.
2. Read or query the Workspace A dataset.
3. Try the Workspace B and organization-global datasets by known ID.

Expected result: the administrator can use Workspace A through the scoped Persona, but cannot
list, read, or query Workspace B's `WORKSPACE` Dataset through that Persona. The
same-Organization `ORGANIZATION` Dataset remains available when its policy admits it.

### 9. Dataset ACL intersects with Workspace scope

If the test environment supports a dataset-specific allow-list, restrict the standard user's
allowed dataset IDs to one Workspace A dataset.

1. Ensure Workspace A contains a second published workspace-scoped dataset.
2. List datasets and request both IDs as `qa-workspace-a`.
3. Remove the allow-list and repeat if the environment permits.

Expected result: while the allow-list is active, only the dataset that is in both the allow-list
and the selected Workspace A set is available. A dataset outside either set is denied.

### 10. Enablement and host checks

Use a test account that otherwise has Workspace A access.

1. Disable Resource Registry for Workspace A and try a Registry request.
2. Re-enable it, then disable it for the Organization and repeat.
3. Restore both settings and make the same authenticated request through an unmapped host in a
   non-local environment using forwarded-token login.

Expected result: the disabled Organization or Workspace is unavailable. The unmapped-host API
request returns `403` with `workspace_host_unmapped` before login completes.

### 11. Default Main is OPEN without direct assignment

In a separate active Registry-enabled Organization, use its default Main Workspace with
`OPEN` state, a published Main `WORKSPACE` Dataset, and a Persona configured for Main.
Sign in as a normal Organization member with no direct Main assignment or privileged group.

Expected result: the member can discover and query the Main Dataset when BIFs and policies
admit it. Repeat in a second `OPEN` Workspace to confirm that Main has no special Registry
permission rule. A `PRIVATE` nondefault Workspace without a direct/object grant is denied.

### 12. Distinguish empty discovery from authorization failures

With an admitted Workspace and listing BIF, first remove all visible test Datasets in a
disposable fixture, then restore one and search for a nonmatching name. Separately omit a
required multi-Workspace selection and disable the list capability in an isolated policy.

Expected result: an unfiltered empty list reports `no_visible_datasets`; a filtered empty
result reports `no_matching_datasets`; missing selection reports
`workspace_selection_required`; capability denial reports `capability_error`. No Dataset
named literally “Content Shelf” is required.

## Pass criteria

The release passes when all expected allow and deny results occur, same-Organization datasets
are visible through admitted Workspaces, no blocked dataset is exposed by listing or a known ID,
and the evidence identifies the user, Persona, selected Workspace, and Dataset used.

## Related documentation

- [User permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md)
- [Getting started](RESOURCE_REGISTRY_GETTING_STARTED.md)
- [Persona workspace-scope technical reference](RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md)
