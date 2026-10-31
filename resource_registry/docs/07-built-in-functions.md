# Resource Registry Built-in Functions

### Registry Namespace Admission

Synchronous Resource Registry BIFs first enforce the active Persona's admitted Workspace scope.
When `RESOURCE_REGISTRY_SCOPE_POLICY=strict`, assistant and BIF operations use the active authorized
Persona Workspace resolved from `X-Workspace` or the Organization default. BIF arguments and REST
scope headers cannot switch it. The policy defaults to `observe`, which preserves the legacy
selector while logging discrepancies. A Registry-capable Persona's selected Workspace scope
takes precedence in every mode; its BIF namespace argument cannot broaden access. Without a
Persona scope, the legacy selector path may use an explicit authorized BIF namespace argument.
If it is absent, admission attempts an authorized
Workspace namespace, then an authorized Organization namespace, then consistent metadata selectors
from `request.resource_registry_tenant_id`, `session["resource_registry_tenant_id"]`, and Persona
metadata. Conflicting metadata selectors are rejected.

The admission service validates the selection against the authenticated principal's authorized
Organization and Workspace scope, then forwards only the admitted namespace to capability checks
and Registry services. Context-manager tenant values and generic `request.tenant_id` or
`session["tenant_id"]` are not selectors in this path.

The selected Persona Workspace admits its own `WORKSPACE` Datasets and same-Organization
`ORGANIZATION` Datasets, subject to active assignments, Dataset restrictions, and policy.
The default `restricted_reader` can use metadata, planning, bounded read-only queries,
exploration, and own query jobs; explicit tenant/Dataset denials take precedence.

## Built-in Function Catalog

The Resource Registry BIF manifest is the authoritative catalog of canonical operations and their
capabilities. Model providers do not receive those 31 operations as 31 independent tools. They
receive at most these four grouped tools, each with the envelope `{"action": "...", "arguments":
{...}}`:

| Model-facing tool | Permitted `action` values |
| --- | --- |
| `resource_registry_inspect` | `list_datasets`, `get_dataset`, `get_dataset_context`, `get_dataset_schema`, `get_dataset_profile`, `list_dataset_versions`, `list_external_jobs`, `explore_dataset`, `analyze_dataset` |
| `resource_registry_query` | `plan_query`, `query_dataset`, `create_filtered_dataset`, `get_query_run`, `start_query_job`, `get_query_job_status`, `get_query_job_result` |
| `resource_registry_manage_outputs` | `create_chart`, `plan_export`, `generate_artifact`, `get_analysis_artifact`, `get_chart_artifact`, `get_export_plan`, `start_intelligence_job`, `get_intelligence_job_status`, `get_intelligence_job_result` |
| `resource_registry_manage_transformations` | `start_transformation_run`, `get_transformation_run`, `list_transformation_runs`, `list_transformation_steps`, `list_transformation_outputs`, `get_transformation_diagnostics` |

A grouped tool advertises only actions whose canonical `resource_registry_<action>` BIF is assigned
to the active Persona. Dispatch resolves the selected action back to that canonical BIF and repeats
its assignment, namespace, capability, Dataset, and job-ownership checks. A group does not combine
or broaden the authority of its actions.

For example, a model lists datasets through this tool call:

```json
{
  "action": "list_datasets",
  "arguments": {}
}
```

Canonical BIF records, Persona relationships, permissions, and policy rules remain individual. The
administrative catalog presents virtual groups, but they are not persisted BIF records. See the
[BIF overview](RESOURCE_REGISTRY_BIFS_OVERVIEW.md) and
[BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md) for the model-facing and administrative contracts.
