# Resource Registry BIF Catalog

## How to read this catalog

Each BIF receives trusted request and context-manager objects from BotServer. `tenant_id` is a
Registry namespace identifier resolved by the platform, not an identifier the LLM can use to
expand access. Dataset, version, job, run, and artifact IDs are validated and re-authorized by
the relevant BIF.

All BIFs return structured, sanitized JSON. The LLM uses that JSON to form an answer or select a
next BIF. It does not call Registry storage or providers directly.

## 1. Discovery and metadata

| BIF | What it does and why | How it works and LLM relationship |
| --- | --- | --- |
| `resource_registry_list_datasets` | Lists datasets available in the current Registry context; used to discover candidates. | Applies search, status, source-type, limit, and offset filters through the authorized read facade. An empty result distinguishes `no_visible_datasets` from `no_matching_datasets`. The LLM normally calls it first when the user asks what data is available. |
| `resource_registry_get_dataset` | Returns one dataset's identity and summary metadata; used to confirm the dataset selected for a task. | Validates and authorizes the dataset ID, then reads it through the facade. The LLM uses its result before asking detailed questions. |
| `resource_registry_get_dataset_context` | Returns contextual information for a dataset; used to understand provenance and usable context. | Reads the authorized context and can include controlled explanation/diagnostics. The LLM uses it to ground a response without reading storage directly. |
| `resource_registry_get_dataset_schema` | Returns columns and types for a dataset version; used to choose valid fields and prevent invented columns. | Reads the selected/latest version's schema through the facade, optionally with explainability. The LLM should use it before structured exploration, query, charts, or exports. |
| `resource_registry_get_dataset_profile` | Returns quality and statistical profile information; used to understand distributions and data condition. | Reads the authorized profile for a dataset/version, optionally with explainability. The LLM uses it for high-level data-quality answers. |
| `resource_registry_list_dataset_versions` | Lists dataset versions; used to select or explain historical versus current data. | Validates pagination and returns only authorized versions. The LLM uses a returned version ID in later version-aware calls. |
| `resource_registry_list_external_jobs` | Lists external processing jobs associated with datasets; used for operational visibility. | Applies dataset, status, provider, and job-type filters through the read facade. It needs the corresponding capability and is normally a reader/admin operation. |

## 2. Controlled exploration and queries

| BIF | What it does and why | How it works and LLM relationship |
| --- | --- | --- |
| `resource_registry_explore_dataset` | Performs bounded value exploration: distinct values, top values, samples, searches, or a column profile. | Reads schema first, validates requested columns/terms/operation, then calls the exploration service with a bounded limit. The LLM uses it for simple questions without composing SQL. |
| `resource_registry_plan_query` | Produces a safe query plan for a dataset question; used before executing supported analysis. | Validates/authorizes the dataset and invokes deterministic schema-driven planning. Supported shapes include counts, previews, simple filters, aggregates, and closed Creative Assembly image-asset or template inventory lookups. Template searches require physical `assettype` `Template`. Creative Assembly inventory projections include non-sensitive `assettype` and optional `instructions` context when those columns exist; provider URLs remain excluded. The LLM can present or use the plan before `query_dataset` or an async query job. |
| `resource_registry_query_dataset` | Executes a controlled SQL query against one dataset; used for direct analytical answers. | Re-authorizes the dataset and applies query routing plus SQL safety and visible-column validation before execution, including function and filter references. Its sanitized bounded rows/aggregates become LLM evidence. The default `restricted_reader` may use this HIGH-risk, non-read-only BIF when policy admits it. |
| `resource_registry_start_query_job` | Submits a durable query job; used when a query should run asynchronously. | Validates the request and visible columns, then persists an authorized job for a worker. The worker rechecks current access before execution. The LLM receives a job reference, then polls status/result BIFs. |
| `resource_registry_get_query_job_status` | Returns the state of an async query job; used to tell the user whether it is queued, running, complete, or failed. | Re-authorizes the job's dataset scope and reads safe lifecycle metadata. An ordinary `restricted_reader` can read only their own job. |
| `resource_registry_get_query_job_result` | Returns the completed async query result; used to answer after a query job finishes. | Validates job ID and dataset scope, then returns sanitized completed output. An ordinary `restricted_reader` can read only their own job result. |
| `resource_registry_get_query_run` | Retrieves a persisted query-run record; used to revisit reproducible query evidence. | Reads an authorized query-run artifact by ID. The LLM can summarize its stored, sanitized metadata and result reference. |

## 3. Analysis and intelligence jobs

| BIF | What it does and why | How it works and LLM relationship |
| --- | --- | --- |
| `resource_registry_analyze_dataset` | Runs a supported analysis request against a dataset; used for deterministic analysis such as previews, summaries, distributions, or top categories. | Resolves and authorizes the dataset, validates the analysis request, and uses Registry analysis/query services. The LLM requests an analysis type rather than accessing raw files. |
| `resource_registry_start_intelligence_job` | Starts a durable intelligence/analysis job; used for work that should be processed asynchronously. | Validates the dataset and request, authorizes capability, and creates a worker job. The LLM receives a job ID for later status/result calls. |
| `resource_registry_get_intelligence_job_status` | Returns the lifecycle state of an intelligence job. | Re-authorizes the job and returns safe progress/status data. The LLM uses it to report progress without claiming completion early. |
| `resource_registry_get_intelligence_job_result` | Returns an available intelligence-job result. | Re-authorizes the job dataset and reads its sanitized terminal output. The LLM uses this as evidence for the final analysis response. |
| `resource_registry_get_analysis_artifact` | Retrieves a persisted analysis artifact. | Reads a tenant-authorized artifact by ID and sanitizes it. The LLM uses it to revisit an earlier analysis without re-running it. |

## 4. Derived datasets, charts, and exports

| BIF | What it does and why | How it works and LLM relationship |
| --- | --- | --- |
| `resource_registry_create_filtered_dataset` | Creates a derived dataset from a numeric filter; used when a reusable, limited subset is needed. | Validates the source schema, supported operator/threshold, and row limit; executes controlled filtering; then registers the derived CSV as a Dataset. The LLM should explain the filter and use the returned derived dataset ID only after success. |
| `resource_registry_create_chart` | Creates a chart artifact for a dataset request. | Reads/authorizes schema, validates chart inputs through the chart service, and persists a successful chart artifact. The LLM supplies the requested presentation and references the resulting artifact. |
| `resource_registry_get_chart_artifact` | Retrieves a previously created chart artifact. | Reads the authorized chart artifact by ID and returns sanitized metadata/content. The LLM can use it when the user asks to revisit an existing chart. |
| `resource_registry_plan_export` | Creates an export plan for a requested format and purpose. | Reads the authorized schema, validates format/request through the export planner, and persists the plan. The LLM can show the plan or pass its ID to artifact generation. |
| `resource_registry_get_export_plan` | Retrieves a persisted export plan. | Reads an authorized plan by ID. The LLM uses it to describe planned output or continue a paused workflow. |
| `resource_registry_generate_artifact` | Generates the artifact described by an export plan. | Re-authorizes the export plan and invokes artifact generation under the Registry context. The LLM receives a safe result/reference, not direct storage access. |

## 5. Transformations

| BIF | What it does and why | How it works and LLM relationship |
| --- | --- | --- |
| `resource_registry_start_transformation_run` | Starts an authorized transformation run; used to apply an approved transformation definition to Registry data. | The transformation boundary admits the namespace, checks HIGH-risk capability and required permissions/scopes, then creates a durable execution run. The LLM receives a run reference rather than executing transformation code. |
| `resource_registry_get_transformation_run` | Retrieves one transformation run's state and summary. | Validates the run identifier and reads it through the transformation read context. The LLM uses it for progress or outcome reporting. |
| `resource_registry_list_transformation_runs` | Lists transformation runs in the current authorized context. | Uses the transformation read context and optional filtering/pagination contracts. The LLM uses it to find a run before inspecting it. |
| `resource_registry_list_transformation_steps` | Lists ordered step attempts for a transformation run. | Reads safe step lifecycle information only after run authorization. The LLM can explain where a run succeeded or stopped. |
| `resource_registry_list_transformation_outputs` | Lists outputs produced by a transformation run. | Reads authorized output references without exposing storage locations. The LLM can identify the output available to the user. |
| `resource_registry_get_transformation_diagnostics` | Returns sanitized aggregate diagnostics for a transformation run. | Requires its diagnostics capability and reads bounded diagnostic information. The LLM can communicate a useful failure summary without leaking internals. |

## Relationships and composition rules

The catalog supports composition, but an ID from one BIF is only a candidate for another BIF.
Each receiving BIF checks its own capability, tenant, Workspace scope, and dataset authorization.

| Starting BIF | Typical follow-up | Relationship |
| --- | --- | --- |
| `list_datasets` | `get_dataset`, schema, profile, versions | Discover a dataset before inspecting it. |
| `get_dataset` / schema | explore, plan query, query, analyze, create chart, plan export | Confirm the dataset and its columns before data operations. |
| `start_query_job` | query job status → query job result | Submit and observe durable query work. |
| `start_intelligence_job` | intelligence status → intelligence result | Submit and observe durable analysis work. |
| `query_dataset` / `analyze_dataset` | create chart, plan export, get query run/analysis artifact | Turn analysis evidence into a reusable presentation or retrieve it later. |
| `plan_export` | get export plan → generate artifact | Review or resume an export workflow. |
| `start_transformation_run` | get/list runs → steps/outputs/diagnostics | Observe an approved transformation lifecycle. |

## Capability reference

The capability key, risk level, required permissions, scopes, and default allowed roles for every
BIF are defined by `RESOURCE_REGISTRY_BIF_MANIFEST` in
`resource_registry/bifs/manifest.py`. Capability policy may grant, deny, or override a function
for a given tenant or dataset. A Persona must still have the BIF assigned for the assistant to
offer it.

## Related documentation

- [Resource Registry BIF overview](RESOURCE_REGISTRY_BIFS_OVERVIEW.md)
- [Resource Registry user permissions](RESOURCE_REGISTRY_USER_PERMISSIONS.md)
- [Persona workspace-scope technical reference](RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md)
