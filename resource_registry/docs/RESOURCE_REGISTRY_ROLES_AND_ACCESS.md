# Resource Registry: roles and access

## Purpose

Explain, for Workspace administrators, end users, and developers, how Resource Registry decides
what a person can do. This is the current implementation, not a proposal for a future permission
model.

## Quick answer

There are three effective Registry roles:

| Effective role | Intended user | Typical access |
| --- | --- | --- |
| `restricted_reader` | Ordinary user or QA tester consuming authorized data | List/read datasets, plan and run bounded read-only queries, explore data, and use their own query jobs. |
| `reader` | User approved for broader analytical operations | Everything available to `restricted_reader`, plus analysis, charts, exports, derived datasets, other jobs, and read-only transformation history. |
| `admin` | Platform administrator | All Registry capabilities, including starting a transformation run. |

`restricted_reader` is not a Django group. It is the default Registry role for a signed-in user
who does not qualify for the `admin` or `reader` role below. A scoped administrator receives
`reader` for Registry BIFs unless superuser status or `unicornai-admin` grants `admin`.

## What an administrator should configure

### Ordinary user who can read and query authorized datasets

1. Do not add a privileged platform group solely for Registry access.
2. Add the user to **Workspace Users** for every `PRIVATE` Workspace whose datasets they may
   access. An Organization member may instead use an eligible `OPEN` Workspace as described in
   [Workspace admission](#workspace-admission).
3. Use a Persona associated with those Workspaces and assign the required discovery, query,
   exploration, and query-job BIFs.

The user receives `restricted_reader` automatically. This role can run bounded read-only SQL
against visible columns and use its own query jobs. It cannot create derived datasets,
publish, transform, analyze through the separate analysis BIF, create charts, or export.
For a metadata-only Persona, assign only the metadata BIFs; Persona tool assignment can narrow
the experience without changing the user's Registry role.

### User who needs broader analysis, charts, or exports

1. Add the user to the `unicornai-power-users` group through the identity-management process.
2. Add the user to the appropriate **Workspace Users** entries for `PRIVATE` Workspaces. An
   eligible `OPEN` Workspace is available to Organization members without a direct Workspace
   membership.
3. Associate the Persona with those Workspaces and give the Persona the required analysis BIFs.

The group makes the effective Registry role `reader`. It is unnecessary for ordinary
query, planning, exploration, or query-job access. Do not use `unicornai-publishers` or
`unicornai-bif-managers` merely to obtain Registry analysis access: they also grant unrelated
platform responsibilities.

### Platform administrator

Use `unicornai-admin` only for users who need platform-wide administration. It maps to `admin`
and should not be used as a substitute for normal Registry access.

The role table below describes Persona BIF access. Staged-intake operations have separate
authorization: the HTTP `publish/` endpoint accepts superusers, `unicornai-admin`, and
`unicornai-scoped-admin` only when the uploader also has both publication permissions and
destination access. Django Admin upload/publish requires the global intake-management permission
or superuser status. Django Admin destination removal also accepts active staff in
`unicornai-admin` globally and `unicornai-scoped-admin` within the current Organization host.
See [Intake permissions](RESOURCE_REGISTRY_INTAKE_LIFECYCLE.md#permissions).

## Group-to-role mapping

The backend derives the effective role on every Registry request:

| User condition or Django group | Effective Registry role |
| --- | --- |
| Superuser or `unicornai-admin` | `admin` |
| `unicornai-scoped-admin` | `reader` |
| `unicornai-power-users` | `reader` |
| `unicornai-publishers` | `reader` |
| `unicornai-bif-managers` | `reader` |
| None of the above | `restricted_reader` |

The mapping uses the highest applicable row: a scoped administrator who is also a superuser or
`unicornai-admin` receives `admin`. The scoped-admin group also affects Organization/Workspace
access and certain staged-intake operations, but does not grant the Registry BIF `admin` role.

The implementation is in
[`resource_registry/authorization/registry_roles.py`](../authorization/registry_roles.py),
function `derive_registry_roles`. The BIF wrapper `_derive_roles` calls it.

## BIFs permitted by role

Each BIF must be both assigned to the active Persona and allowed by the user's effective role.
The table uses canonical BIF names because roles and policies are evaluated for those individual
operations. Model providers receive the matching grouped tool and only the assigned actions inside
it: for example, an assigned `resource_registry_query_dataset` appears as
`resource_registry_query` with `action="query_dataset"`. A group neither grants nor exposes its
other actions.

| Role | Permitted BIFs |
| --- | --- |
| `restricted_reader` | `resource_registry_list_datasets`, `resource_registry_get_dataset`, `resource_registry_get_dataset_context`, `resource_registry_get_dataset_schema`, `resource_registry_get_dataset_profile`, `resource_registry_list_dataset_versions`, `resource_registry_plan_query`, `resource_registry_query_dataset`, `resource_registry_explore_dataset`, `resource_registry_start_query_job`, `resource_registry_get_query_job_status`, `resource_registry_get_query_job_result` |
| `reader` | Every Registry BIF except `resource_registry_start_transformation_run` |
| `admin` | Every Registry BIF |

For a metadata-only Persona, assign only the six metadata BIFs. The model then receives one
`resource_registry_inspect` tool containing only those actions. A user cannot obtain a capability
simply because it is assigned to a Persona: the effective policy must also permit it. Explicit
tenant and Dataset denials take precedence over the default role capabilities.

## Access is the intersection of several checks

Being a `reader` or `restricted_reader` does not grant access to every dataset. A normal user can
use a Registry BIF only when all of these are true:

1. The request uses the correct active, Registry-enabled Organization.
2. The Workspace is active and Registry-enabled.
3. The user is admitted to that Workspace under the Registry policy.
4. The active Persona is associated with that Workspace.
5. The Persona has the requested BIF.
6. The effective Registry role and capability policy permit that BIF.
7. The Dataset is visible through the selected Workspace: its own `WORKSPACE` Dataset or a
   same-Organization `ORGANIZATION` Dataset. Status, publication assignments, Dataset ACL,
   schema visibility, and explicit policy restrictions still apply.

This makes Workspace admission the answer to “which data may this person see?” and the Registry
role the answer to “which actions may this person take with that data?”

## Workspace admission

Resource Registry starts with the existing Organization and Workspace access rules, then applies
its Registry-specific requirements. A Workspace must be active, have **Resource Registry enabled**,
and have its persisted Registry Namespace before it can be offered.

For an eligible Workspace, Registry admits a user when at least one of the following applies:

- The user has direct Workspace access, including a Workspace membership or Guardian
  `view_workspace` or `manage_workspace` object permission.
- The user is an Organization member and the Workspace state is `OPEN`.
- The user is an Organization owner, scoped administrator, platform administrator, or superuser
  with access under the existing Organization/Workspace ACL.

`OPEN` is a Registry admission rule, not an administrative grant. It lets an Organization member
use the Workspace namespace through the Persona and BIFs they are already allowed to use; it does
not grant Workspace management, Registry administrative capabilities, or mutation privileges.
An Organization member alone does not admit a `PRIVATE` Workspace.

The Organization namespace is separately available for Registry read/selection to a user with an
Organization membership or the existing Guardian `view_organization` permission. This does not
grant Registry mutation capabilities; those continue to use their existing checks.

## Common questions

### Why can a user list a dataset but not query it?

Check that the Persona has the query BIF, the selected Workspace and Dataset are authorized,
and the effective tenant/Dataset capability and schema policies allow the request. Current
`restricted_reader` defaults permit bounded read-only queries. Older persisted policy copies
may still deny them; audit with `upgrade_registry_reader_policies` before changing policy.
SQL references to columns outside the visible schema return `invalid_column`.

### Can individual Django permissions replace `unicornai-power-users`?

Individual Django permissions do not change the derived Registry role. They are also not
needed to make a normal user a `restricted_reader` or to enable the default read/query tools.
Broader `reader` operations still require their role, capability, and operation-specific grants.

### Can we create a new group such as `registry-analysts`?

Not through administration alone. A new group will resolve to `restricted_reader` unless backend
role derivation and the capability policy are deliberately extended to recognize it. That default
already includes read/query/explore. A broader role mapping is a development and security-policy
change requiring allowed and denied BIF tests.

### Can a user be an analyst in one Workspace and view-only in another?

Not with the current global-group mapping. `unicornai-power-users` resolves to `reader` for all
Registry requests; Workspace admission still limits which datasets are reachable. A future
Workspace-role model would represent `Viewer`, `Analyst`, and `Workspace administrator` per
Workspace rather than inferring one Registry role from a global group.

## Current-state and future-state guidance

The current system uses global platform groups to derive Registry roles and Workspace admission
to limit data scope. The backend is the authorization source of truth; frontend visibility must
mirror, never replace, its authorization decision.

For a future user-level and Workspace-level authorization model, document and agree on at least:

- Workspace roles and their capability sets, such as Viewer, Analyst, and Workspace administrator.
- Migration from the existing global groups without granting broader access during the transition.
- One backend authorization API or policy decision that both save operations and frontend controls
  consume.
- Audit events and tests for role changes, Workspace membership changes, and denied operations.

## Related documentation

- [Resource Registry user permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md)
- [Resource Registry BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md)
- [Resource Registry BIF technical overview](RESOURCE_REGISTRY_BIFS_OVERVIEW.md)
- [Reader access matrix and policy upgrade](READER_ACCESS.md)
