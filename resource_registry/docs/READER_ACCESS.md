# Resource Registry reader access

This page describes the current read and query access for ordinary users and QA testers.
It applies to every Registry-enabled Workspace in an Organization; `Main` has no special
Resource Registry permission rule.

## Admission and selection

A user must be active, operate in a trusted, active, Registry-enabled Organization context,
and be authorized for an active, Registry-enabled Workspace with a mapped Registry
namespace. An Organization member can
access an `OPEN` Workspace without a direct Workspace assignment. A `PRIVATE` Workspace
requires a Workspace membership, an applicable Guardian object permission, or an existing
administrator/Organization-management grant. The selected Persona must have the required
Resource Registry BIF relationships and be configured for that Workspace. With one
authorized Persona Workspace, the assistant can resolve it automatically; with several,
the conversation must select one. Requests without `X-Workspace` use the Organization's
default Workspace at the HTTP scope layer, subject to Registry authorization and Persona
scope. This default is usually `Main`, but it has no extra Dataset visibility.

The selected Workspace can see its own `WORKSPACE`-visible Datasets and
`ORGANIZATION`-visible Datasets belonging to the same Organization. It cannot see another
Workspace's `WORKSPACE`-visible Datasets. Dataset status, active publication assignments,
read constraints, Guardian permissions, and effective tenant/Dataset policies further narrow
the result. A known Dataset ID never bypasses these checks.
`PRIVATE` is a Workspace admission state; Dataset visibility uses `WORKSPACE` or
`ORGANIZATION` scope.

### Example: Digitas

Assume `Main` is the default Workspace for the `Digitas` Organization. Both `Main` and `Data`
are active, `OPEN`, Registry-enabled, and mapped to namespaces. All four users are Digitas
members with the same allowed reader policies and a Persona configured for the Workspace
they select. `rr1` is `WORKSPACE`-visible in Main, `rr2` is
`WORKSPACE`-visible in Data, and `rr3` is `ORGANIZATION`-visible in Digitas. All three are
active with valid publication assignments.

| User | Direct Workspace assignments | Select Main | Select Data |
| --- | --- | --- | --- |
| A | Main | `rr1`, `rr3` | `rr2`, `rr3` |
| B | Data | `rr1`, `rr3` | `rr2`, `rr3` |
| C | Main and Data | `rr1`, `rr3` | `rr2`, `rr3` |
| D | None | `rr1`, `rr3` | `rr2`, `rr3` |

Direct assignment does not change this matrix while both Workspaces are `OPEN` and all
users have Organization access. If `Data` becomes `PRIVATE`, A and D cannot select Data
under these assumptions; B and C can. All four can still see `rr3` through Main. The
default Workspace creation service keeps Main `OPEN`; a user with no admitted Workspace
cannot reach `rr3` through a Workspace Persona.

## Ordinary reader capabilities

The default `restricted_reader` policy permits Dataset discovery, detail, context, schema,
profile, versions, query planning, bounded read-only SQL queries, exploration, and query
job start/status/result. It does not permit Dataset creation, publication, derived Dataset
creation, transformation, analysis/chart, or export capabilities. Normal users do not need
`is_staff`, `unicornai-power-users`, or a publisher role for the permitted interactions.
Persona tool assignment, manifest permissions, Workspace/Dataset scope, and explicit policy
denials still apply. An explicit tenant or Dataset denial takes precedence over the default.

Queries validate every referenced column against the caller's visible schema, including
columns inside functions, `WHERE`, grouping, `HAVING`, and `ORDER BY`. Existing row,
time, and resource limits still apply. The same validation applies to direct queries,
exploration, and query jobs. Job execution rechecks current Dataset access; a
`restricted_reader` can read only their own job status and results.

## Diagnose access

| Observation | Meaning / next check |
| --- | --- |
| `workspace_selection_required` | Select an authorized Workspace supported by the Persona. |
| `empty_reason: no_visible_datasets` | No Dataset is visible in the selected scope; check Registry enablement, namespace, status, assignments, visibility, and read policy. |
| `empty_reason: no_matching_datasets` | A search/filter/offset yielded no rows; clear the filter or pagination offset. |
| `capability_error` | The requested tool capability is denied; inspect effective tenant and Dataset policies and Persona tool assignment. |
| `invalid_column` | SQL refers to a column outside the visible schema, including in an expression. |

There is no requirement for a Dataset named literally “Content Shelf.” A Creative Assembly
Persona consumes the same authorized Registry scope and may use a same-Organization
`ORGANIZATION`-visible Dataset through an authorized Workspace.

## Existing policy copies after deployment

The new code default does not rewrite persisted GovernancePolicy revisions. Audit each
environment after deployment with:

```bash
uv run --env-file .env python manage.py upgrade_registry_reader_policies
```

The command is a dry run by default and reports active capability/schema policies.
If it reports `classification=legacy_copy action=candidate`, apply only those exact old
default copies with `--apply` (optionally scoped by `--tenant-id`). It creates an active
revision and updates Dataset policy assignments. `classification=explicit_denial` is
retained; review such policies with the tenant owner rather than changing them through
this command. An environment with no candidates needs no policy migration. Different
environment databases can have different persisted policy revisions even when running
the same application code.

## Code references

- `resource_registry/authorization/registry_authorization_context_service.py`
- `resource_registry/dataset_scope.py` and `resource_registry/dataset_visibility.py`
- `resource_registry/assistant/persona_workspace_scope.py`
- `resource_registry/policies/defaults.py` and `resource_registry/bifs/manifest.py`
- `resource_registry/services/dataset_query_executor.py`
- `resource_registry/management/commands/upgrade_registry_reader_policies.py`
