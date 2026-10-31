# Resource Registry Getting Started

## Goal

Set up a new Organization, Workspace, user, Persona, and minimum Resource Registry BIF set.
This guide uses Django Admin and automatic Registry Namespace provisioning.

## Prerequisites

- You can administer Organizations, Workspaces, Personas, and Built-in Functions.
- The intended user exists in the configured identity system.
- Application migrations have been applied.

```bash
uv run --env-file .env python manage.py migrate
```

## Setup checklist

1. Create and enable the Organization.
2. Add its request hostname and Organization members.
3. Create and enable each Workspace; assign users directly where a `PRIVATE` Workspace requires it.
4. Provision one Registry Namespace for each Workspace.
5. Configure the Persona and its Workspace list.
6. Add the minimum Registry BIFs.
7. Create a workspace-scoped Dataset through [Dataset Management](DATASET_MAINTENANCE.md)
   and test the Persona against its published current version.

## 1. Create the Organization

In Django Admin, open **Organizations** and select **Add Organization**.
Set a stable lowercase **Slug**, enable **Is active**, and enable **Resource Registry enabled**.
The slug cannot be changed after creation.

Resource Registry must be enabled on the Organization before it can be enabled on a Workspace.

Saving a new Organization with this flag enabled, or changing it from off to on, also
enables Dataset Management atomically. Existing cleanup/retention policy is preserved;
new policy defaults to `SIMULATE`. Pending legacy registration/workbook jobs prevent
activation until drained. Saving unrelated fields does not resume an intentional
management pause. See [activation and migration](DATASET_MANAGEMENT_ENABLEMENT.md),
including organizations that were already enabled before this change.

## 2. Add the hostname and members

On the Organization page, add an **Organization host** inline row.
Use a lowercase FQDN without a port, for example `analytics.acme.example`.
Mark one host as **Primary host**.

The host is a security boundary: it resolves the trusted Organization for the request.

Use **Manage memberships** to open the Organization membership list, and add an
**Organization membership** for every intended user.
Choose **Org Member** for normal users or **Org Owner** for an organization manager.
Organization membership admits a user to an eligible `OPEN` Workspace in Resource Registry, but
does not admit a `PRIVATE` Workspace or grant Workspace administration.

## 3. Create and enable Workspaces

Create Workspaces from the Organization page or **Workspaces** in Django Admin.
For each Workspace, set its Organization, Name, stable Slug, and **Is active**.
Enable **Resource Registry enabled** and choose one active Workspace as **Is default** when needed.

After saving a `PRIVATE` Workspace, add normal users in **Workspace Users**.
Use **Workspace Owners** only for owners.
The admin form only allows users who already belong to the Organization.

For Resource Registry, normal users can access an active, Registry-enabled, provisioned Workspace
when they have direct Workspace access. Organization members can also use such a Workspace when its
state is `OPEN`; `OPEN` does not grant administration or Registry mutation capabilities.

## 4. Activate the Workspace Namespace

Each Resource Registry-enabled Workspace has one internal, immutable Registry Namespace. You do not
create, copy, or enter a Namespace UUID in Django Admin. When you save a Workspace with
**Resource Registry enabled**, the application automatically creates its Namespace if it does not
already exist.

The same is true for an Organization Namespace when Resource Registry is enabled on the Organization.
The Workspace Namespace must exist before data can be published to that Workspace.

When you publish a staged intake, choose the target Workspace destination. The intake publication uses
the Namespace that was already created for that Workspace; publishing an intake does not create a new
Namespace. A Workspace can own only one Namespace.

If the Workspace was enabled before this feature was introduced, or activation fails, ask the platform
or operations team to verify the Namespace mapping. Do not add a namespace UUID to Persona metadata,
prompts, browser storage, or tickets.

## 5. Configure the Persona

Open **Personas** in Django Admin, create or edit a Persona, and complete its normal model settings.
Then set **Workspaces** as a security boundary:

| Persona setup | Result |
| --- | --- |
| No Workspace | Resource Registry access is denied. |
| One authorized Persona Workspace | In the default `observe` policy, Registry automatically uses that Workspace. In `strict`, it must also be the active Workspace in the interface. |
| Two or more authorized Persona Workspaces | In `observe`, the user explicitly names one in chat. In `strict`, Registry uses the active authorized Workspace from the interface. |

Do not leave **Workspaces** empty for a Persona intended to use Resource Registry.
Even an eligible `OPEN` Workspace must be explicitly associated with the Persona before it can be
selected for that Persona's Registry BIFs.

## 6. Assign minimum Registry actions

On the Persona form, add rows in the **Built-in Function Persona Relationship** inline.
Persona relationships remain canonical BIF assignments. The model does not receive one tool per
row: all assigned discovery actions below are advertised inside `resource_registry_inspect`, using
the action name without the `resource_registry_` prefix. For a minimum metadata-only discovery
Persona, add:

| Canonical assigned BIF | Model-facing action | Purpose |
| --- | --- |
| `resource_registry_list_datasets` | `resource_registry_inspect` / `list_datasets` | List available workspace datasets. |
| `resource_registry_get_dataset` | `resource_registry_inspect` / `get_dataset` | Read safe dataset details. |
| `resource_registry_get_dataset_schema` | `resource_registry_inspect` / `get_dataset_schema` | Inspect columns and types. |
| `resource_registry_get_dataset_profile` | `resource_registry_inspect` / `get_dataset_profile` | Read aggregate profile information. |
| `resource_registry_list_dataset_versions` | `resource_registry_inspect` / `list_dataset_versions` | List Dataset versions. |

`resource_registry_get_dataset_context` is another optional `resource_registry_inspect` action.

For a Persona that must inspect rows or aggregates, also assign `resource_registry_plan_query`,
`resource_registry_query_dataset`, and optionally `resource_registry_explore_dataset`. For
asynchronous queries, assign `resource_registry_start_query_job`,
`resource_registry_get_query_job_status`, and `resource_registry_get_query_job_result`.
The first two query actions are published through `resource_registry_query`; exploration is an
`resource_registry_inspect` action. Query-job actions are also published through
`resource_registry_query`.
The default `restricted_reader` role can use these bounded read-only operations; no staff,
power-user, or publisher role is needed. Effective tenant/Dataset policies, visible columns,
and SQL row/time limits still apply. Separate analysis/chart/export BIFs require broader authority.

The API catalog groups canonical operations into virtual entries; those entries are not persisted
BIF records. Adding a whole group through that API assigns each visible canonical action. Use
individual canonical assignments when a Persona needs only a subset; grouped publication preserves
that subset in its `action` enum.

## 7. Publish a Dataset

Use the approved Registry upload and staged-intake process to publish data.
For a Dataset to be visible to this Persona, verify all of the following:

- It belongs to the selected Workspace with `visibility_scope=WORKSPACE`, or to the same
  Organization with `visibility_scope=ORGANIZATION`.
- Its status is `DRAFT` or `ACTIVE`, it is not archived, and any staged publication assignment
  required for visibility is active and not detached.
- The effective Dataset read constraints and tenant/Dataset policies admit it.

Another Workspace's `WORKSPACE` Dataset remains unavailable, even by known ID or to an
administrator using a Persona scoped to this Workspace.

## 8. Test the Persona

1. Sign in as an Organization member with direct Workspace access, or use an eligible `OPEN`
   Workspace.
2. Open the configured Organization hostname.
3. Start a conversation with the Persona.
4. Ask to list datasets.

With the default `RESOURCE_REGISTRY_SCOPE_POLICY=observe` and two or more authorized Persona Workspaces,
type an unambiguous workspace name or slug:

```text
Use campaign-analytics and list datasets.
```

In `strict`, select an authorized Persona Workspace in the interface before asking. The request's
active Workspace comes from `X-Workspace` or the Organization default. Naming a different Workspace
in chat cannot switch it; change the interface selection and retry. In either mode, verify that
only the selected Workspace's `WORKSPACE` Datasets and same-Organization
`ORGANIZATION` Datasets are listed or read. A stale tenant or session value cannot grant
access to another Workspace's `WORKSPACE` Datasets.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Registry unavailable | Organization is active and Registry-enabled. |
| Workspace not offered | Workspace is active, Registry-enabled, provisioned, and configured on the Persona. For `PRIVATE`, verify direct Workspace access; for `OPEN`, verify Organization membership. |
| Persona asks to choose a Workspace | In `observe`, name one authorized Persona Workspace in chat. In `strict`, select it in the interface. |
| No datasets | `no_visible_datasets` means none are visible in the selected scope; check namespace, Workspace/Organization visibility, active assignments, status, archive state, and policy. `no_matching_datasets` means a filter or offset returned no rows. |
| Registry action missing | Confirm the Persona/canonical-BIF relationship, Registry BIF seed migrations, and that the action is present in the advertised group. |
| Query denied | Confirm the query BIF, effective tenant/Dataset capability policy, visible schema, and SQL safety limits. Normal `restricted_reader` users can query under current defaults. |

## Security reminders

- Keep the Persona BIF list minimal.
- Do not add credentials, storage tokens, or namespace UUIDs to prompts or metadata.
- Re-test access after changing membership, Workspace enablement, Persona Workspaces, or BIFs.

## Related documentation

- [Resource Registry documentation index](RESOURCE_REGISTRY_DOCUMENTATION_INDEX.md)
- [Persona workspace-scope technical reference](RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md)
- [Resource Registry intakes API](RESOURCE_REGISTRY_INTAKES_API.md)
- [AI engine configuration](../../docs/AI_ENGINE_CONFIGURATION.md)
