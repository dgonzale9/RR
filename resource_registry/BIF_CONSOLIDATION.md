# Grouped Resource Registry tools


Assigned Registry operations are always advertised as at most four grouped tools in all clients.
There is no publication switch. A Persona with all 31 operations receives all
four; empty groups and unassigned actions are omitted. Canonical BIF database records, assignments,
capabilities and internal names remain unchanged. Grouping itself does not migrate assignments.
Data migration `BotServer.0114_seed_resource_registry_transformation_bifs` registers the six
transformation operations if an environment lacks them. The same migration then
removes the six obsolete transformation records without the `resource_registry_` prefix, after
moving their existing assignments and permissions to their corresponding canonical operations.

| Tool | Actions (original name without `resource_registry_`) |
| --- | --- |
| `resource_registry_inspect` | `list_datasets`, `get_dataset`, `get_dataset_context`, `get_dataset_schema`, `get_dataset_profile`, `list_dataset_versions`, `list_external_jobs`, `explore_dataset`, `analyze_dataset` |
| `resource_registry_query` | `plan_query`, `query_dataset`, `create_filtered_dataset`, `get_query_run`, `start_query_job`, `get_query_job_status`, `get_query_job_result` |
| `resource_registry_manage_outputs` | `create_chart`, `plan_export`, `generate_artifact`, `get_analysis_artifact`, `get_chart_artifact`, `get_export_plan`, `start_intelligence_job`, `get_intelligence_job_status`, `get_intelligence_job_result` |
| `resource_registry_manage_transformations` | `start_transformation_run`, `get_transformation_run`, `list_transformation_runs`, `list_transformation_steps`, `list_transformation_outputs`, `get_transformation_diagnostics` |

Call `resource_registry_inspect` with:

```json
{"action": "analyze_dataset", "arguments": {"dataset_id": "<dataset UUID>", "analysis_type": "summary"}}
```

Each action preserves its original parameters, variants, defaults, responses and effects.
`inspect/analyze_dataset` may persist analysis artifacts; the group is not a read-only permission.
Intelligence jobs retain every existing operation, including legacy transformations. They do not
replace the separate transformation-run contract. Nested `operation` and `payload` values remain
inside `arguments`. `input_bindings` is advertised as the array required by transformation handlers.
Exploration retains strings, arrays of strings and wrapper objects for `columns` and `terms`.

The dispatcher resolves the action and rechecks its original persisted Persona assignment on
every call. Capability denials, namespace admission, Dataset restrictions and job ownership remain
enforced by the original operation. Verified conversation selectors are resolved before required
arguments are checked. No jobs, retries or execution chains are introduced by the projection.

Grouping, parameter loading, provider envelopes and dispatch translation live in
`resource_registry/bifs/`. BotServer only connects each client's existing assignment collection
to this adapter and recognizes registered Registry tool names. Its original formatter and
dispatcher body remain responsible for other BIFs. No generic tool catalog is introduced.
Only Registry relationships and parameters are loaded in bulk; unrelated
assignments keep their original formatter and relative order. Grouped execution checks the
original assignment twice (before validation and again through the original dispatcher),
but executes the handler once.

`BotServer.builtin_functions.__all__` and its lazy function map expose the four
Registry group names. Grouping selects the explicit 31 operation names, matching the
full Registry capability manifest.
Each grouped entry point receives the active dispatch client, the original
request and the action/arguments envelope; it forwards the trusted execution channel unchanged.
The host's Registry guard uses these entry points before canonical operation admission.
After admission, canonical Registry calls resolve through
`resource_registry.bifs.operation_handlers` instead of the public host namespace.
Other BIFs continue to use the host's original lazy resolver.

Saved calls using the 31 original operation names still work through `callGenericFunction`.
The original handlers remain in `BotServer.builtin_functions.resource_registry_functions`
and `resource_registry.bifs.transformation`, retaining their existing Python signatures.
Direct imports of the old operation names from `BotServer.builtin_functions` are removed;
internal Python consumers must import from the owning implementation module.
No compatibility aliases repopulate the public host namespace.

OpenAI Chat, OpenAI Responses, Gemini and Anthropic use the same Registry projection. Gemini uses
[`parametersJsonSchema`](https://ai.google.dev/api/generate-content#FunctionDeclaration) for the
grouped schemas. Responses explicitly uses
[`strict: false`](https://developers.openai.com/api/docs/guides/function-calling#strict-mode)
so action-specific optional arguments are not normalized into mandatory fields. Action validation
remains server-side. Unrelated tools retain their existing schemas.

Campaign rendering continues to hide only `explore_dataset`; other inspection actions remain
available. Existing internal routing, saved history and result compaction use canonical operation
names. Grouped responses translate recommendation metadata to the grouped name plus
`suggested_action` or `recommended_action`, without rewriting dataset content.

### Administrative catalog and assignments

`/api/v1/botserver/built-in-function` and its session-auth counterpart group the same 31
canonical records **before pagination**. `resource_registry_manage_outputs` and
`resource_registry_manage_transformations` are temporarily hidden from this administrative
discovery catalog. Their individual operations do not appear as fallback entries. Only operations
in the caller's existing secure queryset contribute actions; empty groups remain hidden. A caller
with access to the visible operations sees two Registry entries: Inspect and Query. Other BIFs
retain their metadata and relative order. This presentation rule lives in `admin_catalog.py`; it introduces
no configuration flag or migration. All four groups remain available to models according to
existing assignments. Assigned output/transformation groups remain visible in Persona relationship
lists so administrators can inspect and remove them. No records or assignments are deleted.

Groups use stable, virtual numeric catalog IDs: `inspect=-4101`, `query=-4102`,
`manage_outputs=-4103`, `manage_transformations=-4104`. They are not persisted BIF primary keys.
Each group's `actions` includes the canonical `builtin_function_id` for individual assignment
clients. Categories and tags are merged from the visible operations. Other record-level metadata
comes from the first visible member; it does not describe a persisted group record.

The existing Persona batch add/remove endpoint accepts these group IDs. Group additions require
change access to the Persona, recheck current operation visibility, and use the existing service's
organization admission and per-operation relationships. Removal checks delete access for each
actual relationship. Partial failures retain the existing HTTP 207 contract, with rejection IDs
mapped to the requested group so existing UI clients can identify failed selections. Repeated
requests and overlapping group/operation IDs do not create duplicate assignments. Existing
positive IDs still use the original endpoint path.

Persona relationship lists and assignment responses use the same virtual catalog IDs, with
`actions` limited to the operations actually assigned. Reading a partial assignment never expands
it. `relationship_ids` retains all underlying relationship IDs; the single-relationship endpoint
continues to address individual persisted relationships. Clients requiring individual action
selection can submit the canonical positive IDs from `actions` through the existing batch API.
The current UI selects whole groups; it preserves existing partial assignments on reload.

The group entries are projections, so changing the Python export list alone cannot update the
admin API. No persisted group records, new permission types, or frontend changes are required.
The backend serving the frontend proxy must run this code for the grouped catalog to appear.

### Migration between environments

The Registry catalog migrations extend dev's `0112_add_persona_primary_category_assignment` in order:
`0113_register_agent_dataset_replacement_bifs` (a retained no-op slot) and
`0114_seed_resource_registry_transformation_bifs`. The former chat-import migration and
`resource_registry.0004_agent_dataset_replacement` are not part of this graph.

Renaming files does not update `django_migrations`. A database that already records either
removed BotServer migration name is not on this repository's graph and requires a separate,
approved deployment reconciliation. Do not blanket-fake a migration: verify the transformation
registration and cleanup data before recording `0114`. This code change does not modify existing
database history.

| Prior branch migration | Current graph |
| --- | --- |
| `0113_register_agent_dataset_replacement_bifs` | retained as a no-op compatibility slot |
| `0114_enable_chat_dataset_import_bif` | removed |
| `0115_seed_resource_registry_transformation_bifs` | `0114_seed_resource_registry_transformation_bifs` |

Apply `BotServer.0114_seed_resource_registry_transformation_bifs` through the normal deployment
migration step. It follows the retained `0113` slot. It adds only missing canonical transformation
BIFs and their 24 parameters using frozen metadata and historical models. It preserves existing
IDs, descriptions, parameter customizations, permissions and Persona assignments.
It uses the existing `uniadmin` catalog owner, falling back to `AnonymousUser`, consistently with
earlier catalog migrations. It grants no new object permissions or runtime capabilities; manage
visibility and Persona assignments through the existing administration flow.

The migration is atomic and safe to repeat. Its reverse is intentionally a no-op so rolling
application code back does not delete operations or assignments needed by saved calls. Legacy
`FunctionParameter` storage has no array type; the `input_bindings` JSON parameter is stored as
`object`, while the authoritative Registry schema continues to publish and validate an array.

The second operation in `BotServer.0114_seed_resource_registry_transformation_bifs` deletes exactly these obsolete
names if present: `start_transformation_run`, `get_transformation_run`, `list_transformation_runs`,
`list_transformation_steps`, `list_transformation_outputs`, and `get_transformation_diagnostics`.
It does not add compatibility aliases. Their prefixed canonical operations remain inside
`resource_registry_manage_transformations`.

Before deletion, it transfers existing Persona assignments and user/group object permissions only
to the matching canonical operation. An assignment keeps its relationship ID unless the Persona
already has both versions; in that case the canonical relationship survives with their combined
permissions. Transfers run in batches in one transaction. Missing canonical replacements abort
the migration before any deletion. Other BIFs remain intact.
Registration and cleanup share one migration transaction, so a cleanup failure rolls back both
phases. Repeated execution is safe. Both reverse operations are intentionally no-ops: they do not recreate deleted
records or their obsolete parameters, and old unprefixed IDs stop being valid.

An environment missing the canonical records cannot assign or publish their model actions until
migration and the normal permissions/assignments are in place. The transformation group remains
hidden from administrative discovery under the temporary presentation rule above. Apply the
consolidated migration through the normal deployment migration step, or target it manually:

```bash
uv run --env-file .env python manage.py migrate BotServer 0114_seed_resource_registry_transformation_bifs
```

Tests run on isolated PostgreSQL databases. Staging and production application remain part of
the normal deployment process; the local cleanup uses this same migration rather than direct SQL.

### Validation and deployment

1. In staging, compare Personas with full, partial and no Registry assignments on all four clients.
2. Verify that only grouped Registry tools are advertised, with exactly the assigned actions.
3. Verify discovery, analysis, sync/async query, output retrieval and
   transformation submission/polling. Include pinned Dataset versions, ambiguous selections,
   revoked assignments, restricted datasets, foreign jobs and campaign rendering.
4. Verify `REGISTRY_GROUPED_TOOL_COMPLETED` logs contain tool/action, outcome and duration, without
   arguments, query text or results. Confirm handlers are invoked once per call.
5. Complete pending calls using both grouped and original names. Original names remain accepted
   for existing conversations and internal consumers, but are never advertised to the model.

Grouped publication takes effect whenever this implementation runs. Reverting to the old
publication behavior requires restoring an earlier code version; it cannot be changed through
configuration. This task delivers local changes only; commit, push and deployment are manual.

### Local verification

Run PostgreSQL tests with local storage set only for that process, before Django initializes.
Do not change `.env` or global test settings:

```bash
uv run --env-file .env --no-sync python -c 'import os, sys; os.environ["AZ_STORAGE_ACCOUNT_NAME"] = ""; import pytest; sys.exit(pytest.main(sys.argv[1:]))' \
  -q --reuse-db tests/botserver/test_grouped_resource_registry_tools_integration.py \
  tests/botserver/test_resource_registry_bif_cross_client_contracts_integration.py \
  resource_registry/tests/test_grouped_bif_tools.py \
  resource_registry/tests/test_grouped_bif_boundaries.py \
  resource_registry/tests/test_grouped_bif_namespace.py \
  tests/botserver/test_grouped_registry_admin_catalog.py \
  tests/botserver/test_registry_transformation_catalog_migration.py \
  tests/botserver/test_legacy_transformation_bif_removal_migration.py
```

For a full migration/catalog check, use a fresh isolated PostgreSQL test database.
Reusing a database after transaction tests have flushed it can leave migration-seeded BIF
records absent even though its migration history is up to date.
