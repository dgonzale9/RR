# Registry performance baseline (E1.1–E1.3)

Registry timing is disabled by default. Set `RESOURCE_REGISTRY_PERFORMANCE_ENABLED=true`
in the process environment to emit events through the `resource_registry.performance`
Python logger. `RESOURCE_REGISTRY_PERFORMANCE_SAMPLE_RATE` accepts a number from
`0.0` to `1.0` (default `1.0` when enabled); `0.0` never samples, `1.0` always
samples, and intermediate values use a random draw per stage. The optional
`RESOURCE_REGISTRY_PERFORMANCE_SINK=noop` disables emission. Invalid rates use
`1.0`. Configuration is read once per process; restart workers to change it.
Set `RESOURCE_REGISTRY_PERFORMANCE_SQL_ENABLED=true` to add E1.2 SQL profiling
to sampled events. SQL profiling is disabled by default and has no effect when
E1.1 timing is disabled, sampled out, or set to the `noop` sink. The SQL flag
uses the same process-stable configuration and requires a worker restart to change.
Set `RESOURCE_REGISTRY_PERFORMANCE_PAYLOAD_ENABLED=true` to add E1.3
publication metrics. It is disabled by default, uses the same sampling
decision as the `registry_tool_publication` stage, and has no effect when E1.1
is disabled or the `noop` sink is selected. Restart workers to change the flag.

The logger inherits the existing root handler under the repository's current
logging configuration. Deployments that override root logger level, handlers, or
propagation may suppress events. E1.1 does not configure handlers or guarantee
delivery to an external collector.

Each E1.1 event contains only `schema_version`, `event`, `stage`, `operation`,
`outcome`, `duration_ms`, and `inclusive`. An active E1.2 profile adds only
`db_query_count`, `db_duration_ms`, and `db_alias_count`; zero-query stages
emit zeroes. Counts and durations are nonnegative, finite, and capped at
2,147,483,647 queries, 1,024 aliases, and 86,400,000 milliseconds. Saturated
values are lower bounds, not exact counts. Stage and outcome values are fixed;
operation values are restricted to the four public Registry groups, registered
canonical Registry actions, or `unknown`.
An active E1.3 publication event also adds `tool_count`, `action_count`,
`schema_property_count`, `description_char_count`, and `payload_bytes`.
These numeric fields are capped at 2,147,483,647; a saturated value is a lower
bound. E1.1/E1.2 events keep their original schema when E1.3 is disabled.
No request, Persona, tenant, workspace, user, or dataset identifiers; prompts,
tool arguments, dataset content, raw SQL, query parameters, database alias names,
credentials, or exception details are recorded. A rejected or failed operation
retains its original public result or exception. A failed SQL execution counts
as one attempted query.

Durations are **inclusive**. Nested stages overlap and must not be added
together. SQL query counts and SQL durations are also inclusive. A query in an
inner stage appears in its outer stage's count and must not be summed across
stage events for an aggregate report. Exclusive SQL measurements are deferred.
`duration_ms` measures stage wall time; `db_duration_ms` sums time at Django's
execution-wrapper boundary, including possible driver and network waiting.
It is not database server execution time. Nested SQL wrappers can add a small
amount of wrapper overhead to outer SQL duration measurements.
`canonical_bif_dispatch_bridge` and `grouped_dispatch_bridge` include
calls into shared dispatch and are separate from Registry-owned stage timing.
`deterministic_routing` and `grouped_call_total` can also include nested bridge
time; they are orchestration envelopes, not an exclusive Registry CPU total.
SQL captured in bridge or orchestration envelopes may originate in shared code;
do not claim that every captured query is Registry-owned.
These events do not measure end-to-end `/chat` latency, provider network time,
streaming, or shared agent orchestration. Benchmark reports belong to E1.4.

E1.3 measures only the grouped Registry definitions returned by
`prepare_registry_tools()`, after assigned operations have been grouped,
Persona/request presentation filtering has run, and Registry's provider envelope
has been applied. An empty publication has zero tools/actions and a two-byte
`[]` payload. `tool_count` is the number of returned grouped definitions;
`action_count` sums the string values in each returned schema's `action.enum`.
It never assumes that all 31 actions are available. Assignment and capability
checks remain with their existing owners; this metric grants no authority.

`payload_bytes` is the UTF-8 byte length of the entire returned Registry tool
list serialized with `json.dumps(..., ensure_ascii=False, sort_keys=True,
separators=(",", ":"), allow_nan=False)`. This deterministic serialization is
measurement-only: it is never persisted or substituted for the published
definitions. It includes Registry's provider-formatted envelope where available
for OpenAI, OpenAI Responses, Gemini, and Anthropic. Later transformations by
provider clients or SDKs are outside this measurement. JSON bytes are not LLM
tokens, provider request bytes, or provider tokenization cost.

`schema_property_count` counts each entry in every physically present
`properties` mapping in the returned definitions, including nested properties,
`$defs`, and every `oneOf`, `anyOf`, or `allOf` branch. `$ref` is not resolved;
its target is counted only if physically present elsewhere in the payload.
Repeated fragments count once per occurrence, even when their contents match.
`description_char_count` sums Python Unicode character lengths of all
physically present `description` string fields. This includes grouped tool
descriptions (which embed available action descriptions) and parameter/schema
descriptions; it is not a byte or token count. Neither descriptions nor schemas
are emitted in metrics.

`grouped_schema_build`, `grouped_schema_validation`, and
`registry_tool_publication` already emit inclusive E1.1 `duration_ms` values.
Derive schema build and validation costs from those stage events; E1.3 does not
duplicate them as `schema_build_duration_ms` or
`schema_validation_duration_ms`. Publication wall time is captured before
payload serialization/traversal, so its own measurement overhead is excluded
from that event's duration. Nested stage durations must not be added together.

E1.2 installs Django `execute_wrapper()` only around sampled, measured calls.
It does not require `DEBUG=True` or `connection.queries`, and it does not open
connections or execute SQL itself. The profile covers each configured alias on
the current thread or Django execution context, including the optional `replica`
alias used by the project read router. The event reports only how many aliases
executed queries, not their names. Queries sent to another thread, another
async context, a spawned job, a provider, or a non-Django database client are
not captured. Such work must not be inferred from the stage count. SQL profiling
does not change authorization, tenant isolation, routing, or validation.

Instrumentation uses a best-effort logger sink. Logging, clock, or sampling
failures do not change Registry execution results. The disabled path performs
only a cached configuration check and never reads the clock or calls the sink.
Wrapper setup, teardown, and measurement failures are also best effort; if
setup fails, the E1.1 event is emitted without SQL fields. The SQL-enabled path
adds one wrapper per configured alias per measured stage and two monotonic clock
reads per query, plus local counter updates. Nested stages add nested wrappers.
E1.3 adds one deterministic serialization and one iterative schema traversal
per sampled publication event. Disabled or unsampled publication never runs
either operation. Collection failures omit E1.3 fields but retain the E1.1/E1.2
event and the original application result. This overhead has not yet been
benchmarked; E1.4 will establish a baseline.
