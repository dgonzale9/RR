# Resource Registry User Permissions

Dataset Maintenance REST/Admin authorization is separate from Persona BIF roles.
Within an admitted destination, creation requires management authority or Guardian
`resource_registry.create_datasets` on the namespace; editing requires management
authority or `resource_registry.change_dataset` on the dataset. Sensitive publication,
manual archive/recovery, manual historical restoration and deletion require a destination
manager. External proposal creation additionally requires
`resource_registry.synchronize_dataset_sources` on the namespace; being a manager alone
does not supply that grant. Metadata editing does not grant external-content replacement.
Admin also requires active staff access. Enabling management assigns no new user grants.
See [activation and permissions](DATASET_MANAGEMENT_ENABLEMENT.md).

## Purpose

This guide explains who can use a Workspace through Resource Registry and why access may be denied.
It is intended for Organization and Workspace administrators.

## The access checks

For a normal user to use Resource Registry in a Workspace, all of these checks must pass:

1. The request is made through the correct Organization host.
2. The Organization is active and Resource Registry-enabled.
3. The Workspace is active and Resource Registry-enabled.
4. The user has direct Workspace access, or is an Organization member using an eligible `OPEN`
   Workspace.
5. The Workspace is assigned to the active Persona.
6. The Persona has the requested canonical Resource Registry BIF action.
7. The user's Registry role permits that action.
8. The Dataset is visible through the selected Workspace and passes any tenant/Dataset policy,
   read constraint, and active publication-assignment check.

If any check fails, the Workspace and its Datasets are unavailable to that user.

## Assigning a user to a Workspace

First add the user to the Organization in Django Admin.
Then open each `PRIVATE` Workspace and add the user under **Workspace Users**.

This creates the Workspace access assignment used by Resource Registry.
Use **Workspace Owners** only for users who must manage Workspace membership.

An Organization membership by itself does not grant Registry access to a `PRIVATE` Workspace.
It does admit the member to an `OPEN` Workspace only when that Workspace is active, Resource
Registry-enabled, and has a persisted Registry Namespace. Guardian `view_workspace` and
`manage_workspace` object permissions are also direct Workspace access paths.
`PRIVATE` describes Workspace admission; Dataset visibility is separately `WORKSPACE` or
`ORGANIZATION` scope.
Likewise, a Persona assignment by itself does not grant the user access to the Persona's Workspace.
The Persona Workspace assignment is always required.

## What happens when a user is not assigned

If a normal user has neither direct Workspace access nor Organization membership for an eligible
`OPEN` Workspace:

- That Workspace is not an authorized Registry destination for the user.
- Its Datasets are not listed, read, or queried by the user.
- A Persona assigned only to that Workspace cannot use Resource Registry for that user.
- If the Persona has no other Workspace authorized for the user, Registry access is denied.

Adding a user to a different Workspace does not grant access to this Workspace. Marking a Workspace
`OPEN` does not grant Workspace administration or Registry mutation privileges.

## Persona Workspace rules

The active Persona applies an additional boundary:

| Persona configuration | Result for an authorized user |
| --- | --- |
| No configured Workspace | Resource Registry is denied. |
| One configured and authorized Workspace | In the default `observe` policy, it is selected automatically. In `strict`, it must match the active Workspace. |
| Two or more configured and authorized Workspaces | In `observe`, name one in chat. In `strict`, select an authorized Persona Workspace in the interface. |

The active Workspace is resolved from `X-Workspace` or the Organization default. In `strict`, a
chat instruction cannot switch it; the user changes the interface selection and retries. In
`observe`, a Persona with multiple authorized Workspaces requires an explicit chat choice, so browser Workspace,
stale session, and prior tenant values cannot silently choose it.

## Registry roles and BIFs

Workspace access controls *where* a user may work. Registry roles control *which BIFs* the user may use.

| Registry role | Typical capability |
| --- | --- |
| `restricted_reader` | Dataset listing/details/schema/profile/context/versions; query planning, bounded read-only SQL, exploration, and own query jobs. |
| `reader` | The normal reader tools plus broader analysis, derived datasets, charts, exports, and other permitted Registry BIF actions, except `resource_registry_start_transformation_run`. |
| `admin` | All Registry BIFs. |

The canonical query BIF must be assigned to the Persona, but the default `restricted_reader` role
can use it. Models receive that assignment as `resource_registry_query` with
`action="query_dataset"`. Queries are read-only and bounded by the existing SQL row/time limits.
Every referenced column, including inside functions and filters, must be visible to the user.
Explicit tenant/Dataset denials still take precedence.

## Administrators

Platform administrators may access active, Registry-enabled Workspaces without a normal Workspace Users
assignment. Organization members can also use an eligible `OPEN` Workspace without that assignment.
Neither path overrides the Persona Workspace Dataset boundary.

When an administrator uses a scoped Persona, the Persona can access the selected Workspace's
`WORKSPACE` Datasets and same-Organization `ORGANIZATION` Datasets. Other Workspaces'
`WORKSPACE` Datasets remain unavailable through that Persona. Administrator status does not
bypass this selected scope.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Workspace is missing | Confirm it is active, Registry-enabled, and provisioned. For `PRIVATE`, add the user to **Workspace Users** or grant an object permission; for `OPEN`, confirm Organization membership. |
| Registry is denied | Confirm Organization, Workspace, Persona, and the applicable direct-access or `OPEN` admission rule all match. |
| Query is denied | Confirm the query BIF, effective tenant/Dataset capability policy, visible columns, and row/time limits. `restricted_reader` can query by default. |
| Dataset is missing | Confirm the selected Workspace, same-Organization visibility, active assignment, Dataset status, and archive state. An empty unfiltered list reports `no_visible_datasets`. |
| Workspace selection is required | Select an authorized Workspace configured on the Persona; Main is only the HTTP default and does not override Persona selection. |

## Related documentation

- [Getting started](RESOURCE_REGISTRY_GETTING_STARTED.md)
- [Publishing datasets](RESOURCE_REGISTRY_DATASET_PUBLICATION.md)
- [Persona workspace-scope technical reference](RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md)
- [Reader access matrix and policy upgrade](READER_ACCESS.md)
