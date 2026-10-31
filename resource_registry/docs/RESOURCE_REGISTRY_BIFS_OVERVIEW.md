# Resource Registry BIFs — Technical Overview

## Purpose

Resource Registry built-in functions (BIFs) are the controlled tool boundary between a
Persona/LLM and registered data. They let an LLM discover datasets, inspect their metadata,
analyze data, create derived outputs, and observe long-running work without giving the model
direct database, storage, namespace, or provider access.

The complete per-BIF reference is [Resource Registry BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md).

## Published tool contract

The platform keeps 31 canonical Registry BIF records for Persona assignment, authorization, policy,
and handler dispatch. It advertises those operations to every supported model provider as at most
four stable, grouped tools instead of 31 independent tool definitions. A group is advertised only
when the Persona has at least one of its canonical actions; its `action` enum contains only the
actions that Persona is assigned.

| Model-facing tool | Canonical actions exposed through `action` |
| --- | --- |
| `resource_registry_inspect` | `list_datasets`, `get_dataset`, `get_dataset_context`, `get_dataset_schema`, `get_dataset_profile`, `list_dataset_versions`, `list_external_jobs`, `explore_dataset`, `analyze_dataset` |
| `resource_registry_query` | `plan_query`, `query_dataset`, `create_filtered_dataset`, `get_query_run`, `start_query_job`, `get_query_job_status`, `get_query_job_result` |
| `resource_registry_manage_outputs` | `create_chart`, `plan_export`, `generate_artifact`, `get_analysis_artifact`, `get_chart_artifact`, `get_export_plan`, `start_intelligence_job`, `get_intelligence_job_status`, `get_intelligence_job_result` |
| `resource_registry_manage_transformations` | `start_transformation_run`, `get_transformation_run`, `list_transformation_runs`, `list_transformation_steps`, `list_transformation_outputs`, `get_transformation_diagnostics` |

Every call uses exactly this envelope. Its `action` is the canonical BIF name without the
`resource_registry_` prefix; `arguments` preserve that action's original parameters and defaults.

```json
{
  "action": "analyze_dataset",
  "arguments": {
    "dataset_id": "<dataset UUID>",
    "analysis_type": "summary"
  }
}
```

The grouped adapter resolves the action to its canonical BIF before execution and repeats its
normal Persona-assignment, namespace, capability, Dataset, and job-ownership checks. A group is a
transport projection, not an authorization grant. Canonical calls remain supported for saved calls
and internal consumers, but are never advertised to the model.

## The model does not authorize itself

The LLM can propose a tool call, but the platform supplies and verifies the trusted context:

```mermaid
flowchart LR
    U[User message] --> R[Assistant runtime adapter]
    R --> N[Resolve Organization, user, Persona, and Workspace]
    N --> P[Routing policy / LLM tool selection]
    P --> G[Grouped Registry tool and action]
    G --> B[Canonical Registry BIF]
    B --> A[Namespace admission and capability policy]
    A --> D[Authorized Registry services]
    D --> S[Sanitized tool result]
    S --> L[LLM response]
```

1. The runtime first confirms that the active Persona has at least one canonical Registry BIF.
2. It builds authorization from the host-resolved Organization, authenticated user, enabled
   Workspaces, Registry namespaces, Persona configuration, and capability policy.
3. With the default `observe` scope policy, a Registry-capable Persona with one authorized Workspace
   uses it; with multiple authorized Workspaces, the user selects one in chat. Under `strict`, assistant and BIF calls
   use the active authorized Persona Workspace from the interface, regardless of how many the
   Persona has. A chat request to change Workspaces requires an interface switch.
4. The grouped dispatcher validates the `action`/`arguments` envelope, then the canonical BIF
   validates its identifiers, checks its capability, builds a read/execution context, and invokes
   a Registry service. It returns JSON with sanitized metadata or results.
5. The LLM uses that result as evidence for its answer; it never receives direct credentials or
   broad data-store authority.

## Authorization layers

Every BIF is protected by more than the LLM's decision to call it.

| Layer | Responsibility |
| --- | --- |
| Persona BIF assignment | Determines whether the active Persona may expose the Registry function at all. |
| Organization and Workspace admission | Requires active, Registry-enabled Organization and Workspace access. |
| Persona Workspace scope | Enforces zero/one/multiple Workspace behavior and admits selected-Workspace `WORKSPACE` plus same-Organization `ORGANIZATION` Datasets. |
| Capability policy | Evaluates Registry role, scopes, policy overrides, and disabled capabilities. |
| Dataset authorization | Enforces tenant, Workspace dataset set, and any dataset ACL before an ID is read or used. |
| Service validation | Validates UUIDs, query safety, schemas, limits, and operation-specific inputs. |
| Result sanitization | Avoids returning unsafe storage, credential, or internal provider information to the model. |

`restricted_reader` permits metadata, planning, bounded read-only queries, exploration,
and the user's own query jobs under current defaults. `reader` and `admin` can use the
broader catalog subject to capability policy. A disabled capability overrides an otherwise
eligible role. See [User permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md).

## BIF families

| Family | Model-facing group | Typical actions |
| --- | --- | --- |
| Discovery and metadata | `resource_registry_inspect` | List datasets, get dataset, schema, profile, versions, and context. |
| Exploration and analysis | `resource_registry_inspect` | Explore dataset and analyze dataset. |
| Query and derived data | `resource_registry_query` | Plan/query dataset, query jobs, create filtered dataset, and get query run. |
| Durable outputs | `resource_registry_manage_outputs` | Charts, export plans, artifacts, and intelligence jobs. |
| Operations and transformations | `resource_registry_inspect` / `resource_registry_manage_transformations` | External jobs and transformation run/step/output actions. |

## How the LLM uses the catalog

The assistant runtime supports deterministic routes for common data questions, then calls the
existing BIF dispatch mechanism. It can also leave unrelated messages to the normal model flow.
The exact actions available to the model are the canonical Registry BIFs assigned to its Persona
and allowed by the request capability context.

Common grouped-tool sequences are:

```text
Discovery:    inspect/list_datasets → inspect/get_dataset → inspect/get_dataset_schema
Exploration:  inspect/get_dataset_schema → inspect/explore_dataset OR query/plan_query → query/query_dataset
Async query:  query/start_query_job → query/get_query_job_status → query/get_query_job_result
Analysis:     manage_outputs/start_intelligence_job → manage_outputs/get_intelligence_job_status
Output:       query/query_dataset or inspect/analyze_dataset → manage_outputs/create_chart
Transform:    manage_transformations/start_transformation_run → manage_transformations/get_transformation_run
```

These are guidance patterns, not permission grants. Each canonical BIF repeats capability and
scope checks, so a later call cannot inherit broader access from an earlier result.

## Side effects and risk

Most metadata actions are read-only. A subset has durable effects, such as submitting a job,
creating a filtered dataset, producing a chart/export artifact, or starting a transformation.
Those canonical BIFs are marked `is_read_only=False` in the manifest and commonly have HIGH risk.
`resource_registry_inspect` is therefore not a read-only permission: its `analyze_dataset` action
may persist analysis artifacts.

`resource_registry_query_dataset` is also classified as non-read-only because it executes a
controlled data operation, even though it is expected to use read-only SQL. The query service
requires safe SQL and bounded results. Every column reference in the SQL tree, including
inside functions and filters, is checked against the caller's visible schema. Query-job
execution rechecks current access; ordinary users can read only their own job status/results.

## Integration points

- Registry BIF definitions and capability metadata:
  `resource_registry/bifs/manifest.py`
- Main Registry BIF handlers:
  `BotServer/builtin_functions/resource_registry_functions.py`
- Transformation BIF handlers:
  `resource_registry/bifs/transformation/handlers.py`
- Grouped publication, schemas, and dispatch:
  `resource_registry/bifs/grouped_publication.py`, `grouped_tools.py`, and `grouped_runtime.py`
- Persona capability detection and deterministic routing:
  `resource_registry/assistant/persona_capabilities.py` and
  `resource_registry/assistant/runtime_adapter.py`
- Namespace admission and read authorization:
  `resource_registry/bifs/read_only_namespace_admission.py` and `resource_registry/read_auth/`

## Related documentation

- [Resource Registry BIF catalog](RESOURCE_REGISTRY_BIF_CATALOG.md)
- [Persona workspace-scope technical reference](RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md)
- [Resource Registry user permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md)
